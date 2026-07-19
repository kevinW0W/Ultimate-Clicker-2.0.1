import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import os
import sys

def modify_track_events_to_grand_piano(track_data, transpose=0, velocity_boost=0):
    """
    Parses a single track's MIDI bytes and:
    1. Rewrites all Program Change events (0xC0 - 0xCF) to Program 0 (Acoustic Grand Piano).
    2. Transposes Note On/Off/Pressure events by 'transpose' semitones to create massive chords.
    3. Boosts Note On velocities by velocity_boost to increase loudness, avoiding Note Off (velocity 0) events.
    4. Automatically maps MIDI Drum Channel (9) to Piano Channel (0) so all notes play as Grand Piano.
    """
    track_bytes = bytearray(track_data)
    limit = len(track_bytes)
    idx = 0
    running_status = 0
    
    while idx < limit:
        # 1. Decode Delta-Time (Variable Length Quantity)
        while idx < limit:
            b = track_bytes[idx]
            idx += 1
            if b < 0x80:
                break
        
        if idx >= limit:
            break
            
        # 2. Read Status Byte
        status = track_bytes[idx]
        inherited = False
        
        if status >= 0x80:
            idx += 1
            # Only update running status for Channel Voice/Mode messages (0x80 to 0xEF)
            # Meta events (0xFF) and Sysex (0xF0, 0xF7) do NOT set running status.
            if status < 0xF0:
                running_status = status
        else:
            if running_status == 0:
                # Out of sync or malformed event, skip byte to avoid infinite loops
                idx += 1
                continue
            status = running_status
            inherited = True
        
        # Remap Drum Channel (Channel 9 / index 9) to Channel 0 to force Grand Piano play
        if status < 0xF0:
            channel = status & 0x0F
            if channel == 9:
                status = (status & 0xF0) | 0x00
                if not inherited:
                    track_bytes[idx - 1] = status
                running_status = status
        
        status_type = status & 0xF0
        
        # 3. Process Event Types
        if status == 0xFF: # Meta Event
            if idx >= limit: 
                break
            meta_type = track_bytes[idx]
            idx += 1
            # Decode length (VLQ)
            meta_len = 0
            while idx < limit:
                b = track_bytes[idx]
                idx += 1
                meta_len = (meta_len << 7) | (b & 0x7F)
                if b < 0x80:
                    break
            idx = min(limit, idx + meta_len) # Skip event contents safely
            
        elif status in (0xF0, 0xF7): # Sysex Event
            sysex_len = 0
            while idx < limit:
                b = track_bytes[idx]
                idx += 1
                sysex_len = (sysex_len << 7) | (b & 0x7F)
                if b < 0x80:
                    break
            idx = min(limit, idx + sysex_len) # Skip sysex data safely
            
        elif status_type in (0x80, 0x90, 0xA0):
            # Note Off, Note On, Polyphonic Key Pressure
            if idx < limit:
                # Transpose note safely within MIDI standard range [0, 127]
                original_note = track_bytes[idx]
                new_note = max(0, min(127, original_note + transpose))
                track_bytes[idx] = new_note
                
                # Apply velocity/loudness boost on Note On events
                if status_type == 0x90 and idx + 1 < limit:
                    original_velocity = track_bytes[idx+1]
                    # CRITICAL FIX: Only boost if velocity is > 0.
                    # This prevents Note On with velocity 0 (Note Off) from turning into stuck notes!
                    if original_velocity > 0:
                        new_velocity = max(1, min(127, original_velocity + velocity_boost))
                        track_bytes[idx+1] = new_velocity
                
                idx += 2 # Skip note parameters
            
        elif status_type in (0xB0, 0xE0):
            # Control Change, Pitch Bend
            idx += 2
            
        elif status_type == 0xC0:
            # Program Change event (Force to Acoustic Grand Piano)
            if idx < limit:
                track_bytes[idx] = 0x00
                idx += 1
                
        elif status_type == 0xD0:
            # Channel Pressure
            idx += 1
            
        else:
            # Safely step forward if parser hits an anomalous block
            idx += 1
            
    return bytes(track_bytes)

