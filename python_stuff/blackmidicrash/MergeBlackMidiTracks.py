import os
import tkinter as tk
from tkinter import filedialog, messagebox
import mido
from mido import MidiFile, MidiTrack


def create_black_midi_multiplier():
    # 1. Initialize hidden Tkinter GUI window
    root = tk.Tk()
    root.withdraw()

    # 2. STEP 1: Select the single master tempo file
    messagebox.showinfo(
        "Step 1: Select Tempo Leader",
        "Please select the ONE MIDI file that should dictate the main tempo/BPM.",
    )
    tempo_file_path = filedialog.askopenfilename(
        title="Select Tempo Leader MIDI File",
        filetypes=[("MIDI files", "*.mid *.midi"), ("All files", "*.*")],
    )

    if not tempo_file_path:
        print("File canceled")
        messagebox.showwarning("Canceled", "File canceled")
        return

    # Load the master tempo file to find its native timing resolution (ticks_per_beat)
    try:
        master_input = MidiFile(tempo_file_path)
        master_ticks = master_input.ticks_per_beat
    except Exception as e:
        print(f"Error reading master file: {e}")
        messagebox.showerror("Error", f"Could not read master file: {e}")
        return

    # Set up our master multi-track container using the master file's clock rate
    output_midi = MidiFile(type=1, ticks_per_beat=master_ticks)

    # Global master metadata arrays
    master_metadata = []
    source_tracks_pool = []

    # Harvest tracks from the Master Leader (File 0)
    filename_master = os.path.basename(tempo_file_path)
    for track in master_input.tracks:
        # Extract meta instructions
        for msg in track:
            if msg.is_meta and msg.type in [
                "set_tempo",
                "time_signature",
                "key_signature",
            ]:
                master_metadata.append(msg.copy())

        # Pull notes out
        if any(msg.type in ["note_on", "note_off"] for msg in track):
            # Scale ratio for file 0 is 1.0 since it sets the clock
            source_tracks_pool.append((filename_master, track, 1.0))

    # 3. INTERACTIVE LOOP: Continuously ask if the user wants to add more files
    while True:
        add_more = messagebox.askyesnocancel(
            "Add More Files?",
            "Do you want to add another MIDI file (or a batch of files) to this project?\n\n"
            "Click 'Yes' to select files.\n"
            "Click 'No' when you are done adding files and ready to proceed.\n"
            "Click 'Cancel' to abort entirely.",
        )

        if add_more is None:  # User clicked Cancel or closed the window
            print("File canceled")
            messagebox.showwarning("Canceled", "File canceled")
            return

        if add_more is False:  # User clicked No, break loop and proceed to duplication/save
            break

        # User clicked Yes, open file picker for single or batch selection
        next_file_paths = filedialog.askopenfilenames(
            title="Select MIDI files to add (Choose 1, 2, or a whole batch)",
            filetypes=[("MIDI files", "*.mid *.midi"), ("All files", "*.*")],
        )

        # If they clicked cancel inside the file picker itself, count it as exiting the loop
        if not next_file_paths:
            print("No files picked in this step. Moving forward...")
            continue

        # Ingest and scale whatever files they just selected in this batch
        for path in next_file_paths:
            filename = os.path.basename(path)
            try:
                secondary_midi = MidiFile(path)
                scale_ratio = master_ticks / secondary_midi.ticks_per_beat

                for track in secondary_midi.tracks:
                    if any(
                        msg.type in ["note_on", "note_off"] for msg in track
                    ):
                        source_tracks_pool.append(
                            (filename, track, scale_ratio)
                        )
                print(f"Successfully added and scaled: {filename}")
            except Exception as e:
                print(f"Error reading file {filename}: {e}")

    # 4. Ask the user if they want to double the final track count
    double_option = messagebox.askyesnocancel(
        "Duplication Option",
        "Do you want to double the combined tracks?\n\n"
        "Choosing 'Yes' will clone all tracks to create a dense Black MIDI setup.",
    )

    if double_option is None:
        print("File canceled")
        messagebox.showwarning("Canceled", "File canceled")
        return

    # ---- TRACK 0: Dedicated Global Tempo/Conductor Track ----
    conductor_track = MidiTrack()
    output_midi.tracks.append(conductor_track)
    conductor_track.append(
        mido.MetaMessage("track_name", name="Conductor (Master Tempo)")
    )
    for meta_msg in master_metadata:
        conductor_track.append(meta_msg)

    track_counter = 0

    # ---- LAYER 1: Compile all baseline files with strict timing recalculations ----
    for filename, track, ratio in source_tracks_pool:
        track_counter += 1
        master_track = MidiTrack()
        output_midi.tracks.append(master_track)
        master_track.append(
            mido.MetaMessage(
                "track_name", name=f"{track_counter:02d}_Base_{filename}"
            )
        )

        for msg in track:
            if msg.is_meta and msg.type == "set_tempo":
                continue
            new_msg = msg.copy()
            new_msg.time = int(round(msg.time * ratio))
            master_track.append(new_msg)

    # ---- LAYER 2: Duplicate everything cleanly if user selected 'Yes' ----
    if double_option is True:
        for filename, track, ratio in source_tracks_pool:
            track_counter += 1
            duplicate_track = MidiTrack()
            output_midi.tracks.append(duplicate_track)
            duplicate_track.append(
                mido.MetaMessage(
                    "track_name", name=f"{track_counter:02d}_Clone_{filename}"
                )
            )

            for msg in track:
                if msg.is_meta and msg.type == "set_tempo":
                    continue
                new_msg = msg.copy()
                new_msg.time = int(round(msg.time * ratio))
                duplicate_track.append(new_msg)

    # 5. Save the final synchronized layout
    save_path = filedialog.asksaveasfilename(
        title="Name and Save Your Exported MIDI File",
        defaultextension=".mid",
        filetypes=[("MIDI files", "*.mid"), ("All files", "*.*")],
        initialfile=f"synchronized_black_midi_{track_counter}_tracks.mid",
    )

    if not save_path:
        print("File canceled")
        messagebox.showwarning("Canceled", "File canceled")
        return

    try:
        output_midi.save(save_path)
        messagebox.showinfo(
            "Success",
            f"Successfully compiled and saved {track_counter + 1} tracks perfectly on beat!",
        )
        print(f"Saved to: {save_path}")
    except Exception as e:
        print(f"Error saving file: {e}")
        messagebox.showerror("Error", f"Failed to save file: {e}")


if __name__ == "__main__":
    create_black_midi_multiplier()
