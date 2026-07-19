import os
import sys
import time
import threading
import tkinter as tk
from tkinter import ttk
import cv2
import mss
import numpy as np

# Global thread-safe shared system configurations
config = {
    "running": True,
    "monitor_idx": 1,
    "color_mode": "ANSI 256 Color (Fastest)",
    "max_fps_cap": 30, # Hard locked to 30 FPS to ensure stable terminal flushes
    "current_fps": 0.0,
    "invert": False  # Keeps track of the inversion state
}

ASCII_CHARS = np.array(list("@%#*+=-:. "))
CHAR_RANGE = len(ASCII_CHARS)

def get_terminal_size():
    try:
        columns, lines = os.get_terminal_size()
        return columns, lines
    except OSError:
        return 80, 24

def set_cursor_visibility(visible):
    """Low-level kernel command to hide/show terminal prompt cursor to stop flickering."""
    if os.name == 'nt':
        import ctypes
        class CONSOLE_CURSOR_INFO(ctypes.Structure):
            _fields_ = [("dwSize", ctypes.c_uint32), ("bVisible", ctypes.c_bool)]
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        cursor_info = CONSOLE_CURSOR_INFO()
        kernel32.GetConsoleCursorInfo(handle, ctypes.byref(cursor_info))
        cursor_info.bVisible = visible
        kernel32.SetConsoleCursorInfo(handle, ctypes.byref(cursor_info))

