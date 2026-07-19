#!/usr/bin/env python3
"""
MESSAGE TERMINAL - LAN Chat Server & Terminal CLI
Author: Gemini
A self-contained Python application that starts a local web server for multi-device 
LAN chatting alongside a live interactive terminal CLI. Optimized for modern and 
legacy devices (including iOS 12/WebKit).
"""

import http.server
import socketserver
import threading
import json
import sys
import os
import socket
import urllib.parse
import time
import queue
import uuid

# --- CONFIGURABLE HOST SETTINGS ---
# Set to your custom domain or hostname if you have one (e.g., "chat.local", "mycoolchat.net")
# This can be a local network address or a global public internet domain.
# If set to None, it will automatically default to local network IP addresses.
# You can also override this by passing it as a command-line argument: python chat.py chat.local
CUSTOM_DOMAIN = None

PORT = 8080
messages = []            # Stores message objects: {"id": str, "sender": str, "text": str, "time": str, "edited": bool}
clients = set()          # Set of active Client Connection Queues (for SSE)
active_users = {}        # Tracking active usernames mapped to their last-seen timestamp: {username: timestamp}
state_lock = threading.Lock()
terminal_username = "👑Host👑"
terminal_notifications_enabled = True

USER_FILE = "terminal_user.txt"
HISTORY_FILE = "reply_history.txt"
NOTIFY_FILE = "terminal_notify.txt"

# ASCII ART & SYSTEM METRICS
BANNER = r"""
███╗   ███╗███████╗███████╗███████╗ █████╗  ██████╗ ███████╗
████╗ ████║██╔════╝██╔════╝██╔════╝██╔══██╗██╔════╝ ██╔════╝
██╔████╔██║█████╗  ███████╗███████╗███████║██║  ███╗█████╗  
██║╚██╔╝██║██╔══╝  ╚════██║╚════██║██╔══██║██║   ██║██╔══╝  
██║ ╚═╝ ██║███████╗███████║███████║██║  ██║╚██████╔╝███████╗
╚═╝     ╚═╝╚══════╝╚══════╝╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝
                 --- LOCAL MESSAGE TERMINAL ---
"""

# Try importing readline for robust POSIX-compliant terminal line-reading
try:
    import readline
except ImportError:
    readline = None

# --- TERMINAL BEEP SYNTHESIZER ---
def play_terminal_beep():
    """
    Plays a custom Windows chord sound (SystemExclamation)
    on Windows hosts instead of a simple synthesized tone.
    Falls back gracefully to the terminal bell on macOS/Linux.
    """
    try:
        import winsound
        # Play the standard Windows Asterisk chord sound asynchronously so it does not block execution
        winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS | winsound.SND_ASYNC)
    except (ImportError, Exception):
        # Fallback for macOS, Linux, and other POSIX terminals
        sys.stdout.write('\a')
        sys.stdout.flush()

# --- INPUT BUFFER PRESERVATION ---
def get_active_input_buffer():
    """
    Attempts to read the current typed input text directly from the OS console/terminal
    so we can reprint it cleanly after displaying incoming remote messages.
    """
    # 1. Handle Windows host terminal reading
    if os.name == 'nt':
        try:
            import ctypes
            from ctypes import wintypes
            
            h = ctypes.windll.kernel32.GetStdHandle(-11) # STD_OUTPUT_HANDLE
            if h and h != -1:
                class COORD(ctypes.Structure):
                    _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]
                    
                class SMALL_RECT(ctypes.Structure):
                    _fields_ = [("Left", ctypes.c_short), ("Top", ctypes.c_short),
                                ("Right", ctypes.c_short), ("Bottom", ctypes.c_short)]
                                
                class CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
                    _fields_ = [("dwSize", COORD),
                                ("dwCursorPosition", COORD),
                                ("wAttributes", ctypes.c_ushort),
                                ("srWindow", SMALL_RECT),
                                ("dwMaximumWindowSize", COORD)]
                                
                csbi = CONSOLE_SCREEN_BUFFER_INFO()
                if ctypes.windll.kernel32.GetConsoleScreenBufferInfo(h, ctypes.byref(csbi)):
                    cursor_pos = csbi.dwCursorPosition
                    row = cursor_pos.Y
                    length = cursor_pos.X
                    
                    if length > 0:
                        read_coord = COORD(0, row)
                        num_chars_read = wintypes.DWORD(0)
                        buffer = ctypes.create_unicode_buffer(length)
                        
                        if ctypes.windll.kernel32.ReadConsoleOutputCharacterW(
                            h, buffer, length, read_coord, ctypes.byref(num_chars_read)
                        ):
                            full_text = buffer.value
                            prompt_str = f"{terminal_username} > "
                            if full_text.startswith(prompt_str):
                                return full_text[len(prompt_str):]
                            return full_text
        except Exception:
            pass

    # 2. Handle macOS / Linux readline terminal reading
    if readline is not None:
        try:
            return readline.get_line_buffer()
        except Exception:
            pass
            
    return ""

# --- USERNAME & NOTIFICATION STORAGE MANAGER ---
def load_terminal_username():
    global terminal_username
    if os.path.exists(USER_FILE):
        try:
            with open(USER_FILE, "r", encoding="utf-8") as f:
                name = f.read().strip()
                if name:
                    terminal_username = name
        except Exception:
            pass

def save_terminal_username(name):
    try:
        with open(USER_FILE, "w", encoding="utf-8") as f:
            f.write(name)
    except Exception:
        pass

def load_terminal_notifications():
    global terminal_notifications_enabled
    if os.path.exists(NOTIFY_FILE):
        try:
            with open(NOTIFY_FILE, "r", encoding="utf-8") as f:
                val = f.read().strip().lower()
                if val == "off":
                    terminal_notifications_enabled = False
                elif val == "on":
                    terminal_notifications_enabled = True
        except Exception:
            pass

def save_terminal_notifications(enabled):
    try:
        with open(NOTIFY_FILE, "w", encoding="utf-8") as f:
            f.write("on" if enabled else "off")
    except Exception:
        pass

# --- REPLY & QUOTE HISTORY STORAGE MANAGER ---
def load_reply_history():
    global messages
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                if isinstance(saved, list):
                    # Restore saved messages (up to last 5)
                    messages = saved[-5:]
        except Exception:
            pass

def save_reply_history():
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            # Save only the last 10 messages to preserve memory/footprint
            with state_lock:
                to_save = messages[-10:]
            json.dump(to_save, f, indent=2)
    except Exception:
        pass

# Ensure terminal configurations load before server start
load_terminal_username()
load_terminal_notifications()
load_reply_history()

