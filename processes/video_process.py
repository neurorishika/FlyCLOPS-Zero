import zmq
import signal
import sys
import os
import time
import threading
import numpy as np
import psutil
from collections import deque
from flyclopszero.utils.config_loader import load_config
from flyclopszero.utils.messaging import unpack_frame, unpack_msg
from flyclopszero.utils.logging import setup_process_logging
from flyclopszero.projection.renderer import SceneRenderer
from flyclopszero.utils.calibration_loader import Calibration
from flyclopszero.utils.video import AsyncVideoWriter

running = True


class VideoPerformanceMonitor:
    def __init__(self):
        self.start_time = time.time()
        self.process = psutil.Process()
        self.camera_write_times = deque(maxlen=100)
        self.stimulus_render_times = deque(maxlen=100)
        self.stimulus_write_times = deque(maxlen=100)
        self.total_camera_frames = 0
        self.total_stimulus_frames = 0
        self.dropped_camera_frames = 0
        self.dropped_stimulus_frames = 0
        self.cpu_percent = 0
        self.memory_mb = 0

    def record_camera_write(self, duration_ms):
        self.camera_write_times.append(duration_ms)
        self.total_camera_frames += 1

    def record_stimulus_render(self, duration_ms):
        self.stimulus_render_times.append(duration_ms)

    def record_stimulus_write(self, duration_ms):
        self.stimulus_write_times.append(duration_ms)
        self.total_stimulus_frames += 1

    def record_dropped_frame(self, frame_type):
        if frame_type == "camera":
            self.dropped_camera_frames += 1
        elif frame_type == "stimulus":
            self.dropped_stimulus_frames += 1

    def update_resources(self):
        try:
            self.cpu_percent = self.process.cpu_percent()
            self.memory_mb = self.process.memory_info().rss / 1024 / 1024
        except psutil.NoSuchProcess:
            pass

    def get_stats(self):
        uptime = time.time() - self.start_time
        if not self.camera_write_times and not self.stimulus_render_times:
            return None
        camera_fps = self.total_camera_frames / uptime if uptime > 0 else 0
        stimulus_fps = self.total_stimulus_frames / uptime if uptime > 0 else 0
        return {
            "camera_frames": self.total_camera_frames,
            "stimulus_frames": self.total_stimulus_frames,
            "dropped_camera": self.dropped_camera_frames,
            "dropped_stimulus": self.dropped_stimulus_frames,
            "camera_fps": camera_fps,
            "stimulus_fps": stimulus_fps,
            "avg_camera_write_ms": (
                np.mean(self.camera_write_times) if self.camera_write_times else 0
            ),
            "avg_stimulus_render_ms": (
                np.mean(self.stimulus_render_times) if self.stimulus_render_times else 0
            ),
            "avg_stimulus_write_ms": (
                np.mean(self.stimulus_write_times) if self.stimulus_write_times else 0
            ),
            "cpu_percent": self.cpu_percent,
            "memory_mb": self.memory_mb,
            "uptime_seconds": uptime,
        }

    def print_stats(self):
        stats = self.get_stats()
        if stats:
            print(
                f"[VIDEO] Cam: {stats['camera_frames']} ({stats['camera_fps']:.1f}fps), "
                f"Stim: {stats['stimulus_frames']} ({stats['stimulus_fps']:.1f}fps), "
                f"Dropped: {stats['dropped_camera']}+{stats['dropped_stimulus']}, "
                f"Render(ms): {stats['avg_stimulus_render_ms']:.1f}, "
                f"CPU: {stats['cpu_percent']:.1f}%, RAM: {stats['memory_mb']:.1f}MB"
            )


def resource_monitor_thread(perf_monitor):
    while running:
        perf_monitor.update_resources()
        time.sleep(1.0)


def signal_handler(sig, frame):
    global running
    print("Caught signal, shutting down video process...")
    running = False


