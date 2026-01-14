import zmq
import signal
import sys
import os
import time
import threading
import psutil
import cv2
from collections import deque
from flyclopszero.utils.config_loader import load_config
from flyclopszero.utils.calibration_loader import Calibration
from flyclopszero.utils.messaging import unpack_msg, pack_msg
from flyclopszero.projection.artist import Artist
from flyclopszero.utils.logging import setup_process_logging


running = True


# Performance monitoring for artist process
class ArtistPerformanceMonitor:
    def __init__(self, window_size=100):
        self.render_times = deque(maxlen=window_size)
        self.message_times = deque(maxlen=window_size)
        self.last_message_time = time.time()
        self.message_count = 0
        self.timeouts = 0
        self.start_time = time.time()

    def log_message_received(self, render_time):
        current_time = time.time()
        if self.message_count > 0:
            message_interval = current_time - self.last_message_time
            self.message_times.append(message_interval)
        self.render_times.append(render_time)
        self.last_message_time = current_time
        self.message_count += 1

    def log_timeout(self):
        self.timeouts += 1

    def get_stats(self):
        if not self.render_times:
            return {}

        avg_render_time = sum(self.render_times) / len(self.render_times)
        max_render_time = max(self.render_times)

        render_rate = 0
        if self.message_times:
            avg_message_interval = sum(self.message_times) / len(self.message_times)
            render_rate = 1.0 / avg_message_interval if avg_message_interval > 0 else 0

        return {
            "render_rate": render_rate,
            "avg_render_time_ms": avg_render_time * 1000,
            "max_render_time_ms": max_render_time * 1000,
            "total_renders": self.message_count,
            "timeouts": self.timeouts,
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

            if perf_stats and perf_stats["total_renders"] > 0:
                print(
                    f"[ARTIST PERF] Rate: {perf_stats['render_rate']:.1f} Hz | "
                    f"Render: {perf_stats['avg_render_time_ms']:.1f}ms "
                    f"(max: {perf_stats['max_render_time_ms']:.1f}ms) | "
                    f"CPU: {cpu_percent:.1f}% | "
                    f"RAM: {memory_mb:.1f}MB | "
                    f"Renders: {perf_stats['total_renders']} | "
                    f"Timeouts: {perf_stats['timeouts']}"
                )

            time.sleep(interval)
        except Exception as e:
            print(f"[ERROR] Artist resource monitor: {e}")
            time.sleep(interval)


def signal_handler(sig, frame):
    global running
    print("Caught signal, shutting down artist process...")
    running = False


def main(experiment_name: str, session_timestamp: str):
    global running

    # --- SETUP LOGGING ---
    log_dir = os.path.join("data", session_timestamp, "logs")
    setup_process_logging("artist", log_dir)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    config = load_config(experiment_name)
    zmq_config = config["zmq_sockets"]
    camera_config = config["camera"]

    # Load the rig calibration data
    # TODO: Make the calibration file path configurable
    calibration = Calibration("calibrations/calib.h5")

    context = zmq.Context()

    shutdown_sub = context.socket(zmq.SUB)
    shutdown_sub.connect(zmq_config["shutdown_signal"])
    shutdown_sub.setsockopt(zmq.SUBSCRIBE, b"")

    draw_sub = context.socket(zmq.SUB)
    draw_sub.connect(zmq_config["stimulus_draw"])
    draw_sub.setsockopt(zmq.SUBSCRIBE, b"")
    draw_sub.setsockopt(zmq.CONFLATE, 1)  # Always show the latest stimulus
    draw_sub.setsockopt(zmq.RCVTIMEO, 1000)  # 1 second timeout

    stim_frame_pub = context.socket(zmq.PUB)
    stim_frame_pub.setsockopt(zmq.SNDHWM, 1)
    stim_frame_pub.setsockopt(zmq.LINGER, 0)
    stim_frame_pub.bind(zmq_config["video_stimulus"])
    print(f"Stimulus video publisher bound to {zmq_config['video_stimulus']}")

    poller = zmq.Poller()
    poller.register(shutdown_sub, zmq.POLLIN)

    # Initialize performance monitoring
    perf_monitor = ArtistPerformanceMonitor()

    # Start resource monitoring thread
    monitor_thread = threading.Thread(
        target=resource_monitor_thread, args=(perf_monitor,), daemon=True
    )
    monitor_thread.start()

    artist = Artist(config["projector"], config["camera"], calibration)

    log_dir = os.path.join("data", session_timestamp, "logs")
    render_log_path = os.path.join(log_dir, "render_timestamps.csv")
    render_log_file = open(render_log_path, "w")
    render_log_file.write("frame_number,t_render\n")  # Write header

    SAVE_RESOLUTION = (
        camera_config["width"] // camera_config["downscale_factor"],
        camera_config["height"] // camera_config["downscale_factor"],
    )
    JPEG_QUALITY = camera_config["jpeg_quality"]

    print("Artist process started, displaying on projector.")
    print("Performance monitoring enabled - stats will be logged every 5 seconds")

    try:
        while running:
            socks = dict(poller.poll(timeout=0))
            if shutdown_sub in socks:
                print("Shutdown signal received, stopping camera.")
                running = False
                continue

            try:
                draw_bytes = draw_sub.recv()

                # Time the rendering operation
                render_start = time.time()
                draw_payload = unpack_msg(draw_bytes)
                frame_number = draw_payload["frame_number"]
                draw_instructions = draw_payload["instructions"]
                camera_image_bgr = artist.render(draw_instructions)

                # --- NEW: Downscale, compress, and publish the stimulus frame ---
                if camera_image_bgr is not None:
                    try:
                        resized_frame = cv2.resize(
                            camera_image_bgr,
                            SAVE_RESOLUTION,
                            interpolation=cv2.INTER_AREA,
                        )
                        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]
                        _, jpeg_buffer = cv2.imencode(".jpg", resized_frame)

                        stim_frame_payload = {"frame_number": frame_number}
                        stim_frame_pub.send_multipart(
                            [pack_msg(stim_frame_payload), jpeg_buffer.tobytes()]
                        )
                    except Exception as e:
                        print(f"[ERROR] Stimulus video frame processing failed: {e}")

                # --- NEW: Log the render timestamp immediately after display ---
                t_render = time.time()
                render_log_file.write(f"{frame_number},{t_render}\n")

                render_time = time.time() - render_start

                perf_monitor.log_message_received(render_time)

                # Warn about slow rendering
                if render_time > 0.033:  # >33ms means <30 FPS
                    print(f"[WARNING] Slow render: {render_time*1000:.1f}ms")

            except zmq.Again:
                perf_monitor.log_timeout()
                continue  # Timeout, continue waiting

    except (Exception, KeyboardInterrupt) as e:
        if not isinstance(e, KeyboardInterrupt):
            print(f"An error occurred in the artist process: {e}")
            import traceback

            traceback.print_exc()
    finally:
        print("Artist process shutting down.")
        render_log_file.close()
        # Print final performance summary
        final_stats = perf_monitor.get_stats()
        if final_stats:
            print(
                f"[ARTIST FINAL] Total renders: {final_stats['total_renders']}, "
                f"Avg render time: {final_stats['avg_render_time_ms']:.1f}ms, "
                f"Render rate: {final_stats['render_rate']:.1f} Hz, "
                f"Timeouts: {final_stats['timeouts']}, "
                f"Uptime: {final_stats['uptime_seconds']:.1f}s"
            )
        stim_frame_pub.close()
        artist.close()
        draw_sub.close()
        shutdown_sub.close()
        context.term()
        # sys.exit(0) is implicit when main ends


if __name__ == "__main__":
    main()