# HTML, CSS, and JS Assets bundled directly for zero-dependency execution
# Declared as a raw string literal (r""") to avoid Python syntax escape warnings
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>MESSAGE TERMINAL</title>
    <style>
        /* CSS reset & base settings */
        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            -webkit-tap-highlight-color: transparent;
        }

        body, html {
            width: 100%;
            height: 100%;
            background-color: #000000;
            color: #ffffff;
            font-family: "Courier New", Courier, monospace, monospace;
            overflow: hidden;
            display: flex;
            flex-direction: column;
        }

        /* Scanline CRT overlay effect */
        .scanlines {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: linear-gradient(
                rgba(18, 16, 16, 0) 50%, 
                rgba(0, 0, 0, 0.25) 50%
            ), linear-gradient(
                90deg, 
                rgba(255, 0, 0, 0.06), 
                rgba(0, 255, 0, 0.02), 
                rgba(0, 0, 255, 0.06)
            );
            background-size: 100% 4px, 6px 100%;
            z-index: 10;
            pointer-events: none;
        }

        /* Header UI resembling Terminal */
        .terminal-header {
            background-color: #080808;
            border-bottom: 2px solid #222222;
            padding: 12px 16px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 14px;
            z-index: 20;
            box-shadow: 0 4px 10px rgba(0, 0, 0, 0.8);
        }

        .terminal-title {
            font-weight: bold;
            color: #ffffff;
            letter-spacing: 2px;
            text-shadow: 0 0 5px rgba(255, 255, 255, 0.6);
        }

        .status-dot {
            display: inline-block;
            width: 8px;
            height: 8px;
            background-color: #00ff00;
            border-radius: 50%;
            margin-right: 6px;
            box-shadow: 0 0 8px #00ff00;
            animation: blink 2s infinite ease-in-out;
        }

        @keyframes blink {
            0%, 100% { opacity: 0.3; }
            50% { opacity: 1; }
        }

        .header-buttons {
            display: flex;
            gap: 10px;
            align-items: center;
        }

        .user-badge {
            color: #888888;
            border: 1px solid #333;
            padding: 2px 8px;
            font-size: 12px;
            border-radius: 3px;
            cursor: pointer;
        }

        .user-badge:hover {
            color: #00ff00;
            border-color: #00ff00;
        }

        .user-badge.badge-active {
            color: #00ff00;
            border-color: #00ff00;
            text-shadow: 0 0 4px rgba(0, 255, 0, 0.5);
        }

        .user-badge.badge-inactive {
            color: #ff3333;
            border-color: #ff3333;
            text-shadow: 0 0 4px rgba(255, 51, 51, 0.5);
        }

        /* Message feed container */
        .message-feed {
            flex: 1;
            overflow-y: auto;
            padding: 20px;
            display: flex;
            flex-direction: column;
            gap: 12px;
            /* Scrollbar styling */
            -webkit-overflow-scrolling: touch; /* smooth scrolling for iOS 12 */
        }

        .message-feed::-webkit-scrollbar {
            width: 6px;
        }
        .message-feed::-webkit-scrollbar-thumb {
            background-color: #222;
            border-radius: 3px;
        }

        /* System Messages & Tips */
        .system-banner {
            color: #888888;
            font-size: 13px;
            line-height: 1.6;
            margin-bottom: 15px;
            padding-bottom: 15px;
            border-bottom: 1px dashed #222222;
        }

        /* Message styling resembling classic terminal logs */
        .message-row {
            line-height: 1.5;
            word-wrap: break-word;
            word-break: break-all;
            font-size: 15px;
        }

        .msg-time {
            color: #555555;
            margin-right: 8px;
            user-select: none;
        }

        .msg-sender {
            font-weight: bold;
            margin-right: 8px;
            cursor: pointer;
        }

        .msg-sender:hover {
            text-decoration: underline;
        }

        /* Color schemes depending on username */
        .sender-host {
            color: #00ff00;
            text-shadow: 0 0 4px rgba(0, 255, 0, 0.4);
        }

        .sender-guest {
            color: #00ddff;
            text-shadow: 0 0 4px rgba(0, 221, 255, 0.4);
        }

        .sender-system {
            color: #ffcc00;
            font-style: italic;
        }

        .msg-text {
            color: #ffffff;
        }

        .mention-text {
            color: #ff33aa !important;
            font-weight: bold;
            text-shadow: 0 0 4px rgba(255, 51, 170, 0.4);
        }

        /* Structured Quote Styling */
        .quote-container {
            border-left: 3px solid #ff33aa;
            padding: 4px 10px;
            margin: 6px 0;
            background-color: #050505;
            border-radius: 2px;
            display: block;
        }

        .quote-header {
            color: #ff33aa;
            font-weight: bold;
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 1px;
            display: block;
            margin-bottom: 2px;
        }

        .quote-body {
            color: #888888;
            font-style: italic;
            font-size: 13px;
            display: block;
        }

        .quote-btn {
            color: #444;
            font-size: 11px;
            margin-left: 8px;
            cursor: pointer;
            user-select: none;
        }

        .quote-btn:hover {
            color: #ff33aa;
            text-shadow: 0 0 4px rgba(255, 51, 170, 0.6);
        }

        /* Input command bar at bottom */
        .input-bar {
            background-color: #000000;
            border-top: 2px solid #222222;
            padding: 12px 16px;
            display: flex;
            align-items: center;
            z-index: 20;
            position: relative;
        }

        .terminal-prompt {
            color: #00ff00;
            font-weight: bold;
            margin-right: 10px;
            white-space: nowrap;
            text-shadow: 0 0 5px rgba(0, 255, 0, 0.5);
            cursor: pointer;
        }

        .input-form {
            display: flex;
            flex: 1;
            align-items: center;
        }

        .terminal-input {
            flex: 1;
            background-color: transparent;
            border: none;
            outline: none;
            color: #ffffff;
            font-family: inherit;
            font-size: 16px; /* 16px prevents automatic iOS auto-zoom */
            caret-color: #00ff00;
            padding: 4px 0;
            width: 100%;
        }

        /* Custom glow indicator for active input */
        .terminal-input:focus {
            text-shadow: 0 0 3px rgba(255, 255, 255, 0.8);
        }

        .action-btn {
            background-color: #111;
            border: 1px solid #333;
            color: #aaa;
            padding: 6px 14px;
            font-family: inherit;
            font-size: 12px;
            cursor: pointer;
            border-radius: 4px;
            margin-left: 10px;
            transition: all 0.2s ease;
        }

        .action-btn:hover {
            color: #ffffff;
            border-color: #666;
        }

        .action-btn:active {
            background-color: #00ff00;
            color: #000;
            box-shadow: 0 0 10px #00ff00;
            border-color: #00ff00;
        }

        /* Connection error warning */
        .connection-alert {
            display: none;
            position: absolute;
            top: -40px;
            left: 0;
            right: 0;
            background-color: #ff3333;
            color: #ffffff;
            text-align: center;
            padding: 8px;
            font-size: 13px;
            font-weight: bold;
            z-index: 30;
            box-shadow: 0 -4px 10px rgba(255, 0, 0, 0.3);
        }

        /* Floating Retro Overlay Modal */
        .retro-modal {
            display: none;
            position: fixed;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            background-color: #0a0a0a;
            border: 2px solid #00ff00;
            box-shadow: 0 0 20px #00ff00;
            padding: 20px;
            width: 90%;
            max-width: 420px;
            z-index: 100;
        }

        .retro-modal-title {
            color: #00ff00;
            font-size: 16px;
            font-weight: bold;
            margin-bottom: 12px;
            text-align: center;
            border-bottom: 1px dashed #00ff00;
            padding-bottom: 8px;
        }

        .user-list-container {
            max-height: 120px;
            overflow-y: auto;
            margin-bottom: 15px;
            border: 1px solid #222;
            padding: 4px;
        }

        .list-item {
            padding: 8px;
            cursor: pointer;
            border: 1px solid transparent;
            margin-bottom: 4px;
            font-size: 13px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .list-item:hover {
            border-color: #00ff00;
            background-color: #111;
        }

        .modal-footer {
            text-align: right;
        }

        .modal-overlay {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background-color: rgba(0, 0, 0, 0.7);
            z-index: 99;
        }

        /* Tablet/Mobile viewport adaptation rules */
        @media (max-width: 768px) {
            .terminal-header {
                padding: 10px;
            }
            .message-feed {
                padding: 12px;
                gap: 8px;
            }
            .message-row {
                font-size: 14px;
            }
            .input-bar {
                padding: 10px;
            }
            .header-buttons {
                gap: 6px;
            }
        }
    </style>
</head>
<body>
    <div class="scanlines"></div>

    <header class="terminal-header">
        <div style="display: flex; align-items: center;">
            <span class="status-dot"></span>
            <span class="terminal-title">MESSAGE TERMINAL</span>
        </div>
        <div class="header-buttons">
            <button class="user-badge" id="notify-toggle-btn">NOTIFY: ON</button>
            <button class="user-badge" id="online-btn">ONLINE</button>
            <div id="user-display" class="user-badge">Initializing...</div>
        </div>
    </header>

    <main class="message-feed" id="feed">
        <div class="system-banner">
            <div>&gt; CONNECTED TO LOCAL LAN TERMINAL EXPRESS</div>
            <div style="margin-top: 4px; color: #666666;">Use <b>/reply</b>, <b>/edit</b>, <b>/online</b>, <b>/notify &lt;on|off&gt;</b> or <b>/name &lt;username&gt;</b> directly in the command prompt.</div>
        </div>
        <!-- Live-populated messages go here -->
    </main>

    <footer class="input-bar">
        <div class="connection-alert" id="conn-alert">OFFLINE - RECONNECTING TERMINAL FEED...</div>
        <div class="terminal-prompt" id="prompt-display" onclick="triggerReplySelection()">guest &gt;</div>
        <form class="input-form" id="chat-form" action="#" onsubmit="sendMessage(event)">
            <input 
                type="text" 
                id="message-input" 
                class="terminal-input" 
                placeholder="Type message or commands..." 
                autocomplete="off" 
                autocorrect="off" 
                autocapitalize="off" 
                spellcheck="false"
            />
            <button type="button" class="action-btn" id="reply-btn-label" onclick="handleReplyOrCancel()">REPLY</button>
            <button type="submit" class="action-btn" id="send-btn-label">SEND</button>
        </form>
    </footer>

    <!-- Interactive User Selector Dialog -->
    <div class="modal-overlay" id="overlay" onclick="closeModal()"></div>
    
    <!-- REPLY MODAL -->
    <div class="retro-modal" id="reply-modal">
        <div class="retro-modal-title">REPLY / QUOTE SYSTEM</div>
        <div style="font-size: 11px; color: #888; margin-bottom: 6px; letter-spacing: 1px;">SELECT RECENT MESSAGE TO QUOTE:</div>
        <div class="user-list-container" id="recent-messages-list">
            <!-- Rendered inline messages to quote -->
        </div>
        <div style="font-size: 11px; color: #888; margin-bottom: 6px; letter-spacing: 1px;">OR SELECT ACTIVE USER TO MENTION:</div>
        <div class="user-list-container" id="user-list">
            <!-- Dynamic Active Users -->
        </div>
        <div class="modal-footer">
            <button class="action-btn" onclick="closeModal()">CANCEL</button>
        </div>
    </div>

    <!-- EDIT MODAL -->
    <div class="retro-modal" id="edit-modal">
        <div class="retro-modal-title">EDIT SYSTEM</div>
        <div style="font-size: 11px; color: #888; margin-bottom: 6px; letter-spacing: 1px;">SELECT AN OWN MESSAGE TO EDIT (LAST 10):</div>
        <div class="user-list-container" id="own-messages-list">
            <!-- Rendered own editable messages -->
        </div>
        <div class="modal-footer">
            <button class="action-btn" onclick="closeModal()">CANCEL</button>
        </div>
    </div>

    <script>
        // Ensure legacy WebKit browser compatibility (safe assignments, no optional chaining)
        var username = localStorage.getItem("terminal_user");
        if (!username) {
            username = "guest" + Math.floor(1000 + Math.random() * 9000);
            localStorage.setItem("terminal_user", username);
        }

        // Load or initialize persistent web notifications setting
        var notificationsEnabled = localStorage.getItem("terminal_notify");
        if (notificationsEnabled === null) {
            notificationsEnabled = "on";
            localStorage.setItem("terminal_notify", "on");
        }

        var feed = document.getElementById("feed");
        var messageInput = document.getElementById("message-input");
        var promptDisplay = document.getElementById("prompt-display");
        var userDisplay = document.getElementById("user-display");
        var connAlert = document.getElementById("conn-alert");
        var modal = document.getElementById("reply-modal");
        var editModal = document.getElementById("edit-modal");
        var overlay = document.getElementById("overlay");
        var userList = document.getElementById("user-list");
        var recentMessagesList = document.getElementById("recent-messages-list");
        var ownMessagesList = document.getElementById("own-messages-list");
        var onlineBtn = document.getElementById("online-btn");
        var notifyToggleBtn = document.getElementById("notify-toggle-btn");

        // Local cache buffer mapping the last 20 messages for quoting and editing instantly
        var recentMsgBuffer = [];
        var editingMessageId = null;

        // Web Audio API context for zero-asset beep generation
        var audioCtx = null;

        // Custom unlock tracker for mobile and modern browser gesture rules
        var audioUnlocked = false;

        function updateNotifyButtonUI() {
            if (notificationsEnabled === "on") {
                notifyToggleBtn.innerHTML = "NOTIFY: ON";
                notifyToggleBtn.className = "user-badge badge-active";
            } else {
                notifyToggleBtn.innerHTML = "NOTIFY: OFF";
                notifyToggleBtn.className = "user-badge badge-inactive";
            }
        }
        updateNotifyButtonUI();

        // Safe Web Audio API synthesizer for nostalgic terminal beeps
        function playBeep() {
            if (notificationsEnabled !== "on") return;
            try {
                if (!audioCtx) {
                    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
                }
                if (audioCtx.state === 'suspended') {
                    audioCtx.resume();
                }
                var osc = audioCtx.createOscillator();
                var gainNode = audioCtx.createGain();
                
                osc.connect(gainNode);
                gainNode.connect(audioCtx.destination);
                
                osc.type = 'sine';
                osc.frequency.setValueAtTime(800, audioCtx.currentTime); // Standard 800Hz beep frequency
                gainNode.gain.setValueAtTime(0.04, audioCtx.currentTime);  // Safe, quiet beep level
                gainNode.gain.exponentialRampToValueAtTime(0.00001, audioCtx.currentTime + 0.12); // Short decay
                
                osc.start();
                osc.stop(audioCtx.currentTime + 0.12);
            } catch(e) {
                console.log("Audio Context alert blocked or not supported:", e);
            }
        }

        // Toggle notifications setting
        function toggleNotifications(forcedState) {
            if (forcedState !== undefined) {
                notificationsEnabled = forcedState;
            } else {
                notificationsEnabled = (notificationsEnabled === "on") ? "off" : "on";
            }
            localStorage.setItem("terminal_notify", notificationsEnabled);
            updateNotifyButtonUI();
            
            // Append a local status line showing the change
            var row = document.createElement("div");
            row.className = "message-row";
            row.innerHTML = '<span class="msg-sender sender-system">System:</span>' +
                            '<span class="msg-text">Web alerts turned ' + notificationsEnabled.toUpperCase() + '.</span>';
            feed.appendChild(row);
            feed.scrollTop = feed.scrollHeight;
        }

        notifyToggleBtn.onclick = function() {
            toggleNotifications();
            playBeep(); // Play confirmation chime
        };

        // Set live system prompt & label
        function updateUIIdentity() {
            promptDisplay.innerHTML = escapeHTML(username) + " &gt;";
            userDisplay.innerHTML = "USER: " + escapeHTML(username);
        }
        updateUIIdentity();

        // Helper to securely clean HTML payload
        function escapeHTML(str) {
            if (!str) return '';
            return str.replace(/&/g, '&amp;')
                      .replace(/</g, '&lt;')
                      .replace(/>/g, '&gt;')
                      .replace(/"/g, '&quot;')
                      .replace(/'/g, '&#39;');
        }

        // Keep viewport position focused on latest inputs on mobile layout recalculation
        window.addEventListener('resize', function() {
            setTimeout(function() {
                feed.scrollTop = feed.scrollHeight;
            }, 80);
        });

        // Initialize Server-Sent Events (SSE) Live Feed subscription
        var eventSource = null;
        function connectSSE() {
            if (eventSource) {
                eventSource.close();
            }

            eventSource = new EventSource('/stream');

            eventSource.onopen = function() {
                connAlert.style.display = 'none';
            };

            eventSource.onerror = function() {
                connAlert.style.display = 'block';
                // Exponential reconnect fallback
                setTimeout(connectSSE, 3000);
            };

            // Capture raw inbound network message updates
            eventSource.onmessage = function(event) {
                try {
                    var data = JSON.parse(event.data);
                    if (data) {
                        if (data.action === "edit" && data.message) {
                            updateSingleMessage(data.message);
                        } else if (data.messages) {
                            appendMessages(data.messages);
                        }
                    }
                } catch(e) {
                    console.error("Payload decoding failure:", e);
                }
            };
        }

        var renderedIds = {};

        function appendMessages(msgArray) {
            var addedNew = false;
            for (var i = 0; i < msgArray.length; i++) {
                var m = msgArray[i];
                if (renderedIds[m.id]) continue; // Prevent double rendering

                renderedIds[m.id] = true;
                addedNew = true;

                // Cache to recent buffer for overlay quoting (ignore system status logs)
                if (m.sender.toLowerCase() !== "system") {
                    recentMsgBuffer.push(m);
                    if (recentMsgBuffer.length > 20) {
                        recentMsgBuffer.shift();
                    }
                    
                    // Trigger dynamic beep warning on incoming message if it is not sent by us
                    if (m.sender !== username) {
                        playBeep();
                    }
                }

                var row = document.createElement("div");
                row.className = "message-row";
                row.id = "msg-" + m.id;

                // Resolve sender color class rules
                var senderClass = "sender-guest";
                var senderLower = m.sender.toLowerCase();
                if (senderLower === "host" || senderLower === "admin") {
                    senderClass = "sender-host";
                } else if (senderLower === "system") {
                    senderClass = "sender-system";
                }

                // Detect @mention targeting logic
                var textContent = escapeHTML(m.text);
                var mentionToken = "@" + username;
                if (textContent.indexOf(mentionToken) !== -1) {
                    var regex = new RegExp(mentionToken, 'g');
                    textContent = textContent.replace(regex, '<span class="mention-text">' + mentionToken + '</span>');
                }

                // Check and parse quote blocks: [Reply to @Sender: "quoted text"] actual reply
                var quoteRegex = /^\[Reply to @([^:]+):\s*&quot;([\s\S]*?)&quot;\]\s*([\s\S]*)$/;
                var quoteMatch = textContent.match(quoteRegex);
                if (quoteMatch) {
                    var quotedUser = quoteMatch[1];
                    var quotedText = quoteMatch[2];
                    var actualText = quoteMatch[3];

                    textContent = '<div class="quote-container">' +
                                    '<span class="quote-header">↳ Replying to @' + quotedUser + '</span>' +
                                    '<span class="quote-body">"' + quotedText + '"</span>' +
                                  '</div>' +
                                  '<div style="margin-top: 4px;" class="actual-msg-content">' + actualText + '</div>';
                } else {
                    textContent = '<span class="actual-msg-content">' + textContent + '</span>';
                }

                // Clean arguments safely for onclick attributes
                var escapedSenderAttr = escapeHTML(m.sender).replace(/'/g, "&#39;");
                var cleanTextForAttr = escapeHTML(m.text).replace(/'/g, "&#39;");
                var canEdit = (m.sender === username);

                var editAppendLabel = m.edited ? ' <span style="color: #555; font-size: 11px; font-style: italic;">(edited)</span>' : '';

                row.innerHTML = '<span class="msg-time">[' + escapeHTML(m.time) + ']</span>' +
                                '<span class="msg-sender ' + senderClass + '" onclick="setMention(\'' + escapedSenderAttr + '\')">' + escapeHTML(m.sender) + ':</span>' +
                                '<span class="msg-text">' + textContent + editAppendLabel + '</span>' +
                                (m.sender.toLowerCase() !== 'system' ? 
                                 '<span class="quote-btn" onclick="quoteMessage(\'' + escapedSenderAttr + '\', this)" data-text="' + cleanTextForAttr + '">[Quote]</span>' : '') +
                                (canEdit ? 
                                 '<span class="quote-btn" style="color: #00ff00; margin-left: 6px;" onclick="setEditState(\'' + m.id + '\', \'' + cleanTextForAttr + '\')">[Edit]</span>' : '');

                feed.appendChild(row);
            }

            if (addedNew) {
                // Auto scroll smoothly to bottom
                feed.scrollTop = feed.scrollHeight;
            }
        }

        // Handle single message updates via SSE edit push
        function updateSingleMessage(m) {
            var row = document.getElementById("msg-" + m.id);
            if (row) {
                var textSpan = row.querySelector(".msg-text");
                if (textSpan) {
                    var textContent = escapeHTML(m.text);
                    var mentionToken = "@" + username;
                    if (textContent.indexOf(mentionToken) !== -1) {
                        var regex = new RegExp(mentionToken, 'g');
                        textContent = textContent.replace(regex, '<span class="mention-text">' + mentionToken + '</span>');
                    }

                    // Check and parse quote blocks
                    var quoteRegex = /^\[Reply to @([^:]+):\s*&quot;([\s\S]*?)&quot;\]\s*([\s\S]*)$/;
                    var quoteMatch = textContent.match(quoteRegex);
                    if (quoteMatch) {
                        var quotedUser = quoteMatch[1];
                        var quotedText = quoteMatch[2];
                        var actualText = quoteMatch[3];

                        textContent = '<div class="quote-container">' +
                                        '<span class="quote-header">↳ Replying to @' + quotedUser + '</span>' +
                                        '<span class="quote-body">"' + quotedText + '"</span>' +
                                      '</div>' +
                                      '<div style="margin-top: 4px;" class="actual-msg-content">' + actualText + '</div>';
                    } else {
                        textContent = '<span class="actual-msg-content">' + textContent + '</span>';
                    }

                    textSpan.innerHTML = textContent + ' <span style="color: #555; font-size: 11px; font-style: italic;">(edited)</span>';

                    // Update click data attribute too if the quote buttons exist
                    var quoteBtn = row.querySelector('.quote-btn');
                    if (quoteBtn) {
                        quoteBtn.setAttribute("data-text", m.text.replace(/'/g, "&#39;"));
                    }
                    
                    // Update cache buffer locally
                    for (var i = 0; i < recentMsgBuffer.length; i++) {
                        if (recentMsgBuffer[i].id === m.id) {
                            recentMsgBuffer[i].text = m.text;
                            recentMsgBuffer[i].edited = true;
                            break;
                        }
                    }
                }
            }
        }

        // Setup editing interface state
        function setEditState(msgId, text) {
            // Strip out nesting quotes to let them edit only the actual reply body
            var cleanedText = text.replace(/^\[Reply to @[^:]+:\s*&quot;[\s\S]*?&quot;\]\s*/g, '');
            cleanedText = cleanedText.replace(/&amp;/g, '&')
                                     .replace(/&lt;/g, '<')
                                     .replace(/&gt;/g, '>')
                                     .replace(/&quot;/g, '"')
                                     .replace(/&#39;/g, "'");

            editingMessageId = msgId;
            messageInput.value = cleanedText;
            promptDisplay.innerHTML = "edit &gt;";
            promptDisplay.style.color = "#ff33aa";
            promptDisplay.style.textShadow = "0 0 5px rgba(255, 51, 170, 0.5)";

            // Shift buttons labels
            document.getElementById("send-btn-label").innerHTML = "SAVE";
            document.getElementById("reply-btn-label").innerHTML = "CANCEL";
            messageInput.focus();
        }

        function clearEditState() {
            editingMessageId = null;
            messageInput.value = "";
            updateUIIdentity();
            promptDisplay.style.color = "#00ff00";
            promptDisplay.style.textShadow = "0 0 5px rgba(0, 255, 0, 0.5)";
            document.getElementById("send-btn-label").innerHTML = "SEND";
            document.getElementById("reply-btn-label").innerHTML = "REPLY";
        }

        function handleReplyOrCancel() {
            if (editingMessageId) {
                clearEditState();
            } else {
                triggerReplySelection();
            }
        }

        // Set mention prefix on message sender click
        function setMention(targetName) {
            if (targetName === "System") return;
            messageInput.value = "@" + targetName + " " + messageInput.value;
            messageInput.focus();
        }

        // Direct quote function triggered from individual [Quote] triggers
        window.quoteMessage = function(sender, element) {
            var rawText = element.getAttribute("data-text");
            // Strip out pre-existing quotes to avoid massive recursive nesting loops
            var cleanedText = rawText.replace(/^\[Reply to @[^:]+:\s*&quot;[\s\S]*?&quot;\]\s*/g, '');
            
            // Truncate to keep quote line clean
            var truncated = cleanedText.length > 40 ? cleanedText.substring(0, 40) + "..." : cleanedText;
            
            // Standardize entities back to ASCII for inputs
            truncated = truncated.replace(/&amp;/g, '&')
                                 .replace(/&lt;/g, '<')
                                 .replace(/&gt;/g, '>')
                                 .replace(/&quot;/g, '"')
                                 .replace(/&#39;/g, "'");

            messageInput.value = '[Reply to @' + sender + ': "' + truncated + '"] ' + messageInput.value;
            messageInput.focus();
        };

        // Post client-side keyboard action
        function sendMessage(e) {
            if (e) e.preventDefault();

            var text = messageInput.value.trim();
            if (!text) return;

            // Check if currently editing
            if (editingMessageId) {
                postEdit(editingMessageId, text);
                clearEditState();
                return;
            }

            // Handle client commands directly
            var nameCommand = parseNameCommand(text);
            if (nameCommand) {
                var oldName = username;
                username = nameCommand;
                localStorage.setItem("terminal_user", username);
                updateUIIdentity();

                // Inform the channel server-side
                postMessage("System", "* " + oldName + " changed username to " + username + " *");
                messageInput.value = "";
                return;
            }

            // Command /notify routing
            if (text.toLowerCase().startsWith("/notify")) {
                var parts = text.split(/\s+/);
                if (parts.length > 1) {
                    var val = parts[1].toLowerCase();
                    if (val === "on" || val === "off") {
                        toggleNotifications(val);
                    } else {
                        toggleNotifications();
                    }
                } else {
                    toggleNotifications();
                }
                messageInput.value = "";
                return;
            }

            // Command /online routing
            if (text.toLowerCase() === "/online") {
                showOnlineList();
                messageInput.value = "";
                return;
            }

            // Command /reply routing
            if (text.toLowerCase() === "/reply") {
                triggerReplySelection();
                messageInput.value = "";
                return;
            }

            // Command /edit routing
            if (text.toLowerCase() === "/edit") {
                triggerEditSelection();
                messageInput.value = "";
                return;
            }

            // Normal Message Dispatch
            postMessage(username, text);
            messageInput.value = "";
            
            // On mobile iOS 12, force refocusing to keep keyboard up
            messageInput.focus();
        }

        // Utility checking command structures
        function parseNameCommand(text) {
            // Checks for both "/name XYZ" and "/reply /name XYZ"
            var patternReplyName = /^\/reply\s+\/name\s+(.+)$/i;
            var patternName = /^\/name\s+(.+)$/i;

            var match = text.match(patternReplyName);
            if (match && match[1].trim()) {
                return match[1].trim();
            }

            match = text.match(patternName);
            if (match && match[1].trim()) {
                return match[1].trim();
            }

            return null;
        }

        function postMessage(sender, text) {
            var xhr = new XMLHttpRequest();
            xhr.open("POST", "/send", true);
            xhr.setRequestHeader("Content-Type", "application/json");
            xhr.onreadystatechange = function() {
                if (xhr.readyState === 4 && xhr.status !== 200) {
                    console.error("Message delivery failed");
                }
            };
            xhr.send(JSON.stringify({
                sender: sender,
                text: text
            }));
        }

        function postEdit(msgId, text) {
            var xhr = new XMLHttpRequest();
            xhr.open("POST", "/edit", true);
            xhr.setRequestHeader("Content-Type", "application/json");
            xhr.onreadystatechange = function() {
                if (xhr.readyState === 4 && xhr.status !== 200) {
                    console.error("Message edit delivery failed");
                }
            };
            xhr.send(JSON.stringify({
                id: msgId,
                sender: username,
                text: text
            }));
        }

        // Keep Presence active in server state
        function sendPresence() {
            var xhr = new XMLHttpRequest();
            xhr.open("POST", "/presence", true);
            xhr.setRequestHeader("Content-Type", "application/json");
            sendPresencePayload(xhr);
        }

        function sendPresencePayload(xhr) {
            xhr.send(JSON.stringify({
                username: username
            }));
        }

        // Pull and display online lists directly inside the terminal feed
        function showOnlineList() {
            var xhr = new XMLHttpRequest();
            xhr.open("GET", "/online_list", true);
            xhr.onreadystatechange = function() {
                if (xhr.readyState === 4 && xhr.status === 200) {
                    try {
                        var data = JSON.parse(xhr.responseText);
                        var list = data.online || [];
                        var row = document.createElement("div");
                        row.className = "message-row";
                        
                        var box = '<div style="border: 1px dashed #00ff00; padding: 10px; margin: 5px 0; color: #00ff00; background: #050505;">';
                        box += '<div>┌── ONLINE TERMINAL USERS ──────┐</div>';
                        for (var i = 0; i < list.length; i++) {
                            var isMe = list[i] === username ? " (You)" : "";
                            box += '<div>│  • ' + escapeHTML(list[i]) + isMe + '</div>';
                        }
                        box += '└── ACTIVE NETWORK CLIENTS ────┘</div>';
                        box += '</div>';

                        row.innerHTML = box;
                        feed.appendChild(row);
                        feed.scrollTop = feed.scrollHeight;
                    } catch(e) {
                        console.error(e);
                    }
                }
            };
            xhr.send();
        }

        // Interactive modal overlay controller
        function triggerReplySelection() {
            // First: Populating local recent messages list to quote
            recentMessagesList.innerHTML = "";
            if (recentMsgBuffer.length === 0) {
                recentMessagesList.innerHTML = '<div style="color: #444; padding: 10px; text-align: center; font-size: 13px;">No recent messages to quote.</div>';
            } else {
                // Show last 5 messages, newest on top
                var displayed = 0;
                for (var i = recentMsgBuffer.length - 1; i >= 0 && displayed < 5; i--) {
                    var msg = recentMsgBuffer[i];
                    displayed++;

                    var div = document.createElement("div");
                    div.className = "list-item";
                    
                    // Strip old nested quotes before displaying inside selector
                    var quoteBodyText = msg.text.replace(/^\[Reply to @[^:]+:\s*\"[\s\S]*?\"\]\s*/g, '');
                    var displayQuote = quoteBodyText.length > 30 ? quoteBodyText.substring(0, 30) + "..." : quoteBodyText;
                    
                    div.innerHTML = "&gt; <b>" + escapeHTML(msg.sender) + "</b>: \"" + escapeHTML(displayQuote) + "\"";
                    
                    (function(mSender, mBody) {
                        div.onclick = function() {
                            var trunc = mBody.length > 40 ? mBody.substring(0, 40) + "..." : mBody;
                            messageInput.value = '[Reply to @' + mSender + ': "' + trunc + '"] ' + messageInput.value;
                            closeModal();
                            messageInput.focus();
                        };
                    })(msg.sender, quoteBodyText);

                    recentMessagesList.appendChild(div);
                }
            }

            // Second: Fetching online users for basic mentioning
            var xhr = new XMLHttpRequest();
            xhr.open("GET", "/online_list", true);
            xhr.onreadystatechange = function() {
                if (xhr.readyState === 4 && xhr.status === 200) {
                    try {
                        var data = JSON.parse(xhr.responseText);
                        var list = data.online || [];
                        
                        userList.innerHTML = "";
                        var otherUsersCount = 0;

                        for (var i = 0; i < list.length; i++) {
                            if (list[i] === username) continue; // Skip ourselves
                            otherUsersCount++;

                            var div = document.createElement("div");
                            div.className = "list-item";
                            div.innerHTML = "&gt; " + escapeHTML(list[i]);
                            div.setAttribute("onclick", "applyMentionTarget('" + escapeHTML(list[i]) + "')");
                            userList.appendChild(div);
                        }

                        if (otherUsersCount === 0) {
                            userList.innerHTML = '<div style="color: #ff3333; padding: 10px; text-align: center; font-size: 13px;">No other active users online.</div>';
                        }

                        modal.style.display = "block";
                        overlay.style.display = "block";
                    } catch(e) {
                        console.error(e);
                    }
                }
            };
            xhr.send();
        }

        // Open edit dialog modal displaying up to 10 of own sent messages
        function triggerEditSelection() {
            ownMessagesList.innerHTML = "";
            var ownMsgs = [];
            for (var i = recentMsgBuffer.length - 1; i >= 0; i--) {
                if (recentMsgBuffer[i].sender === username) {
                    ownMsgs.push(recentMsgBuffer[i]);
                }
            }

            if (ownMsgs.length === 0) {
                ownMessagesList.innerHTML = '<div style="color: #ff3333; padding: 10px; text-align: center; font-size: 13px;">No editable messages found.</div>';
            } else {
                var displayed = 0;
                for (var j = 0; j < ownMsgs.length && displayed < 10; j++) {
                    var msg = ownMsgs[j];
                    displayed++;

                    var div = document.createElement("div");
                    div.className = "list-item";

                    var quoteBodyText = msg.text.replace(/^\[Reply to @[^:]+:\s*\"[\s\S]*?\"\]\s*/g, '');
                    var displayQuote = quoteBodyText.length > 30 ? quoteBodyText.substring(0, 30) + "..." : quoteBodyText;

                    div.innerHTML = "&gt; \"" + escapeHTML(displayQuote) + "\" (at " + escapeHTML(msg.time) + ")";

                    (function(mId, mBody) {
                        div.onclick = function() {
                            setEditState(mId, mBody);
                            closeModal();
                        };
                    })(msg.id, quoteBodyText);

                    ownMessagesList.appendChild(div);
                }
            }
            editModal.style.display = "block";
            overlay.style.display = "block";
        }

        function applyMentionTarget(targetUser) {
            messageInput.value = "@" + targetUser + " " + messageInput.value;
            closeModal();
            messageInput.focus();
        }

        function closeModal() {
            modal.style.display = "none";
            editModal.style.display = "none";
            overlay.style.display = "none";
        }

        // Attach buttons
        onlineBtn.onclick = function() {
            showOnlineList();
        };

        // Listen to initial user interactions on the window to unlock AudioContext
        var unlockEvents = ['click', 'touchstart', 'keydown', 'mousedown'];
        function unlockAudio() {
            if (audioUnlocked) return;
            if (!audioCtx) {
                try {
                    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
                } catch(e) { return; }
            }
            if (audioCtx) {
                if (audioCtx.state === 'suspended') {
                    audioCtx.resume();
                }
                // Play a brief silent note to bypass modern browser autoplay restrictions
                var osc = audioCtx.createOscillator();
                var gainNode = audioCtx.createGain();
                gainNode.gain.setValueAtTime(0, audioCtx.currentTime);
                osc.connect(gainNode);
                gainNode.connect(audioCtx.destination);
                osc.start(0);
                osc.stop(0.01);
                audioUnlocked = true;
            }
            // Remove listeners once audio is successfully unlocked
            for (var i = 0; i < unlockEvents.length; i++) {
                window.removeEventListener(unlockEvents[i], unlockAudio, false);
            }
        }
        for (var i = 0; i < unlockEvents.length; i++) {
            window.addEventListener(unlockEvents[i], unlockAudio, false);
        }

        // Handle page/tab leave dynamically (covers both Desktop and Mobile / iOS Safari WebKit standards safely)
        var hasLeft = false;
        function handleDeparture() {
            if (hasLeft) return;
            hasLeft = true;

            var departureText = "* " + username + " disconnected/left the chat *";
            var payload = JSON.stringify({
                sender: "System",
                text: departureText
            });

            if (navigator.sendBeacon) {
                navigator.sendBeacon('/send', payload);
            } else {
                var xhr = new XMLHttpRequest();
                xhr.open("POST", "/send", false); // Synchronous block ensures execution before page destroy
                xhr.setRequestHeader("Content-Type", "application/json");
                xhr.send(payload);
            }
        }

        // Universal listeners for both desktop and mobile termination hooks
        window.addEventListener('beforeunload', handleDeparture);
        window.addEventListener('unload', handleDeparture);
        window.addEventListener('pagehide', handleDeparture);

        // Execute initial connectivity check
        connectSSE();

        // Let the system know a client has loaded up
        postMessage("System", "* " + username + " connected from " + window.location.hostname + " *");

        // Schedule periodic presence polls
        sendPresence();
        setInterval(sendPresence, 5000);
    </script>
</body>
</html>
"""

# --- NETWORK BROADCAST MECHANISMS ---
def get_local_ips():
    """Retrieve all active network interface IPs to instruct connection paths."""
    ips = ['127.0.0.1']
    try:
        # Probe interfaces
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0)
        # Dummy connect to find outbound routing IP
        s.connect(('10.254.254.254', 1))
        ip = s.getsockname()[0]
        if ip not in ips:
            ips.append(ip)
        s.close()
    except Exception:
        pass
    return ips

def prune_presence():
    """Clean up old active user presence markers (idle more than 15s)."""
    now = time.time()
    with state_lock:
        # Host stays online
        active_users[terminal_username] = now
        for u, last_seen in list(active_users.items()):
            if now - last_seen > 15:
                active_users.pop(u, None)

def broadcast_message(sender, text, is_local=False):
    """Saves message locally and dispatches real-time events to all SSE streams."""
    global messages
    current_time = time.strftime("%H:%M:%S")
    msg_id = str(uuid.uuid4())
    
    msg_obj = {
        "id": msg_id,
        "sender": sender,
        "text": text,
        "time": current_time,
        "edited": False
    }
    
    # Threading Lock visually synchronizes all stdout writes to prevent race conditions on simultaneous printing.
    with state_lock:
        messages.append(msg_obj)
        # Keep host presence updated
        active_users[sender] = time.time()
        # Trim historical log footprint to manage heap memory over uptime
        if len(messages) > 100:
            messages.pop(0)
            
    # Auto-save changes locally to keep 10 saved reply logs active
    save_reply_history()

    # Capture the current input line buffer visually before we clear the line
    current_buffer = ""
    if not is_local:
        current_buffer = get_active_input_buffer()

    # Handle console output clearing to hide raw user input
    if is_local:
        # Move cursor up 1 line and clear it completely to remove raw typed text
        sys.stdout.write("\033[F\033[K")
        sys.stdout.flush()
    else:
        # Clear current prompt line before outputting incoming remote client text
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()

    # Output instantly to Terminal Host console UI
    if sender.lower() == "system":
        print(f"\033[93m[{current_time}] {sender}: {text}\033[0m")
    elif sender.lower() == "host" or sender == terminal_username:
        print(f"\033[92m[{current_time}] {sender}: {text}\033[0m")
    else:
        print(f"\033[96m[{current_time}] {sender}: {text}\033[0m")
        
        # Trigger alert synthesized beep on Windows (or terminal bell on POSIX)
        if terminal_notifications_enabled:
            play_terminal_beep()
        
    # Re-render command CLI prompt only if the message did NOT originate from the local terminal.
    # If it originated locally, the local console input loop will draw the single next prompt cleanly.
    if not is_local:
        print(f"\033[1m{terminal_username} > {current_buffer}\033[0m", end="", flush=True)

    # Wake and push message payload to all active web browser streaming sockets
    json_payload = json.dumps({"messages": [msg_obj]})
    disconnected_clients = set()
    
    with state_lock:
        active_clients = list(clients)
    
    for q in active_clients:
        try:
            q.put_nowait(json_payload)
        except Exception:
            disconnected_clients.add(q)
            
    if disconnected_clients:
        with state_lock:
            clients.difference_update(disconnected_clients)

def broadcast_edit(msg_id, sender, new_text, is_local=False):
    """Locates message inside history by ID, modifies content, and dispatches edit payload."""
    global messages
    current_time = time.strftime("%H:%M:%S")
    target_msg = None

    with state_lock:
        for m in messages:
            if m["id"] == msg_id and m["sender"] == sender:
                m["text"] = new_text
                m["edited"] = True
                target_msg = m
                break

    if not target_msg:
        return

    # Persist updated logs
    save_reply_history()

    # Handle console output clearing to hide raw user inputs cleanly
    if is_local:
        # Move up three lines in input selection sequence and clear
        sys.stdout.write("\033[F\033[K\033[F\033[K\033[F\033[K")
        sys.stdout.flush()
    else:
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()

    # Output instantly to Terminal Host console UI
    with state_lock:
        if sender.lower() == "host" or sender == terminal_username:
            print(f"\033[92m[{current_time}] {sender} (edited): {new_text}\033[0m")
        else:
            print(f"\033[96m[{current_time}] {sender} (edited): {new_text}\033[0m")
            if terminal_notifications_enabled:
                play_terminal_beep()

        if not is_local:
            # Capture the current input line buffer visually before we reprint the prompt
            current_buffer = get_active_input_buffer()
            print(f"\033[1m{terminal_username} > {current_buffer}\033[0m", end="", flush=True)

    # Dispatch to SSE clients
    json_payload = json.dumps({"action": "edit", "message": target_msg})
    disconnected_clients = set()

    with state_lock:
        active_clients = list(clients)

    for q in active_clients:
        try:
            q.put_nowait(json_payload)
        except Exception:
            disconnected_clients.add(q)

    if disconnected_clients:
        with state_lock:
            clients.difference_update(disconnected_clients)

# --- LIGHTWEIGHT HTTP REQUEST HANDLER ---
class TerminalHTTPServer(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'

    def log_message(self, format, *args):
        # Override to suppress default HTTP connection spamming inside CLI output
        pass

    def send_cors_headers(self):
        """Appends complete, secure CORS response headers for robust cross-origin access."""
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Cache-Control')

    def do_OPTIONS(self):
        """Handles HTTP preflight requests gracefully to secure cross-network access."""
        self.send_response(200)
        self.send_cors_headers()
        self.end_headers()

    def do_GET(self):
        global messages
        parsed_path = urllib.parse.urlparse(self.path)
        
        # Stream Endpoint (SSE Connection)
        if parsed_path.path == '/stream':
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'keep-alive')
            self.send_cors_headers()
            self.end_headers()

            # Client registration queue
            q = queue.Queue()
            with state_lock:
                clients.add(q)
                
            # Immediately catch client up with historical chat state
            with state_lock:
                history_payload = json.dumps({"messages": messages})
            try:
                self.wfile.write(f"data: {history_payload}\n\n".encode('utf-8'))
                self.wfile.flush()
            except Exception:
                with state_lock:
                    clients.discard(q)
                return

            # Keep Connection Stream Active
            while True:
                try:
                    # Maintain heartbeat or process pending queue transfers
                    try:
                        msg_data = q.get(timeout=15.0)
                        self.wfile.write(f"data: {msg_data}\n\n".encode('utf-8'))
                        self.wfile.flush()
                    except queue.Empty:
                        # Send periodic SSE ping comments to keep the socket connection alive
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                except Exception:
                    # Connection terminated by browser client
                    with state_lock:
                        clients.discard(q)
                    break
            return

        # Fetch active online user list
        if parsed_path.path == '/online_list':
            prune_presence()
            with state_lock:
                online_users = sorted(list(active_users.keys()))
            
            response_bytes = json.dumps({"online": online_users}).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Connection', 'close')
            self.send_header('Content-Length', str(len(response_bytes)))
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write(response_bytes)
            return

        # Main Webpage Asset
        if parsed_path.path == '/' or parsed_path.path == '/index.html':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Connection', 'close')
            html_bytes = HTML_TEMPLATE.encode('utf-8')
            self.send_header('Content-Length', str(len(html_bytes)))
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write(html_bytes)
            return

        # Route fallback
        self.send_error(404, "File not found")

    def do_POST(self):
        parsed_path = urllib.parse.urlparse(self.path)
        
        # Keep client presence active
        if parsed_path.path == '/presence':
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            try:
                payload = json.loads(post_data.decode('utf-8'))
                username = payload.get('username', '').strip()
                if username:
                    with state_lock:
                        active_users[username] = time.time()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Connection', 'close')
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(b'{"status": "alive"}')
            except Exception:
                self.send_error(400)
            return

        if parsed_path.path == '/send':
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            
            try:
                payload = json.loads(post_data.decode('utf-8'))
                sender = payload.get('sender', 'Anonymous').strip()
                text = payload.get('text', '').strip()
                
                if sender and text:
                    broadcast_message(sender, text, is_local=False)
                    
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Connection', 'close')
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(b'{"status": "delivered"}')
            except Exception as e:
                self.send_error(400, f"Malformed input data: {str(e)}")
            return

        if parsed_path.path == '/edit':
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)

            try:
                payload = json.loads(post_data.decode('utf-8'))
                msg_id = payload.get('id', '').strip()
                sender = payload.get('sender', '').strip()
                text = payload.get('text', '').strip()

                if msg_id and sender and text:
                    broadcast_edit(msg_id, sender, text, is_local=False)

                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Connection', 'close')
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(b'{"status": "edited"}')
            except Exception as e:
                self.send_error(400, f"Malformed edit data: {str(e)}")
            return
            
        self.send_error(404)

class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        """
        Suppresses noisy standard library socket disconnection tracebacks (e.g. WinError 10053/10054).
        These occur naturally when mobile devices or old browsers abruptly close their connection.
        """
        exc_type, exc_value, _ = sys.exc_info()
        if exc_type is not None:
            # Silence connection abortion, connection reset, and broken pipe errors
            if issubclass(exc_type, (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)):
                return
            # Check string representation for Windows-specific socket codes
            err_str = str(exc_value)
            if "10053" in err_str or "10054" in err_str or "WSAECONNABORTED" in err_str:
                return
        # Pass non-network unexpected exceptions back to the default handler safely
        super().handle_error(request, client_address)

# --- CLI INTERACTION THREAD ---
def terminal_input_thread():
    """Listens directly on standard input for console/terminal messages and commands."""
    global terminal_username, terminal_notifications_enabled
    time.sleep(1.0) # Grace period for initialization outputs
    
    while True:
        try:
            print(f"\033[1m{terminal_username} > \033[0m", end="", flush=True)
            user_input = sys.stdin.readline()
            if not user_input:
                break
                
            command = user_input.strip()
            if not command:
                continue
                
            # Parse command operations
            if command.lower().startswith("/name ") or command.lower().startswith("/reply /name "):
                parts = command.split()
                if command.lower().startswith("/reply "):
                    new_name = " ".join(parts[2:]) if len(parts) > 2 else ""
                else:
                    new_name = " ".join(parts[1:]) if len(parts) > 1 else ""
                    
                if new_name.strip():
                    old_name = terminal_username
                    terminal_username = new_name.strip()
                    save_terminal_username(terminal_username)
                    broadcast_message("System", f"* Host changed username to '{terminal_username}' *", is_local=True)
                else:
                    print("\033[91mError: Name parameter cannot be blank.\033[0m")
            
            # Interactive CLI notification toggles
            elif command.lower().startswith("/notify"):
                with state_lock:
                    # Clear raw command output
                    sys.stdout.write("\033[F\033[K")
                    sys.stdout.flush()
                    
                    parts = command.split()
                    if len(parts) > 1:
                        arg = parts[1].lower()
                        if arg == "on":
                            terminal_notifications_enabled = True
                        elif arg == "off":
                            terminal_notifications_enabled = False
                    else:
                        terminal_notifications_enabled = not terminal_notifications_enabled
                    
                    save_terminal_notifications(terminal_notifications_enabled)
                    status = "ON" if terminal_notifications_enabled else "OFF"
                    
                    # Render system state alert to standard printout
                    print(f"\033[93mSystem: Terminal audio alerts turned {status}.\033[0m")
                    if terminal_notifications_enabled:
                        play_terminal_beep()

            # Interactive Terminal `/help` commands directory
            elif command.lower() == "/help":
                with state_lock:
                    # Clear raw typed '/help' input line
                    sys.stdout.write("\033[F\033[K")
                    sys.stdout.flush()

                    print("\033[94m┌── TERMINAL COMMANDS GUIDE ────────────────────────┐\033[0m")
                    print("\033[94m│  • /name <username>  - Change your handle/username\033[0m")
                    print("\033[94m│  • /online           - View active LAN web users  \033[0m")
                    print("\033[94m│  • /notify <on/off>  - Toggle message alert beeps \033[0m")
                    print("\033[94m│  • /reply            - Quote recent message & reply\033[0m")
                    print("\033[94m│  • /edit             - Edit your own recent messages\033[0m")
                    print("\033[94m│  • /help             - Display this help system   \033[0m")
                    print("\033[94m└───────────────────────────────────────────────────┘\033[0m")

            # Interactive Terminal `/online` list command
            elif command.lower() == "/online":
                prune_presence()
                with state_lock:
                    online_users = sorted(list(active_users.keys()))
                    
                    # Clear raw typed '/online' input line
                    sys.stdout.write("\033[F\033[K")
                    sys.stdout.flush()

                    print("\033[94m┌── ONLINE USERS ─────────────────────┐\033[0m")
                    for user in online_users:
                        flag = " (You)" if user == terminal_username else ""
                        print(f"\033[94m│  • {user}{flag}\033[0m")
                    print("\033[94m└─────────────────────────────────────┘\033[0m")

            # Interactive Graphical Terminal `/reply` selection sequence
            elif command.lower() == "/reply":
                prune_presence()
                
                # Fetch recent messages, ignoring status logs from the System profile
                with state_lock:
                    recent_msgs = [m for m in messages if m["sender"].lower() != "system"][-5:]
                
                if not recent_msgs:
                    with state_lock:
                        # Clear raw typed '/reply' line
                        sys.stdout.write("\033[F\033[K")
                        sys.stdout.flush()
                        print("\033[91mNo recent messages found to reply to.\033[0m")
                    continue
                
                with state_lock:
                    # Clear raw typed '/reply' line
                    sys.stdout.write("\033[F\033[K")
                    sys.stdout.flush()

                    print("\033[94m┌── SELECT MESSAGE TO REPLY & QUOTE ──┐\033[0m")
                    for i, msg in enumerate(reversed(recent_msgs), 1):
                        # Clean pre-existing quoting artifacts for clear listing representation
                        cleaned_body = msg['text']
                        if cleaned_body.startswith("[Reply to @"):
                            parts = cleaned_body.split("] ", 1)
                            if len(parts) > 1:
                                cleaned_body = parts[1]
                        
                        trunc_txt = cleaned_body if len(cleaned_body) <= 30 else cleaned_body[:30] + "..."
                        print(f"\033[94m│  [{i}] {msg['sender']}: \"{trunc_txt}\"\033[0m")
                    print("\033[94m└─────────────────────────────────────┘\033[0m")
                
                print("\033[1mSelect message number > \033[0m", end="", flush=True)
                choice_input = sys.stdin.readline().strip()
                try:
                    choice_idx = len(recent_msgs) - int(choice_input)
                    if 0 <= choice_idx < len(recent_msgs):
                        target_msg = recent_msgs[choice_idx]
                        target_user = target_msg['sender']
                        
                        # Strip pre-existing nesting quotes
                        target_raw_text = target_msg['text']
                        if target_raw_text.startswith("[Reply to @"):
                            parts = target_raw_text.split("] ", 1)
                            if len(parts) > 1:
                                target_raw_text = parts[1]
                                
                        trunc_target = target_raw_text if len(target_raw_text) <= 40 else target_raw_text[:40] + "..."
                        
                        print(f"\033[1mMessage to @{target_user} > \033[0m", end="", flush=True)
                        msg_text = sys.stdin.readline().strip()
                        if msg_text:
                            # Deliver matching formatted quote structure
                            broadcast_message(
                                terminal_username, 
                                f'[Reply to @{target_user}: "{trunc_target}"] {msg_text}', 
                                is_local=True
                            )
                    else:
                        print("\033[91mSelection error: invalid number.\033[0m")
                except ValueError:
                    print("\033[91mSelection error: number required.\033[0m")

            # Interactive Graphical Terminal `/edit` selection sequence
            elif command.lower() == "/edit":
                prune_presence()

                # Fetch last 10 messages sent by Host
                with state_lock:
                    host_msgs = [m for m in messages if m["sender"] == terminal_username][-10:]

                if not host_msgs:
                    with state_lock:
                        sys.stdout.write("\033[F\033[K")
                        sys.stdout.flush()
                        print("\033[91mNo editable messages found.\033[0m")
                    continue

                with state_lock:
                    # Clear raw typed '/edit' line
                    sys.stdout.write("\033[F\033[K")
                    sys.stdout.flush()

                    print("\033[94m┌── SELECT AN OWN MESSAGE TO EDIT ──────────────────┐\033[0m")
                    for i, msg in enumerate(reversed(host_msgs), 1):
                        cleaned_body = msg['text']
                        if cleaned_body.startswith("[Reply to @"):
                            parts = cleaned_body.split("] ", 1)
                            if len(parts) > 1:
                                cleaned_body = parts[1]
                        trunc_txt = cleaned_body if len(cleaned_body) <= 30 else cleaned_body[:30] + "..."
                        print(f"\033[94m│  [{i}] \"{trunc_txt}\" (at {msg['time']})\033[0m")
                    print("\033[94m└───────────────────────────────────────────────────┘\033[0m")

                print("\033[1mSelect message number to edit > \033[0m", end="", flush=True)
                choice_input = sys.stdin.readline().strip()
                try:
                    choice_idx = len(host_msgs) - int(choice_input)
                    if 0 <= choice_idx < len(host_msgs):
                        target_msg = host_msgs[choice_idx]

                        # Strip nesting quotes before displaying
                        target_raw_text = target_msg['text']
                        if target_raw_text.startswith("[Reply to @"):
                            parts = target_raw_text.split("] ", 1)
                            if len(parts) > 1:
                                target_raw_text = parts[1]

                        print(f"\033[93mCurrent Text: {target_raw_text}\033[0m")
                        print("\033[1mNew Text > \033[0m", end="", flush=True)
                        new_text = sys.stdin.readline().strip()
                        if new_text:
                            broadcast_edit(target_msg['id'], terminal_username, new_text, is_local=True)
                    else:
                        print("\033[91mSelection error: invalid number.\033[0m")
                except ValueError:
                    print("\033[91mSelection error: number required.\033[0m")

            else:
                # Normal Host message transmission (marking is_local=True to prevent duplicate rendering)
                broadcast_message(terminal_username, command, is_local=True)
                
        except (KeyboardInterrupt, SystemExit):
            break
        except Exception as e:
            print(f"\nError processing input command: {str(e)}")

# --- SYSTEM ENTRYPOINT ---
def main():
    global PORT, CUSTOM_DOMAIN
    
    # 1. Parse command-line customization arguments (e.g. `python chat.py chat.local:9000`)
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if ":" in arg:
            parts = arg.split(":")
            CUSTOM_DOMAIN = parts[0]
            try:
                PORT = int(parts[1])
            except ValueError:
                pass
        else:
            # Check if port-only argument
            try:
                PORT = int(arg)
            except ValueError:
                CUSTOM_DOMAIN = arg
                
    if len(sys.argv) > 2:
        try:
            PORT = int(sys.argv[2])
        except ValueError:
            pass

    print("\033[92m" + BANNER + "\033[0m")
    print("----------------------------------------------------------------")
    print("🚀 Initializing Live TCP/IP Service Stack...")
    
    # Identify connection avenues
    local_ips = get_local_ips()
    print("----------------------------------------------------------------")
    print("📡 LAN & WAN BROADCAST SERVICES ACTIVE")
    print("Your terminal is listening on all network interfaces (0.0.0.0).")
    print("This allows connections from both your local network and globally.")
    print("----------------------------------------------------------------")
    
    # Render customized domain route if configured, alongside traditional local IP fallbacks
    if CUSTOM_DOMAIN:
        print(f"   🌐  Global/Custom URL: \033[1m\033[93mhttp://{CUSTOM_DOMAIN}:{PORT}/\033[0m")
    
    print("   👉  Local LAN URLs:")
    for ip in local_ips:
        print(f"       • \033[1m\033[94mhttp://{ip}:{PORT}/\033[0m")
        
    print("----------------------------------------------------------------")
    print("💡 HOST TERMINAL GUIDE:")
    print(" - Type directly to send a message.")
    print(" - Change identity via command: /name <your_new_name>")
    print(" - View online network clients: /online")
    print(" - Toggle audio notification alerts: /notify <on/off>")
    print(" - Interactively mention/reply/quote users: /reply")
    print(" - Edit your own recent messages: /edit")
    print(" - See all commands: /help")
    print("----------------------------------------------------------------\n")

    # Bind and deploy the socket handler
    try:
        server = ThreadedTCPServer(('0.0.0.0', PORT), TerminalHTTPServer)
    except Exception as e:
        print(f"\033[91mInitialization failed: Port {PORT} is currently in use.\033[0m")
        print(f"Detail: {str(e)}")
        sys.exit(1)

    # Launch CLI Listener Background Thread
    cli_thread = threading.Thread(target=terminal_input_thread, daemon=True)
    cli_thread.start()

    # Launch Web Server Loop
    try:
        server.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        print("\nShutting down Terminal connection stack. Goodbye.")
        server.shutdown()
        server.server_close()

if __name__ == '__main__':
    main()