def main(experiment_name: str, session_timestamp: str):
    global running
    setup_process_logging("video", os.path.join("data", session_timestamp, "logs"))
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    config = load_config(experiment_name)
    zmq_config = config["zmq_sockets"]

    session_dir = os.path.join("data", session_timestamp)
    # ... (paths for video and meta files) ...
    cam_video_path = os.path.join(session_dir, "camera.mp4")
    stim_video_path = os.path.join(session_dir, "stimulus.mp4")
    cam_meta_path = os.path.join(session_dir, "camera_meta.csv")
    stim_meta_path = os.path.join(session_dir, "stimulus_meta.csv")

    SAVE_FPS = 30
    save_resolution = (1024, 1024)
    save_interval = 1.0 / SAVE_FPS

    # --- Initialize components ---
    camera_writer = AsyncVideoWriter(
        cam_video_path,
        config["camera"]["width"],
        config["camera"]["height"],
        fps=SAVE_FPS,
        scale_wh=save_resolution,
    )
    calibration = Calibration("calibrations/calib.h5")
    stimulus_renderer_instance = SceneRenderer(
        config["camera"]["width"], config["camera"]["height"]
    )
    stimulus_video_writer = AsyncVideoWriter(
        stim_video_path,
        config["camera"]["width"],
        config["camera"]["height"],
        fps=SAVE_FPS,
        pix_fmt_in="bgr24",
        scale_wh=save_resolution,
    )

    perf_monitor = VideoPerformanceMonitor()
    resource_thread = threading.Thread(
        target=resource_monitor_thread, args=(perf_monitor,), daemon=True
    )
    resource_thread.start()

    # --- ZMQ Setup ---
    context = zmq.Context()
    shutdown_sub = context.socket(zmq.SUB)
    shutdown_sub.connect(zmq_config["shutdown_signal"])
    shutdown_sub.setsockopt(zmq.SUBSCRIBE, b"")
    cam_sub = context.socket(zmq.SUB)
    cam_sub.connect(zmq_config["camera_frames"])
    cam_sub.setsockopt(zmq.SUBSCRIBE, b"")
    cam_sub.setsockopt(zmq.CONFLATE, 1)
    stim_sub = context.socket(zmq.SUB)
    stim_sub.connect(zmq_config["stimulus_draw"])
    stim_sub.setsockopt(zmq.SUBSCRIBE, b"")
    stim_sub.setsockopt(zmq.CONFLATE, 1)

    print(f"Video process started. Saving videos at {SAVE_FPS} FPS.")

    last_save_time = 0
    stats_timer = time.time()
    video_frame_index = 0

    with open(cam_meta_path, "w") as cam_log, open(stim_meta_path, "w") as stim_log:
        cam_log.write("video_frame_index,frame_number\n")
        stim_log.write("video_frame_index,frame_number\n")

        try:
            while running:
                if shutdown_sub.poll(timeout=1):
                    print("Shutdown signal received, stopping video process.")
                    running = False
                    continue

                current_time = time.time()
                if current_time - last_save_time < save_interval:
                    time.sleep(0.001)
                    continue

                last_save_time = current_time

                try:
                    # --- ATOMIC SAMPLING ---
                    cam_msg = cam_sub.recv_multipart(flags=zmq.NOBLOCK)
                    stim_msg = stim_sub.recv(flags=zmq.NOBLOCK)

                    # --- CAMERA PROCESSING & MONITORING ---
                    frame, cam_meta = unpack_frame(cam_msg)
                    write_start = time.time()
                    success = camera_writer.write_frame(frame)
                    perf_monitor.record_camera_write((time.time() - write_start) * 1000)
                    if not success:
                        perf_monitor.record_dropped_frame("camera")
                    cam_log.write(f"{video_frame_index},{cam_meta['frame_number']}\n")

                    # --- STIMULUS PROCESSING & MONITORING ---
                    draw_payload = unpack_msg(stim_msg)

                    render_start = time.time()
                    stim_img_bgr = stimulus_renderer_instance.render_to_image(
                        draw_payload["instructions"]
                    )
                    perf_monitor.record_stimulus_render(
                        (time.time() - render_start) * 1000
                    )

                    write_start = time.time()
                    success = stimulus_video_writer.write_frame(stim_img_bgr)
                    perf_monitor.record_stimulus_write(
                        (time.time() - write_start) * 1000
                    )
                    if not success:
                        perf_monitor.record_dropped_frame("stimulus")
                    stim_log.write(
                        f"{video_frame_index},{draw_payload['frame_number']}\n"
                    )

                    video_frame_index += 1

                except zmq.Again:
                    # This is normal, just means no new message was available on one of the streams at this exact tick
                    pass

                # Print stats every 5 seconds
                if time.time() - stats_timer > 5:
                    perf_monitor.print_stats()
                    stats_timer = time.time()

        finally:
            print("Video process shutting down. Finalizing videos...")
            # ... (Final stats printing and closing sockets as before) ...
            final_stats = perf_monitor.get_stats()
            if final_stats:
                print(
                    f"[VIDEO FINAL] Camera: {final_stats['camera_frames']} frames ({final_stats['camera_fps']:.1f}fps), "
                    f"Stimulus: {final_stats['stimulus_frames']} frames ({final_stats['stimulus_fps']:.1f}fps), "
                    f"Dropped: {final_stats['dropped_camera']}+{final_stats['dropped_stimulus']}, "
                    f"Avg render: {final_stats['avg_stimulus_render_ms']:.1f}ms, Uptime: {final_stats['uptime_seconds']:.1f}s"
                )
            camera_writer.close()
            stimulus_video_writer.close()
            shutdown_sub.close()
            cam_sub.close()
            stim_sub.close()
            context.term()
