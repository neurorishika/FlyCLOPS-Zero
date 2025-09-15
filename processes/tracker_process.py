import zmq
import time
import signal
import sys
import numpy as np
import psutil
import threading
import cv2
import os
from collections import deque
from flyclopszero.behavior.tracker import FastTracker
from flyclopszero.utils.config_loader import load_config
from flyclopszero.utils.messaging import unpack_frame, pack_msg, pack_frame
from flyclopszero.utils.logging import setup_process_logging

# Global flag for graceful shutdown
running = True


# Performance monitoring variables
class PerformanceMonitor:
    def __init__(self, window_size=100):
        self.frame_times = deque(maxlen=window_size)
        self.processing_times = deque(maxlen=window_size)
        self.last_frame_time = time.time()
        self.frame_count = 0
        self.timeout_drops = 0  # ZMQ timeouts
        self.sequence_drops = 0  # Actual missing frame numbers
        self.last_frame_number = None
        self.start_time = time.time()

    def log_frame_received(self, frame_number=None):
        current_time = time.time()
        if self.frame_count > 0:
            frame_interval = current_time - self.last_frame_time
            self.frame_times.append(frame_interval)
        self.last_frame_time = current_time
        self.frame_count += 1

        # Check for sequence gaps (actual dropped frames)
        if frame_number is not None:
            if self.last_frame_number is not None:
                expected_frame = self.last_frame_number + 1
                if frame_number > expected_frame:
                    dropped_count = frame_number - expected_frame
                    self.sequence_drops += dropped_count
                    print(
                        f"[WARNING] Detected {dropped_count} dropped frames: "
                        f"expected {expected_frame}, got {frame_number}"
                    )
                elif frame_number < expected_frame:
                    print(
                        f"[WARNING] Frame number went backwards: "
                        f"expected {expected_frame}, got {frame_number}"
                    )
            self.last_frame_number = frame_number

    def log_processing_time(self, processing_time):
        self.processing_times.append(processing_time)

    def log_timeout_drop(self):
        """Log a ZMQ timeout (network issue, not necessarily a dropped frame)"""
        self.timeout_drops += 1

    def get_stats(self):
        if not self.frame_times or not self.processing_times:
            return {}

        avg_frame_interval = np.mean(self.frame_times)
        fps = 1.0 / avg_frame_interval if avg_frame_interval > 0 else 0
        avg_processing_time = np.mean(self.processing_times)
        max_processing_time = np.max(self.processing_times)
        processing_load = (
            (avg_processing_time / avg_frame_interval * 100)
            if avg_frame_interval > 0
            else 0
        )

        return {
            "fps": fps,
            "avg_frame_interval_ms": avg_frame_interval * 1000,
            "avg_processing_time_ms": avg_processing_time * 1000,
            "max_processing_time_ms": max_processing_time * 1000,
            "processing_load_percent": processing_load,
            "total_frames": self.frame_count,
            "sequence_drops": self.sequence_drops,  # Real dropped frames
            "timeout_drops": self.timeout_drops,  # Network timeouts
            "uptime_seconds": time.time() - self.start_time,
            "last_frame_number": self.last_frame_number,
        }


def resource_monitor_thread(monitor, interval=5.0):
    """Background thread to log resource usage and performance stats"""
    process = psutil.Process()
    last_cpu_time = process.cpu_times()

    while running:
        try:
            # Get system resources (less frequent CPU sampling for accuracy)
            cpu_percent = process.cpu_percent(interval=0.1)
            memory_info = process.memory_info()
            memory_mb = memory_info.rss / 1024 / 1024

            # Get performance stats
            perf_stats = monitor.get_stats()

            if perf_stats:
                # Only print if there are actual frames processed
                if perf_stats["total_frames"] > 0:
                    # Show both types of drops for better diagnostics
                    drops_info = f"SeqDrops: {perf_stats['sequence_drops']}"
                    if perf_stats["timeout_drops"] > 0:
                        drops_info += f" | Timeouts: {perf_stats['timeout_drops']}"

                    print(
                        f"[PERF] FPS: {perf_stats['fps']:.1f} | "
                        f"Proc: {perf_stats['avg_processing_time_ms']:.1f}ms "
                        f"(max: {perf_stats['max_processing_time_ms']:.1f}ms) | "
                        f"Load: {perf_stats['processing_load_percent']:.1f}% | "
                        f"CPU: {cpu_percent:.1f}% | "
                        f"RAM: {memory_mb:.1f}MB | "
                        f"{drops_info} | "
                        f"Frame: {perf_stats.get('last_frame_number', 'N/A')}"
                    )

            time.sleep(interval)
        except Exception as e:
            print(f"[ERROR] Resource monitor: {e}")
            time.sleep(interval)


