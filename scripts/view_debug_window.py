import zmq
import cv2
import numpy as np
import time
from collections import deque
from flyclopszero.utils.config_loader import load_config
from flyclopszero.utils.messaging import unpack_frame


class DisplayPerformanceMonitor:
    def __init__(self, window_size=100):
        self.frame_times = deque(maxlen=window_size)
        self.decode_times = deque(maxlen=window_size)
        self.jpeg_sizes = deque(maxlen=window_size)
        self.last_frame_time = time.time()
        self.frame_count = 0
        self.start_time = time.time()
        self.total_bytes_received = 0

    def log_frame(self, decode_time, jpeg_size):
        current_time = time.time()
        if self.frame_count > 0:
            frame_interval = current_time - self.last_frame_time
            self.frame_times.append(frame_interval)
        self.decode_times.append(decode_time)
        self.jpeg_sizes.append(jpeg_size)
        self.total_bytes_received += jpeg_size
        self.last_frame_time = current_time
        self.frame_count += 1

        # Print stats every 25 frames (more frequent for lower frame rate)
        if self.frame_count % 25 == 0:
            self.print_stats()

    def print_stats(self):
        if not self.frame_times or not self.decode_times:
            return

        avg_frame_interval = np.mean(self.frame_times)
        fps = 1.0 / avg_frame_interval if avg_frame_interval > 0 else 0
        avg_decode_time = np.mean(self.decode_times)
        max_decode_time = np.max(self.decode_times)
        avg_jpeg_size = np.mean(self.jpeg_sizes)
        total_mb = self.total_bytes_received / (1024 * 1024)

        print(
            f"[DISPLAY] FPS: {fps:.1f} | "
            f"Decode: {avg_decode_time*1000:.1f}ms "
            f"(max: {max_decode_time*1000:.1f}ms) | "
            f"JPEG: {avg_jpeg_size/1024:.1f}KB | "
            f"Total: {total_mb:.1f}MB | "
            f"Frames: {self.frame_count}"
        )

    def get_final_stats(self):
        if self.frame_count == 0:
            return {}
        total_time = time.time() - self.start_time
        avg_fps = self.frame_count / total_time if total_time > 0 else 0
        avg_decode = np.mean(self.decode_times) if self.decode_times else 0
        avg_jpeg_size = np.mean(self.jpeg_sizes) if self.jpeg_sizes else 0
        total_mb = self.total_bytes_received / (1024 * 1024)

        return {
            "total_frames": self.frame_count,
            "avg_fps": avg_fps,
            "runtime_s": total_time,
            "avg_decode_ms": avg_decode * 1000,
            "avg_jpeg_kb": avg_jpeg_size / 1024,
            "total_mb": total_mb,
        }


