import sys

# Python 3.13 Audioop Compatibility Bootleg Patch
# This forces transitively dependent libraries (like pydub/shazamio) looking for 'audioop' 
# to seamlessly direct their queries to 'audioop_lts' instead.
try:
    import audioop_lts
    sys.modules['audioop'] = audioop_lts
except ImportError:
    pass

import tkinter as tk
from tkinter import filedialog, messagebox
from tkinter import ttk  # Required for modern progress bar elements
from tkinter.scrolledtext import ScrolledText
import threading
import os
import shutil
import re
import json
import urllib.request
import urllib.parse
import ssl  # Used to fix the SSL: CERTIFICATE_VERIFY_FAILED error
import asyncio  # Required to handle the asynchronous sound recognition library
import time  # Precision clock timer for internal timeline tracking

# Advanced Diagnostic Tracking for environment dependencies
WHISPER_AVAILABLE = False
WHISPER_ERROR = ""
try:
    import whisper
    import torch
    WHISPER_AVAILABLE = True
except Exception as e:
    WHISPER_ERROR = str(e)

MUTAGEN_AVAILABLE = False
MUTAGEN_ERROR = ""
try:
    from mutagen.mp3 import MP3
    from mutagen.id3 import ID3, USLT, SYLT, ID3NoHeaderError
    MUTAGEN_AVAILABLE = True
except Exception as e:
    MUTAGEN_ERROR = str(e)

SHAZAM_AVAILABLE = False
SHAZAM_ERROR = ""
try:
    import shazamio
    SHAZAM_AVAILABLE = True
except Exception as e:
    SHAZAM_ERROR = str(e)

PYGAME_AVAILABLE = False
PYGAME_ERROR = ""
try:
    import pygame
    PYGAME_AVAILABLE = True
except Exception as e:
    PYGAME_ERROR = str(e)

class LyricsGeneratorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("AI Audio Lyrics Generator Pro (Smart Sound Match Edition)")
        self.root.geometry("780x940")
        self.root.minsize(750, 800)
        
        # Color palette for a clean desktop interface
        self.bg_color = "#f5f6fa"
        self.primary_color = "#4a69bd"
        self.text_color = "#2f3542"
        self.accent_color = "#1e3799"
        self.success_color = "#2ed573"
        self.warning_color = "#ff9f43"
        
        self.root.configure(bg=self.bg_color)
        
        # Audio Playback and Sync State Management
        self.selected_file_path = ""
        self.current_segments = []  # Keeps track of filtered timestamp boundary arrays
        self.is_playing = False
        self.audio_paused = False
        
        # Progress Tracking Metrics
        self.processing_active = False
        self.elapsed_time = 0
        self.estimated_total_time = 0
        
        # Initialize internal audio hardware sub-mixers if available
        if PYGAME_AVAILABLE:
            try:
                pygame.mixer.init()
            except Exception:
                pass
        
        # Hook window close action to terminate media streams safely
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        self.create_widgets()
        self.check_dependencies()

    def create_widgets(self):
        """Initializes and packs all GUI elements into the window layout."""
        # Top Frame for File Selection
        top_frame = tk.Frame(self.root, bg=self.bg_color, padx=15, pady=10)
        top_frame.pack(fill=tk.X)
        
        self.import_btn = tk.Button(
            top_frame, 
            text="Choose Audio File", 
            command=self.browse_audio_file,
            bg=self.primary_color, 
            fg="white", 
            font=("Arial", 10, "bold"),
            padx=10, 
            pady=5,
            relief=tk.FLAT,
            cursor="hand2"
        )
        self.import_btn.pack(side=tk.LEFT)
        
        self.file_label = tk.Label(
            top_frame, 
            text="No audio file selected.", 
            fg="#747d8c", 
            bg=self.bg_color,
            font=("Arial", 9, "italic"),
            padx=10
        )
        self.file_label.pack(side=tk.LEFT, fill=tk.X, expand=True, anchor=tk.W)

        # Smart Cloud Data Lookup & Sound Identification Panel
        cloud_frame = tk.LabelFrame(
            self.root,
            text="Smart Sound Recognition & Cloud Lookup (Matches actual audio frequency)",
            bg=self.bg_color,
            fg=self.text_color,
            font=("Arial", 9, "bold"),
            padx=15,
            pady=10
        )
        cloud_frame.pack(fill=tk.X, padx=15, pady=5)
        
        tk.Label(cloud_frame, text="Artist:", bg=self.bg_color, fg=self.text_color, font=("Arial", 9)).grid(row=0, column=0, sticky=tk.W, pady=2)
        self.artist_var = tk.StringVar()
        self.artist_entry = tk.Entry(cloud_frame, textvariable=self.artist_var, width=28, bg="white", font=("Arial", 9))
        self.artist_entry.grid(row=0, column=1, padx=5, pady=2, sticky=tk.W)
        
        tk.Label(cloud_frame, text="Song Title:", bg=self.bg_color, fg=self.text_color, font=("Arial", 9)).grid(row=0, column=2, sticky=tk.W, pady=2, padx=(15, 0))
        self.title_var = tk.StringVar()
        self.title_entry = tk.Entry(cloud_frame, textvariable=self.title_var, width=28, bg="white", font=("Arial", 9))
        self.title_entry.grid(row=0, column=3, padx=5, pady=2, sticky=tk.W)
        
        self.cloud_fetch_btn = tk.Button(
            cloud_frame,
            text="Smart Sound Find",
            command=self.start_smart_pipeline_thread,
            bg=self.accent_color,
            fg="white",
            font=("Arial", 9, "bold"),
            padx=12,
            pady=3,
            relief=tk.FLAT,
            state=tk.DISABLED,
            cursor="hand2"
        )
        self.cloud_fetch_btn.grid(row=0, column=4, padx=(15, 0), pady=2, sticky=tk.E)
        
        cloud_frame.columnconfigure(1, weight=1)
        cloud_frame.columnconfigure(3, weight=1)

        # Middle Frame for Model Settings, Calibration, and Generation
        mid_frame = tk.Frame(self.root, bg=self.bg_color, padx=15, pady=5)
        mid_frame.pack(fill=tk.X)
        
        tk.Label(
            mid_frame, 
            text="AI Model:", 
            bg=self.bg_color, 
            fg=self.text_color,
            font=("Arial", 9, "bold")
        ).pack(side=tk.LEFT)
        
        self.model_var = tk.StringVar(value="small")
        model_menu = tk.OptionMenu(mid_frame, self.model_var, "tiny", "base", "small", "medium", "large")
        model_menu.config(bg="white", relief=tk.GROOVE)
        model_menu.pack(side=tk.LEFT, padx=5)
        
        # Manual Speed/Sync Stretching Scale
        tk.Label(
            mid_frame, 
            text="Speed Scaling Slider:", 
            bg=self.bg_color, 
            fg=self.text_color,
            font=("Arial", 9, "bold")
        ).pack(side=tk.LEFT, padx=(15, 0))
        
        self.speed_scale_var = tk.DoubleVar(value=1.0)
        self.speed_scale = tk.Scale(
            mid_frame,
            from_=0.5,
            to=2.0,
            resolution=0.01,
            orient=tk.HORIZONTAL,
            variable=self.speed_scale_var,
            length=130,
            bg=self.bg_color,
            bd=0,
            highlightthickness=0,
            command=lambda v: self.refresh_lyrics_display()
        )
        self.speed_scale.pack(side=tk.LEFT, padx=2)
        
        # Calibration Tuning Field (Fixes early/late synchronization shifting offsets)
        tk.Label(
            mid_frame, 
            text="Sync Trim (s):", 
            bg=self.bg_color, 
            fg=self.text_color,
            font=("Arial", 9, "bold")
        ).pack(side=tk.LEFT, padx=(15, 0))
        
        self.sync_offset_var = tk.DoubleVar(value=0.00)
        self.sync_offset_spin = tk.Spinbox(
            mid_frame, 
            from_=-5.0, 
            to=5.0, 
            increment=0.05, 
            textvariable=self.sync_offset_var, 
            width=6,
            bg="white",
            font=("Arial", 10),
            command=self.refresh_lyrics_display
        )
        self.sync_offset_spin.pack(side=tk.LEFT, padx=5)
        
        self.sync_offset_spin.bind("<Return>", lambda e: self.refresh_lyrics_display())
        self.sync_offset_spin.bind("<FocusOut>", lambda e: self.refresh_lyrics_display())
        
        self.generate_btn = tk.Button(
            mid_frame, 
            text="Force Local AI Transcribe", 
            command=self.start_generation_thread,
            bg=self.success_color, 
            fg="white", 
            font=("Arial", 10, "bold"),
            padx=15, 
            pady=5,
            relief=tk.FLAT,
            state=tk.DISABLED,
            cursor="hand2"
        )
        self.generate_btn.pack(side=tk.RIGHT)

        # Status, Progress bar and Time Remaining Display Panel
        self.status_frame = tk.Frame(self.root, bg=self.bg_color, padx=15, pady=5)
        self.status_frame.pack(fill=tk.X)
        
        self.status_label = tk.Label(
            self.status_frame, 
            text="Ready", 
            fg=self.text_color, 
            bg=self.bg_color,
            font=("Arial", 10, "bold")
        )
        self.status_label.pack(anchor=tk.W)

        # Integrated Loading Progress Indicators
        self.progress_bar = ttk.Progressbar(self.status_frame, orient=tk.HORIZONTAL, mode='determinate')
        self.progress_bar.pack(fill=tk.X, pady=(4, 2))
        
        self.time_left_label = tk.Label(
            self.status_frame,
            text="",
            fg="#57606f",
            bg=self.bg_color,
            font=("Arial", 9, "bold")
        )
        self.time_left_label.pack(anchor=tk.W)

        # Live Preview & Media Karaoke Panel
        preview_frame = tk.LabelFrame(self.root, text="Live Synchronization Karaoke Preview", bg=self.bg_color, fg=self.text_color, font=("Arial", 9, "bold"), padx=15, pady=10)
        preview_frame.pack(fill=tk.X, padx=15, pady=5)
        
        self.preview_btn = tk.Button(
            preview_frame,
            text="Play Preview",
            command=self.toggle_preview_playback,
            bg=self.warning_color,
            fg="white",
            font=("Arial", 10, "bold"),
            padx=12,
            pady=5,
            relief=tk.FLAT,
            state=tk.DISABLED,
            cursor="hand2"
        )
        self.preview_btn.pack(side=tk.LEFT, padx=(0, 5))

        self.stop_btn = tk.Button(
            preview_frame,
            text="Stop Preview",
            command=self.stop_preview_playback,
            bg="#747d8c",
            fg="white",
            font=("Arial", 10, "bold"),
            padx=12,
            pady=5,
            relief=tk.FLAT,
            state=tk.DISABLED,
            cursor="hand2"
        )
        self.stop_btn.pack(side=tk.LEFT, padx=(0, 10))
        
        self.live_lyrics_label = tk.Label(
            preview_frame,
            text="Click play preview to track timeline lines dynamically.",
            fg="#747d8c",
            bg="white",
            font=("Arial", 11, "bold"),
            anchor=tk.W,
            padx=15,
            relief=tk.SOLID,
            bd=1
        )
        self.live_lyrics_label.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=4)

        # Bottom Frame for Text Output Area
        output_frame = tk.Frame(self.root, bg=self.bg_color, padx=15, pady=5)
        output_frame.pack(fill=tk.BOTH, expand=True)
        
        header_text_frame = tk.Frame(output_frame, bg=self.bg_color)
        header_text_frame.pack(fill=tk.X, pady=(0, 5))
        
        tk.Label(
            header_text_frame, 
            text="Precision Timestamp-Synced Lyrics (Editable - You can correct mistakes directly below):", 
            bg=self.bg_color, 
            fg=self.text_color,
            font=("Arial", 10, "bold")
        ).pack(side=tk.LEFT, anchor=tk.W)

        # Plugnnb / Intro Template Line Injector Utility Button
        self.add_line_btn = tk.Button(
            header_text_frame,
            text="+ Insert Blank Timestamp Line",
            command=self.insert_blank_template_line,
            bg=self.primary_color,
            fg="white",
            font=("Arial", 8, "bold"),
            padx=8,
            pady=2,
            relief=tk.FLAT,
            cursor="hand2"
        )
        self.add_line_btn.pack(side=tk.RIGHT, anchor=tk.E)
        
        self.lyrics_display = ScrolledText(
            output_frame, 
            wrap=tk.WORD, 
            font=("Consolas", 11), 
            bg="white", 
            fg=self.text_color,
            bd=1,
            relief=tk.SOLID
        )
        self.lyrics_display.pack(fill=tk.BOTH, expand=True)

        # Action Management Footer (Download & Embedding Controls)
        action_frame = tk.Frame(self.root, bg=self.bg_color, padx=15, pady=15)
        action_frame.pack(fill=tk.X)
        
        self.download_btn = tk.Button(
            action_frame,
            text="Download Synced File (.lrc)",
            command=self.download_lyrics_file,
            bg="#" + "2f3542",
            fg="white",
            font=("Arial", 10, "bold"),
            padx=12,
            pady=6,
            relief=tk.FLAT,
            state=tk.DISABLED,
            cursor="hand2"
        )
        self.download_btn.pack(side=tk.LEFT, padx=(0, 10))
        
        self.embed_btn = tk.Button(
            action_frame,
            text="Embed Synced Lyrics into Audio File",
            command=self.embed_lyrics_to_audio,
            bg=self.accent_color,
            fg="white",
            font=("Arial", 10, "bold"),
            padx=12,
            pady=6,
            relief=tk.FLAT,
            state=tk.DISABLED,
            cursor="hand2"
        )
        self.embed_btn.pack(side=tk.LEFT)

    def check_dependencies(self):
        """Verifies if the necessary speech-to-text libraries and FFmpeg are installed."""
        missing = []
        if not WHISPER_AVAILABLE: missing.append(f"openai-whisper / torch (Diagnostic: {WHISPER_ERROR})")
        if not MUTAGEN_AVAILABLE: missing.append(f"mutagen (Diagnostic: {MUTAGEN_ERROR})")
        if not SHAZAM_AVAILABLE: missing.append(f"shazamio (Diagnostic: {SHAZAM_ERROR})")
        if not PYGAME_AVAILABLE: missing.append(f"pygame (Diagnostic: {PYGAME_ERROR})")
        
        if missing:
            self.status_label.config(text="Error: Missing private libraries", fg="#ff4757")
            self.lyrics_display.delete("1.0", tk.END)
            
            diagnostic_report = "Required dependencies are missing or failed to load in your virtual environment:\n\n"
            for item in missing:
                diagnostic_report += f"❌ {item}\n"
                
            diagnostic_report += f"\n👉 HOW TO FIX THE PYTHON 3.13 AUDIOOP BUG INSTANTLY:\n"
            diagnostic_report += f"Because Python 3.13 removed legacy audio handlers, you must install the replacement package.\n"
            diagnostic_report += f"Copy and run the following command directly into your black active (lyric_env) terminal:\n\n"
            diagnostic_report += f"pip install audioop-lts openai-whisper torch mutagen shazamio pygame\n"
            
            self.lyrics_display.insert(tk.END, diagnostic_report)
            self.import_btn.config(state=tk.DISABLED)
            return False

        if not shutil.which("ffmpeg"):
            self.status_label.config(text="Error: FFmpeg Not Found", fg="#ff4757")
            self.lyrics_display.delete("1.0", tk.END)
            self.lyrics_display.insert(
                tk.END, 
                "CRITICAL ERROR: FFmpeg is missing from your Windows System PATH.\n\n"
                "OpenAI Whisper requires FFmpeg to convert and process audio formats (like MP3).\n\n"
                "HOW TO FIX THIS ON WINDOWS:\n"
                "1. Open Command Prompt or PowerShell as Administrator.\n"
                "2. Run the following command to install it instantly via Windows Package Manager:\n"
                "   winget install Gyan.FFmpeg\n"
                "3. RESTART your terminal and restart this Python application so it detects the new paths."
            )
            self.import_btn.config(state=tk.DISABLED)
            return False
            
        self.status_label.config(text="Ready", fg=self.text_color)
        self.import_btn.config(state=tk.NORMAL)
        return True

    def insert_blank_template_line(self):
        """Injects a template line [00:00.00] at the top or cursor position to easily insert missing plugnnb opening vocals."""
        self.lyrics_display.insert("1.0", "[00:00.00] (Enter missing plugnnb introductory lyrics here)\n")
        self.parse_current_text_widget_content()
        self.refresh_lyrics_display()

    def browse_audio_file(self):
        """Opens a file dialog picker allowing the user to select an audio file and auto-extracts tags."""
        file_types = [("Audio Files", "*.mp3 *.wav *.m4a *.flac *.ogg *.aac"), ("All Files", "*.*")]
        file_path = filedialog.askopenfilename(title="Select Audio File", filetypes=file_types)
        
        if file_path:
            self.reset_audio_playback_engine()
            self.selected_file_path = file_path
            file_name = os.path.basename(file_path)
            self.file_label.config(text=file_name, fg=self.text_color, font=("Arial", 9, "bold"))
            
            # Reset speed slider to standard on new imports
            self.speed_scale_var.set(1.0)
            
            parsed_artist = ""
            parsed_title = ""
            
            if MUTAGEN_AVAILABLE and file_path.lower().endswith('.mp3'):
                try:
                    audio_tags = ID3(file_path)
                    parsed_title = str(audio_tags.get("TIT2", ""))
                    parsed_artist = str(audio_tags.get("TPE1", ""))
                except Exception:
                    pass

            if not parsed_title:
                base_name = os.splitext(file_name)[0]
                base_name_clean = re.sub(r'\s*[\(\[]?(sped\s*up|speed\s*up|nightcore|remix)[\)\]]?', '', base_name, flags=re.IGNORECASE)
                if " - " in base_name_clean:
                    parts = base_name_clean.split(" - ", 1)
                    parsed_artist = parts[0].strip()
                    parsed_title = parts[1].strip()
                else:
                    parsed_title = base_name_clean.strip()
            
            self.artist_var.set(parsed_artist)
            self.title_var.set(parsed_title)
            
            self.generate_btn.config(state=tk.NORMAL)
            self.cloud_fetch_btn.config(state=tk.NORMAL)
            self.status_label.config(text="File loaded. Press 'Smart Sound Find' to process fully automated lookup.", fg=self.success_color)
            self.progress_bar["value"] = 0
            self.time_left_label.config(text="")

    def get_audio_duration(self):
        """Extracts the audio track length metrics to form precision timeline countdowns."""
        if MUTAGEN_AVAILABLE and self.selected_file_path:
            try:
                if self.selected_file_path.lower().endswith('.mp3'):
                    return MP3(self.selected_file_path).info.length
                else:
                    from mutagen import mutagen
                    audio = mutagen.File(self.selected_file_path)
                    if audio and audio.info:
                        return audio.info.length
            except Exception:
                pass
        return 180.0  # Safe standard fallback default (3 minutes)

    def start_progress_tracking(self, estimated_seconds):
        """Initializes and runs the tracking loops calculation vectors."""
        self.processing_active = True
        self.elapsed_time = 0
        self.estimated_total_time = estimated_seconds
        self.progress_bar["value"] = 0
        self.time_left_label.config(text=f"Estimated Time Remaining: {int(self.estimated_total_time)}s")
        self.root.after(1000, self.poll_processing_progress)

    def poll_processing_progress(self):
        """Asynchronous execution checking loops to update progress metrics without lagging frame rates."""
        if not self.processing_active:
            return
            
        self.elapsed_time += 1
        time_remaining = max(1, int(self.estimated_total_time - self.elapsed_time))
        
        calc_pct = (self.elapsed_time / self.estimated_total_time) * 100
        if calc_pct > 98:
            calc_pct = 98
            self.time_left_label.config(text="Refining structural formatting blocks... Finishing up soon.")
        else:
            self.time_left_label.config(text=f"Estimated Time Remaining: {time_remaining}s")
            
        self.progress_bar["value"] = calc_pct
        self.root.after(1000, self.poll_processing_progress)

    def stop_progress_tracking(self, success=True):
        """Halts calculations and snaps status arrays back into completion postures."""
        self.processing_active = False
        if success:
            self.progress_bar["value"] = 100
            self.time_left_label.config(text="Processing complete!", fg=self.success_color)
        else:
            self.progress_bar["value"] = 0
            self.time_left_label.config(text="Operation halted.")

    def reset_audio_playback_engine(self):
        """Stops the internal private pygame mixer audio layout securely and releases file handles."""
        if PYGAME_AVAILABLE:
            try:
                pygame.mixer.music.stop()
                pygame.mixer.music.unload()
            except Exception:
                pass
        self.is_playing = False
        self.audio_paused = False
        self.preview_btn.config(text="Play Preview", bg=self.warning_color)
        self.stop_btn.config(state=tk.DISABLED)
        self.live_lyrics_label.config(text="Click play preview to track timeline lines dynamically.", fg="#747d8c")

    def start_smart_pipeline_thread(self):
        """Spawns the background thread handling the entire identification -> cloud fetch -> local fallback pipeline."""
        self.reset_audio_playback_engine()
        self.import_btn.config(state=tk.DISABLED)
        self.generate_btn.config(state=tk.DISABLED)
        self.cloud_fetch_btn.config(state=tk.DISABLED)
        self.download_btn.config(state=tk.DISABLED)
        self.embed_btn.config(state=tk.DISABLED)
        
        self.status_label.config(text="Stage 1/3: Analyzing acoustic properties to match song sound...", fg=self.accent_color)
        self.lyrics_display.delete("1.0", tk.END)
        self.current_segments = []
        
        self.start_progress_tracking(6.0)
        
        pipeline_thread = threading.Thread(target=self.run_automated_pipeline)
        pipeline_thread.daemon = True
        pipeline_thread.start()

    def run_automated_pipeline(self):
        """Executes the intelligent audio matching pipeline sequentially with automated fallbacks."""
        if SHAZAM_AVAILABLE:
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                shazam = shazamio.Shazam()
                
                recognition_task = shazam.recognize_song(self.selected_file_path)
                result = loop.run_until_complete(recognition_task)
                loop.close()
                
                if result and 'track' in result:
                    track_data = result['track']
                    discovered_title = track_data.get('title', '')
                    discovered_artist = track_data.get('subtitle', '')
                    
                    if discovered_title:
                        self.artist_var.set(discovered_artist)
                        self.title_var.set(discovered_title)
                        self.root.after(0, lambda t=discovered_title, a=discovered_artist: self.status_label.config(
                            text=f"Match Found: {a} - {t}. Proceeding to cloud...", fg=self.success_color
                        ))
            except Exception:
                pass

        artist = self.artist_var.get().strip()
        title = self.title_var.get().strip()
        cloud_success = False
        
        if title:
            self.root.after(0, lambda: self.status_label.config(text="Stage 2/3: Searching cloud servers for synchronized files...", fg=self.accent_color))
            try:
                query = f"{artist} {title}" if artist else title
                encoded_url = f"https://lrclib.net/api/search?q={urllib.parse.quote(query)}"
                
                req = urllib.request.Request(encoded_url, headers={'User-Agent': 'ProLyricsAutoFetcher/1.2'})
                unverified_ssl_context = ssl._create_unverified_context()
                
                with urllib.request.urlopen(req, timeout=5, context=unverified_ssl_context) as response:
                    payload = json.loads(response.read().decode('utf-8'))
                    
                synced_lrc_string = None
                if payload:
                    for item in payload:
                        if item.get("syncedLyrics"):
                            synced_lrc_string = item["syncedLyrics"]
                            break
                
                if synced_lrc_string:
                    self.current_segments = self.parse_lrc_string(synced_lrc_string)
                    cloud_success = True
                    self.root.after(0, self.display_completed_lyrics)
                    return
                    
            except Exception:
                pass

        if not cloud_success:
            self.root.after(0, lambda: self.stop_progress_tracking(success=False))
            self.root.after(0, lambda: self.status_label.config(
                text="Stage 3/3: Online match unavailable. Initializing Local AI Transcribe...", fg=self.warning_color
            ))
            if WHISPER_AVAILABLE:
                self.process_audio_transcription()
            else:
                msg_str = "Online lookup failed and local transcription dependencies are missing."
                self.root.after(0, lambda m=msg_str: self.handle_cloud_failure(m))

    def parse_lrc_string(self, lrc_text):
        """Transforms a standard line-stamped LRC text block into structured internal dictionaries."""
        segments = []
        lines = lrc_text.splitlines()
        raw_stamps = []
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            match = re.match(r'^\[(\d+):(\d+(?:\.\d+)?)\](.*)', line)
            if match:
                mins = int(match.group(1))
                secs = float(match.group(2))
                txt = match.group(3).strip()
                absolute_seconds = (mins * 60) + secs
                raw_stamps.append((absolute_seconds, txt))
                
        raw_stamps.sort(key=lambda x: x[0])
        for idx, (start_time, txt) in enumerate(raw_stamps):
            end_time = raw_stamps[idx + 1][0] if idx < len(raw_stamps) - 1 else start_time + 4.0
            segments.append({"text": txt, "start": start_time, "end": end_time})
        return segments

    def parse_current_text_widget_content(self):
        """Parses workspace content directly from text element matrices to enable manual editing updates."""
        text_content = self.lyrics_display.get("1.0", tk.END)
        lines = text_content.splitlines()
        raw_stamps = []
        
        try:
            slider_factor = self.speed_scale_var.get()
        except Exception:
            slider_factor = 1.0
            
        for line in lines:
            line = line.strip()
            if not line:
                continue
            match = re.match(r'^\[(\d+):(\d+(?:\.\d+)?)\](.*)', line)
            if match:
                mins = int(match.group(1))
                secs = float(match.group(2))
                txt = match.group(3).strip()
                display_seconds = (mins * 60) + secs
                raw_stamps.append((display_seconds / slider_factor, txt))
            else:
                match_clipped = re.match(r'^\[?(\d+(?:\.\d+)?)\](.*)', line)
                if match_clipped:
                    display_seconds = float(match_clipped.group(1))
                    raw_stamps.append((display_seconds / slider_factor, match_clipped.group(2).strip()))
                    
        if raw_stamps:
            raw_stamps.sort(key=lambda x: x[0])
            self.current_segments = []
            for idx, (start_time, txt) in enumerate(raw_stamps):
                end_time = raw_stamps[idx + 1][0] if idx < len(raw_stamps) - 1 else start_time + 4.0
                self.current_segments.append({"text": txt, "start": start_time, "end": end_time})

    def handle_cloud_failure(self, message):
        """Restores button states cleanly if an online records query returns blank results."""
        self.stop_progress_tracking(success=False)
        self.status_label.config(text="Pipeline Finished", fg=self.warning_color)
        self.import_btn.config(state=tk.NORMAL)
        self.cloud_fetch_btn.config(state=tk.NORMAL)
        if self.selected_file_path:
            self.generate_btn.config(state=tk.NORMAL)
        messagebox.showinfo("Pipeline Result", f"{message}")

    def start_generation_thread(self):
        """Spawns a background thread for processing to avoid freezing the window interface."""
        if not self.check_dependencies():
            return

        self.reset_audio_playback_engine()
        self.generate_btn.config(state=tk.DISABLED)
        self.import_btn.config(state=tk.DISABLED)
        self.cloud_fetch_btn.config(state=tk.DISABLED)
        self.download_btn.config(state=tk.DISABLED)
        self.embed_btn.config(state=tk.DISABLED)
        
        chosen_model = self.model_var.get()
        self.status_label.config(text=f"Loading '{chosen_model}' Model & initializing phrase alignment...", fg=self.accent_color)
        self.live_lyrics_label.config(text="Analyzing audio tracks and voice activity structures...", fg="#747d8c")
        
        self.lyrics_display.delete("1.0", tk.END)
        self.current_segments = []
        
        transcription_thread = threading.Thread(target=self.process_audio_transcription)
        transcription_thread.daemon = True
        transcription_thread.start()

    def format_timestamp(self, seconds_float):
        """Helper to transform raw decimal seconds into standardized [mm:ss.xx] lyric timelines."""
        if seconds_float < 0:
            seconds_float = 0.0
        minutes = int(seconds_float // 60)
        seconds = int(seconds_float % 60)
        hundredths = int((seconds_float % 1) * 100)
        return f"[{minutes:02d}:{seconds:02d}.{hundredths:02d}]"

    def refresh_lyrics_display(self):
        """Re-generates text output arrays combining offset variables and manual speed slider scaling factors."""
        if not self.current_segments:
            return
        try:
            offset = self.sync_offset_var.get()
            slider_factor = self.speed_scale_var.get()
        except Exception:
            offset = 0.0
            slider_factor = 1.0
            
        lrc_lines = []
        for segment in self.current_segments:
            text_line = segment.get("text", "").strip()
            if text_line:
                start_time = (segment.get("start", 0.0) * slider_factor) + offset
                lrc_lines.append(f"{self.format_timestamp(start_time)} {text_line}")
                
        lyrics_text = "\n".join(lrc_lines)
        scroll_pos = self.lyrics_display.yview()
        self.lyrics_display.delete("1.0", tk.END)
        self.lyrics_display.insert(tk.END, lyrics_text)
        self.lyrics_display.yview_moveto(scroll_pos[0])
        self.lyrics_display.xview_moveto(0)

    def process_audio_transcription(self):
        """Performs speech-to-text transcription leveraging native acoustic segments for optimal continuity."""
        try:
            chosen_model_size = self.model_var.get()
            device = "cuda" if (WHISPER_AVAILABLE and torch.cuda.is_available()) else "cpu"
            
            ratios = {"tiny": 0.04, "base": 0.08, "small": 0.22, "medium": 0.70, "large": 1.60}
            coefficient = ratios.get(chosen_model_size, 0.22)
            if device == "cpu":
                coefficient *= 3.5
                
            track_length = self.get_audio_duration()
            estimated_runtime = max(6.0, track_length * coefficient)
            
            self.root.after(0, lambda est=estimated_runtime: self.start_progress_tracking(est))
            
            model = whisper.load_model(chosen_model_size, device=device)
            self.root.after(0, lambda: self.status_label.config(text="Processing vocal structures and timeline mapping...", fg=self.accent_color))
            
            use_fp16 = (device == "cuda")
            
            transcribe_options = {
                "fp16": use_fp16,
                "beam_size": 5,
                "no_speech_threshold": 0.4
            }
            
            result = model.transcribe(self.selected_file_path, **transcribe_options)
            
            precision_segments = []
            for segment in result.get("segments", []):
                text_line = segment.get("text", "").strip()
                if text_line:
                    if precision_segments and text_line == precision_segments[-1]["text"]:
                        continue
                        
                    precision_segments.append({
                        "text": text_line,
                        "start": segment.get("start", 0.0),
                        "end": segment.get("end", 0.0)
                    })
            
            self.current_segments = precision_segments
            self.root.after(0, self.display_completed_lyrics)
            
        except FileNotFoundError:
            self.root.after(0, lambda: self.handle_processing_error(
                "FFmpeg executable not found by the system. Please ensure FFmpeg is installed and added to your System PATH variables."
            ))
        except Exception as e:
            error_msg_str = f"Failed to transcribe file: {e}"
            self.root.after(0, lambda m=error_msg_str: self.handle_processing_error(m))

    def display_completed_lyrics(self):
        """Updates UI components upon successful transcription execution."""
        self.stop_progress_tracking(success=True)
        self.refresh_lyrics_display()
        self.status_label.config(text="Synced lyrics completed successfully!", fg=self.success_color)
        self.live_lyrics_label.config(text="Lyrics ready! Press Play Preview to begin.", fg=self.success_color)
        
        self.generate_btn.config(state=tk.NORMAL)
        self.import_btn.config(state=tk.NORMAL)
        self.cloud_fetch_btn.config(state=tk.NORMAL)
        self.download_btn.config(state=tk.DISABLED if not self.current_segments else tk.NORMAL)
        self.preview_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.NORMAL)
        
        if MUTAGEN_AVAILABLE:
            self.embed_btn.config(state=tk.NORMAL)
        messagebox.showinfo("Success", "Audio timeline processing operation completed successfully.")

    def toggle_preview_playback(self):
        """Toggles music preview playback leveraging native microsecond hardware alignment tracks."""
        if not self.selected_file_path:
            return
        if not self.check_dependencies():
            return
            
        self.parse_current_text_widget_content()
        if not self.current_segments:
            return

        if self.is_playing:
            pygame.mixer.music.pause()
            self.is_playing = False
            self.audio_paused = True
            self.preview_btn.config(text="Play Preview", bg=self.warning_color)
        else:
            try:
                if not self.audio_paused:
                    pygame.mixer.music.load(self.selected_file_path)
                    pygame.mixer.music.play()
                else:
                    pygame.mixer.music.unpause()
                    
                self.is_playing = True
                self.audio_paused = False
                self.preview_btn.config(text="Pause Preview", bg="#ff4757")
                self.stop_btn.config(state=tk.NORMAL)
                self.poll_playback_timeline()
            except Exception as e:
                messagebox.showerror("Audio Load Error", f"The private music framework failed to stream this track file:\n{e}")

    def stop_preview_playback(self):
        """Stops the audio mixer timeline completely and flushes hardware handles."""
        self.reset_audio_playback_engine()
        self.preview_btn.config(text="Play Preview", bg=self.warning_color)
        self.live_lyrics_label.config(text="Preview stopped. Position reset to start.", fg="#747d8c")
        self.lyrics_display.tag_remove("active_sync_line", "1.0", tk.END)

    def poll_playback_timeline(self):
        """Periodic loop execution tracking mixer milliseconds directly for absolute precision tracking sync."""
        if not self.is_playing:
            return

        try:
            # Query the precise number of elapsed playback milliseconds directly from the audio output wave
            position_ms = pygame.mixer.music.get_pos()
            
            # If get_pos returns -1, the song channel track has reached the end naturally
            if position_ms == -1:
                self.reset_audio_playback_engine()
                self.preview_btn.config(state=tk.NORMAL)
                return
                
            current_seconds = position_ms / 1000.0

            try:
                offset = self.sync_offset_var.get()
                slider_factor = self.speed_scale_var.get()
            except Exception:
                offset = 0.0
                slider_factor = 1.0

            active_text = ""
            active_idx = -1
            
            for idx, segment in enumerate(self.current_segments):
                # Lock segments tightly to active speed factor configurations
                start = (segment.get("start", 0.0) * slider_factor) + offset
                if current_seconds >= start:
                    active_text = segment.get("text", "").strip()
                    active_idx = idx
                else:
                    break

            if active_idx != -1:
                self.live_lyrics_label.config(text=active_text, fg=self.accent_color)
                self.highlight_lyrics_text_line(active_idx)
            else:
                self.live_lyrics_label.config(text="... ♪ ...", fg="#747d8c")
                
        except Exception:
            pass
        self.root.after(25, self.poll_playback_timeline)

    def highlight_lyrics_text_line(self, index):
        """Auto-scrolls text widget window and paints lines matching active playback segments."""
        self.lyrics_display.tag_remove("active_sync_line", "1.0", tk.END)
        line_num = index + 1
        start_coord = f"{line_num}.0"
        end_coord = f"{line_num}.end"
        
        self.lyrics_display.tag_config(
            "active_sync_line", background="#dcdde1", foreground=self.accent_color, font=("Consolas", 11, "bold")
        )
        self.lyrics_display.tag_add("active_sync_line", start_coord, end_coord)
        self.lyrics_display.see(start_coord)
        self.lyrics_display.xview_moveto(0)

    def download_lyrics_file(self):
        """Saves the current calibrated contents of the text display element to a synchronized .lrc file."""
        self.parse_current_text_widget_content()
        lyrics_content = self.lyrics_display.get("1.0", tk.END).strip()
        if not lyrics_content:
            messagebox.showwarning("Empty Text", "There are no lyrics generated to download.")
            return
            
        initial_name = os.path.splitext(os.path.basename(self.selected_file_path))[0]
        file_path = filedialog.asksaveasfilename(
            title="Save Calibrated Synced Lyrics File",
            initialfile=initial_name,
            defaultextension=".lrc",
            filetypes=[("Synced Lyrics Files", "*.lrc"), ("Text Files", "*.txt"), ("All Files", "*.*")]
        )
        
        if file_path:
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(lyrics_content)
                messagebox.showinfo("Saved", "Synchronized LRC file successfully saved with calibration parameters!")
            except Exception as e:
                messagebox.showerror("Error Saving File", f"Could not save file:\n{e}")

    def embed_lyrics_to_audio(self):
        """Embeds the calibrated text matrix directly into the selected audio file using precise Synchronized Lyrics frames (SYLT)."""
        if not MUTAGEN_AVAILABLE:
            messagebox.showerror("Dependency Error", "The 'mutagen' library is missing. Run: pip install mutagen")
            return
        if not self.selected_file_path:
            messagebox.showwarning("Missing Data", "No audio file selected.")
            return

        self.parse_current_text_widget_content()
        if not self.current_segments:
            messagebox.showwarning("Missing Data", "Lyrics workspace layout is currently empty.")
            return

        file_extension = os.path.splitext(self.selected_file_path)[1].lower()
        if file_extension != ".mp3":
            messagebox.showwarning(
                "Format Limitation", 
                f"Automated timeline metadata embedding is engineered for standard MP3 files.\n\n"
                f"Your selected file is a '{file_extension}' file."
            )
            return

        try:
            try:
                audio = MP3(self.selected_file_path, ID3=ID3)
            except ID3NoHeaderError:
                audio = MP3(self.selected_file_path)
                audio.add_tags()

            try:
                offset = self.sync_offset_var.get()
                slider_factor = self.speed_scale_var.get()
            except Exception:
                offset = 0.0
                slider_factor = 1.0
                
            sylt_data = []
            offset_ms = int(offset * 1000)
            
            for segment in self.current_segments:
                text_line = segment.get("text", "").strip()
                start_ms = int((segment.get("start", 0.0) * slider_factor) * 1000) + offset_ms
                if start_ms < 0:
                    start_ms = 0
                if text_line:
                    sylt_data.append((text_line, start_ms))

            audio.tags.delall("SYLT")
            audio.tags.add(
                SYLT(encoding=3, lang='eng', format=1, type=1, desc='Synchronized Lyrics', text=sylt_data)
            )
            
            lyrics_plain = self.lyrics_display.get("1.0", tk.END).strip()
            audio.tags.delall("USLT")
            audio.tags.add(USLT(encoding=3, lang='eng', desc='Lyrics', text=lyrics_plain))
            audio.save()
            messagebox.showinfo("Success", "Perfect zero-delay synchronized lyrics embedded into MP3 metadata tags!")
            
        except Exception as e:
            messagebox.showerror("Metadata Error", f"Failed to modify audio file header tracks:\n{e}")

    def handle_processing_error(self, err_msg):
        """Resets the UI states safely if an execution runtime error occurs."""
        self.stop_progress_tracking(success=False)
        self.status_label.config(text="Generation Failed", fg="#ff4757")
        self.lyrics_display.insert(tk.END, f"An error occurred during runtime execution:\n\n{err_msg}")
        self.generate_btn.config(state=tk.NORMAL)
        self.import_btn.config(state=tk.NORMAL)
        self.cloud_fetch_btn.config(state=tk.NORMAL)
        messagebox.showerror("Execution Error", f"Failed to transcribe file:\n{err_msg}")

    def on_closing(self):
        """Safely terminates background audio streams when the app window is closed."""
        self.reset_audio_playback_engine()
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = LyricsGeneratorApp(root)
    root.mainloop()