def force_grand_piano(data, transpose=0, velocity_boost=0):
    """
    Scans complete MIDI payload, targets track chunks, sets to Acoustic Grand Piano,
    and applies octave/harmonic transpositions and velocity boosts if requested.
    """
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        if i + 8 <= n and data[i:i+4] == b'MTrk':
            out.extend(b'MTrk')
            length = int.from_bytes(data[i+4:i+8], byteorder='big')
            out.extend(data[i+4:i+8])
            
            # Extract and parse track payload
            track_data = data[i+8:i+8+length]
            modified_track = modify_track_events_to_grand_piano(track_data, transpose, velocity_boost)
            out.extend(modified_track)
            
            i += 8 + length
        else:
            out.append(data[i])
            i += 1
    return bytes(out)

def create_ui():
    window = tk.Tk()
    window.title("Black MIDI Customizer (Grand Piano & Pitch Stacker)")
    window.geometry("500x530")
    window.resizable(False, False)
    
    # Simple UI Styling
    window.configure(bg="#111827") # Premium dark background for Black MIDI theme
    
    # Configure underlying Option Database to style Tkinter combobox listboxes (popups)
    window.option_add('*TCombobox*Listbox.background', '#1f2937')
    window.option_add('*TCombobox*Listbox.foreground', 'white')
    window.option_add('*TCombobox*Listbox.selectBackground', '#10b981')
    window.option_add('*TCombobox*Listbox.selectForeground', 'white')
    window.option_add('*TCombobox*Listbox.font', ('Arial', 10))
    
    style = ttk.Style()
    style.theme_use('clam')
    
    # Explicitly configure Combobox style across all standard and read-only states
    style.configure("TCombobox", 
                    fieldbackground="#1f2937", 
                    background="#374151", 
                    foreground="white",
                    darkcolor="#1f2937",
                    lightcolor="#374151",
                    bordercolor="#111827",
                    arrowcolor="white")
                    
    style.map("TCombobox", 
              fieldbackground=[("readonly", "#1f2937")],
              foreground=[("readonly", "white")],
              background=[("readonly", "#374151")],
              arrowcolor=[("readonly", "white")])
    
    size_var = tk.StringVar(value="500")
    unit_var = tk.StringVar(value="MB")
    density_var = tk.StringVar(value="Octave Stack (Custom Amount)")
    stack_dir_var = tk.StringVar(value="Bright / Stack Up (High Octaves)")
    loudness_var = tk.IntVar(value=0)
    use_manual_mult_var = tk.BooleanVar(value=False)
    manual_mult_var = tk.StringVar(value="500")
    stack_layers_var = tk.IntVar(value=5)
    
    # Title Label
    tk.Label(
        window, 
        text="BLACK MIDI EXPANDER", 
        font=("Courier New", 14, "bold"),
        bg="#111827",
        fg="#10b981"
    ).pack(pady=(15, 5))
    
    # Grid/Frame for parameters to look organized
    params_frame = tk.Frame(window, bg="#111827")
    params_frame.pack(pady=5)
    
    # Row 1: Target File Size
    tk.Label(params_frame, text="Target File Size:", font=("Arial", 10), bg="#111827", fg="#9ca3af").grid(row=0, column=0, sticky="e", padx=5, pady=5)
    size_inputs = tk.Frame(params_frame, bg="#111827")
    size_inputs.grid(row=0, column=1, sticky="w", padx=5, pady=5)
    
    size_entry = tk.Entry(
        size_inputs, 
        textvariable=size_var, 
        width=10, 
        font=("Arial", 11),
        bg="#1f2937",
        fg="white",
        insertbackground="white",
        justify="center",
        bd=1,
        relief="flat"
    )
    size_entry.pack(side=tk.LEFT, padx=(0, 5))
    
    unit_dropdown = ttk.Combobox(
        size_inputs, 
        textvariable=unit_var, 
        values=["MB", "GB"], 
        width=5, 
        state="readonly",
        font=("Arial", 10)
    )
    unit_dropdown.pack(side=tk.LEFT)
    
    # Row 2: Manual Override Checkbox
    override_check = tk.Checkbutton(
        params_frame,
        text="Use Manual Note Multiplier (Copies) instead of File Size Target",
        variable=use_manual_mult_var,
        onvalue=True,
        offvalue=False,
        bg="#111827",
        fg="#e5e7eb",
        selectcolor="#1f2937",
        activebackground="#111827",
        activeforeground="white",
        font=("Arial", 9)
    )
    override_check.grid(row=1, column=0, columnspan=2, pady=5)
    
    # Row 3: Manual Note Multiplier Input
    tk.Label(params_frame, text="Note Multiplier (Copies):", font=("Arial", 10), bg="#111827", fg="#9ca3af").grid(row=2, column=0, sticky="e", padx=5, pady=5)
    manual_entry = tk.Entry(
        params_frame,
        textvariable=manual_mult_var,
        width=15,
        font=("Arial", 11),
        bg="#1f2937",
        fg="white",
        insertbackground="white",
        justify="center",
        bd=1,
        relief="flat"
    )
    manual_entry.grid(row=2, column=1, sticky="w", padx=5, pady=5)
    
    # Density Label
    tk.Label(
        window, 
        text="Note Stacking / Density Mode:", 
        font=("Arial", 10),
        bg="#111827",
        fg="#9ca3af"
    ).pack(pady=(5, 2))
    
    density_dropdown = ttk.Combobox(
        window,
        textvariable=density_var,
        values=[
            "Standard Duplication",
            "Octave Stack (Super Dense)",
            "Octave Stack (Custom Amount)",
            "Chromatic Wall (Extreme Black)"
        ],
        width=30,
        state="readonly",
        font=("Arial", 10)
    )
    density_dropdown.pack(pady=5)

    # Stacking Direction Dropdown Label
    tk.Label(
        window, 
        text="Stacking Direction (For Custom Amount Mode):", 
        font=("Arial", 10),
        bg="#111827",
        fg="#9ca3af"
    ).pack(pady=(5, 2))
    
    direction_dropdown = ttk.Combobox(
        window,
        textvariable=stack_dir_var,
        values=[
            "Bright / Stack Up (High Octaves)",
            "Centered / Both Directions",
            "Deep / Stack Down (Low Octaves)"
        ],
        width=30,
        state="readonly",
        font=("Arial", 10)
    )
    direction_dropdown.pack(pady=5)

    # Custom Stack Layers (Octaves) Slider
    layers_slider = tk.Scale(
        window,
        from_=1,
        to=10,
        orient=tk.HORIZONTAL,
        bg="#111827",
        fg="#10b981",
        troughcolor="#1f2937",
        highlightthickness=0,
        activebackground="#10b981",
        label="Custom Stack Layers (Octaves to Stack)",
        font=("Arial", 9, "bold"),
        variable=stack_layers_var
    )
    layers_slider.pack(fill=tk.X, padx=80, pady=5)
    
    # Loudness / Velocity Boost Slider
    loudness_slider = tk.Scale(
        window,
        from_=0,
        to=127,
        orient=tk.HORIZONTAL,
        bg="#111827",
        fg="#10b981",
        troughcolor="#1f2937",
        highlightthickness=0,
        activebackground="#10b981",
        label="Loudness / Velocity Boost (+0 to +127)",
        font=("Arial", 9, "bold"),
        variable=loudness_var
    )
    loudness_slider.pack(fill=tk.X, padx=80, pady=5)
    
    def run_process():
        try:
            target_bytes = None
            custom_multiplier = None
            
            # 1. Parse custom note multiplier if active
            if use_manual_mult_var.get():
                custom_multiplier = int(manual_mult_var.get())
                if custom_multiplier <= 0:
                    raise ValueError("Multiplier must be positive!")
            else:
                # 2. Otherwise parse standard size configuration
                target_value = float(size_var.get())
                unit = unit_var.get()
                if unit == "MB":
                    target_bytes = int(target_value * 1024 * 1024)
                else:
                    target_bytes = int(target_value * 1024 * 1024 * 1024)
                
                if target_bytes <= 0:
                    raise ValueError("Target size must be positive!")
                
            density = density_var.get()
            stack_dir = stack_dir_var.get()
            velocity_boost = loudness_var.get()
            stack_layers = stack_layers_var.get()
            
            window.destroy()
            execute_valid_crush(target_bytes, density, velocity_boost, custom_multiplier, stack_layers, stack_dir)
        except ValueError as e:
            messagebox.showerror("Error", f"Please enter a valid positive number!\nDetail: {str(e)}")

    # Start button
    tk.Button(
        window, 
        text="SELECT TEMPLATE & STACK NOTES", 
        command=run_process, 
        bg="#10b981", 
        fg="white", 
        font=("Arial", 10, "bold"), 
        padx=20, 
        pady=8,
        activebackground="#34d399",
        activeforeground="black",
        bd=0,
        cursor="hand2"
    ).pack(pady=10)
    
    window.mainloop()

