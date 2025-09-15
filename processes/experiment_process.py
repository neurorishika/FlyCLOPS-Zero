import zmq
import time
import signal
import sys
import importlib
import os
import threading
import psutil
from collections import deque
from flyclopszero.utils.logging import setup_process_logging

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from flyclopszero.utils.config_loader import load_config
from flyclopszero.utils.messaging import unpack_msg, pack_msg

running = True


class ExperimentPerformanceMonitor:
    def __init__(self):
        self.start_time = time.time()
        self.process = psutil.Process()

        # Message processing tracking
        self.update_times = deque(maxlen=100)
        self.stimulus_send_times = deque(maxlen=100)
        self.log_send_times = deque(maxlen=100)

        # Counters
        self.total_updates = 0
        self.stimulus_messages_sent = 0
        self.log_messages_sent = 0
        self.timeout_warnings = 0

        # Resource monitoring
        self.cpu_percent = 0
        self.memory_mb = 0
        self.last_stats_time = time.time()

    def record_update_time(self, duration_ms):
        self.update_times.append(duration_ms)
        self.total_updates += 1

        # Warning for slow updates
        if duration_ms > 100:  # 100ms threshold
            self.timeout_warnings += 1
            print(f"[EXPERIMENT WARNING] Slow update: {duration_ms:.1f}ms")

    def record_stimulus_send(self, duration_ms):
        self.stimulus_send_times.append(duration_ms)
        self.stimulus_messages_sent += 1

    def record_log_send(self, duration_ms):
        self.log_send_times.append(duration_ms)
        self.log_messages_sent += 1

    def update_resources(self):
        try:
            self.cpu_percent = self.process.cpu_percent()
            self.memory_mb = self.process.memory_info().rss / 1024 / 1024
        except psutil.NoSuchProcess:
            pass

    def get_stats(self):
        current_time = time.time()
        uptime = current_time - self.start_time

        if not self.update_times:
            return None

        # Calculate rates
        fps = self.total_updates / uptime if uptime > 0 else 0
        stimulus_rate = self.stimulus_messages_sent / uptime if uptime > 0 else 0
        log_rate = self.log_messages_sent / uptime if uptime > 0 else 0

        return {
            "total_updates": self.total_updates,
            "stimulus_messages": self.stimulus_messages_sent,
            "log_messages": self.log_messages_sent,
            "timeout_warnings": self.timeout_warnings,
            "fps": fps,
            "stimulus_rate": stimulus_rate,
            "log_rate": log_rate,
            "avg_update_time_ms": sum(self.update_times) / len(self.update_times),
            "avg_stimulus_send_ms": (
                sum(self.stimulus_send_times) / len(self.stimulus_send_times)
                if self.stimulus_send_times
                else 0
            ),
            "avg_log_send_ms": (
                sum(self.log_send_times) / len(self.log_send_times)
                if self.log_send_times
                else 0
            ),
            "cpu_percent": self.cpu_percent,
            "memory_mb": self.memory_mb,
            "uptime_seconds": uptime,
        }

    def print_stats(self):
        stats = self.get_stats()
        if stats:
            print(
                f"[EXPERIMENT] Updates: {stats['total_updates']}, "
                f"FPS: {stats['fps']:.1f}, "
                f"Stimulus: {stats['stimulus_messages']}, "
                f"Logs: {stats['log_messages']}, "
                f"Avg update: {stats['avg_update_time_ms']:.1f}ms, "
                f"CPU: {stats['cpu_percent']:.1f}%, "
                f"Memory: {stats['memory_mb']:.1f}MB"
            )


def resource_monitor_thread(perf_monitor):
    """Background thread to monitor system resources"""
    while running:
        perf_monitor.update_resources()
        time.sleep(1.0)  # Update every second


def signal_handler(sig, frame):
    global running
    print("Caught signal, shutting down experiment process...")
    running = False


