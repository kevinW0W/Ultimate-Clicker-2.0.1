import os
import sys
import socket
import threading
import time
import math
import struct
import select
import queue
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
import tkinter as tk
from tkinter import ttk, messagebox

# Try to import PyAudioWPatch (best for Windows loopback), fallback to standard PyAudio
try:
    import pyaudiowpatch as pyaudio
    HAS_WPATCH = True
except ImportError:
    try:
        import pyaudio
        HAS_WPATCH = False
    except ImportError:
        print("Error: PyAudio is required. Install using 'pip install pyaudio' or 'pip install PyAudioWPatch'")
        sys.exit(1)

# Global Configuration & State
SERVER_PORT = 8080
CHUNK_SIZE = 1024
CHANNELS = 2
SAMPLE_RATE = 44100
BITS_PER_SAMPLE = 16

active_streams = []
stream_lock = threading.Lock()
running = True
selected_device_index = None
audio_peak_level = 0.0

def get_local_ip():
    """Gets the local Wi-Fi IP address of the machine."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Doesn't need to connect, just gets local routing interface
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


# Web Receiver HTML Client (Embedded)
# Formatted to support ancient WebKit/Safari engines (specifically iOS 12 Safari)
HTML_RECEIVER = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Local Wi-Fi Audio Receiver</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        .pulse-ring {
            animation: pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite;
        }
        @keyframes pulse {
            0%, 100% { transform: scale(1); opacity: 0.5; }
            50% { transform: scale(1.1); opacity: 0.1; }
        }
        /* Style range slider track and thumb for premium dark look across older browsers */
        input[type=range]::-webkit-slider-thumb {
            -webkit-appearance: none;
            appearance: none;
            width: 16px;
            height: 16px;
            border-radius: 50%;
            background: #38bdf8;
            cursor: pointer;
            box-shadow: 0 0 6px rgba(56, 189, 248, 0.5);
            margin-top: -4px;
        }
        input[type=range]::-moz-range-thumb {
            width: 16px;
            height: 16px;
            border-radius: 50%;
            background: #38bdf8;
            cursor: pointer;
            border: none;
            box-shadow: 0 0 6px rgba(56, 189, 248, 0.5);
        }
        input[type=range]::-webkit-slider-runnable-track {
            width: 100%;
            height: 8px;
            cursor: pointer;
            background: #1e293b;
            border-radius: 4px;
        }
    </style>
</head>
<body class="bg-slate-900 text-white font-sans flex flex-col justify-between min-h-screen">

    <!-- Top Status Header -->
    <header class="p-6 border-b border-slate-800 flex justify-between items-center bg-slate-950">
        <div class="flex items-center space-x-2">
            <span class="w-3 h-3 rounded-full bg-emerald-500 shadow-[0_0_8px_#10b981]"></span>
            <h1 class="text-lg font-bold tracking-wide">WI-FI RECEIVER</h1>
        </div>
        <span class="text-xs bg-slate-800 text-slate-300 px-3 py-1 rounded-full uppercase tracking-wider font-semibold">
            iOS 12 Compatible
        </span>
    </header>

    <!-- Main Controller Area -->
    <main class="flex-grow flex flex-col items-center justify-center p-6 text-center">
        <div class="max-w-md w-full space-y-8">
            <div class="relative flex justify-center items-center">
                <!-- Visual Feedback Rings -->
                <div id="ring-1" class="absolute w-44 h-44 rounded-full border-2 border-emerald-500/20 pulse-ring hidden"></div>
                <div id="ring-2" class="absolute w-56 h-56 rounded-full border border-emerald-500/10 pulse-ring hidden" style="animation-delay: 0.5s;"></div>
                
                <!-- Main Action Button (Required for iOS Safari user interaction bypass) -->
                <button id="playBtn" class="relative z-10 w-36 h-36 bg-gradient-to-tr from-emerald-600 to-emerald-400 hover:from-emerald-500 hover:to-teal-400 active:scale-95 transition-all rounded-full flex flex-col items-center justify-center shadow-lg shadow-emerald-500/30 border border-emerald-300/30">
                    <svg id="playIcon" class="w-16 h-16 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z"></path>
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                    </svg>
                    <svg id="stopIcon" class="w-16 h-16 text-white hidden" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 10a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1H10a1 1 0 01-1-1v-4z"></path>
                    </svg>
                </button>
            </div>

            <div class="space-y-2">
                <h2 id="statusText" class="text-2xl font-bold tracking-tight text-slate-100">Tap to Connect & Listen</h2>
                <p id="subText" class="text-sm text-slate-400">Streams loopback desktop audio via secure Wi-Fi node</p>
            </div>

            <!-- Volume Slider -->
            <div id="volumeControl" class="bg-slate-950 p-4 rounded-2xl border border-slate-800/80 space-y-2 max-w-xs mx-auto">
                <div class="flex justify-between text-xs text-slate-400">
                    <span>Receiver Volume</span>
                    <span id="volumeVal">100%</span>
                </div>
                <input type="range" id="volumeSlider" min="0" max="1" step="0.05" value="1" class="w-full accent-emerald-400 bg-slate-800 rounded-lg appearance-none h-2 cursor-pointer">
            </div>
        </div>
    </main>

    <!-- Footer Stats -->
    <footer class="p-6 text-center text-xs text-slate-500 border-t border-slate-900 bg-slate-950/50">
        <div>Device Channel: <span id="lblInfo" class="text-slate-400 font-mono">Web Audio Engine Loaded</span></div>
        <div class="mt-1">Active Receiver Session &middot; Peer Node</div>
    </footer>

    <!-- Legacy iOS 12 Audio & Web Engine Compatibility Scripts -->
    <script>
        var audioCtx = null;
        var isPlaying = false;
        var reader = null;
        var carryOver = null;
        var nextStartTime = 0;
        var streamSampleRate = 44100;
        var streamChannels = 2;

        const playBtn = document.getElementById('playBtn');
        const playIcon = document.getElementById('playIcon');
        const stopIcon = document.getElementById('stopIcon');
        const statusText = document.getElementById('statusText');
        const subText = document.getElementById('subText');
        const ring1 = document.getElementById('ring-1');
        const ring2 = document.getElementById('ring-2');
        const volumeSlider = document.getElementById('volumeSlider');
        const volumeVal = document.getElementById('volumeVal');
        const lblInfo = document.getElementById('lblInfo');

        // Dynamic Volume Slider Handler
        volumeSlider.addEventListener('input', function(e) {
            var val = Math.round(e.target.value * 100);
            volumeVal.innerText = val + "%";
        });

        // Main Tap Action Handler (Bypasses iOS 12 user gesture requirement)
        playBtn.addEventListener('click', function() {
            if (!isPlaying) {
                startStreaming();
            } else {
                stopStreaming();
            }
        });

        function startStreaming() {
            isPlaying = true;
            statusText.innerText = "Connecting Stream...";
            subText.innerText = "Initiating raw audio stream reader...";
            
            // Toggle icons & active visual animations
            playIcon.classList.add('hidden');
            stopIcon.classList.remove('hidden');
            ring1.classList.remove('hidden');
            ring2.classList.remove('hidden');

            // Force Web Audio API context configuration (essential workaround for iOS 12 Safari)
            try {
                var AudioContextClass = window.AudioContext || window.webkitAudioContext;
                if (AudioContextClass) {
                    audioCtx = new AudioContextClass();
                    if (audioCtx.state === 'suspended') {
                        audioCtx.resume();
                    }
                    nextStartTime = audioCtx.currentTime;
                }
            } catch (e) {
                console.warn("Web Audio API not supported on this device/browser");
            }

            // Fetch live raw audio data directly
            fetch("/audio.raw?t=" + new Date().getTime())
                .then(function(response) {
                    if (!response.ok) {
                        throw new Error("HTTP error " + response.status);
                    }
                    
                    // Retrieve dynamic sample rate & channels from custom headers
                    streamSampleRate = parseInt(response.headers.get('X-Sample-Rate')) || 44100;
                    streamChannels = parseInt(response.headers.get('X-Channels')) || 2;
                    lblInfo.innerText = "16-bit PCM @ " + (streamSampleRate / 1000) + "kHz " + (streamChannels === 2 ? "Stereo" : "Mono");

                    reader = response.body.getReader();
                    statusText.innerText = "Streaming Active";
                    statusText.className = "text-2xl font-bold tracking-tight text-emerald-400";
                    subText.innerText = "Receiving system audio over local Wi-Fi";
                    
                    carryOver = null;
                    return readChunks();
                })
                .catch(function(err) {
                    console.error("Playback failed: ", err);
                    statusText.innerText = "Connection Failed";
                    statusText.className = "text-2xl font-bold tracking-tight text-red-400";
                    subText.innerText = "Ensure server node is running and accessible";
                    stopStreaming();
                });
        }

        function readChunks() {
            if (!isPlaying || !reader) return;

            reader.read().then(function(result) {
                if (result.done) {
                    stopStreaming();
                    return;
                }
                processAudioChunk(result.value);
                readChunks();
            }).catch(function(err) {
                console.error("Stream read error: ", err);
                stopStreaming();
            });
        }

        function processAudioChunk(uint8Array) {
            if (!audioCtx || !isPlaying) return;

            var data = uint8Array;
            
            // Re-align byte stream if chunks were split mid-frame
            if (carryOver && carryOver.length > 0) {
                var combined = new Uint8Array(carryOver.length + uint8Array.length);
                combined.set(carryOver);
                combined.set(uint8Array, carryOver.length);
                data = combined;
                carryOver = null;
            }

            // Frame byte-size calculation: channels * 2 bytes (16-bit Int)
            var bytesPerFrame = streamChannels * 2;
            var totalFrames = Math.floor(data.length / bytesPerFrame);
            var excessBytes = data.length % bytesPerFrame;

            if (excessBytes > 0) {
                carryOver = data.slice(data.length - excessBytes);
            }

            if (totalFrames === 0) return;

            // Generate temporary AudioBuffer node
            var audioBuffer = audioCtx.createBuffer(streamChannels, totalFrames, streamSampleRate);
            var leftChannel = audioBuffer.getChannelData(0);
            var rightChannel = streamChannels === 2 ? audioBuffer.getChannelData(1) : null;

            var dataView = new DataView(data.buffer, data.byteOffset, totalFrames * bytesPerFrame);
            var vol = parseFloat(volumeSlider.value);

            for (var i = 0; i < totalFrames; i++) {
                if (streamChannels === 2) {
                    var leftSample = dataView.getInt16(i * 4, true);
                    var rightSample = dataView.getInt16(i * 4 + 2, true);
                    leftChannel[i] = (leftSample / 32768.0) * vol;
                    rightChannel[i] = (rightSample / 32768.0) * vol;
                } else {
                    var sample = dataView.getInt16(i * 2, true);
                    leftChannel[i] = (sample / 32768.0) * vol;
                }
            }

            // Create Scheduled Node source to bypass seeking requirements entirely
            var source = audioCtx.createBufferSource();
            source.buffer = audioBuffer;
            source.connect(audioCtx.destination);

            var currentTime = audioCtx.currentTime;
            if (nextStartTime < currentTime) {
                // Recover from underrun gaps smoothly
                nextStartTime = currentTime + 0.05;
            }

            source.start(nextStartTime);
            nextStartTime += audioBuffer.duration;
        }

        function stopStreaming() {
            isPlaying = false;
            statusText.innerText = "Stream Disconnected";
            statusText.className = "text-2xl font-bold tracking-tight text-slate-100";
            subText.innerText = "Tap the button to reconnect to local host";
            
            playIcon.classList.remove('hidden');
            stopIcon.classList.add('hidden');
            ring1.classList.add('hidden');
            ring2.classList.add('hidden');

            if (reader) {
                try {
                    reader.cancel();
                } catch(e) {}
                reader = null;
            }

            if (audioCtx) {
                try {
                    audioCtx.close();
                } catch(e) {}
                audioCtx = null;
            }
        }
    </script>
</body>
</html>
"""

