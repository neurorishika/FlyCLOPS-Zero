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
    'video': video_process.main,
}


def launch_log_viewer(log_filepath: str):
    command = [
        sys.executable,
        os.path.join(PROJECT_ROOT, "scripts", "tail.py"),
        log_filepath,
    ]
    try:
        if sys.platform == "linux":
            if shutil.which("gnome-terminal"):
                subprocess.Popen(["gnome-terminal", "--", "python3"] + command[1:])
            elif shutil.which("xterm"):
                subprocess.Popen(["xterm", "-e", "python3"] + command[1:])
            else:
                print(f"Warning: Could not find 'gnome-terminal' or 'xterm'.")
        elif sys.platform == "darwin":
            script = f'tell application "Terminal" to do script "{" ".join(command)}"'
            subprocess.Popen(["osascript", "-e", script])
        elif sys.platform == "win32":
            subprocess.Popen(["start", "cmd.exe", "/K"] + command, shell=True)
        else:
            print(
                f"Warning: Automatic log viewer not supported on this platform ({sys.platform})."
            )
    except Exception as e:
        print(f"Error launching log viewer for {os.path.basename(log_filepath)}: {e}")


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
            print("\n--- Launching live log viewers... ---")
            time.sleep(1)
            for name in processes_to_run:
                log_filepath = os.path.join(log_dir, f"{name}.log")
                launch_log_viewer(log_filepath)

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
        for name in ['logger', 'video']:
            p = active_processes.get(name)
            if p and p.is_alive():
                print(f"  -> Waiting for '{name}' to save data (up to 15 mins)...")
                p.join(timeout=15*60)
                if not p.is_alive(): print(f"  -> '{name}' shut down cleanly.")

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