def main(experiment_name: str, session_timestamp: str):
    global running

    # --- SETUP LOGGING ---
    log_dir = os.path.join("data", session_timestamp, "logs")
    setup_process_logging("experiment", log_dir)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    config = load_config(experiment_name)
    zmq_config = config["zmq_sockets"]

    context = zmq.Context()

    estimate_sub = context.socket(zmq.SUB)
    estimate_sub.connect(zmq_config["tracking_estimates"])
    estimate_sub.setsockopt(zmq.SUBSCRIBE, b"")
    estimate_sub.setsockopt(zmq.CONFLATE, 1)

    draw_pub = context.socket(zmq.PUB)
    draw_pub.setsockopt(zmq.SNDHWM, 1)
    draw_pub.setsockopt(zmq.LINGER, 0)
    draw_pub.bind(zmq_config["stimulus_draw"])
    print(
        f"Draw publisher bound to {zmq_config['stimulus_draw']} (Non-blocking, HWM=1)"
    )

    log_pub = context.socket(zmq.PUB)
    log_pub.bind(zmq_config["log_data"])

    shutdown_pub = context.socket(zmq.PUB)
    shutdown_pub.bind(zmq_config["shutdown_signal"])

    print(f"Experiment process subscribed to {zmq_config['tracking_estimates']}")
    print(f"Draw publisher bound to {zmq_config['stimulus_draw']}")
    print(f"Log publisher bound to {zmq_config['log_data']}")
    print(f"Shutdown publisher bound to {zmq_config['shutdown_signal']}")

    # Initialize performance monitoring
    perf_monitor = ExperimentPerformanceMonitor()

    # Start resource monitoring thread
    resource_thread = threading.Thread(
        target=resource_monitor_thread, args=(perf_monitor,), daemon=True
    )
    resource_thread.start()

    stats_timer = time.time()

    try:
        module_path = f"experiments.{experiment_name}.experiment"
        ExperimentModule = importlib.import_module(module_path)
        class_name = (
            "".join(word.capitalize() for word in experiment_name.split("_"))
            + "Experiment"
        )
        ExperimentClass = getattr(ExperimentModule, class_name)

        experiment = ExperimentClass(config)
        print(
            f"Successfully loaded experiment: {experiment_name} (Class: {class_name})"
        )
    except (ImportError, AttributeError) as e:
        print(f"FATAL: Could not load experiment '{experiment_name}'. Error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)

    # --- NEW: Variable to prevent spamming the same status message ---
    last_status_print_time = 0
    status_print_interval = 1.0  # Print status at most once per second

    try:
        while running:
            msg_bytes = estimate_sub.recv()
            msg_payload = unpack_msg(msg_bytes)

            # Time the experiment update
            update_start = time.time()
            outputs = experiment.update(msg_payload)
            update_time_ms = (time.time() - update_start) * 1000
            perf_monitor.record_update_time(update_time_ms)

            if experiment.get_phase() == "finished":
                print("Experiment duration complete. Publishing shutdown signal.")
                # Send a simple message. The content doesn't matter, just its presence.
                shutdown_pub.send_string("SHUTDOWN")
                time.sleep(1)  # Give other processes a moment to receive it
                running = False  # Break our own loop
                continue  # Skip the rest of the loop

            if "stimulus_draw" in outputs and outputs["stimulus_draw"]:
                send_start = time.time()
                draw_pub.send(pack_msg(outputs["stimulus_draw"]))
                send_time_ms = (time.time() - send_start) * 1000
                perf_monitor.record_stimulus_send(send_time_ms)

            if "log_data" in outputs and outputs["log_data"]:
                send_start = time.time()
                log_pub.send(pack_msg(outputs["log_data"]))
                send_time_ms = (time.time() - send_start) * 1000
                perf_monitor.record_log_send(send_time_ms)

            current_time = time.time()
            if "status_message" in outputs and (
                current_time - last_status_print_time > status_print_interval
            ):
                # Use a carriage return to keep the status on a single updating line
                print(f"\r{outputs['status_message']}", end="", flush=True)
                last_status_print_time = current_time

            # Print stats every 5 seconds
            if time.time() - stats_timer > 5:
                perf_monitor.print_stats()
                stats_timer = time.time()

            if experiment.get_phase() == "finished":
                print("Experiment duration complete. Shutting down.")
                running = False

    except Exception as e:
        print(f"An error occurred in the experiment process: {e}")
        import traceback

        traceback.print_exc()
    finally:
        print("Experiment process shutting down.")
        # Print final performance summary
        final_stats = perf_monitor.get_stats()
        if final_stats:
            print(
                f"[EXPERIMENT FINAL] Total updates: {final_stats['total_updates']}, "
                f"Avg FPS: {final_stats['fps']:.1f}, "
                f"Stimulus msgs: {final_stats['stimulus_messages']}, "
                f"Log msgs: {final_stats['log_messages']}, "
                f"Avg update: {final_stats['avg_update_time_ms']:.1f}ms, "
                f"Uptime: {final_stats['uptime_seconds']:.1f}s"
            )

        draw_pub.close()
        log_pub.close()
        estimate_sub.close()
        shutdown_pub.close()
        context.term()
        sys.exit(0)


if __name__ == "__main__":
    main()
