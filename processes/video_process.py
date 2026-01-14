import zmq
import signal
import sys
import os
import time
import threading
import psutil
from collections import deque
from flyclopszero.utils.config_loader import load_config
from flyclopszero.utils.messaging import unpack_msg
from flyclopszero.utils.logging import setup_process_logging
from flyclopszero.utils.video import AsyncVideoWriter

running = True


class VideoWriterPerformanceMonitor:
    def __init__(self):
        self.start_time = time.time()
        self.process = psutil.Process()
        self.cam_frames_received = 0
        self.stim_frames_received = 0
        self.cam_frames_dropped = 0
        self.stim_frames_dropped = 0
        self.cpu_percent = 0
        self.memory_mb = 0

    def update_resources(self):
        try:
            self.cpu_percent = self.process.cpu_percent()
            self.memory_mb = self.process.memory_info().rss / 1024 / 1024
        except psutil.NoSuchProcess:
            pass

    def get_stats(self):
        uptime = time.time() - self.start_time
        cam_fps = self.cam_frames_received / uptime if uptime > 0 else 0
        stim_fps = self.stim_frames_received / uptime if uptime > 0 else 0
        return {
            "cam_fps": cam_fps,
            "stim_fps": stim_fps,
            "cam_total": self.cam_frames_received,
            "stim_total": self.stim_frames_received,
            "cam_dropped": self.cam_frames_dropped,
            "stim_dropped": self.stim_frames_dropped,
            "cpu_percent": self.cpu_percent,
            "memory_mb": self.memory_mb,
            "uptime_seconds": uptime,
        }

    def print_stats(self):
        stats = self.get_stats()
        if stats:
            print(
                f"[VID_WRITER] FPS(Cam|Stim): {stats['cam_fps']:.1f}|{stats['stim_fps']:.1f} | "
                f"Frames(Cam|Stim): {stats['cam_total']}|{stats['stim_total']} | "
                f"Dropped(Cam|Stim): {stats['cam_dropped']}|{stats['stim_dropped']} | "
                f"CPU: {stats['cpu_percent']:.1f}%, RAM: {stats['memory_mb']:.1f}MB"
            )


def resource_monitor_thread(perf_monitor):
    while running:
        perf_monitor.update_resources()
        time.sleep(5.0)


def signal_handler(sig, frame):
    global running
    print("Caught signal, shutting down video writer process...")
    running = False


def main(experiment_name: str, session_timestamp: str):
    global running
    setup_process_logging("video", os.path.join("data", session_timestamp, "logs"))
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    config = load_config(experiment_name)
    zmq_config = config["zmq_sockets"]

    session_dir = os.path.join("data", session_timestamp)
    cam_video_path = os.path.join(session_dir, "camera.mp4")
    stim_video_path = os.path.join(session_dir, "stimulus.mp4")
    cam_meta_path = os.path.join(session_dir, "camera_meta.csv")
    stim_meta_path = os.path.join(session_dir, "stimulus_meta.csv")

    SAVE_FPS = 30
    SAVE_RESOLUTION = (512, 512)

    camera_writer = AsyncVideoWriter(
        cam_video_path,
        SAVE_RESOLUTION[0],
        SAVE_RESOLUTION[1],
        fps=SAVE_FPS,
        input_codec="mjpeg",
    )
    stimulus_writer = AsyncVideoWriter(
        stim_video_path,
        SAVE_RESOLUTION[0],
        SAVE_RESOLUTION[1],
        fps=SAVE_FPS,
        input_codec="mjpeg",
    )

    perf_monitor = VideoWriterPerformanceMonitor()
    resource_thread = threading.Thread(
        target=resource_monitor_thread, args=(perf_monitor,), daemon=True
    )
    resource_thread.start()

    context = zmq.Context()
    poller = zmq.Poller()

    shutdown_sub = context.socket(zmq.SUB)
    shutdown_sub.connect(zmq_config["shutdown_signal"])
    shutdown_sub.setsockopt(zmq.SUBSCRIBE, b"")
    poller.register(shutdown_sub, zmq.POLLIN)
    cam_sub = context.socket(zmq.SUB)
    cam_sub.connect(zmq_config["video_camera"])
    cam_sub.setsockopt(zmq.SUBSCRIBE, b"")
    poller.register(cam_sub, zmq.POLLIN)
    stim_sub = context.socket(zmq.SUB)
    stim_sub.connect(zmq_config["video_stimulus"])
    stim_sub.setsockopt(zmq.SUBSCRIBE, b"")
    poller.register(stim_sub, zmq.POLLIN)

    print("Video writer process started. Subscribed to video streams.")
    stats_timer = time.time()

    with open(cam_meta_path, "w") as cam_log, open(stim_meta_path, "w") as stim_log:
        cam_log.write("video_frame_index,frame_number\n")
        stim_log.write("video_frame_index,frame_number\n")

        try:
            while running:
                socks = dict(poller.poll(timeout=100))

                if shutdown_sub in socks:
                    print("Shutdown signal received.")
                    running = False
                    continue

                if cam_sub in socks:
                    meta_bytes, frame_bytes = cam_sub.recv_multipart()
                    meta = unpack_msg(meta_bytes)
                    perf_monitor.cam_frames_received += 1
                    if not camera_writer.write_frame_bytes(frame_bytes):
                        perf_monitor.cam_frames_dropped += 1
                    else:
                        cam_log.write(
                            f"{camera_writer.frame_count},{meta['frame_number']}\n"
                        )

                if stim_sub in socks:
                    meta_bytes, frame_bytes = stim_sub.recv_multipart()
                    meta = unpack_msg(meta_bytes)
                    perf_monitor.stim_frames_received += 1
                    if not stimulus_writer.write_frame_bytes(frame_bytes):
                        perf_monitor.stim_frames_dropped += 1
                    else:
                        stim_log.write(
                            f"{stimulus_writer.frame_count},{meta['frame_number']}\n"
                        )

                if time.time() - stats_timer > 5:
                    perf_monitor.print_stats()
                    stats_timer = time.time()
        finally:
            print("Video writer shutting down...")
            camera_writer.close()
            stimulus_writer.close()
            context.term()
            final_stats = perf_monitor.get_stats()
            if final_stats:
                print(
                    f"[VID_WRITER FINAL] FPS(Cam|Stim): {final_stats['cam_fps']:.1f}|{final_stats['stim_fps']:.1f} | "
                    f"Frames(Cam|Stim): {final_stats['cam_total']}|{final_stats['stim_total']} | "
                    f"Dropped(Cam|Stim): {final_stats['cam_dropped']}|{final_stats['stim_dropped']} | "
                    f"CPU: {final_stats['cpu_percent']:.1f}%, RAM: {final_stats['memory_mb']:.1f}MB | "
                    f"Uptime: {final_stats['uptime_seconds']:.1f}s"
                )
            sys.exit(0)
