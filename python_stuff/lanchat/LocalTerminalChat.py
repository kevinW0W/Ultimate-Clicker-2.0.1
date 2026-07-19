#!/usr/bin/env python3
import os
import sys
import json
import time
import socket
import select
import threading
import shutil

# Correct conditional import for Windows systems
if os.name == 'nt':
    import msvcrt
else:
    msvcrt = None

# Try importing signal for resize events (UNIX)
try:
    import signal
except ImportError:
    signal = None

# Thread safety lock for all Terminal writes
terminal_write_lock = threading.RLock()

# Global Chat State
chat_history = []  # List of dicts: {id, username, text, reply_to_id, reply_quote, timestamp, edited}
active_users = {}  # {user_id: {username, last_seen}}
message_counter = 0

# Config File for Host
CONFIG_FILE = "chat_config.json"
host_config = {"username": "HostAdmin", "notify": "on"}

# Load host config if exists
if os.path.exists(CONFIG_FILE):
    try:
        with open(CONFIG_FILE, 'r') as f:
            host_config.update(json.load(f))
    except Exception:
        pass

def save_host_config():
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(host_config, f)
    except Exception:
        pass

# Helper to find Local Wi-Fi IP
def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # doesn't even have to be reachable
        s.connect(('10.255.255.255', 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP

LOCAL_IP = get_local_ip()
PORT = 5000

# Server-Sent Events (SSE) Client queues
sse_clients = []
sse_lock = threading.Lock()

def broadcast_to_web(event_type, data):
    payload = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
    with sse_lock:
        to_remove = []
        for q in sse_clients:
            try:
                q.put_nowait(payload)
            except Exception:
                to_remove.append(q)
        for q in to_remove:
            if q in sse_clients:
                sse_clients.remove(q)

# Simple Queue for thread safety without external libraries
class SimpleQueue:
    def __init__(self):
        self.items = []
        self.cond = threading.Condition()
    def put_nowait(self, item):
        with self.cond:
            self.items.append(item)
            self.cond.notify_all()
    def get(self, timeout=None):
        with self.cond:
            start = time.time()
            while not self.items:
                if timeout is not None:
                    elapsed = time.time() - start
                    if elapsed >= timeout:
                        return None
                    self.cond.wait(timeout - elapsed)
                else:
                    self.cond.wait()
            return self.items.pop(0)

# HTML/JS Code for Web Clients
WEB_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Message Terminal</title>
    <style>
        /* Retro Styling matching Terminal Theme & iOS 12 compatibility */
        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            -webkit-tap-highlight-color: transparent;
        }
        body, html {
            background-color: #000000;
            color: #39ff14; /* Cyber Neon Green */
            font-family: "Courier New", Courier, monospace, sans-serif;
            height: 100%;
            overflow: hidden;
        }
        /* Scanline CRT overlay */
        body::before {
            content: " ";
            display: block;
            position: absolute;
            top: 0; left: 0; bottom: 0; right: 0;
            background: linear-gradient(rgba(18, 16, 16, 0) 50%, rgba(0, 0, 0, 0.25) 50%), linear-gradient(90deg, rgba(255, 0, 0, 0.06), rgba(0, 255, 0, 0.02), rgba(0, 0, 255, 0.06));
            aspect-ratio: auto;
            background-size: 100% 4px, 6px 100%;
            z-index: 2000;
            pointer-events: none;
        }
        .container {
            display: flex;
            flex-direction: column;
            height: 100%;
            max-width: 800px;
            margin: 0 auto;
            border-left: 1px solid #1a5c11;
            border-right: 1px solid #1a5c11;
        }
        /* Header */
        header {
            background-color: #050b04;
            border-bottom: 2px solid #39ff14;
            padding: 10px;
            text-align: center;
            font-weight: bold;
            text-shadow: 0 0 5px #39ff14;
            font-size: 1.1rem;
            flex-shrink: 0;
        }
        .subheader {
            font-size: 0.8rem;
            color: #00ffcc;
            margin-top: 4px;
        }
        /* Message Window */
        .chat-area {
            flex-grow: 1;
            overflow-y: auto;
            padding: 15px;
            -webkit-overflow-scrolling: touch; /* Butter smooth iOS 12 scroll */
        }
        .msg-card {
            margin-bottom: 12px;
            border-bottom: 1px dashed #1a5c11;
            padding-bottom: 8px;
            word-wrap: break-word;
            word-break: break-all;
        }
        .msg-meta {
            font-size: 0.75rem;
            color: #888;
            margin-bottom: 3px;
            display: flex;
            justify-content: space-between;
        }
        .msg-sender {
            color: #00ffcc;
            font-weight: bold;
        }
        .msg-body {
            line-height: 1.4;
            text-shadow: 0 0 2px #39ff14;
        }
        .msg-edited {
            font-size: 0.7rem;
            color: #ff5555;
            margin-left: 5px;
        }
        /* Quote Reply Box */
        .quote-container {
            background-color: #050b04;
            border-left: 3px solid #ffcc00;
            padding: 4px 8px;
            margin: 4px 0;
            font-size: 0.8rem;
            color: #ffcc00;
        }
        /* Reply Bar on Bottom */
        .reply-preview {
            display: none;
            background-color: #111;
            border-top: 1px solid #ffcc00;
            padding: 8px;
            font-size: 0.8rem;
            color: #ffcc00;
            justify-content: space-between;
            align-items: center;
        }
        .reply-preview button {
            background: none;
            border: 1px solid #ff5555;
            color: #ff5555;
            padding: 2px 6px;
            cursor: pointer;
        }
        /* Controls & Input Area */
        .input-area {
            background-color: #050b04;
            border-top: 2px solid #39ff14;
            padding: 10px;
            flex-shrink: 0;
        }
        .controls-row {
            display: flex;
            justify-content: space-between;
            margin-bottom: 8px;
            font-size: 0.8rem;
        }
        .control-btn {
            background: #000;
            color: #39ff14;
            border: 1px solid #39ff14;
            padding: 4px 8px;
            cursor: pointer;
            font-family: inherit;
        }
        .control-btn:hover {
            background: #39ff14;
            color: #000;
        }
        .input-row {
            display: flex;
            width: 100%;
        }
        .chat-input {
            flex-grow: 1;
            background-color: #000;
            color: #39ff14;
            border: 1px solid #39ff14;
            padding: 10px;
            font-family: inherit;
            font-size: 1rem;
            outline: none;
        }
        .chat-input:focus {
            box-shadow: 0 0 5px #39ff14;
        }
        .send-btn {
            background-color: #111;
            color: #39ff14;
            border: 1px solid #39ff14;
            padding: 0 15px;
            font-family: inherit;
            cursor: pointer;
            font-weight: bold;
        }
        .send-btn:active {
            background-color: #39ff14;
            color: #000;
        }
        /* Dialog Modal */
        .modal {
            display: none;
            position: fixed;
            top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(0,0,0,0.85);
            z-index: 1000;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }
        .modal-content {
            background: #050b04;
            border: 2px solid #39ff14;
            padding: 20px;
            max-width: 400px;
            width: 100%;
            box-shadow: 0 0 15px #39ff14;
        }
        .modal-title {
            font-weight: bold;
            margin-bottom: 12px;
            text-align: center;
        }
        .modal-input {
            width: 100%;
            background: #000;
            color: #39ff14;
            border: 1px solid #39ff14;
            padding: 8px;
            margin-bottom: 15px;
            font-family: inherit;
        }
        .modal-buttons {
            display: flex;
            justify-content: flex-end;
            gap: 10px;
        }
        /* Selection List for Replies */
        .selection-list {
            max-height: 200px;
            overflow-y: auto;
            margin-bottom: 15px;
            border: 1px solid #1a5c11;
        }
        .selection-item {
            padding: 8px;
            border-bottom: 1px solid #1a5c11;
            cursor: pointer;
        }
        .selection-item:hover {
            background-color: #1a5c11;
            color: #fff;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            MESSAGE TERMINAL
            <div class="subheader" id="client-info">Scanning connection...</div>
        </header>

        <div class="chat-area" id="chat-area"></div>

        <div class="reply-preview" id="reply-preview">
            <span id="reply-preview-text">Replying...</span>
            <button onclick="clearReply()">Cancel</button>
        </div>

        <div class="input-area">
            <div class="controls-row">
                <button class="control-btn" onclick="openUsernameModal()">/name</button>
                <button class="control-btn" onclick="triggerReplyWorkflow()">/reply</button>
                <button class="control-btn" onclick="triggerEditWorkflow()">/edit</button>
                <button class="control-btn" id="notify-toggle" onclick="toggleNotify()">/notify ON</button>
                <button class="control-btn" onclick="openHelpModal()">/help</button>
            </div>
            <form onsubmit="sendMessage(event)" class="input-row">
                <input type="text" id="chat-input" class="chat-input" autocomplete="off" placeholder="Type a message or command..." />
                <button type="submit" class="send-btn">SEND</button>
            </form>
        </div>
    </div>

    <!-- Modals -->
    <div id="username-modal" class="modal">
        <div class="modal-content">
            <div class="modal-title">CHANGE USERNAME</div>
            <input type="text" id="username-input" class="modal-input" maxlength="20" placeholder="New Username" />
            <div class="modal-buttons">
                <button class="control-btn" onclick="closeModal('username-modal')">Cancel</button>
                <button class="control-btn" onclick="saveUsername()">Save</button>
            </div>
        </div>
    </div>

    <div id="reply-modal" class="modal">
        <div class="modal-content">
            <div class="modal-title">CHOOSE MESSAGE TO REPLY</div>
            <div class="selection-list" id="reply-list"></div>
            <div class="modal-buttons">
                <button class="control-btn" onclick="closeModal('reply-modal')">Cancel</button>
            </div>
        </div>
    </div>

    <div id="edit-modal" class="modal">
        <div class="modal-content">
            <div class="modal-title">CHOOSE MESSAGE TO EDIT</div>
            <div class="selection-list" id="edit-list"></div>
            <div class="modal-buttons">
                <button class="control-btn" onclick="closeModal('edit-modal')">Cancel</button>
            </div>
        </div>
    </div>

    <div id="edit-form-modal" class="modal">
        <div class="modal-content">
            <div class="modal-title">EDIT MESSAGE</div>
            <input type="text" id="edit-content-input" class="modal-input" />
            <div class="modal-buttons">
                <button class="control-btn" onclick="closeModal('edit-form-modal')">Cancel</button>
                <button class="control-btn" onclick="submitEdit()">Apply</button>
            </div>
        </div>
    </div>

    <div id="help-modal" class="modal">
        <div class="modal-content">
            <div class="modal-title">TERMINAL HELP</div>
            <div style="font-size:0.8rem; line-height: 1.6; margin-bottom: 15px;">
                <strong>Commands:</strong><br>
                • <code>/name [username]</code> - Change username<br>
                • <code>/reply</code> - Select message to reply to<br>
                • <code>/edit</code> - Edit your previous messages<br>
                • <code>/notify [on/off]</code> - Toggle sound beeps<br>
                • <code>/online</code> - View active terminal users
            </div>
            <div class="modal-buttons">
                <button class="control-btn" onclick="closeModal('help-modal')">Close</button>
            </div>
        </div>
    </div>

    <script>
        var username = localStorage.getItem("chat_username") || ("WebUser_" + Math.floor(Math.random()*9000+1000));
        var notifySetting = localStorage.getItem("chat_notify") || "on";
        var myId = localStorage.getItem("chat_user_id") || ("uid_" + Math.random().toString(36).substr(2, 9));
        localStorage.setItem("chat_user_id", myId);
        localStorage.setItem("chat_username", username);

        var replyTargetId = null;
        var replyTargetQuote = "";
        var replyTargetUser = "";
        var activeMessages = [];
        var editTargetId = null;

        // Sound Engine using Web Audio API (highly compatible with iOS 12 WebKit)
        var audioCtx = null;
        function playBeep() {
            if (notifySetting !== "on") return;
            try {
                if (!audioCtx) {
                    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
                }
                var osc = audioCtx.createOscillator();
                var gain = audioCtx.createGain();
                osc.type = "sine";
                osc.frequency.setValueAtTime(880, audioCtx.currentTime); // Pitch of classic terminal sound
                gain.gain.setValueAtTime(0.05, audioCtx.currentTime);
                gain.gain.exponentialRampToValueAtTime(0.00001, audioCtx.currentTime + 0.15);
                osc.connect(gain);
                gain.connect(audioCtx.destination);
                osc.start();
                osc.stop(audioCtx.currentTime + 0.15);
            } catch(e) {
                console.log("Audio not allowed yet by user interaction context.");
            }
        }

        // Before leave guard
        window.onbeforeunload = function() {
            return "Are you sure you want to exit the terminal chat?";
        };

        // DOM Elements
        var chatArea = document.getElementById("chat-area");
        var chatInput = document.getElementById("chat-input");
        var clientInfo = document.getElementById("client-info");
        var notifyToggle = document.getElementById("notify-toggle");

        function updateNotifyUI() {
            notifyToggle.innerText = "/notify " + notifySetting.toUpperCase();
            localStorage.setItem("chat_notify", notifySetting);
        }
        updateNotifyUI();

        function toggleNotify() {
            notifySetting = (notifySetting === "on") ? "off" : "on";
            updateNotifyUI();
            if (notifySetting === "on") playBeep();
        }

        // Register Web Client
        function registerClient() {
            fetch('/api/register', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ user_id: myId, username: username })
            });
        }
        registerClient();
        setInterval(registerClient, 10000); // Keep alive

        // Server-Sent Events (SSE) for instant messaging
        var es = new EventSource("/api/events");
        es.addEventListener("message", function(e) {
            var data = JSON.parse(e.data);
            activeMessages = data;
            renderMessages();
            playBeep();
        });

        es.addEventListener("online_list", function(e) {
            var users = JSON.parse(e.data);
            clientInfo.innerText = "Active: " + users.join(", ") + " | IP: " + location.host;
        });

        es.onerror = function() {
            clientInfo.innerText = "Lost connection! Retrying...";
            clientInfo.style.color = "#ff5555";
        };

        es.onopen = function() {
            clientInfo.innerText = "Connected | Name: " + username;
            clientInfo.style.color = "#00ffcc";
        };

        function renderMessages() {
            var currentScroll = chatArea.scrollTop;
            var isAtBottom = (chatArea.scrollHeight - chatArea.clientHeight) - chatArea.scrollTop < 60;
            
            chatArea.innerHTML = "";
            activeMessages.forEach(function(m) {
                var card = document.createElement("div");
                card.className = "msg-card";
                card.id = "msg-" + m.id;

                var meta = document.createElement("div");
                meta.className = "msg-meta";
                
                var senderSpan = document.createElement("span");
                senderSpan.className = "msg-sender";
                senderSpan.innerText = m.username;
                if (m.username === username) {
                    senderSpan.style.color = "#39ff14"; // highlight user own messages
                }
                
                var timeSpan = document.createElement("span");
                timeSpan.innerText = m.timestamp || "";

                meta.appendChild(senderSpan);
                meta.appendChild(timeSpan);
                card.appendChild(meta);

                // Quote Section
                if (m.reply_quote) {
                    var quote = document.createElement("div");
                    quote.className = "quote-container";
                    quote.innerText = "⚡ " + m.reply_quote;
                    card.appendChild(quote);
                }

                // Message Body
                var body = document.createElement("div");
                body.className = "msg-body";
                body.innerText = m.text;
                if (m.edited) {
                    var editedTag = document.createElement("span");
                    editedTag.className = "msg-edited";
                    editedTag.innerText = "(edited)";
                    body.appendChild(editedTag);
                }
                card.appendChild(body);

                chatArea.appendChild(card);
            });

            if (isAtBottom) {
                chatArea.scrollTop = chatArea.scrollHeight;
            } else {
                chatArea.scrollTop = currentScroll;
            }
        }

        // Send Message
        function sendMessage(e) {
            if(e) e.preventDefault();
            var text = chatInput.value.trim();
            if(!text) return;

            // Command Processing on Client
            if (text.startsWith("/")) {
                var parts = text.split(" ");
                var cmd = parts[0].toLowerCase();
                if (cmd === "/name") {
                    if (parts[1]) {
                        username = parts.slice(1).join(" ");
                        localStorage.setItem("chat_username", username);
                        registerClient();
                        clientInfo.innerText = "Connected | Name: " + username;
                    } else {
                        openUsernameModal();
                    }
                    chatInput.value = "";
                    return;
                } else if (cmd === "/notify") {
                    if (parts[1]) {
                        notifySetting = parts[1].toLowerCase() === "on" ? "on" : "off";
                        updateNotifyUI();
                    } else {
                        toggleNotify();
                    }
                    chatInput.value = "";
                    return;
                } else if (cmd === "/reply") {
                    triggerReplyWorkflow();
                    chatInput.value = "";
                    return;
                } else if (cmd === "/edit") {
                    triggerEditWorkflow();
                    chatInput.value = "";
                    return;
                } else if (cmd === "/help") {
                    openHelpModal();
                    chatInput.value = "";
                    return;
                } else if (cmd === "/online") {
                    fetch('/api/online')
                    .then(r => r.json())
                    .then(data => {
                        alert("Online users: " + data.join(", "));
                    });
                    chatInput.value = "";
                    return;
                }
            }

            var payload = {
                user_id: myId,
                username: username,
                text: text
            };

            if (replyTargetId !== null) {
                payload.reply_to_id = replyTargetId;
                payload.reply_quote = '@' + replyTargetUser + ': "' + replyTargetQuote + '"';
            }

            fetch('/api/message', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            }).then(function() {
                // Keep the virtual keyboard UP on mobile browsers
                chatInput.focus();
            });

            chatInput.value = "";
            clearReply();
        }

        // Reply System
        function triggerReplyWorkflow() {
            var list = document.getElementById("reply-list");
            list.innerHTML = "";
            if (activeMessages.length === 0) {
                list.innerHTML = "<div class='selection-item'>No messages to reply to</div>";
            }
            // limit to last 10 messages
            var sliceIdx = Math.max(0, activeMessages.length - 10);
            activeMessages.slice(sliceIdx).forEach(function(m) {
                var item = document.createElement("div");
                item.className = "selection-item";
                item.innerText = m.username + ": " + m.text.substring(0, 40) + (m.text.length > 40 ? "...":"");
                item.onclick = function() {
                    setReply(m.id, m.text, m.username);
                    closeModal('reply-modal');
                };
                list.appendChild(item);
            });
            openModal('reply-modal');
        }

        function setReply(id, text, user) {
            replyTargetId = id;
            replyTargetQuote = text.substring(0, 30);
            if (text.length > 30) replyTargetQuote += "...";
            replyTargetUser = user;
            document.getElementById("reply-preview-text").innerText = "Reply to @" + user + ': "' + replyTargetQuote + '"';
            document.getElementById("reply-preview").style.display = "flex";
        }

        function clearReply() {
            replyTargetId = null;
            replyTargetQuote = "";
            replyTargetUser = "";
            document.getElementById("reply-preview").style.display = "none";
        }

        // Edit System
        function triggerEditWorkflow() {
            var list = document.getElementById("edit-list");
            list.innerHTML = "";
            var myMessages = activeMessages.filter(function(m) { return m.user_id === myId; });
            // last 10 messages
            var sliceIdx = Math.max(0, myMessages.length - 10);
            myMessages.slice(sliceIdx).forEach(function(m) {
                var item = document.createElement("div");
                item.className = "selection-item";
                item.innerText = m.text.substring(0, 45);
                item.onclick = function() {
                    closeModal('edit-modal');
                    openEditForm(m.id, m.text);
                };
                list.appendChild(item);
            });
            if (myMessages.length === 0) {
                list.innerHTML = "<div class='selection-item'>You haven't sent any messages yet</div>";
            }
            openModal('edit-modal');
        }

        function openEditForm(id, oldText) {
            editTargetId = id;
            document.getElementById("edit-content-input").value = oldText;
            openModal('edit-form-modal');
        }

        function submitEdit() {
            var newText = document.getElementById("edit-content-input").value.trim();
            if (!newText || editTargetId === null) return;
            fetch('/api/edit', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id: editTargetId, text: newText })
            });
            closeModal('edit-form-modal');
        }

        // Modals Management
        function openModal(id) {
            document.getElementById(id).style.display = "flex";
        }
        function closeModal(id) {
            document.getElementById(id).style.display = "none";
        }
        function openUsernameModal() {
            document.getElementById("username-input").value = username;
            openModal('username-modal');
        }
        function saveUsername() {
            var newName = document.getElementById("username-input").value.trim();
            if(newName) {
                username = newName;
                localStorage.setItem("chat_username", username);
                registerClient();
                clientInfo.innerText = "Connected | Name: " + username;
            }
            closeModal('username-modal');
        }
        function openHelpModal() {
            openModal('help-modal');
        }
    </script>
