import zmq
import signal
import sys
import os
import time
import threading
import queue
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
        
        # Frame processing tracking
        self.camera_write_times = deque(maxlen=100)
        self.stimulus_render_times = deque(maxlen=100)
        self.stimulus_write_times = deque(maxlen=100)
        
        # Counters
        self.total_camera_frames = 0
        self.total_stimulus_frames = 0
        self.camera_write_warnings = 0
        self.stimulus_render_warnings = 0
        self.dropped_camera_frames = 0
        self.dropped_stimulus_frames = 0
        
        # Resource monitoring
        self.cpu_percent = 0
        self.memory_mb = 0
        self.last_stats_time = time.time()
        
    def record_camera_write(self, duration_ms):
        self.camera_write_times.append(duration_ms)
        self.total_camera_frames += 1
        
        # Warning for slow camera writes
        if duration_ms > 50:  # 50ms threshold
            self.camera_write_warnings += 1
            print(f"[VIDEO WARNING] Slow camera write: {duration_ms:.1f}ms")
    
    def record_stimulus_render(self, duration_ms):
        self.stimulus_render_times.append(duration_ms)
        
        # Warning for slow renders
        if duration_ms > 100:  # 100ms threshold
            self.stimulus_render_warnings += 1
            print(f"[VIDEO WARNING] Slow stimulus render: {duration_ms:.1f}ms")
    
    def record_stimulus_write(self, duration_ms):
        self.stimulus_write_times.append(duration_ms)
        self.total_stimulus_frames += 1
        
        # Warning for slow stimulus writes
        if duration_ms > 50:  # 50ms threshold
            print(f"[VIDEO WARNING] Slow stimulus write: {duration_ms:.1f}ms")
    
    def record_dropped_frame(self, frame_type):
        if frame_type == 'camera':
            self.dropped_camera_frames += 1
        elif frame_type == 'stimulus':
            self.dropped_stimulus_frames += 1
    
    def update_resources(self):
        try:
            self.cpu_percent = self.process.cpu_percent()
            self.memory_mb = self.process.memory_info().rss / 1024 / 1024
        except psutil.NoSuchProcess:
            pass
    
    def get_stats(self):
        current_time = time.time()
        uptime = current_time - self.start_time
        
        if not self.camera_write_times and not self.stimulus_render_times:
            return None
            
        # Calculate rates
        camera_fps = self.total_camera_frames / uptime if uptime > 0 else 0
        stimulus_fps = self.total_stimulus_frames / uptime if uptime > 0 else 0
        
        return {
            'camera_frames': self.total_camera_frames,
            'stimulus_frames': self.total_stimulus_frames,
            'dropped_camera': self.dropped_camera_frames,
            'dropped_stimulus': self.dropped_stimulus_frames,
            'camera_fps': camera_fps,
            'stimulus_fps': stimulus_fps,
            'camera_warnings': self.camera_write_warnings,
            'stimulus_warnings': self.stimulus_render_warnings,
            'avg_camera_write_ms': sum(self.camera_write_times) / len(self.camera_write_times) if self.camera_write_times else 0,
            'avg_stimulus_render_ms': sum(self.stimulus_render_times) / len(self.stimulus_render_times) if self.stimulus_render_times else 0,
            'avg_stimulus_write_ms': sum(self.stimulus_write_times) / len(self.stimulus_write_times) if self.stimulus_write_times else 0,
            'cpu_percent': self.cpu_percent,
            'memory_mb': self.memory_mb,
            'uptime_seconds': uptime
        }
    
    def print_stats(self):
        stats = self.get_stats()
        if stats:
            print(f"[VIDEO] Cam: {stats['camera_frames']} ({stats['camera_fps']:.1f}fps), "
                  f"Stim: {stats['stimulus_frames']} ({stats['stimulus_fps']:.1f}fps), "
                  f"Dropped: {stats['dropped_camera']}+{stats['dropped_stimulus']}, "
                  f"Avg writes: {stats['avg_camera_write_ms']:.1f}+{stats['avg_stimulus_write_ms']:.1f}ms, "
                  f"CPU: {stats['cpu_percent']:.1f}%, "
                  f"Memory: {stats['memory_mb']:.1f}MB")


