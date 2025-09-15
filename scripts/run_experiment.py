import multiprocessing
import time
import sys
import argparse
import os
import datetime
import subprocess
import shutil

os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from processes import (
    camera_process,
    tracker_process,
    experiment_process,
    artist_process,
    logger_process,
    video_process,
)

PROCESS_MAP = {
    "camera": camera_process.main,
    "tracker": tracker_process.main,
    "experiment": experiment_process.main,
    "artist": artist_process.main,
    "logger": logger_process.main,
    "video": video_process.main,
}


def launch_log_viewer(log_filepath: str, window_position: tuple = None):
    command = [
        sys.executable,
        os.path.join(PROJECT_ROOT, "scripts", "tail.py"),
        log_filepath,
    ]
    try:
        if sys.platform == "linux":
            if shutil.which("gnome-terminal"):
                # Calculate window size and position for organized layout
                if window_position:
                    x, y, width, height = window_position
                    subprocess.Popen(
                        [
                            "gnome-terminal",
                            f"--geometry={width}x{height}+{x}+{y}",
                            "--title",
                            f"FlyCLOPS-Zero: {os.path.splitext(os.path.basename(log_filepath))[0].upper()}",
                            "--",
                            "python3",
                        ]
                        + command[1:]
                    )
                else:
                    subprocess.Popen(["gnome-terminal", "--", "python3"] + command[1:])
            elif shutil.which("xterm"):
                if window_position:
                    x, y, width, height = window_position
                    subprocess.Popen(
                        [
                            "xterm",
                            "-geometry",
                            f"{width}x{height}+{x}+{y}",
                            "-title",
                            f"FlyCLOPS-Zero: {os.path.splitext(os.path.basename(log_filepath))[0].upper()}",
                            "-e",
                            "python3",
                        ]
                        + command[1:]
                    )
                else:
                    subprocess.Popen(["xterm", "-e", "python3"] + command[1:])
            else:
                print(f"Warning: Could not find 'gnome-terminal' or 'xterm'.")
        elif sys.platform == "darwin":
            # macOS positioning with AppleScript
            if window_position:
                x, y, width, height = window_position
                script = f"""
                tell application "Terminal"
                    do script "{" ".join(command)}"
                    set bounds of front window to {{{x}, {y}, {x + width * 8}, {y + height * 16}}}
                    set custom title of front window to "FlyCLOPS-Zero: {os.path.splitext(os.path.basename(log_filepath))[0].upper()}"
                end tell
                """
            else:
                script = (
                    f'tell application "Terminal" to do script "{" ".join(command)}"'
                )
            subprocess.Popen(["osascript", "-e", script])
        elif sys.platform == "win32":
            # Windows positioning (limited support)
            subprocess.Popen(["start", "cmd.exe", "/K"] + command, shell=True)
        else:
            print(
                f"Warning: Automatic log viewer not supported on this platform ({sys.platform})."
            )
    except Exception as e:
        print(f"Error launching log viewer for {os.path.basename(log_filepath)}: {e}")


def calculate_window_positions():
    """
    Calculate organized window positions for 6 terminals in a 3x2 grid layout.
    Handles dual monitor setup with secondary (left) + primary (right) configuration.
    Returns a dictionary mapping process names to (x, y, width, height) tuples.
    """
    # Default fallback values
    primary_width, primary_height = 2560, 1440  # Updated for your monitor
    secondary_width = 1280  # Secondary monitor on the left
    primary_x_offset = secondary_width  # Primary starts after secondary

    try:
        if sys.platform == "linux":
            # Try to get display info using xrandr
            import subprocess as sp

            result = sp.run(["xrandr", "--query"], capture_output=True, text=True)
            if result.returncode == 0:
                monitors = {}
                for line in result.stdout.split("\n"):
                    if " connected " in line:
                        parts = line.split()
                        monitor_name = parts[0]
                        is_primary = " primary " in line

                        # Find resolution and position
                        for part in parts:
                            if "x" in part and "+" in part:
                                res_pos = (
                                    part  # e.g., "2560x1440+1280+0" or "1280x800+0+0"
                                )
                                resolution = res_pos.split("+")[0]  # "2560x1440"
                                positions = res_pos.split("+")[1:]  # ["1280", "0"]

                                width, height = map(int, resolution.split("x"))
                                x_offset, y_offset = map(int, positions)

                                monitors[monitor_name] = {
                                    "width": width,
                                    "height": height,
                                    "x_offset": x_offset,
                                    "y_offset": y_offset,
                                    "is_primary": is_primary,
                                }

                                if is_primary:
                                    primary_width, primary_height = width, height
                                    primary_x_offset = x_offset
                                break

                print(f"Detected monitors: {monitors}")

    except Exception as e:
        print(f"Monitor detection failed, using defaults: {e}")

    print(
        f"Using primary monitor: {primary_width}x{primary_height} at offset +{primary_x_offset}"
    )

    # Account for taskbars/panels (leave some margin)
    usable_width = primary_width - 100
    usable_height = primary_height - 150

    # 3 columns, 2 rows
    cols = 3
    rows = 2

    # Calculate individual window dimensions (larger windows for high-res monitor)
    window_width = usable_width // cols  # ~820px per window on 2560px monitor
    window_height = usable_height // rows  # ~645px per window on 1440px monitor

    # Terminal character dimensions (approximate) - larger for readability
    char_width = max(
        80, window_width // 10
    )  # At least 80 chars wide, ~82 chars for 2560px
    char_height = max(
        30, window_height // 20
    )  # At least 30 lines high, ~32 lines for 1440px

    positions = {}
    processes = ["camera", "tracker", "experiment", "artist", "logger", "video"]

    for i, process_name in enumerate(processes):
        row = i // cols
        col = i % cols

        # Calculate position on PRIMARY monitor with proper spacing
        x = primary_x_offset + col * window_width + 50  # Add primary monitor offset
        y = row * window_height + 50

        positions[process_name] = (x, y, char_width, char_height)
        print(
            f"  {process_name}: geometry={char_width}x{char_height}+{x}+{y} (window ~{window_width}x{window_height}px)"
        )

    return positions