def execute_valid_crush(target_bytes, density_mode, velocity_boost, custom_multiplier=None, stack_layers=5, stack_dir="Bright / Stack Up (High Octaves)"):
    input_path = filedialog.askopenfilename(
        title="Select ONE template MIDI file",
        filetypes=[("MIDI files", "*.mid *.midi")]
    )
    if not input_path:
        return

    output_path = filedialog.asksaveasfilename(
        title="Where do you want to save the final file?",
        defaultextension=".mid",
        filetypes=[("MIDI files", "*.mid")]
    )
    if not output_path:
        return

    try:
        with open(input_path, 'rb') as f:
            file_data = f.read()

        track_start = file_data.find(b'MTrk')
        if track_start == -1:
            messagebox.showerror("Error", "Not a valid standard MIDI file structure.")
            return

        # 1. Grab the clean file header
        header = bytearray(file_data[:track_start])
        
        # Read the original track count from header
        orig_track_count = int.from_bytes(header[10:12], byteorder='big')
        if orig_track_count == 0:
            orig_track_count = 1

        # Force the header to MIDI Format 1 (multi-track simultaneous play)
        header[8:10] = b'\x00\x01'

        # 2. Isolate complete original track chunk
        raw_track_chunk = file_data[track_start:]
        chunk_size = len(raw_track_chunk)
        
        # Determine multiplier (either from target file size or manual override)
        if custom_multiplier is not None:
            estimated_multiplier = custom_multiplier
        else:
            estimated_multiplier = max(1, int(target_bytes / chunk_size))
        
        # 3. Create transpositions to generate massive Black MIDI density
        # Pre-compile pitch variations to swap when appending chunks
        print("\n[BLACK ENGINE] Compiling multi-layer harmonic notes...")
        
        track_variations = []
        if density_mode == "Octave Stack (Custom Amount)":
            offsets = [0]
            
            if stack_dir == "Bright / Stack Up (High Octaves)":
                # Stacks only upwards (+12, +24, +36, etc.)
                step = 12
                while len(offsets) < stack_layers:
                    offsets.append(step)
                    step += 12
            elif stack_dir == "Deep / Stack Down (Low Octaves)":
                # Stacks only downwards (-12, -24, -36, etc.)
                step = -12
                while len(offsets) < stack_layers:
                    offsets.append(step)
                    step -= 12
            else:
                # Centered / Both directions
                step = 12
                while len(offsets) < stack_layers:
                    offsets.append(step)
                    if len(offsets) < stack_layers:
                        offsets.append(-step)
                    step += 12
            
            offsets.sort()
            
            print(f"[BLACK ENGINE] Creating {stack_layers} custom octave layers (Direction: {stack_dir}) offsets: {offsets}")
            for offset in offsets:
                var_chunk = force_grand_piano(raw_track_chunk, transpose=offset, velocity_boost=velocity_boost)
                track_variations.append(var_chunk)
                
        elif density_mode == "Octave Stack (Super Dense)":
            # Stack notes across exactly 5 octaves (including original pitch 0)
            offsets = [-24, -12, 0, 12, 24]
            for offset in offsets:
                var_chunk = force_grand_piano(raw_track_chunk, transpose=offset, velocity_boost=velocity_boost)
                track_variations.append(var_chunk)
                
        elif density_mode == "Chromatic Wall (Extreme Black)":
            # Stack notes across every semitone (including original pitch 0)
            offsets = [-12, -11, -10, -9, -8, -7, -6, -5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
            for offset in offsets:
                var_chunk = force_grand_piano(raw_track_chunk, transpose=offset, velocity_boost=velocity_boost)
                track_variations.append(var_chunk)
        else:
            # Standard exact duplication (preserves original pitch exactly)
            track_variations.append(force_grand_piano(raw_track_chunk, transpose=0, velocity_boost=velocity_boost))

        # Correctly calculate total tracks written (limit to MIDI standard max of 65,535)
        total_tracks = min(65535, orig_track_count * estimated_multiplier)
        header[10:12] = total_tracks.to_bytes(2, byteorder='big')

        print(f"\n[VALID STRUCT ENGINE ACTIVE]")
        print(f"Original tracks: {orig_track_count} | Duplications: {estimated_multiplier} | Declared playable tracks: {total_tracks}")
        print(f"Injecting highly-dense Black MIDI structures...")

        num_variations = len(track_variations)
        with open(output_path, 'wb') as f_out:
            f_out.write(header)
            
            # Loop and append the modified pitch chunks sequentially
            for i in range(estimated_multiplier):
                selected_chunk = track_variations[i % num_variations]
                f_out.write(selected_chunk)
                
                if i % 100 == 0 or i == (estimated_multiplier - 1):
                    # Fallback check if file isn't written yet
                    try:
                        current_size = os.path.getsize(output_path)
                    except FileNotFoundError:
                        current_size = 0
                        
                    if custom_multiplier is not None:
                        percent = min(100.0, ((i + 1) / estimated_multiplier) * 100)
                    else:
                        percent = min(100.0, (current_size / target_bytes) * 100)
                        
                    bar_length = int(percent / 2)
                    progress_bar = "#" * bar_length + "." * (50 - bar_length)
                    sys.stdout.write(f"\rFusing Black Tracks: [{progress_bar}] {percent:.1f}%")
                    sys.stdout.flush()

        print("\n\n[SUCCESS] Black MIDI notes multiplied & merged successfully!")
        final_size_mb = os.path.getsize(output_path) / (1024 * 1024)
        messagebox.showinfo(
            "Success", 
            f"Black MIDI File generated successfully!\n\n"
            f"Final Size: {final_size_mb:.2f} MB\n"
            f"Active Playable Tracks: {total_tracks}\n"
            f"Density Mode: {density_mode}\n"
            f"Stack Layers: {stack_layers if density_mode == 'Octave Stack (Custom Amount)' else 'N/A'}\n"
            f"Loudness Boost Applied: +{velocity_boost}\n"
            f"Instrument: Acoustic Grand Piano"
        )
    except Exception as e:
        messagebox.showerror("Error", f"Something went wrong during generation:\n{str(e)}")

if __name__ == "__main__":
    create_ui()