def resource_monitor_thread(perf_monitor):
    """Background thread to monitor system resources"""
    while running:
        perf_monitor.update_resources()
        time.sleep(1.0)  # Update every second


# Simplified AsyncStimulusRenderer - no longer needed with the new approach
class AsyncStimulusRenderer:
    def __init__(self, renderer: SceneRenderer, video_writer: AsyncVideoWriter, perf_monitor: VideoPerformanceMonitor):
        self.renderer = renderer
        self.video_writer = video_writer
        self.perf_monitor = perf_monitor
        self.render_queue = queue.Queue(maxsize=video_writer.frame_queue.maxsize)
        self.stop_event = threading.Event()
        self.render_thread = threading.Thread(target=self._render_loop, daemon=True)
        self.render_thread.start()
    def _render_loop(self):
        while not self.stop_event.is_set():
            try:
                instructions = self.render_queue.get(timeout=1)
                if instructions is None: break
                
                # Time the render operation
                render_start = time.time()
                stim_img_bgr = self.renderer.render_to_image(instructions)
                render_time_ms = (time.time() - render_start) * 1000
                self.perf_monitor.record_stimulus_render(render_time_ms)
                
                # Time the write operation
                write_start = time.time()
                success = self.video_writer.write_frame(stim_img_bgr)
                write_time_ms = (time.time() - write_start) * 1000
                
                if success:
                    self.perf_monitor.record_stimulus_write(write_time_ms)
                else:
                    self.perf_monitor.record_dropped_frame('stimulus')
                
            except queue.Empty: continue
    def render_async(self, instructions):
        try: self.render_queue.put_nowait(instructions)
        except queue.Full: 
            print("[WARNING] Stimulus render queue is full.")
            self.perf_monitor.record_dropped_frame('stimulus')
    def close(self):
        if self.render_thread.is_alive():
            self.render_queue.put(None); self.stop_event.set(); self.render_thread.join(timeout=10)

def signal_handler(sig, frame):
    global running
    print("Caught signal, shutting down video process...")
    running = False