def main():
    parser = argparse.ArgumentParser(description="Run a FlyCLOPS-Zero experiment.")
    parser.add_argument(
        "experiment_name",
        type=str,
        help="The name of the experiment directory (e.g., 'sample').",
    )
    parser.add_argument(
        "--no-logs",
        action="store_true",
        help="Do not automatically open live log viewer windows.",
    )
    args = parser.parse_args()

    session_timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    session_dir = os.path.join("data", session_timestamp)
    log_dir = os.path.join(session_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    print(f"Session data will be saved in: {session_dir}")

    experiment_path = os.path.join(PROJECT_ROOT, "experiments", args.experiment_name)
    if not os.path.isdir(experiment_path):
        print(f"Error: Experiment directory not found at '{experiment_path}'")
        sys.exit(1)

    processes_to_run = ["camera", "tracker", "experiment", "artist", "logger", "video"]
    active_processes = {}  # Use a dictionary to easily find the logger
    print(
        f"--- Starting Experiment: {args.experiment_name} (Session: {session_timestamp}) ---"
    )

    try:
        for name in processes_to_run:
            if name not in PROCESS_MAP:
                print(f"Warning: Process '{name}' not found. Skipping.")
                continue

            process = multiprocessing.Process(
                target=PROCESS_MAP[name],
                args=(
                    args.experiment_name,
                    session_timestamp,
                ),
                name=name,
            )
            active_processes[name] = process
            process.start()
            print(f"  -> Started '{name}' process (PID: {process.pid})")

        if not args.no_logs:
            print("\n--- Launching organized log viewers... ---")
            time.sleep(1)

            # Calculate organized window positions
            window_positions = calculate_window_positions()

            for name in processes_to_run:
                log_filepath = os.path.join(log_dir, f"{name}.log")
                position = window_positions.get(name)
                launch_log_viewer(log_filepath, position)
                # Small delay between launches to prevent window manager issues
                time.sleep(0.2)

        print(
            "\n--- All processes running. Press Ctrl+C in this master terminal to stop. ---"
        )

        # Wait for any process to finish (or for interrupt)
        for p in active_processes.values():
            p.join()

    except KeyboardInterrupt:
        print("\n--- Caught KeyboardInterrupt, initiating graceful shutdown... ---")

    finally:
        # --- NEW: Graceful, Prioritized Shutdown Logic ---
        # Send SIGTERM to all processes. Our signal handlers will catch this.
        for name, p in active_processes.items():
            if p.is_alive():
                print(f"  -> Sending shutdown signal to '{name}'...")
                p.terminate()

        # Prioritize waiting for I/O-heavy processes
        for name in ["logger", "video"]:
            p = active_processes.get(name)
            if p and p.is_alive():
                print(f"  -> Waiting for '{name}' to save data (up to 15 mins)...")
                p.join(timeout=15 * 60)
                if not p.is_alive():
                    print(f"  -> '{name}' shut down cleanly.")

        # Clean up any other remaining processes.
        print("  -> Cleaning up remaining processes...")
        for name, p in active_processes.items():
            if p.is_alive():
                print(f"  -> Forcing kill on '{name}'...")
                p.kill()

        print("\n--- Experiment finished. ---")


if __name__ == "__main__":
    multiprocessing.set_start_method("spawn", force=True)
    main()
