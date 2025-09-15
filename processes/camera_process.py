import zmq
import time
import signal
import sys
import os
import threading
import psutil
from collections import deque
from flyclopszero.camera.basler import BaslerCamera
from flyclopszero.utils.config_loader import load_config
from flyclopszero.utils.messaging import pack_frame
from flyclopszero.utils.logging import setup_process_logging

# Global flag for graceful shutdown
running = True


# Performance monitoring for camera process
class CameraPerformanceMonitor:
    def __init__(self, window_size=100):
        self.capture_times = deque(maxlen=window_size)
        self.frame_intervals = deque(maxlen=window_size)
        self.last_frame_time = time.time()
        self.frame_count = 0
        self.failed_captures = 0
        self.start_time = time.time()

    def log_frame_captured(self, capture_time):
        current_time = time.time()
        if self.frame_count > 0:
            frame_interval = current_time - self.last_frame_time
            self.frame_intervals.append(frame_interval)
        self.capture_times.append(capture_time)
        self.last_frame_time = current_time
        self.frame_count += 1

    def log_failed_capture(self):
        self.failed_captures += 1

    def get_stats(self):
        if not self.capture_times:
            return {}

        avg_capture_time = sum(self.capture_times) / len(self.capture_times)
        max_capture_time = max(self.capture_times)

        fps = 0
        if self.frame_intervals:
            avg_frame_interval = sum(self.frame_intervals) / len(self.frame_intervals)
            fps = 1.0 / avg_frame_interval if avg_frame_interval > 0 else 0

        return {
            "fps": fps,
            "avg_capture_time_ms": avg_capture_time * 1000,
            "max_capture_time_ms": max_capture_time * 1000,
            "total_frames": self.frame_count,
            "failed_captures": self.failed_captures,
            "uptime_seconds": time.time() - self.start_time,
        }


def resource_monitor_thread(monitor, interval=5.0):
    """Background thread to log resource usage and performance stats"""
    process = psutil.Process()

    while running:
        try:
            # Get system resources
            cpu_percent = process.cpu_percent(interval=0.1)
            memory_info = process.memory_info()
            memory_mb = memory_info.rss / 1024 / 1024

            # Get performance stats
            perf_stats = monitor.get_stats()

            if perf_stats and perf_stats["total_frames"] > 0:
                print(
                    f"[CAMERA PERF] FPS: {perf_stats['fps']:.1f} | "
                    f"Capture: {perf_stats['avg_capture_time_ms']:.1f}ms "
                    f"(max: {perf_stats['max_capture_time_ms']:.1f}ms) | "
                    f"CPU: {cpu_percent:.1f}% | "
                    f"RAM: {memory_mb:.1f}MB | "
                    f"Frames: {perf_stats['total_frames']} | "
                    f"Failed: {perf_stats['failed_captures']}"
                )

            time.sleep(interval)
        except Exception as e:
            print(f"[ERROR] Camera resource monitor: {e}")
            time.sleep(interval)


def signal_handler(sig, frame):
    global running
    print("Caught signal, shutting down camera process...")
    running = False


def main(experiment_name: str, session_timestamp: str):
    """
    The main entry point for the camera process.
    Initializes the camera, and continuously captures and publishes frames.
    """
    global running

    # --- SETUP LOGGING ---
    log_dir = os.path.join("data", session_timestamp, "logs")
    setup_process_logging("camera", log_dir)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Note: We load the 'sample' experiment config here as a default.
    # A more advanced `run_experiment.py` would pass this as an argument.
    config = load_config(experiment_name)
    camera_config = config["camera"]
    zmq_config = config["zmq_sockets"]

    # Setup ZMQ Publisher
    context = zmq.Context()
    
    shutdown_sub = context.socket(zmq.SUB)
    shutdown_sub.connect(zmq_config['shutdown_signal'])
    shutdown_sub.setsockopt(zmq.SUBSCRIBE, b'')

    socket = context.socket(zmq.PUB)
    socket.setsockopt(zmq.SNDHWM, 1) # Set High-Water Mark to 1
    socket.setsockopt(zmq.LINGER, 0)  # Do not block on send
    socket.bind(zmq_config["camera_frames"])
    print(f"Camera publisher bound to {zmq_config['camera_frames']} (Non-blocking, HWM=1)")

    poller = zmq.Poller()
    poller.register(shutdown_sub, zmq.POLLIN)
    print("Shutdown subscriber connected to {}".format(zmq_config['shutdown_signal']))

    # Initialize performance monitoring
    perf_monitor = CameraPerformanceMonitor()

    # Start resource monitoring thread
    monitor_thread = threading.Thread(
        target=resource_monitor_thread, args=(perf_monitor,), daemon=True
    )
    monitor_thread.start()

    print("Performance monitoring enabled - stats will be logged every 5 seconds")

    frame_no = 0

    try:
        # The 'with' statement ensures the camera is properly closed on exit
        with BaslerCamera(**camera_config) as camera:
            while running:

                # --- NEW: Check for shutdown signal first (non-blocking) ---
                socks = dict(poller.poll(timeout=0))
                if shutdown_sub in socks:
                    print("Shutdown signal received, stopping camera.")
                    running = False
                    continue


                # 1. Get Frame (this is a blocking call)
                capture_start = time.time()
                frame = camera.get_array()
                capture_time = time.time() - capture_start

                if frame is None:
                    perf_monitor.log_failed_capture()
                    continue

                perf_monitor.log_frame_captured(capture_time)

                # Warn about slow capture
                if capture_time > 0.020:  # >20ms capture time
                    print(f"[WARNING] Slow capture: {capture_time*1000:.1f}ms")

                # 2. Prepare Message
                metadata = {
                    "timestamp": time.time(),
                    "frame_number": frame_no,
                }

                # 3. Serialize and Send
                # The .copy() is important here to ensure we don't send a buffer
                # that the camera might reclaim. tobytes() implicitly copies.
                multipart_message = pack_frame(frame, metadata)
                socket.send_multipart(multipart_message)

                frame_no += 1

    except Exception as e:
        print(f"An error occurred in the camera process: {e}")
    finally:
        print("Camera process shutting down.")
        # Print final performance summary
        final_stats = perf_monitor.get_stats()
        if final_stats:
            print(
                f"[CAMERA FINAL] Total frames: {final_stats['total_frames']}, "
                f"Failed captures: {final_stats['failed_captures']}, "
                f"Avg FPS: {final_stats['fps']:.1f}, "
                f"Avg capture: {final_stats['avg_capture_time_ms']:.1f}ms, "
                f"Uptime: {final_stats['uptime_seconds']:.1f}s"
            )

        socket.close()
        shutdown_sub.close()
        context.term()
        sys.exit(0)


if __name__ == "__main__":
    main()
