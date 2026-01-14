import subprocess
import threading
import queue
import numpy as np
import sys
import os


class AsyncVideoWriter:
    """
    A versatile, asynchronous video writer using FFmpeg.
    Can accept raw numpy frames or pre-compressed JPEG byte streams.
    """

    def __init__(
        self,
        output_file: str,
        width: int,
        height: int,
        fps: int,
        input_codec: str = "rawvideo",
        pix_fmt_in: str = "gray",
        output_codec: str = "h264_nvenc",
        quality: int = 23,
    ):
        """
        Initializes the FFmpeg video writer.

        Args:
            output_file (str): Path to the output video file.
            width (int): Width of the video frames.
            height (int): Height of the video frames.
            fps (int): Frames per second for the output video.
            input_codec (str): FFmpeg input codec. 'rawvideo' for numpy arrays,
                               'mjpeg' for JPEG byte streams.
            pix_fmt_in (str): Pixel format for raw video (e.g., 'gray', 'bgr24').
            output_codec (str): FFmpeg output video codec (e.g., 'h264_nvenc', 'libx264').
            quality (int): Quality factor (CRF/CQ). Lower is better. 23 is a good default.
        """
        self.output_file = output_file
        self.frame_queue = queue.Queue(maxsize=fps * 10)  # 10-second buffer
        self.stop_event = threading.Event()
        self.ffmpeg_log_file = None
        self.frame_count = 0  # Public counter for frames written

        # --- Build FFmpeg Command ---
        cmd = ["ffmpeg", "-y"]

        # Input options
        if input_codec == "mjpeg":
            cmd.extend(["-f", "mjpeg", "-i", "pipe:0"])
        else:  # Default to rawvideo
            cmd.extend(
                [
                    "-f",
                    "rawvideo",
                    "-pix_fmt",
                    pix_fmt_in,
                    "-s",
                    f"{width}x{height}",
                    "-r",
                    str(fps),
                    "-i",
                    "pipe:0",
                ]
            )

        # Output options
        cmd.extend(
            [
                "-c:v",
                output_codec,
                "-preset",
                "p1",
                "-r",
                str(fps),  # Set output framerate
                "-cq",
                str(quality),
                "-loglevel",
                "warning",
                output_file,
            ]
        )

        try:
            log_path = os.path.splitext(output_file)[0] + "_ffmpeg.log"
            self.ffmpeg_log_file = open(log_path, "w")

            self.process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=self.ffmpeg_log_file,
            )
            self.writer_thread = threading.Thread(target=self._writer_loop, daemon=True)
            self.writer_thread.start()
            print(
                f"FFmpeg ({input_codec}->{output_codec}) started for {os.path.basename(output_file)}. Log: {log_path}"
            )

        except Exception as e:
            print(
                f"FATAL: Error starting ffmpeg for {os.path.basename(output_file)}: {e}"
            )
            if self.ffmpeg_log_file:
                self.ffmpeg_log_file.close()
            raise

    def _writer_loop(self):
        """Continuously pulls data from the queue and writes to FFmpeg's stdin."""
        while not self.stop_event.is_set():
            try:
                data = self.frame_queue.get(timeout=1)
                if data is None:
                    break

                self.process.stdin.write(data)
                self.frame_count += 1
            except queue.Empty:
                continue
            except BrokenPipeError:
                print(
                    f"ERROR: Broken pipe for {os.path.basename(self.output_file)}. FFmpeg crashed."
                )
                break

        if self.process.stdin:
            self.process.stdin.close()

    def write_frame(self, frame: np.ndarray) -> bool:
        """Add a numpy frame to the writing queue."""
        if not frame.flags["C_CONTIGUOUS"]:
            frame = np.ascontiguousarray(frame)
        return self._queue_data(frame.tobytes())

    def write_frame_bytes(self, frame_bytes: bytes) -> bool:
        """Add raw frame bytes (e.g., a JPEG) to the writing queue."""
        return self._queue_data(frame_bytes)

    def _queue_data(self, data: bytes) -> bool:
        """Internal method to add data to the queue."""
        try:
            self.frame_queue.put_nowait(data)
            return True
        except queue.Full:
            # This warning is now managed by the performance monitor
            return False

    def close(self):
        """Signals the writer to finish and waits for FFmpeg to exit."""
        print(f"Closing video writer for {os.path.basename(self.output_file)}...")
        if self.writer_thread.is_alive():
            self.frame_queue.put(None)
            self.stop_event.set()
            self.writer_thread.join(timeout=10)

        if self.process and self.process.poll() is None:
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                print(
                    f"FFmpeg for {os.path.basename(self.output_file)} did not exit gracefully. Killing."
                )
                self.process.kill()
                self.process.wait()

        if self.ffmpeg_log_file:
            self.ffmpeg_log_file.close()
        print(
            f"Video {os.path.basename(self.output_file)} writer closed. Total frames: {self.frame_count}"
        )