def signal_handler(sig, frame):
    global running
    print("Caught signal, shutting down tracker process...")
    running = False


def main(experiment_name: str, session_timestamp: str):
    global running

    # --- SETUP LOGGING ---
    log_dir = os.path.join("data", session_timestamp, "logs")
    setup_process_logging("tracker", log_dir)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    config = load_config(experiment_name)
    tracker_config = config["tracker"]
    camera_config = config["camera"]
    zmq_config = config["zmq_sockets"]

    # Initialize performance monitoring
    perf_monitor = PerformanceMonitor()

    # Start resource monitoring thread
    monitor_thread = threading.Thread(
        target=resource_monitor_thread, args=(perf_monitor,), daemon=True
    )
    monitor_thread.start()

    context = zmq.Context()

    shutdown_sub = context.socket(zmq.SUB)
    shutdown_sub.connect(zmq_config['shutdown_signal'])
    shutdown_sub.setsockopt(zmq.SUBSCRIBE, b'')

    # Subscriber socket for camera frames
    frame_sub = context.socket(zmq.SUB)
    frame_sub.connect(zmq_config["camera_frames"])
    frame_sub.setsockopt(zmq.SUBSCRIBE, b"")
    frame_sub.setsockopt(zmq.CONFLATE, 1)
    # Add timeout to detect frame drops
    frame_sub.setsockopt(zmq.RCVTIMEO, 1000)  # 1 second timeout

    # Publisher socket for tracking estimates
    estimate_pub = context.socket(zmq.PUB)
    estimate_pub.setsockopt(zmq.SNDHWM, 1)
    estimate_pub.setsockopt(zmq.LINGER, 0)
    estimate_pub.bind(zmq_config["tracking_estimates"])


    poller = zmq.Poller()
    poller.register(shutdown_sub, zmq.POLLIN)

    # --- New: Publisher for debug images ---
    debug_pub = None
    if tracker_config.get("debug", False):
        debug_pub = context.socket(zmq.PUB)
        debug_pub.setsockopt(zmq.SNDHWM, 1)
        debug_pub.setsockopt(zmq.LINGER, 0)
        debug_pub.bind(zmq_config["debug_tracker"])
        print(f"Tracker debug publisher bound to {zmq_config['debug_tracker']} (Non-blocking, HWM=1)")

    print(f"Tracker process subscribed to {zmq_config['camera_frames']}")
    print(f"Tracker publisher bound to {zmq_config['tracking_estimates']}")
    print("Performance monitoring enabled - stats will be logged every 5 seconds")

    # Extract only tracker-specific config (exclude debug config)
    tracker_params = {
        k: v
        for k, v in tracker_config.items()
        if k
        not in [
            "debug",
            "debug_frame_skip",
            "debug_jpeg_quality",
            "debug_resize_width",
            "debug_resize_height",
        ]
    }

    tracker = FastTracker(
        width=camera_config["width"], height=camera_config["height"], **tracker_params
    )

    # Debug frame management with enhanced options
    debug_frame_counter = 0
    debug_frame_skip = tracker_config.get(
        "debug_frame_skip", 10
    )  # Send every Nth frame
    jpeg_quality = tracker_config.get(
        "debug_jpeg_quality", 60
    )  # JPEG compression quality
    debug_resize_width = tracker_config.get("debug_resize_width", 512)  # Resize width
    debug_resize_height = tracker_config.get(
        "debug_resize_height", 512
    )  # Resize height

    print(
        f"Debug config: Skip every {debug_frame_skip} frames, "
        f"JPEG quality: {jpeg_quality}%, "
        f"Resize to: {debug_resize_width}x{debug_resize_height}"
    )

    try:
        while running:
            socks = dict(poller.poll(timeout=0))
            if shutdown_sub in socks:
                print("Shutdown signal received, stopping camera.")
                running = False
                continue
            try:
                multipart_message = frame_sub.recv_multipart()
                processing_start = time.time()
                frame, frame_meta = unpack_frame(multipart_message)

                # Log frame received with sequence number for drop detection
                frame_number = frame_meta.get("frame_number")
                perf_monitor.log_frame_received(frame_number)

                # Ask the tracker to generate the debug image if needed
                return_debug_image = debug_pub is not None and (
                    debug_frame_counter % debug_frame_skip == 0
                )
                estimates, debug_image = tracker.process_frame(
                    frame, return_debug_image=return_debug_image
                )

                processing_time = time.time() - processing_start
                perf_monitor.log_processing_time(processing_time)

                # Warn about slow processing
                if processing_time > 0.033:  # >33ms means <30 FPS
                    print(f"[WARNING] Slow processing: {processing_time*1000:.1f}ms")

                if estimates:
                    message_payload = {
                        "timestamp": time.time(),
                        "estimates": estimates,
                        "frame_number": frame_meta["frame_number"],
                        "t_capture": frame_meta["timestamp"], # Forward the original
                        "t_tracking_done": time.time(),     # Add our own
                    }
                    estimate_pub.send(pack_msg(message_payload))

                # --- Optimized debug image publishing with resizing ---
                if debug_image is not None and debug_pub is not None:
                    try:
                        # Create a copy for processing
                        debug_overlay = debug_image.copy()

                        # Resize image for performance (before adding text for better readability)
                        if (
                            debug_overlay.shape[1] != debug_resize_width
                            or debug_overlay.shape[0] != debug_resize_height
                        ):
                            debug_overlay = cv2.resize(
                                debug_overlay,
                                (debug_resize_width, debug_resize_height),
                                interpolation=cv2.INTER_AREA,  # Better for downsampling
                            )

                        # Add frame info overlay (scaled for smaller image)
                        font_scale = 0.5 if debug_resize_width <= 512 else 0.7
                        font_thickness = 1 if debug_resize_width <= 512 else 2
                        fn_text = f"Frame: {frame_meta['frame_number']} (1/{debug_frame_skip})"
                        perf_text = f"Size: {debug_resize_width}x{debug_resize_height} | Q: {jpeg_quality}%"

                        cv2.putText(
                            debug_overlay,
                            fn_text,
                            (10, 25),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            font_scale,
                            (0, 255, 0),
                            font_thickness,
                        )
                        cv2.putText(
                            debug_overlay,
                            perf_text,
                            (10, debug_resize_height - 15),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            font_scale * 0.8,
                            (255, 255, 0),
                            font_thickness,
                        )

                        # Compress to JPEG for efficient transmission
                        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality]
                        success, jpeg_buffer = cv2.imencode(
                            ".jpg", debug_overlay, encode_param
                        )

                        if success:
                            # Send as single-part message (JPEG bytes)
                            debug_pub.send(jpeg_buffer.tobytes())
                        else:
                            print("[WARNING] Failed to encode debug frame as JPEG")

                        # Explicit cleanup to prevent memory leaks
                        del debug_overlay, jpeg_buffer

                    except Exception as e:
                        print(f"[ERROR] Debug image processing failed: {e}")

                debug_frame_counter += 1

            except zmq.Again:
                # Timeout - no frame received (network issue, not necessarily dropped frame)
                perf_monitor.log_timeout_drop()
                print(
                    "[WARNING] Frame timeout - possible network issue or camera pause"
                )
                continue

    except Exception as e:
        print(f"An error occurred in the tracker process: {e}")
        import traceback

        traceback.print_exc()
    finally:
        print("Tracker process shutting down.")
        # Print final performance summary
        final_stats = perf_monitor.get_stats()
        if final_stats:
            print(
                f"[FINAL STATS] Total frames: {final_stats['total_frames']}, "
                f"Sequence drops: {final_stats['sequence_drops']}, "
                f"Timeout drops: {final_stats['timeout_drops']}, "
                f"Avg FPS: {final_stats['fps']:.1f}, "
                f"Avg processing: {final_stats['avg_processing_time_ms']:.1f}ms, "
                f"Last frame #: {final_stats.get('last_frame_number', 'N/A')}, "
                f"Uptime: {final_stats['uptime_seconds']:.1f}s"
            )

        tracker.close()
        estimate_pub.close()
        frame_sub.close()
        shutdown_sub.close()
        if debug_pub:
            debug_pub.close()
        context.term()
        sys.exit(0)


if __name__ == "__main__":
    main()