class AudioHTTPStreamHandler(BaseHTTPRequestHandler):
    """Handles HTTP requests, serving the Web Client or streaming loopback audio chunks."""
    
    def log_message(self, format, *args):
        # Suppress logging spam in console for continuous streams
        return

    def do_GET(self):
        global active_streams, stream_lock
        
        if self.path == '/' or self.path == '/index.html':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
            self.end_headers()
            self.wfile.write(HTML_RECEIVER.encode('utf-8'))
            
        elif self.path.startswith('/audio.raw'):
            # Fetch current properties under lock safely
            with stream_lock:
                current_sample_rate = SAMPLE_RATE
                current_channels = CHANNELS
                current_bits = BITS_PER_SAMPLE

            # Web Audio continuous live binary octet transmission
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Transfer-Encoding', 'chunked')
            self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
            self.send_header('Connection', 'keep-alive')
            self.send_header('Access-Control-Allow-Origin', '*')
            # Dynamic system config mapping headers to auto-configure iOS client AudioContext sampler
            self.send_header('X-Sample-Rate', str(current_sample_rate))
            self.send_header('X-Channels', str(current_channels))
            self.send_header('Access-Control-Expose-Headers', 'X-Sample-Rate, X-Channels')
            self.end_headers()

            # Create a queue for this specific client connection
            client_queue = queue.Queue(maxsize=128)

            with stream_lock:
                active_streams.append(client_queue)
            
            try:
                # Continually capture blocks from system loopback queue and push to client
                while running:
                    try:
                        # Grab audio frames from our loopback engine loop (short timeout for swift exit)
                        audio_data = client_queue.get(timeout=0.5)
                        self.send_chunk(audio_data)
                    except queue.Empty:
                        # Send null/silent chunk to keep connection alive if queue momentarily starves
                        silent_chunk = b'\x00' * (CHUNK_SIZE * current_channels * (current_bits // 8))
                        self.send_chunk(silent_chunk)
            except Exception:
                pass  # Clean exit on client disconnect, page refresh, or connection aborts
            finally:
                with stream_lock:
                    if client_queue in active_streams:
                        active_streams.remove(client_queue)

        else:
            self.send_error(404, "Resource Not Found")

    def send_chunk(self, data):
        """Standard HTTP Chunked Transfer format."""
        chunk_size_header = f"{len(data):X}\r\n".encode('ascii')
        self.wfile.write(chunk_size_header)
        self.wfile.write(data)
        self.wfile.write(b"\r\n")
        self.wfile.flush()


class RobustHTTPServer(ThreadingMixIn, HTTPServer):
    """Custom Threading HTTP Server that handles requests in separate threads and silences benign client connection errors."""
    daemon_threads = True

    def handle_error(self, request, client_address):
        exc_type, exc_val, _ = sys.exc_info()
        # Suppress standard library socketserver exception spams for client disconnections and Windows abort status codes
        if exc_type in (ConnectionResetError, ConnectionAbortedError, BrokenPipeError) or (
            isinstance(exc_val, OSError) and getattr(exc_val, 'errno', None) in (10053, 10054)
        ):
            return
        super().handle_error(request, client_address)


class AudioServerThread(threading.Thread):
    """Background thread running the lightweight HTTP service."""
    def __init__(self, host, port):
        super().__init__()
        self.host = host
        self.port = port
        self.daemon = True
        self.httpd = None

    def run(self):
        try:
            self.httpd = RobustHTTPServer((self.host, self.port), AudioHTTPStreamHandler)
            self.httpd.serve_forever()
        except Exception as e:
            print(f"Server Thread Exception: {e}")

    def shutdown(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()


class AudioCaptureThread(threading.Thread):
    """Captures default Loopback / Stereo Mix hardware signal to stream to registered clients."""
    def __init__(self, device_index):
        super().__init__()
        self.device_index = device_index
        self.daemon = True

    def run(self):
        global running, active_streams, stream_lock, audio_peak_level, SAMPLE_RATE, CHANNELS
        p = pyaudio.PyAudio()

        try:
            device_info = p.get_device_info_by_index(self.device_index)
            # Fetch loopback or standard input configuration
            rate = int(device_info.get("defaultSampleRate", SAMPLE_RATE))
            channels = int(device_info.get("maxInputChannels", CHANNELS))
            if channels > 2:
                channels = 2 # Clamp to stereo for transmission efficiency

            # Update engine dynamics globally
            SAMPLE_RATE = rate
            CHANNELS = channels

            stream = p.open(
                format=pyaudio.paInt16,
                channels=channels,
                rate=rate,
                input=True,
                input_device_index=self.device_index,
                frames_per_buffer=CHUNK_SIZE
            )
        except Exception as e:
            messagebox.showerror("Capture Device Error", f"Unable to hook into audio source:\n{e}")
            p.terminate()
            return

        while running:
            try:
                # Read raw frames from our targeted desktop loopback source
                data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
                if not data:
                    continue

                # Calculate real-time Peak Level for GUI visualizer meter
                count = len(data) // 2
                shorts = struct.unpack("%dh" % count, data)
                if shorts:
                    max_val = max(abs(s) for s in shorts)
                    # Smooth peak meter decay over time
                    target_peak = max_val / 32768.0
                    audio_peak_level = audio_peak_level * 0.7 + target_peak * 0.3

                # Distribute audio frame block to all active browser receivers
                with stream_lock:
                    for client_queue in active_streams:
                        if not client_queue.full():
                            client_queue.put_nowait(data)
            except Exception:
                break

        try:
            stream.stop_stream()
            stream.close()
        except Exception:
            pass
        p.terminate()


# Tkinter Graphical User Interface
class StreamerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Wi-Fi SoundCard & Receiver System")
        self.root.geometry("520x460")
        self.root.resizable(False, False)

        self.server_thread = None
        self.capture_thread = None
        self.server_running = False

        self.style = ttk.Style()
        self.style.theme_use('clam')
        self.setup_ui_styles()
        self.create_widgets()
        
        # Populate and focus loopback devices on startup
        self.load_audio_devices()

    def setup_ui_styles(self):
        # Configure a sleek dark theme
        self.root.configure(bg="#0f172a")
        self.style.configure(".", background="#0f172a", foreground="#f1f5f9")
        self.style.configure("TLabel", background="#0f172a", foreground="#cbd5e1", font=("Segoe UI", 10))
        self.style.configure("Header.TLabel", font=("Segoe UI", 14, "bold"), foreground="#38bdf8")
        self.style.configure("Sub.TLabel", font=("Segoe UI", 8), foreground="#64748b")
        self.style.configure("TButton", font=("Segoe UI", 10, "bold"), borderwidth=0, focuscolor="none")
        self.style.map("TButton",
            background=[("active", "#0284c7"), ("!disabled", "#0369a1")],
            foreground=[("active", "#ffffff"), ("!disabled", "#ffffff")]
        )

        # Style the Entry widget to match the dark theme, eliminating any white spot background
        self.style.configure("TEntry", fieldbackground="#1e293b", foreground="#f1f5f9", insertcolor="#f1f5f9", bordercolor="#475569", lightcolor="#475569")
        self.style.map("TEntry", 
            fieldbackground=[("disabled", "#0f172a")],
            foreground=[("disabled", "#64748b")]
        )
        
        # Style the Combobox widget to match the dark theme, eliminating any white spot background
        self.style.configure("TCombobox", fieldbackground="#1e293b", foreground="#f1f5f9", background="#1e293b", bordercolor="#475569", arrowcolor="#cbd5e1")
        self.style.map("TCombobox", 
            fieldbackground=[("readonly", "#1e293b"), ("disabled", "#0f172a")],
            foreground=[("readonly", "#f1f5f9"), ("disabled", "#64748b")]
        )

        # Style the popup listbox of Combobox dropdown to completely prevent white popups
        self.root.option_add("*TCombobox*Listbox.background", "#1e293b")
        self.root.option_add("*TCombobox*Listbox.foreground", "#f1f5f9")
        self.root.option_add("*TCombobox*Listbox.selectBackground", "#0284c7")
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")

    def create_widgets(self):
        # Top Header Banner
        header_frame = tk.Frame(self.root, bg="#1e293b", height=70)
        header_frame.pack(fill="x", side="top")
        header_frame.pack_propagate(False)

        lbl_title = ttk.Label(header_frame, text="📡 Wi-Fi Loopback Audio Node", style="Header.TLabel", background="#1e293b")
        lbl_title.pack(anchor="w", padx=20, pady=12)

        # Body Container
        body = tk.Frame(self.root, bg="#0f172a", padx=20, pady=15)
        body.pack(fill="both", expand=True)

        # Device Selection Label and Dropdown
        lbl_select = ttk.Label(body, text="Select Desktop System Sound Output Source:")
        lbl_select.pack(anchor="w", pady=(0, 5))

        self.combo_devices = ttk.Combobox(body, state="readonly", font=("Segoe UI", 10))
        self.combo_devices.pack(fill="x", pady=(0, 15))

        # Connection Metadata Panel
        info_frame = tk.LabelFrame(body, text=" Network Hub Status ", bg="#0f172a", fg="#38bdf8", font=("Segoe UI", 10, "bold"), padx=15, pady=15, bd=1, relief="solid", highlightthickness=0)
        info_frame.pack(fill="x", pady=(0, 15))

        # Stream Connection URI
        self.lbl_address = ttk.Label(info_frame, text="Access Address: Server Stopped", font=("Consolas", 12, "bold"), foreground="#38bdf8")
        self.lbl_address.pack(anchor="w", pady=(0, 5))

        self.lbl_instruction = ttk.Label(info_frame, text="Open Safari on iOS 12, enter address and tap CONNECT.", style="Sub.TLabel")
        self.lbl_instruction.pack(anchor="w", pady=(0, 10))

        # Real-time Stats Frame
        stats_frame = tk.Frame(info_frame, bg="#0f172a")
        stats_frame.pack(fill="x")

        self.lbl_clients = ttk.Label(stats_frame, text="Receivers Connected: 0", font=("Segoe UI", 10, "bold"), foreground="#a7f3d0")
        self.lbl_clients.grid(row=0, column=0, sticky="w", padx=(0, 20))

        # Real-time Volume / Peak Meter Canvas
        self.meter_canvas = tk.Canvas(stats_frame, width=150, height=12, bg="#1e293b", highlightthickness=0)
        self.meter_canvas.grid(row=0, column=1, sticky="e")
        self.draw_meter(0.0)

        # Bottom Control Panel
        control_frame = tk.Frame(body, bg="#0f172a")
        control_frame.pack(fill="x", pady=(10, 0))

        self.btn_toggle = ttk.Button(control_frame, text="START STREAM NODE", width=22, command=self.toggle_server)
        self.btn_toggle.pack(side="left")

        # Port selection
        port_frame = tk.Frame(control_frame, bg="#0f172a")
        port_frame.pack(side="right")
        lbl_port = ttk.Label(port_frame, text="Port: ")
        lbl_port.pack(side="left")
        self.entry_port = ttk.Entry(port_frame, width=6, font=("Segoe UI", 10))
        self.entry_port.insert(0, str(SERVER_PORT))
        self.entry_port.pack(side="left")

        # Bottom compatibility advisory text
        advisory_lbl = ttk.Label(body, text="*Windows users: Look for [Loopback] devices to capture system audio output.\nInstall 'PyAudioWPatch' to discover native virtual system soundcards.", style="Sub.TLabel")
        advisory_lbl.pack(anchor="w", side="bottom", pady=(10, 0))

        # Start dynamic periodic checks
        self.periodic_update()

    def draw_meter(self, level):
        """Draws a smooth Peak volume meter directly onto the canvas."""
        self.meter_canvas.delete("all")
        width = 150
        height = 12
        fill_width = int(width * level)
        
        # Draw background track
        self.meter_canvas.create_rectangle(0, 0, width, height, fill="#1e293b", outline="")
        # Gradient color logic for DB peaking (Emerald -> Yellow -> Orange)
        color = "#10b981"
        if level > 0.8:
            color = "#f97316"
        elif level > 0.6:
            color = "#eab308"
            
        self.meter_canvas.create_rectangle(0, 0, fill_width, height, fill=color, outline="")

    def load_audio_devices(self):
        """Enumerates sound interfaces on host platform, highlighting WASAPI loopback entries."""
        p = pyaudio.PyAudio()
        self.devices = []
        device_names = []

        try:
            # Query standard input and custom loopback virtual sound interfaces
            for i in range(p.get_device_count()):
                try:
                    dev_info = p.get_device_info_by_index(i)
                except Exception:
                    continue
                
                name = dev_info.get("name", "Unknown Device")
                max_inputs = dev_info.get("maxInputChannels", 0)
                is_loopback = dev_info.get("isLoopbackDevice", False)
                
                # Filter out pure playback/output devices which can't be set as capturing input
                if max_inputs > 0:
                    tag = " [Loopback]" if (is_loopback or "loopback" in name.lower() or "stereo mix" in name.lower()) else ""
                    self.devices.append((i, name, is_loopback))
                    device_names.append(f"{i}: {name}{tag}")
        finally:
            p.terminate()

        self.combo_devices['values'] = device_names
        
        # Select best loopback device candidate automatically on Windows
        default_index = 0
        for idx, (dev_idx, name, is_loopback) in enumerate(self.devices):
            if is_loopback or "loopback" in name.lower() or "stereo mix" in name.lower():
                default_index = idx
                break
                
        if device_names:
            self.combo_devices.current(default_index)
        else:
            self.combo_devices['values'] = ["No matching audio capture devices found"]
            self.combo_devices.current(0)

    def toggle_server(self):
        global SERVER_PORT, running, active_streams
        if not self.server_running:
            # Validate input port
            try:
                SERVER_PORT = int(self.entry_port.get())
            except ValueError:
                messagebox.showerror("Port Error", "Please provide a valid numeric port.")
                return

            # Grab chosen index
            sel_idx = self.combo_devices.current()
            if not self.devices or sel_idx < 0:
                messagebox.showerror("Source Error", "Ensure an audio loopback source is selected.")
                return
            
            chosen_dev_index = self.devices[sel_idx][0]

            # Fire Up Stream Engine
            running = True
            active_streams = []

            # 1. Start HTTP Server Thread
            local_ip = get_local_ip()
            self.server_thread = AudioServerThread("0.0.0.0", SERVER_PORT)
            self.server_thread.start()

            # 2. Start Hardware Capture Thread
            self.capture_thread = AudioCaptureThread(chosen_dev_index)
            self.capture_thread.start()

            # Update GUI State
            self.server_running = True
            self.combo_devices.configure(state="disabled")
            self.entry_port.configure(state="disabled")
            self.btn_toggle.configure(text="STOP STREAM NODE")
            self.lbl_address.configure(text=f"http://{local_ip}:{SERVER_PORT}")
            
        else:
            # Shutdown and release locks
            running = False
            self.server_running = False
            
            if self.server_thread:
                self.server_thread.shutdown()
                self.server_thread = None
                
            self.btn_toggle.configure(text="START STREAM NODE")
            self.combo_devices.configure(state="readonly")
            self.entry_port.configure(state="normal")
            self.lbl_address.configure(text="Access Address: Server Stopped")
            self.lbl_clients.configure(text="Receivers Connected: 0")
            self.draw_meter(0.0)

    def periodic_update(self):
        """Monitors network client counts and system input signals continuously."""
        if self.server_running:
            with stream_lock:
                client_count = len(active_streams)
            self.lbl_clients.configure(text=f"Receivers Connected: {client_count}")
            self.draw_meter(audio_peak_level)
        self.root.after(100, self.periodic_update)

    def on_close(self):
        global running
        running = False
        if self.server_thread:
            self.server_thread.shutdown()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = StreamerApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()