def frame_to_ascii_30fps(frame, target_width, target_height, mode):
    """Highly stable, standard NumPy matrix formatter optimized for stable 30 FPS flushes."""
    target_width = max(10, target_width)
    target_height = max(10, target_height)
    
    # Fast native downsampling
    resized = cv2.resize(frame, (target_width, target_height), interpolation=cv2.INTER_AREA)
    
    # INVERSION TRIGGER: Flips the color pixels instantly if selected in the dropdown
    if config["invert"]:
        resized = ~resized
    
    if mode in ["True Color (RGB)", "ANSI 256 Color (Fastest)"]:
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
    else:
        gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
        rgb = None

    char_indices = (gray * (CHAR_RANGE / 256.0)).astype(np.int32)
    chars = ASCII_CHARS[char_indices]
    
    if mode == "ANSI 256 Color (Fastest)":
        r_bin = (rgb[:, :, 0] // 51).astype(np.int32)
        g_bin = (rgb[:, :, 1] // 51).astype(np.int32)
        b_bin = (rgb[:, :, 2] // 51).astype(np.int32)
        ansi_codes = 16 + (r_bin * 36) + (g_bin * 6) + b_bin
        
        ansi_blocks = np.char.add("\033[38;5;", ansi_codes.astype(str))
        ansi_blocks = np.char.add(ansi_blocks, "m")
        ansi_blocks = np.char.add(ansi_blocks, chars)
        return "\n".join("".join(row) for row in ansi_blocks) + "\033[0m"
        
    elif mode == "True Color (RGB)":
        r, g, b = rgb[:,:,0], rgb[:,:,1], rgb[:,:,2]
        ansi_blocks = np.char.add("\033[38;2;", r.astype(str))
        ansi_blocks = np.char.add(ansi_blocks, ";")
        ansi_blocks = np.char.add(ansi_blocks, g.astype(str))
        ansi_blocks = np.char.add(ansi_blocks, ";")
        ansi_blocks = np.char.add(ansi_blocks, b.astype(str))
        ansi_blocks = np.char.add(ansi_blocks, "m")
        ansi_blocks = np.char.add(ansi_blocks, chars)
        return "\n".join("".join(row) for row in ansi_blocks) + "\033[0m"
        
    else: # Matrix Green or Pure Grayscale
        lines = ["".join(row) for row in chars]
        if mode == "Matrix Green":
            return "\033[32m" + "\n".join(lines) + "\033[0m"
        return "\n".join(lines) + "\033[0m"

def stream_worker():
    """Background engine that takes over the terminal window instantly upon launch."""
    if os.name == 'nt':
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 0x0001 | 0x0004)

    # Hide cursor and wipe screen immediately
    set_cursor_visibility(False)
    sys.stdout.write("\033[2J\033[?25l")
    sys.stdout.flush()
    
    last_w, last_h = get_terminal_size()
    prev_frame_cached = None
    fps_smoothing = 0.9
    
    with mss.mss() as sct:
        while config["running"]:
            start_time = time.time()
            
            monitors = sct.monitors
            idx = config["monitor_idx"]
            if idx >= len(monitors):
                idx = 1
                
            try:
                screenshot = sct.grab(monitors[idx])
                frame = np.array(screenshot)
            except Exception:
                time.sleep(0.02)
                continue
            
            # Anti-lag frame diffing check
            motion_check_frame = cv2.resize(frame, (80, 45), interpolation=cv2.INTER_NEAREST)
            if prev_frame_cached is not None:
                frame_delta = cv2.absdiff(motion_check_frame, prev_frame_cached)
                if np.sum(frame_delta) == 0:
                    time.sleep(0.01)
                    continue
            prev_frame_cached = motion_check_frame
                
            term_w, term_h = get_terminal_size()
            max_avail_w = term_w - 2
            max_avail_h = term_h - 3  # Reserve bottom line for telemetry readouts
            
            if term_w != last_w or term_h != last_h:
                sys.stdout.write("\033[2J")
                sys.stdout.flush()
                last_w, last_h = term_w, term_h
            
            screen_w = int(frame.shape[1])
            screen_h = int(frame.shape[0])
            screen_aspect = screen_w / screen_h
            term_char_aspect = 2.0 
            
            width_if_height_limited = int(max_avail_h * screen_aspect * term_char_aspect)
            height_if_width_limited = int((max_avail_w / screen_aspect) / term_char_aspect)
            
            if width_if_height_limited <= max_avail_w:
                target_width = width_if_height_limited
                target_height = max_avail_h
            else:
                target_width = max_avail_w
                target_height = height_if_width_limited

            ascii_art = frame_to_ascii_30fps(
                frame, target_width, target_height, config["color_mode"]
            )
            
            # Overwrite terminal frame smoothly
            meta_string = f"\n\033[1;32m[Terminal Stream] Target: 30 FPS | Current: {config['current_fps']:.1f} FPS | Resolution: {target_width}x{target_height}\033[0m"
            sys.stdout.write("\033[H" + ascii_art + meta_string)
            sys.stdout.flush()
            
            # Target 30 FPS pacing lock
            elapsed = time.time() - start_time
            target_delay = 1.0 / config["max_fps_cap"]
            sleep_time = max(target_delay - elapsed, 0)
            
            if sleep_time == 0:
                sleep_time = 0.002
            time.sleep(sleep_time)
            
            elapsed = time.time() - start_time
            instant_fps = 1.0 / elapsed if elapsed > 0 else 0.0
            config["current_fps"] = (config["current_fps"] * fps_smoothing) + (instant_fps * (1.0 - fps_smoothing))

def setup_gui():
    """Launches the desktop window GUI control dashboard."""
    root = tk.Tk()
    root.title("ASCII Control Dashboard")
    root.geometry("380x180")
    root.attributes("-topmost", True)  # Keeps control panel accessible over windows
    
    with mss.mss() as sct:
        monitor_count = len(sct.monitors) - 1

    ttk.Label(root, text="ASCII Live Screen Controller", font=("Arial", 14, "bold")).pack(pady=10)
    
    # 1. Screen Monitor Dropdown Choice Selector
    frame_mon = ttk.Frame(root)
    frame_mon.pack(fill="x", padx=20, pady=5)
    ttk.Label(frame_mon, text="Select Screen:").pack(side="left")
    mon_var = tk.StringVar(value="Monitor 1")
    mon_choices = [f"Monitor {i}" for i in range(1, monitor_count + 1)]
    mon_dropdown = ttk.Combobox(frame_mon, textvariable=mon_var, values=mon_choices, state="readonly")
    mon_dropdown.pack(side="right", fill="x", expand=True, padx=10)
    
    def on_monitor_change(event):
        config["monitor_idx"] = int(mon_var.get().split(" ")[1])
    mon_dropdown.bind("<<ComboboxSelected>>", on_monitor_change)
    
    # 2. Color Mode Dropdown Menu with Inverted choices
    frame_col = ttk.Frame(root)
    frame_col.pack(fill="x", padx=20, pady=5)
    ttk.Label(frame_col, text="Color Styling Mode:").pack(side="left")
    color_var = tk.StringVar(value="ANSI 256 Color (Fastest)")
    
    color_dropdown = ttk.Combobox(frame_col, textvariable=color_var, values=[
        "ANSI 256 Color (Fastest)", 
        "ANSI 256 Color (Inverted)",
        "True Color (RGB)", 
        "True Color (Inverted)",
        "Matrix Green", 
        "Pure Grayscale",
        "Pure Grayscale (Inverted)"
    ], state="readonly")
    color_dropdown.pack(side="right", fill="x", expand=True, padx=10)
    
    def on_color_change(event):
        selected = color_var.get()
        if "Inverted" in selected:
            config["invert"] = True
            config["color_mode"] = selected.replace(" (Inverted)", "")
            # Edge case handling to align name tags cleanly
            if config["color_mode"] == "ANSI 256 Color":
                config["color_mode"] = "ANSI 256 Color (Fastest)"
        else:
            config["invert"] = False
            config["color_mode"] = selected
            
    color_dropdown.bind("<<ComboboxSelected>>", on_color_change)

    # 3. Dynamic Telemetry Frame Rate Tracker Label
    stats_lbl = ttk.Label(root, text="Active Telemetry - Frame Rate: 0.0 FPS", font=("Arial", 11, "bold"), foreground="#00aa55")
    stats_lbl.pack(pady=10)

    def refresh_telemetry_ui():
        if config["running"]:
            stats_lbl.config(text=f"Active Telemetry - Frame Rate: {config['current_fps']:.1f} FPS")
            root.after(400, refresh_telemetry_ui)

    def on_close():
        config["running"] = False
        root.destroy()
        set_cursor_visibility(True)
        sys.stdout.write("\033[?25h\n\033[0m\033[2J\033[H")
        sys.stdout.flush()
        print("Screen sharing stopped cleanly.")
        os._exit(0)
    
    root.protocol("WM_DELETE_WINDOW", on_close)
    refresh_telemetry_ui()
    root.mainloop()    

if __name__ == "__main__":
    t = threading.Thread(target=stream_worker, daemon=True)
    t.start()
    setup_gui()
