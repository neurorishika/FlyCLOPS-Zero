import time
import sys
import os


def tail_f(filepath: str):
    """
    A Python implementation of the 'tail -f' command.
    Monitors a file for new lines and prints them to the console.

    Args:
        filepath (str): The path to the file to monitor.
    """
    print(f"--- Monitoring log file: {os.path.basename(filepath)} ---")
    print("--- Press Ctrl+C in this window to stop monitoring. ---")

    try:
        with open(filepath, "r") as f:
            # Seek to the end of the file
            f.seek(0, 2)
            while True:
                line = f.readline()
                if not line:
                    # No new line, wait a bit and try again
                    time.sleep(0.1)
                    continue
                sys.stdout.write(line)
                sys.stdout.flush()
    except FileNotFoundError:
        print(
            f"\nERROR: Log file not found at '{filepath}'. Waiting for it to be created..."
        )
        # Wait for the file to be created before trying again
        while not os.path.exists(filepath):
            time.sleep(1)
        tail_f(filepath)  # Retry once the file exists
    except KeyboardInterrupt:
        print(f"\n--- Stopped monitoring {os.path.basename(filepath)} ---")
    except Exception as e:
        print(f"\nAn error occurred: {e}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python tail.py <path_to_log_file>")
        sys.exit(1)

    log_file_path = sys.argv[1]
    tail_f(log_file_path)
