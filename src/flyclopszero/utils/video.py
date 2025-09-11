import subprocess
import threading
import queue
import numpy as np
import sys
import os

class AsyncVideoWriter:
    """
    An asynchronous video writer that uses FFmpeg in a separate thread.
    Can be configured to downscale video to reduce performance load.
    Logs FFmpeg's stderr to a dedicated file.
    """
    def __init__(self, output_file: str, width: int, height: int, fps: int, 
                 pix_fmt_in: str = 'gray', codec: str = 'h264_nvenc',
                 scale_wh: tuple = None, quality: int = 23):
        """
        Initializes the FFmpeg video writer optimized for maximum performance.

        Args:
            output_file (str): Path to the output video file.
            width (int): Width of the INPUT frames.
            height (int): Height of the INPUT frames.
            fps (int): Frames per second for the output video.
            pix_fmt_in (str): Pixel format of input frames (e.g., 'gray', 'bgr24').
            codec (str): FFmpeg video codec to use (e.g., 'h264_nvenc').
            scale_wh (tuple, optional): A (width, height) tuple to resize the video to.
            quality (int, optional): The quality factor (CRF, etc.). Lower is higher quality.
        """
        self.output_file = output_file
        # Larger queue for better buffering (30 seconds at fps)
        self.frame_queue = queue.Queue(maxsize=fps * 30)
        self.stop_event = threading.Event()
        self.ffmpeg_log_file = None
        self.bytes_buffer = b''  # Buffer for batched writes
        self.buffer_size = 0
        self.max_buffer_size = 1024 * 1024 * 4  # 4MB buffer
        
        cmd = [
            'ffmpeg', '-y',
            '-f', 'rawvideo',
            '-pix_fmt', pix_fmt_in,
            '-s', f"{width}x{height}",
            '-r', str(fps),
            '-i', 'pipe:0',
            '-c:v', codec,
            '-preset', 'p1',  # Fastest preset for NVENC
            '-rc', 'cbr',     # Constant bitrate for predictable performance
            '-b:v', '10M',    # 10Mbps bitrate - balance quality/speed
            '-bufsize', '20M', # 2x bitrate buffer
            '-maxrate', '12M', # Slightly higher max rate
            '-cq', str(quality),
            '-loglevel', 'error',  # Only show errors
        ]

        if scale_wh and len(scale_wh) == 2:
            cmd.extend(['-vf', f'scale={scale_wh[0]}:{scale_wh[1]}'])
        
        cmd.append(output_file)
        
        try:
            # --- CORRECTED BLOCK ---
            # Open a dedicated log file for ffmpeg's stderr
            log_path = os.path.splitext(output_file)[0] + "_ffmpeg.log"
            self.ffmpeg_log_file = open(log_path, 'w')
            
            # Redirect stderr to the dedicated file handle, not sys.stderr
            self.process = subprocess.Popen(
                cmd, 
                stdin=subprocess.PIPE, 
                stdout=subprocess.DEVNULL, # Discard stdout
                stderr=self.ffmpeg_log_file,
                bufsize=self.max_buffer_size  # Large buffer for pipe
            )
            # --- END CORRECTED BLOCK ---

            self.writer_thread = threading.Thread(target=self._writer_loop, daemon=True)
            self.writer_thread.start()
            print(f"FFmpeg process started for {os.path.basename(output_file)}. See log at {log_path}")

        except FileNotFoundError:
            print("ERROR: ffmpeg command not found. Please ensure ffmpeg is in your PATH.")
            if self.ffmpeg_log_file: self.ffmpeg_log_file.close()
            sys.exit(1)
        except Exception as e:
            print(f"Error starting ffmpeg for {os.path.basename(output_file)}: {e}")
            if self.ffmpeg_log_file: self.ffmpeg_log_file.close()
            raise

    def _writer_loop(self):
        """Optimized writer loop with batched writes for maximum throughput."""
        frames_to_process = []
        
        while not self.stop_event.is_set():
            try:
                # Try to get multiple frames at once for batch processing
                frame = self.frame_queue.get(timeout=0.01)  # Very short timeout
                if frame is None:
                    break
                    
                frames_to_process.append(frame)
                
                # Batch process frames or when we hit buffer limit
                while len(frames_to_process) > 0 and (
                    len(frames_to_process) >= 10 or  # Process 10 frames at once
                    self.frame_queue.empty() or       # Or if queue is empty
                    self.buffer_size >= self.max_buffer_size  # Or buffer is full
                ):
                    try:
                        # Process a batch of frames
                        batch_data = b''
                        batch_size = min(10, len(frames_to_process))
                        
                        for _ in range(batch_size):
                            if frames_to_process:
                                frame_data = frames_to_process.pop(0)
                                batch_data += frame_data.tobytes()
                        
                        # Write batch to FFmpeg
                        if batch_data:
                            self.process.stdin.write(batch_data)
                            self.process.stdin.flush()  # Ensure data is sent
                            
                    except BrokenPipeError:
                        print(f"ERROR: Broken pipe for {os.path.basename(self.output_file)}. FFmpeg may have crashed.")
                        break
                        
            except queue.Empty:
                # If queue is empty but we have frames to process, process them
                if frames_to_process:
                    continue
                else:
                    continue
        
        # Process any remaining frames
        if frames_to_process:
            try:
                batch_data = b''.join(frame.tobytes() for frame in frames_to_process)
                if batch_data:
                    self.process.stdin.write(batch_data)
                    self.process.stdin.flush()
            except BrokenPipeError:
                pass
        
        if self.process.stdin:
            self.process.stdin.close()

    def write_frame(self, frame: np.ndarray):
        """Add a frame to the writing queue. Non-blocking and optimized."""
        try:
            # Ensure frame is contiguous for fastest memory access
            if not frame.flags['C_CONTIGUOUS']:
                frame = np.ascontiguousarray(frame)
            
            self.frame_queue.put_nowait(frame)
            return True  # Successfully queued
        except queue.Full:
            print(f"[WARNING] Video writer queue for {os.path.basename(self.output_file)} is full. Dropping frame.")
            return False  # Failed to queue

    def close(self):
        """Signals the writer to finish and waits for FFmpeg to exit."""
        print(f"Closing video writer for {os.path.basename(self.output_file)}...")
        
        if self.writer_thread.is_alive():
            # Signal completion and wait for thread to finish
            self.frame_queue.put(None)
            self.stop_event.set()
            self.writer_thread.join(timeout=15)  # Longer timeout for large queues
            
            if self.writer_thread.is_alive():
                print(f"[WARNING] Writer thread for {os.path.basename(self.output_file)} did not finish cleanly")
        
        if self.process and self.process.poll() is None:
            try:
                # Give FFmpeg more time to finish encoding
                self.process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                print(f"FFmpeg for {os.path.basename(self.output_file)} did not exit gracefully. Killing.")
                self.process.kill()
                self.process.wait()  # Wait for kill to complete

        if self.ffmpeg_log_file:
            self.ffmpeg_log_file.close()
        
        print(f"Video {os.path.basename(self.output_file)} writer closed.")