def main():
    """
    Subscribes to a ZMQ topic publishing JPEG-compressed debug frames and displays them
    in an OpenCV window with comprehensive performance monitoring.
    """
    config = load_config("sample")
    address = config["zmq_sockets"]["debug_tracker"]

    context = zmq.Context()
    socket = context.socket(zmq.SUB)
    socket.connect(address)
    socket.setsockopt(zmq.SUBSCRIBE, b"")
    socket.setsockopt(zmq.CONFLATE, 1)
    # Reduce timeout for better responsiveness
    socket.setsockopt(zmq.RCVTIMEO, 500)  # 500ms timeout

    print(f"Debug window listening on {address}...")
    print("Ultra-optimized mode: 512x512 + 1/10 frame rate + 60% JPEG quality")
    print("Performance monitoring enabled - stats printed every 25 frames")
    window_name = "Tracker Debug View (Ultra-Optimized)"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    
    # Initialize with a default size, will be adjusted based on first frame
    cv2.resizeWindow(window_name, 800, 400)  # Default 2:1 aspect ratio

    monitor = DisplayPerformanceMonitor(window_size=50)  # Smaller monitoring window
    consecutive_timeouts = 0
    last_frame = None
    waiting_message_frame = None
    last_frame_time = time.time()  # Track when we last received a frame

    try:
        while True:
            try:
                # Receive JPEG-compressed frame
                decode_start = time.time()
                jpeg_bytes = socket.recv()
                jpeg_size = len(jpeg_bytes)

                # Decode JPEG to numpy array
                image_buffer = np.frombuffer(jpeg_bytes, dtype=np.uint8)
                frame = cv2.imdecode(image_buffer, cv2.IMREAD_COLOR)
                decode_time = time.time() - decode_start

                if frame is None:
                    print("[WARNING] Could not decode JPEG frame")
                    continue

                # Reset timeout counter on successful frame
                consecutive_timeouts = 0
                last_frame = frame.copy()  # Keep a copy of the last good frame
                last_frame_time = time.time()  # Update the time when we received this frame

                # Adjust window size based on frame aspect ratio (only on first frame)
                if monitor.frame_count == 0:
                    height, width = frame.shape[:2]
                    aspect_ratio = width / height
                    # Set window size to maintain aspect ratio with reasonable dimensions
                    if aspect_ratio > 1:  # Landscape
                        window_width = 1000
                        window_height = int(window_width / aspect_ratio)
                    else:  # Portrait or square
                        window_height = 600
                        window_width = int(window_height * aspect_ratio)
                    cv2.resizeWindow(window_name, window_width, window_height)
                    print(f"[INFO] Frame size: {width}x{height}, aspect ratio: {aspect_ratio:.2f}")

                # Log performance metrics
                monitor.log_frame(decode_time, jpeg_size)

                # Warn about slow decoding (adjusted threshold for smaller images)
                if decode_time > 0.015:  # >15ms decode time for 512x512 JPEG
                    print(f"[WARNING] Slow JPEG decode: {decode_time*1000:.1f}ms")

                # Add performance overlay to frame
                perf_text = (
                    f"Decode: {decode_time*1000:.1f}ms | Size: {jpeg_size/1024:.1f}KB"
                )
                cv2.putText(
                    frame,
                    perf_text,
                    (10, frame.shape[0] - 35),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.4,
                    (255, 255, 0),
                    1,
                )

                # Add optimization info overlay
                opt_text = f"Ultra-Opt: 512x512, 1/10 frames, 60% JPEG"
                cv2.putText(
                    frame,
                    opt_text,
                    (10, frame.shape[0] - 15),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.4,
                    (0, 255, 255),
                    1,
                )

                cv2.imshow(window_name, frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

            except zmq.Again:
                consecutive_timeouts += 1
                current_time = time.time()
                time_since_last_frame = current_time - last_frame_time
                
                # Show last frame if we have one, otherwise show black screen
                if last_frame is not None:
                    display_frame = last_frame.copy()
                    
                    # Only add "no new frames" message if more than 5 seconds have passed
                    if time_since_last_frame > 5.0:
                        # Add semi-transparent overlay for the message
                        overlay = display_frame.copy()
                        height, width = display_frame.shape[:2]
                        
                        # Create semi-transparent rectangle for text background
                        cv2.rectangle(overlay, (0, 0), (width, 80), (0, 0, 0), -1)
                        cv2.addWeighted(display_frame, 0.7, overlay, 0.3, 0, display_frame)
                        
                        # Add "No new frames" message
                        text = f"No new frames for {time_since_last_frame:.1f}s - waiting..."
                        font = cv2.FONT_HERSHEY_SIMPLEX
                        font_scale = max(0.6, min(width, height) / 1000)
                        thickness = max(1, int(font_scale * 2))
                        
                        cv2.putText(display_frame, text, (10, 30), 
                                   font, font_scale, (0, 255, 255), thickness)
                        
                        # Add timeout counter
                        timeout_text = f"Timeout count: {consecutive_timeouts}"
                        cv2.putText(display_frame, timeout_text, (10, 60), 
                                   cv2.FONT_HERSHEY_SIMPLEX, 0.5, (100, 100, 100), 1)
                else:
                    # No frame received yet - show black screen with message
                    height, width = 512, 1024  # Default 2:1 aspect ratio
                    display_frame = np.zeros((height, width, 3), dtype=np.uint8)
                    
                    # Add centered "Waiting for first frame..." text
                    text = "Waiting for first frame..."
                    font = cv2.FONT_HERSHEY_SIMPLEX
                    font_scale = max(0.8, min(width, height) / 800)
                    thickness = max(1, int(font_scale * 2))
                    
                    # Get text size for centering
                    text_size = cv2.getTextSize(text, font, font_scale, thickness)[0]
                    text_x = (width - text_size[0]) // 2
                    text_y = (height + text_size[1]) // 2
                    
                    cv2.putText(display_frame, text, (text_x, text_y), 
                               font, font_scale, (0, 255, 255), thickness)
                
                cv2.imshow(window_name, display_frame)
                
                # Print status message less frequently
                if consecutive_timeouts == 1:
                    print("[INFO] No debug frames - tracker may not be in debug mode")
                elif consecutive_timeouts % 20 == 0:  # Every 10 seconds (20 * 500ms)
                    print(f"[INFO] Still waiting for frames... (timeout count: {consecutive_timeouts}, {time_since_last_frame:.1f}s since last frame)")
                
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
                continue

            except Exception as e:
                print(f"[ERROR] Frame processing error: {e}")
                continue

    except KeyboardInterrupt:
        print("\nStopping debug viewer...")
    finally:
        # Print comprehensive final stats
        final_stats = monitor.get_final_stats()
        if final_stats:
            print(f"\n[FINAL STATS]")
            print(f"  Total frames: {final_stats['total_frames']}")
            print(f"  Runtime: {final_stats['runtime_s']:.1f}s")
            print(f"  Average FPS: {final_stats['avg_fps']:.1f}")
            print(f"  Average decode time: {final_stats['avg_decode_ms']:.1f}ms")
            print(f"  Average JPEG size: {final_stats['avg_jpeg_kb']:.1f}KB")
            print(f"  Total data received: {final_stats['total_mb']:.1f}MB")

            # Calculate efficiency metrics
            if final_stats["runtime_s"] > 0:
                mbps = final_stats["total_mb"] / final_stats["runtime_s"]
                print(f"  Data rate: {mbps:.2f}MB/s")

        cv2.destroyAllWindows()
        socket.close()
        context.term()


if __name__ == "__main__":
    main()