def main(experiment_name: str, session_timestamp: str):
    global running
    setup_process_logging('video', os.path.join('data', session_timestamp, 'logs'))
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    config = load_config(experiment_name)
    zmq_config = config['zmq_sockets']
    
    session_dir = os.path.join('data', session_timestamp)
    cam_video_path = os.path.join(session_dir, "camera.mp4")
    stim_video_path = os.path.join(session_dir, "stimulus.mp4")

    # --- NEW: Paths for our metadata log files ---
    cam_meta_path = os.path.join(session_dir, "camera_meta.csv")
    stim_meta_path = os.path.join(session_dir, "stimulus_meta.csv")

    # Define our target save FPS. This is the key parameter.
    SAVE_FPS = 30
    save_resolution = (1024, 1024)
    
    camera_writer = AsyncVideoWriter(cam_video_path, config['camera']['width'], config['camera']['height'], fps=SAVE_FPS, scale_wh=save_resolution)
    
    calibration = Calibration('calibrations/calib.h5')
    stimulus_renderer_instance = SceneRenderer(config['camera']['width'], config['camera']['height'])
    stimulus_video_writer = AsyncVideoWriter(stim_video_path, config['camera']['width'], config['camera']['height'], fps=SAVE_FPS, pix_fmt_in='bgr24', scale_wh=save_resolution)
    
    # Initialize performance monitoring
    perf_monitor = VideoPerformanceMonitor()
    
    # Start resource monitoring thread
    resource_thread = threading.Thread(target=resource_monitor_thread, args=(perf_monitor,), daemon=True)
    resource_thread.start()
    
    # No longer need the AsyncStimulusRenderer with the new approach

    context = zmq.Context()
    poller = zmq.Poller()
    
    cam_sub = context.socket(zmq.SUB)
    cam_sub.connect(zmq_config['camera_frames'])
    cam_sub.setsockopt(zmq.SUBSCRIBE, b'')
    # --- CRITICAL CHANGE 1: Use CONFLATE for Camera Stream ---
    # We only care about saving the most recent frame to match our SAVE_FPS rate.
    cam_sub.setsockopt(zmq.CONFLATE, 1)
    poller.register(cam_sub, zmq.POLLIN)

    stim_sub = context.socket(zmq.SUB)
    stim_sub.connect(zmq_config['stimulus_draw'])
    stim_sub.setsockopt(zmq.SUBSCRIBE, b'')
    # --- CRITICAL CHANGE 2: Use CONFLATE for Stimulus Stream ---
    # This is the most important fix. It tells ZMQ to automatically discard
    # old stimulus messages, ensuring we only render the latest one.
    stim_sub.setsockopt(zmq.CONFLATE, 1)
    poller.register(stim_sub, zmq.POLLIN)
    
    print(f"Video process started. Saving videos at {SAVE_FPS} FPS.")
    
    stats_timer = time.time()
    last_camera_save_time = 0
    last_stim_save_time = 0
    save_interval = 1.0 / SAVE_FPS

    # --- NEW: Open log files and write headers ---
    cam_log = open(cam_meta_path, 'w')
    stim_log = open(stim_meta_path, 'w')
    cam_log.write("video_frame_index,frame_number\n")
    stim_log.write("video_frame_index,frame_number\n")
    video_frame_index_cam = 0
    video_frame_index_stim = 0

    try:
        while running:
            # We poll with a short timeout to create our own save loop
            socks = dict(poller.poll(timeout=10))  # Poll for 10ms
            current_time = time.time()

            # Process Camera Frames at SAVE_FPS
            if cam_sub in socks and (current_time - last_camera_save_time > save_interval):
                frame, frame_meta = unpack_frame(cam_sub.recv_multipart(zmq.NOBLOCK))

                # Time the camera write operation
                write_start = time.time()
                success = camera_writer.write_frame(frame)
                write_time_ms = (time.time() - write_start) * 1000
                cam_log.write(f"{video_frame_index_cam},{frame_meta['frame_number']}\n")
                video_frame_index_cam += 1
                
                if success:
                    perf_monitor.record_camera_write(write_time_ms)
                else:
                    perf_monitor.record_dropped_frame('camera')
                
                last_camera_save_time = current_time
                
            # Process Stimulus Frames at SAVE_FPS
            if stim_sub in socks and (current_time - last_stim_save_time > save_interval):
                draw_payload = unpack_msg(stim_sub.recv(zmq.NOBLOCK))
                # Unpack the dictionary to get just the list of instructions
                draw_instructions = draw_payload['instructions'] 
                # Time the render operation
                render_start = time.time()
                stim_img_bgr = stimulus_renderer_instance.render_to_image(draw_instructions)
                render_time_ms = (time.time() - render_start) * 1000
                perf_monitor.record_stimulus_render(render_time_ms)
                
                # Time the write operation
                write_start = time.time()
                success = stimulus_video_writer.write_frame(stim_img_bgr)
                write_time_ms = (time.time() - write_start) * 1000
                stim_log.write(f"{video_frame_index_stim},{draw_payload['frame_number']}\n")
                video_frame_index_stim += 1
                
                if success:
                    perf_monitor.record_stimulus_write(write_time_ms)
                else:
                    perf_monitor.record_dropped_frame('stimulus')
                
                last_stim_save_time = current_time

            # Print stats every 5 seconds
            if time.time() - stats_timer > 5:
                perf_monitor.print_stats()
                stats_timer = time.time()

    finally:
        print("Video process shutting down. Finalizing videos...")
        cam_log.close()
        stim_log.close()    
        # Print final performance summary
        final_stats = perf_monitor.get_stats()
        if final_stats:
            print(f"[VIDEO FINAL] Camera: {final_stats['camera_frames']} frames ({final_stats['camera_fps']:.1f}fps), "
                  f"Stimulus: {final_stats['stimulus_frames']} frames ({final_stats['stimulus_fps']:.1f}fps), "
                  f"Dropped: {final_stats['dropped_camera']}+{final_stats['dropped_stimulus']}, "
                  f"Avg camera write: {final_stats['avg_camera_write_ms']:.1f}ms, "
                  f"Avg stimulus render: {final_stats['avg_stimulus_render_ms']:.1f}ms, "
                  f"Uptime: {final_stats['uptime_seconds']:.1f}s")
        
        camera_writer.close()
        stimulus_video_writer.close()
        cam_sub.close()
        stim_sub.close()
        context.term()