</body>
</html>
"""

# Custom JSON Body Parser to avoid standard library dependency complexities
def parse_json_body(req_handler):
    content_length = int(req_handler.headers.get('Content-Length', 0))
    if content_length == 0:
        return {}
    body = req_handler.rfile.read(content_length)
    try:
        return json.loads(body.decode('utf-8'))
    except Exception:
        return {}

# Extremely robust, pure-python HTTP server that handles SSE streaming beautifully
from http.server import BaseHTTPRequestHandler, HTTPServer
import socketserver

class ChatServerHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, format, *args):
        # Suppress automatic request logging to keep the server terminal clean
        pass

    def handle(self):
        # Overridden to catch ConnectionResetError and socket disconnect exceptions gracefully
        try:
            super().handle()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, socket.error):
            # Suppress noisy/unavoidable tracebacks on rapid browser refresh or tab closing
            pass
        except Exception:
            pass

    def do_GET(self):
        # Serve main UI
        if self.path == '/' or self.path == '/index.html':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Cache-Control', 'no-cache')
            html_bytes = WEB_HTML.encode('utf-8')
            self.send_header('Content-Length', str(len(html_bytes)))
            self.end_headers()
            try:
                self.wfile.write(html_bytes)
            except Exception:
                pass
            return

        # Serve SSE stream
        elif self.path == '/api/events':
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'keep-alive')
            self.end_headers()

            q = SimpleQueue()
            with sse_lock:
                sse_clients.append(q)

            # Send initial state immediately
            initial_history = f"event: message\ndata: {json.dumps(chat_history)}\n\n"
            current_users = [active_users[uid]["username"] for uid in active_users if time.time() - active_users[uid]["last_seen"] < 25]
            if host_config["username"] not in current_users:
                current_users.append(host_config["username"])
            initial_users = f"event: online_list\ndata: {json.dumps(current_users)}\n\n"

            try:
                self.wfile.write(initial_history.encode('utf-8'))
                self.wfile.write(initial_users.encode('utf-8'))
                self.wfile.flush()
            except Exception:
                with sse_lock:
                    if q in sse_clients: sse_clients.remove(q)
                return

            while True:
                payload = q.get(timeout=10)
                if payload is None:
                    # Keepalive ping
                    payload = ": ping\n\n"
                try:
                    self.wfile.write(payload.encode('utf-8'))
                    self.wfile.flush()
                except Exception:
                    with sse_lock:
                        if q in sse_clients: sse_clients.remove(q)
                    break
            return

        elif self.path == '/api/online':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            current_users = [active_users[uid]["username"] for uid in active_users if time.time() - active_users[uid]["last_seen"] < 25]
            if host_config["username"] not in current_users:
                current_users.append(host_config["username"])
            try:
                self.wfile.write(json.dumps(current_users).encode('utf-8'))
            except Exception:
                pass
            return

        else:
            self.send_error(404)

    def do_POST(self):
        global message_counter
        
        if self.path == '/api/register':
            data = parse_json_body(self)
            uid = data.get('user_id')
            uname = data.get('username')
            if uid and uname:
                active_users[uid] = {
                    "username": uname,
                    "last_seen": time.time()
                }
                # Broadcast updated online list
                current_users = [active_users[k]["username"] for k in active_users if time.time() - active_users[k]["last_seen"] < 25]
                if host_config["username"] not in current_users:
                    current_users.append(host_config["username"])
                broadcast_to_web("online_list", current_users)
                
            self.send_response(204)
            self.end_headers()
            return

        elif self.path == '/api/message':
            data = parse_json_body(self)
            text = data.get('text', '').strip()
            uname = data.get('username', 'Anonymous')
            uid = data.get('user_id', 'unknown')

            if text:
                # Add to chat history
                message_counter += 1
                msg_id = message_counter
                msg_obj = {
                    "id": msg_id,
                    "user_id": uid,
                    "username": uname,
                    "text": text,
                    "reply_to_id": data.get('reply_to_id'),
                    "reply_quote": data.get('reply_quote'),
                    "timestamp": time.strftime("%H:%M:%S"),
                    "edited": False
                }
                chat_history.append(msg_obj)
                # Cap memory at 100 entries
                if len(chat_history) > 100:
                    chat_history.pop(0)

                # Push to all Web SSE clients
                broadcast_to_web("message", chat_history)

                # Push up to Terminal client through event queue
                terminal_event_queue.put_nowait({"type": "msg", "data": msg_obj})

            self.send_response(204)
            self.end_headers()
            return

        elif self.path == '/api/edit':
            data = parse_json_body(self)
            msg_id = data.get('id')
            new_text = data.get('text', '').strip()
            if msg_id and new_text:
                for m in chat_history:
                    if m["id"] == msg_id:
                        m["text"] = new_text
                        m["edited"] = True
                        break
                broadcast_to_web("message", chat_history)
                terminal_event_queue.put_nowait({"type": "edit", "id": msg_id, "text": new_text})

            self.send_response(204)
            self.end_headers()
            return

        else:
            self.send_error(404)

# Multi-threading HTTP Server to keep requests async and isolated
class ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True

def start_http_server():
    server = ThreadedHTTPServer(('0.0.0.0', PORT), ChatServerHandler)
    server.serve_forever()

# Terminal Graphics Engine (Pure ANSI Control)
# We will draw a fixed-layout split CLI terminal:
# Rows 1 - 2: Server Info, IP, Commands helper
# Rows 3 - Height-2: Chat message stream (Scroll region!)
# Row Height-1: Divider line
# Row Height: Active User Input line (> Input...)

terminal_event_queue = SimpleQueue()
terminal_input_buffer = []
term_width, term_height = shutil.get_terminal_size()
input_cursor_index = 0

# Colors & Glowing ANSI tokens
C_GREEN = "\033[1;32m"
C_AMBER = "\033[1;33m"
C_CYAN = "\033[1;36m"
C_RED = "\033[1;31m"
C_RESET = "\033[0m"
C_DIM = "\033[2m"
C_HIDE_CURSOR = "\033[?25l"
C_SHOW_CURSOR = "\033[?25h"

# Global tracking variables for scroll region
term_is_initialized = False
terminal_scroll_offset = 0

# Helper to format and aggregate all printable lines for static redraw
def get_all_printable_lines():
    lines = []
    for msg in chat_history:
        sender = f"{C_CYAN}{msg['username']}{C_RESET}"
        if msg['username'] == host_config['username']:
            sender = f"{C_GREEN}{msg['username']} (You){C_RESET}"
        edited_tag = f" {C_RED}(edited){C_RESET}" if msg.get("edited") else ""
        
        if msg.get('reply_quote'):
            lines.append(f"{C_AMBER}   ↳ {msg['reply_quote']}{C_RESET}")
        lines.append(f"[{msg['timestamp']}] <{sender}>: {msg['text']}{edited_tag}")
    return lines

def draw_messages_static_inner():
    global terminal_scroll_offset
    # Hide the cursor during batch redraw to prevent screen flicker
    sys.stdout.write(C_HIDE_CURSOR)
    all_lines = get_all_printable_lines()
    capacity = term_height - 5  # rows 4 to term_height-2 inclusive
    if capacity < 1:
        sys.stdout.write(C_SHOW_CURSOR)
        return
        
    # Clamp scroll offset safely based on historical messages size
    max_offset = max(0, len(all_lines) - capacity)
    if terminal_scroll_offset > max_offset:
        terminal_scroll_offset = max_offset
    if terminal_scroll_offset < 0:
        terminal_scroll_offset = 0
        
    # Slice the lines based on the active scroll offset
    start_idx = max(0, len(all_lines) - terminal_scroll_offset - capacity)
    end_idx = len(all_lines) - terminal_scroll_offset
    
    visible_lines = all_lines[start_idx:end_idx]
    
    # Draw text lines at absolute rows statically
    for idx, line in enumerate(visible_lines):
        row = 4 + idx
        sys.stdout.write(f"\033[{row};1H\033[K{line}")
        
    # Clear any residual empty scroll rows
    for row in range(4 + len(visible_lines), term_height - 1):
        sys.stdout.write(f"\033[{row};1H\033[K")
        
    sys.stdout.write(C_SHOW_CURSOR)

def init_terminal_display():
    global term_is_initialized, term_width, term_height
    curr_w, curr_h = shutil.get_terminal_size()
    # Guard against invalid, closed, or zero/negative dimensions
    term_width = max(40, curr_w)
    term_height = max(10, curr_h)
    
    with terminal_write_lock:
        # Reset scroll region parameters entirely to prevent shifted double rendering
        sys.stdout.write("\033[r\033[2J\033[H")
        
        # Draw header (Lines 1, 2, 3)
        draw_header_inner()
        
        # Define standard VT100 scroll boundaries for safety
        scroll_bottom = max(4, term_height - 2)
        sys.stdout.write(f"\033[4;{scroll_bottom}r")
        
        # Static-redraw all existing messages in boundaries
        draw_messages_static_inner()
        
        # Draw input divider and baseline
        draw_input_area_base_inner()
        
        term_is_initialized = True
        redraw_input_line_inner()
        sys.stdout.flush()

def draw_header_inner():
    # Write line 1 - Designed with a perfectly sized, aligned "DAMAGE CHAT" logo
    sys.stdout.write("\033[1;1H" + C_GREEN + "█▀▀▄ █▀▀█ █▀▄▀█ █▀▀█ █▀▀▀ █▀▀   █▀▀ █ █ █▀▀█ ▀█▀  " + C_AMBER + "STATUS: ONLINE" + C_RESET + "\033[K")
    # Write line 2
    users_str = ", ".join([active_users[u]["username"] for u in active_users if time.time() - active_users[u]["last_seen"] < 25])
    if not users_str: users_str = "None"
    sys.stdout.write(f"\033[2;1H{C_CYAN}LOCAL IP: http://{LOCAL_IP}:{PORT} | WEB_ACTIVE: [{users_str}]{C_RESET}\033[K")
    # Write line 3 (Header underline) - Guarded line-width limits wrap scrolling
    sys.stdout.write(f"\033[3;1H{C_GREEN}" + "═" * (term_width - 1) + C_RESET + "\033[K")

def draw_header():
    with terminal_write_lock:
        draw_header_inner()
        # Explicitly align the user cursor position back to input
        sys.stdout.write(f"\033[{term_height};{input_cursor_index + 3}H")
        sys.stdout.flush()

def draw_input_area_base_inner():
    # Draw Divider on term_height-1
    divider_char = "─"
    divider_str = divider_char * (term_width - 1)
    
    # Overlay an attractive scrolling status notification if actively scrolled up
    if terminal_scroll_offset > 0:
        indicator = f" [SCROLL HISTORY: UP {terminal_scroll_offset} lines] "
        if len(divider_str) > len(indicator) + 10:
            divider_str = divider_str[:-len(indicator) - 5] + C_AMBER + indicator + C_GREEN + "─────"
            
    sys.stdout.write(f"\033[{term_height-1};1H{C_GREEN}" + divider_str + C_RESET + "\033[K")
    # Draw prompt skeleton on term_height
    sys.stdout.write(f"\033[{term_height};1H{C_GREEN}> {C_RESET}\033[K")

def draw_input_area_base():
    with terminal_write_lock:
        draw_input_area_base_inner()
        sys.stdout.write(f"\033[{term_height};{input_cursor_index + 3}H")
        sys.stdout.flush()

def redraw_input_line_inner():
    # Draw input baseline
    sys.stdout.write(f"\033[{term_height};1H\033[K{C_GREEN}> {C_RESET}" + "".join(terminal_input_buffer))
    # Place cursor accurately at current typing index location
    sys.stdout.write(f"\033[{term_height};{input_cursor_index + 3}H")

def redraw_input_line():
    with terminal_write_lock:
        redraw_input_line_inner()
        sys.stdout.flush()

def append_msg_to_terminal_scroll(msg):
    global terminal_scroll_offset
    # Reset scroll state when a new message arrives so that the user receives updates actively
    terminal_scroll_offset = 0
    with terminal_write_lock:
        # Re-render feed statically to include new message
        draw_messages_static_inner()
        draw_input_area_base_inner()
        
        # Play local alert beep if enabled
        if host_config["notify"] == "on":
            sys.stdout.write("\a")
            
        # Return typing cursor back safely
        sys.stdout.write(f"\033[{term_height};{input_cursor_index + 3}H")
        sys.stdout.flush()

def refresh_all_messages_after_edit():
    global terminal_scroll_offset
    # Reset scroll offset on edits/redraws
    terminal_scroll_offset = 0
    with terminal_write_lock:
        draw_messages_static_inner()
        draw_input_area_base_inner()
        sys.stdout.write(f"\033[{term_height};{input_cursor_index + 3}H")
        sys.stdout.flush()

# Check window resize loop (highly robust cross-platform checking thread with debouncing)
def monitor_terminal_resize():
    global term_width, term_height
    while True:
        time.sleep(0.5)  # Cooldown check
        try:
            curr_w, curr_h = shutil.get_terminal_size()
        except Exception:
            continue
            
        # Ignore zero/negative invalid sizes
        if curr_w < 40 or curr_h < 10:
            continue
            
        if curr_w != term_width or curr_h != term_height:
            # Debounce: wait to verify the resize process is completely finished
            time.sleep(0.2)
            try:
                next_w, next_h = shutil.get_terminal_size()
            except Exception:
                next_w, next_h = curr_w, curr_h
                
            if next_w == curr_w and next_h == curr_h:
                term_width = curr_w
                term_height = curr_h
                # Safe re-initialization
                init_terminal_display()

# Keyboard Input capture with raw mode handling across UNIX and Windows
def non_blocking_input_reader():
    if os.name == 'nt':
        # Windows Keyboard hook
        while True:
            if msvcrt.kbhit():
                char = msvcrt.getch()
                # Handle special key escapes
                if char in (b'\x00', b'\xe0'):
                    # Arrow keys or functional inputs
                    special = msvcrt.getch()
                    if special == b'K': # Left arrow
                        terminal_event_queue.put_nowait({"type": "key_left"})
                    elif special == b'M': # Right arrow
                        terminal_event_queue.put_nowait({"type": "key_right"})
                    elif special == b'H': # Up arrow
                        terminal_event_queue.put_nowait({"type": "key_up"})
                    elif special == b'P': # Down arrow
                        terminal_event_queue.put_nowait({"type": "key_down"})
                elif char == b'\r' or char == b'\n':
                    terminal_event_queue.put_nowait({"type": "enter"})
                elif char == b'\x08': # Backspace
                    terminal_event_queue.put_nowait({"type": "backspace"})
                elif char == b'\x03': # Ctrl+C
                    terminal_event_queue.put_nowait({"type": "exit"})
                else:
                    try:
                        text_char = char.decode('utf-8')
                        if text_char:  # Avoid inserting empty characters
                            terminal_event_queue.put_nowait({"type": "char", "val": text_char})
                    except Exception:
                        pass
            time.sleep(0.01)
    else:
        # UNIX Keyboard hook using termios
        import termios
        import tty
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(sys.stdin.fileno())
            while True:
                # Check for input availability without blocking
                r, _, _ = select.select([sys.stdin], [], [], 0.1)
                if r:
                    char = sys.stdin.read(1)
                    if char == "":  # EOF reached (stdin detached / background process)
                        time.sleep(0.1)
                        continue
                    if char == '\x1b': # Escape codes
                        # read next chars
                        r, _, _ = select.select([sys.stdin], [], [], 0.05)
                        if r:
                            next1 = sys.stdin.read(1)
                            if next1 == '[':
                                next2 = sys.stdin.read(1)
                                if next2 == 'D': # Left Arrow
                                    terminal_event_queue.put_nowait({"type": "key_left"})
                                elif next2 == 'C': # Right Arrow
                                    terminal_event_queue.put_nowait({"type": "key_right"})
                                elif next2 == 'A': # Up Arrow
                                    terminal_event_queue.put_nowait({"type": "key_up"})
                                elif next2 == 'B': # Down Arrow
                                    terminal_event_queue.put_nowait({"type": "key_down"})
                    elif char == '\n' or char == '\r':
                        terminal_event_queue.put_nowait({"type": "enter"})
                    elif char == '\x7f' or char == '\x08': # Backspace / Delete
                        terminal_event_queue.put_nowait({"type": "backspace"})
                    elif char == '\x03': # Ctrl+C
                        terminal_event_queue.put_nowait({"type": "exit"})
                    else:
                        terminal_event_queue.put_nowait({"type": "char", "val": char})
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

# Handle Terminal Commands gracefully
# Store reply history quotes (maximum of 5)
reply_history = []

def add_reply_to_history(text, author):
    global reply_history
    reply_history.insert(0, {"text": text, "author": author})
    if len(reply_history) > 5:
        reply_history.pop()

def execute_terminal_command(text):
    global message_counter
    parts = text.split(" ")
    cmd = parts[0].lower()

    if cmd == "/name":
        if len(parts) > 1:
            old_name = host_config["username"]
            host_config["username"] = " ".join(parts[1:])
            save_host_config()
            system_msg = f"System: Host changed name from {old_name} to {host_config['username']}"
            # Push system message to chat history
            message_counter += 1
            chat_history.append({
                "id": message_counter,
                "user_id": "host",
                "username": "SYSTEM",
                "text": system_msg,
                "timestamp": time.strftime("%H:%M:%S")
            })
            broadcast_to_web("message", chat_history)
            draw_header()
        else:
            show_system_error_msg("Usage: /name <new_username>")

    elif cmd == "/notify":
        if len(parts) > 1:
            status = parts[1].lower()
            if status in ["on", "off"]:
                host_config["notify"] = status
                save_host_config()
                show_system_info_msg(f"Terminal notification sound set to: {status.upper()}")
            else:
                show_system_error_msg("Usage: /notify <on/off>")
        else:
            # Toggle
            host_config["notify"] = "off" if host_config["notify"] == "on" else "on"
            save_host_config()
            show_system_info_msg(f"Terminal notification sound set to: {host_config['notify'].upper()}")

    elif cmd == "/online":
        current_users = [active_users[uid]["username"] for uid in active_users if time.time() - active_users[uid]["last_seen"] < 25]
        show_system_info_msg(f"Online web clients: {', '.join(current_users) if current_users else 'None'}")

    elif cmd == "/help":
        show_terminal_help()

    elif cmd == "/reply":
        # Interactive reply selector for host
        trigger_terminal_reply_flow()

    elif cmd == "/edit":
        # Interactive edit selector for host
        trigger_terminal_edit_flow()

    elif cmd == "/exit":
        clean_exit()
    else:
        show_system_error_msg(f"Unknown command: {cmd}. Type /help for assistance.")

def trigger_terminal_reply_flow():
    # Find last messages
    if not chat_history:
        show_system_error_msg("No messages in database to reply to!")
        return

    with terminal_write_lock:
        # Clear scroll area
        for r in range(4, term_height-1):
            sys.stdout.write(f"\033[{r};1H\033[K")
        
        sys.stdout.write(f"\033[4;1H{C_AMBER}CHOOSE RECENT MESSAGE TO REPLY TO:{C_RESET}")
        limit = min(5, len(chat_history))
        targets = chat_history[-limit:]
        
        for idx, t in enumerate(targets):
            row = 5 + idx
            sys.stdout.write(f"\033[{row};1H  [{idx + 1}] <{t['username']}>: {t['text'][:40]}")
            
        sys.stdout.write(f"\033[{5 + limit};1HPress (1-5) to select, or any other key to abort.")
        sys.stdout.write(f"\033[{term_height};{input_cursor_index + 3}H")
        sys.stdout.flush()
    
    # Loop waiting for choice key in queue
    choice = None
    start_t = time.time()
    while time.time() - start_t < 15: # 15s timeout
        ev = terminal_event_queue.get()
        if ev["type"] == "char":
            if ev["val"] != "":
                if ev["val"].isdigit():
                    val = int(ev["val"])
                    if 1 <= val <= limit:
                        choice = targets[val - 1]
                        break
                # abort on other letters
                break
            
    if not choice:
        show_system_info_msg("Reply flow aborted.")
        refresh_all_messages_after_edit()
        return

    # Trigger custom reply input
    with terminal_write_lock:
        for r in range(4, term_height-1):
            sys.stdout.write(f"\033[{r};1H\033[K")
        sys.stdout.write(f"\033[4;1HReplying to @{choice['username']}...")
        sys.stdout.write(f"\033[5;1HType message and hit ENTER:")
        sys.stdout.write(f"\033[6;1H> ")
        sys.stdout.flush()
    
    # Dynamic input fetcher for reply message
    reply_buffer = []
    while True:
        ev = terminal_event_queue.get()
        if ev["type"] == "char":
            if ev["val"] != "":
                reply_buffer.append(ev["val"])
                with terminal_write_lock:
                    sys.stdout.write(ev["val"])
                    sys.stdout.flush()
        elif ev["type"] == "backspace":
            if reply_buffer:
                reply_buffer.pop()
                with terminal_write_lock:
                    sys.stdout.write("\b \b")
                    sys.stdout.flush()
        elif ev["type"] == "enter":
            break
            
    reply_text = "".join(reply_buffer).strip()
    if reply_text:
        global message_counter
        message_counter += 1
        quote_str = f"@{choice['username']}: \"{choice['text'][:30]}\""
        
        msg_obj = {
            "id": message_counter,
            "user_id": "host",
            "username": host_config["username"],
            "text": reply_text,
            "reply_to_id": choice["id"],
            "reply_quote": quote_str,
            "timestamp": time.strftime("%H:%M:%S")
        }
        chat_history.append(msg_obj)
        if len(chat_history) > 100: chat_history.pop(0)
        
        # Broadcast to web and append locally
        broadcast_to_web("message", chat_history)
        add_reply_to_history(reply_text, host_config["username"])
        
    refresh_all_messages_after_edit()

def trigger_terminal_edit_flow():
    my_msgs = [m for m in chat_history if m["user_id"] == "host"]
    if not my_msgs:
        show_system_error_msg("You haven't sent any messages in this session yet!")
        return
        
    with terminal_write_lock:
        # Clear scroll area
        for r in range(4, term_height-1):
            sys.stdout.write(f"\033[{r};1H\033[K")
        
        sys.stdout.write(f"\033[4;1H{C_AMBER}CHOOSE MESSAGE TO EDIT:{C_RESET}")
        limit = min(5, len(my_msgs))
        targets = my_msgs[-limit:]
        
        for idx, t in enumerate(targets):
            row = 5 + idx
            sys.stdout.write(f"\033[{row};1H  [{idx + 1}] {t['text'][:50]}")
            
        sys.stdout.write(f"\033[{5 + limit};1HPress (1-5) to select, or any other key to abort.")
        sys.stdout.write(f"\033[{term_height};{input_cursor_index + 3}H")
        sys.stdout.flush()
    
    choice = None
    start_t = time.time()
    while time.time() - start_t < 15:
        ev = terminal_event_queue.get()
        if ev["type"] == "char":
            if ev["val"] != "":
                if ev["val"].isdigit():
                    val = int(ev["val"])
                    if 1 <= val <= limit:
                        choice = targets[val - 1]
                        break
                break
            
    if not choice:
        show_system_info_msg("Edit flow aborted.")
        refresh_all_messages_after_edit()
        return

    with terminal_write_lock:
        for r in range(4, term_height-1):
            sys.stdout.write(f"\033[{r};1H\033[K")
        sys.stdout.write(f"\033[4;1HEditing: \"{choice['text']}\"")
        sys.stdout.write(f"\033[5;1HType new text and press ENTER:")
        sys.stdout.write(f"\033[6;1H> ")
        
        edit_buffer = []
        # pre-fill last message content for quick editing
        for char in choice["text"]:
            edit_buffer.append(char)
            sys.stdout.write(char)
        sys.stdout.flush()
    
    while True:
        ev = terminal_event_queue.get()
        if ev["type"] == "char":
            if ev["val"] != "":
                edit_buffer.append(ev["val"])
                with terminal_write_lock:
                    sys.stdout.write(ev["val"])
                    sys.stdout.flush()
        elif ev["type"] == "backspace":
            if edit_buffer:
                edit_buffer.pop()
                with terminal_write_lock:
                    sys.stdout.write("\b \b")
                    sys.stdout.flush()
        elif ev["type"] == "enter":
            break
            
    new_text = "".join(edit_buffer).strip()
    if new_text:
        choice["text"] = new_text
        choice["edited"] = True
        broadcast_to_web("message", chat_history)
        
    refresh_all_messages_after_edit()

def show_system_info_msg(text):
    with terminal_write_lock:
        sys.stdout.write(f"\033[{term_height-2};1H\033[K{C_AMBER}[SYSTEM INFO] {text}{C_RESET}")
        sys.stdout.write(f"\033[{term_height};{input_cursor_index + 3}H")
        sys.stdout.flush()

def show_system_error_msg(text):
    with terminal_write_lock:
        sys.stdout.write(f"\033[{term_height-2};1H\033[K{C_RED}[ERROR] {text}{C_RESET}")
        sys.stdout.write(f"\033[{term_height};{input_cursor_index + 3}H")
        sys.stdout.flush()

def show_terminal_help():
    with terminal_write_lock:
        # Clean scroll viewport first
        for r in range(4, term_height-1):
            sys.stdout.write(f"\033[{r};1H\033[K")
        
        sys.stdout.write(f"\033[4;1H{C_GREEN}AVAILABLE CLI COMMANDS:{C_RESET}")
        sys.stdout.write(f"\033[5;1H  {C_CYAN}/name <username>{C_RESET} - Change terminal username")
        sys.stdout.write(f"\033[6;1H  {C_CYAN}/reply{C_RESET}           - Trigger interactive message reply picker")
        sys.stdout.write(f"\033[7;1H  {C_CYAN}/edit{C_RESET}            - Edit your last sent host messages")
        sys.stdout.write(f"\033[8;1H  {C_CYAN}/notify <on/off>{C_RESET} - Toggle beep alerts for incoming web chat")
        sys.stdout.write(f"\033[9;1H  {C_CYAN}/online{C_RESET}          - Display list of connected LAN browser users")
        sys.stdout.write(f"\033[10;1H  {C_CYAN}/exit{C_RESET}            - Instantly safe close server and CLI client")
        
        sys.stdout.write(f"\033[{term_height};{input_cursor_index + 3}H")
        sys.stdout.flush()

def clean_exit():
    with terminal_write_lock:
        # Clear screen and show cursor again safely
        sys.stdout.write("\033[r") # Reset scroll region margins to whole screen
        sys.stdout.write(f"\033[{term_height};1H\n" + C_SHOW_CURSOR + C_GREEN + "Shutting down Terminal Chat Engine. Goodbye!\n" + C_RESET)
        sys.stdout.flush()
    os._exit(0)

# Main orchestrator engine
def run_chat():
    global input_cursor_index, message_counter, terminal_scroll_offset
    
    # Spin background HTTP server
    server_thread = threading.Thread(target=start_http_server, daemon=True)
    server_thread.start()
    
    # Hide terminal cursor while initializing window grid layout
    sys.stdout.write(C_HIDE_CURSOR)
    sys.stdout.flush()
    
    # Launch cross-platform monitor for resize events
    resize_thread = threading.Thread(target=monitor_terminal_resize, daemon=True)
    resize_thread.start()
    
    # Setup interactive keyboard capturing thread
    kb_thread = threading.Thread(target=non_blocking_input_reader, daemon=True)
    kb_thread.start()
    
    time.sleep(0.5) # Let layout settle
    init_terminal_display()
    sys.stdout.write(C_SHOW_CURSOR)
    sys.stdout.flush()
    
    # Loop listening to combined user inputs + incoming server API messages safely
    while True:
        ev = terminal_event_queue.get()
        if not ev: continue
        
        if ev["type"] == "char":
            if ev["val"] != "":  # Avoid silent empty-string mutations
                terminal_input_buffer.insert(input_cursor_index, ev["val"])
                input_cursor_index += 1
                redraw_input_line()
            
        elif ev["type"] == "backspace":
            if input_cursor_index > 0:
                terminal_input_buffer.pop(input_cursor_index - 1)
                input_cursor_index -= 1
                redraw_input_line()
                
        elif ev["type"] == "key_left":
            if input_cursor_index > 0:
                input_cursor_index -= 1
                redraw_input_line()
                
        elif ev["type"] == "key_right":
            if input_cursor_index < len(terminal_input_buffer):
                input_cursor_index += 1
                redraw_input_line()
                
        elif ev["type"] == "key_up":
            # Scroll up the message list
            all_lines = get_all_printable_lines()
            capacity = term_height - 5
            max_offset = max(0, len(all_lines) - capacity)
            if terminal_scroll_offset < max_offset:
                terminal_scroll_offset += 1
                with terminal_write_lock:
                    draw_messages_static_inner()
                    draw_input_area_base_inner()
                    redraw_input_line_inner()
                    sys.stdout.flush()

        elif ev["type"] == "key_down":
            # Scroll down the message list
            if terminal_scroll_offset > 0:
                terminal_scroll_offset -= 1
                with terminal_write_lock:
                    draw_messages_static_inner()
                    draw_input_area_base_inner()
                    redraw_input_line_inner()
                    sys.stdout.flush()

        elif ev["type"] == "enter":
            inp_text = "".join(terminal_input_buffer).strip()
            # Clear input state
            terminal_input_buffer.clear()
            input_cursor_index = 0
            # Reset scroll position when sending a message
            terminal_scroll_offset = 0
            redraw_input_line()
            
            if not inp_text:
                continue
                
            if inp_text.startswith("/"):
                execute_terminal_command(inp_text)
            else:
                # Add regular host message to history
                message_counter += 1
                msg_obj = {
                    "id": message_counter,
                    "user_id": "host",
                    "username": host_config["username"],
                    "text": inp_text,
                    "timestamp": time.strftime("%H:%M:%S")
                }
                chat_history.append(msg_obj)
                if len(chat_history) > 100: chat_history.pop(0)
                
                # Push message dynamically to browsers and update local scroll block
                broadcast_to_web("message", chat_history)
                append_msg_to_terminal_scroll(msg_obj)
                add_reply_to_history(inp_text, host_config["username"])

        elif ev["type"] == "msg":
            # Event triggered by background web client HTTP endpoints
            append_msg_to_terminal_scroll(ev["data"])
            draw_header() # Update header user list if changed
            
        elif ev["type"] == "edit":
            # Event triggered by web client editing messages
            refresh_all_messages_after_edit()

        elif ev["type"] == "exit":
            clean_exit()

if __name__ == '__main__':
    try:
        run_chat()
    except KeyboardInterrupt:
        clean_exit()