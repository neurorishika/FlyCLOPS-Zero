import zmq
import signal
import sys
import h5py
import numpy as np
import time
import os
import threading
import psutil
from collections import deque
from flyclopszero.utils.config_loader import load_config
from flyclopszero.utils.messaging import unpack_msg
from flyclopszero.utils.logging import setup_process_logging

running = True


class LoggerPerformanceMonitor:
    def __init__(self):
        self.start_time = time.time()
        self.process = psutil.Process()
        
        # HDF5 write tracking
        self.write_times = deque(maxlen=100)
        self.resize_times = deque(maxlen=100)
        
        # Counters
        self.total_logs = 0
        self.total_fly_records = 0
        self.slow_write_warnings = 0
        
        # Data size tracking
        self.bytes_written = 0
        
        # Resource monitoring
        self.cpu_percent = 0
        self.memory_mb = 0
        self.last_stats_time = time.time()
        
    def record_write_time(self, duration_ms, fly_count=0, data_size_bytes=0):
        self.write_times.append(duration_ms)
        self.total_logs += 1
        self.total_fly_records += fly_count
        self.bytes_written += data_size_bytes
        
        # Warning for slow writes
        if duration_ms > 50:  # 50ms threshold
            self.slow_write_warnings += 1
            print(f"[LOGGER WARNING] Slow HDF5 write: {duration_ms:.1f}ms ({fly_count} flies)")
    
    def record_resize_time(self, duration_ms):
        self.resize_times.append(duration_ms)
    
    def update_resources(self):
        try:
            self.cpu_percent = self.process.cpu_percent()
            self.memory_mb = self.process.memory_info().rss / 1024 / 1024
        except psutil.NoSuchProcess:
            pass
    
    def get_stats(self):
        current_time = time.time()
        uptime = current_time - self.start_time
        
        if not self.write_times:
            return None
            
        # Calculate rates
        log_rate = self.total_logs / uptime if uptime > 0 else 0
        fly_rate = self.total_fly_records / uptime if uptime > 0 else 0
        
        return {
            'total_logs': self.total_logs,
            'total_fly_records': self.total_fly_records,
            'slow_writes': self.slow_write_warnings,
            'log_rate': log_rate,
            'fly_rate': fly_rate,
            'avg_write_time_ms': sum(self.write_times) / len(self.write_times),
            'avg_resize_time_ms': sum(self.resize_times) / len(self.resize_times) if self.resize_times else 0,
            'bytes_written': self.bytes_written,
            'cpu_percent': self.cpu_percent,
            'memory_mb': self.memory_mb,
            'uptime_seconds': uptime
        }
    
    def print_stats(self):
        stats = self.get_stats()
        if stats:
            print(f"[LOGGER] Logs: {stats['total_logs']}, "
                  f"Fly records: {stats['total_fly_records']}, "
                  f"Rate: {stats['log_rate']:.1f}/s, "
                  f"Avg write: {stats['avg_write_time_ms']:.1f}ms, "
                  f"Data: {stats['bytes_written']/1024:.1f}KB, "
                  f"CPU: {stats['cpu_percent']:.1f}%, "
                  f"Memory: {stats['memory_mb']:.1f}MB")


def resource_monitor_thread(perf_monitor):
    """Background thread to monitor system resources"""
    while running:
        perf_monitor.update_resources()
        time.sleep(1.0)  # Update every second


# The HDF5Logger and PerformanceMonitor can be simplified as they are now the only major component.
class HDF5Logger:
    def __init__(self, filepath, perf_monitor):
        self.file = h5py.File(filepath, "w")
        self.perf_monitor = perf_monitor
        self.fly_data_dtype = np.dtype([
            ("frame_number", np.int32),
            ("t_capture", np.float64),      # Timestamp from camera process
            ("t_tracking_done", np.float64),# Timestamp from tracker process
            ("t_logic_done", np.float64),   # Timestamp from experiment process
            ("phase", "S10"),
            ("id", np.int32), ("position_x", np.float64), ("position_y", np.float64),
            ("velocity_smooth", np.float64), ("angle_smooth", np.float64),
            ("direction_smooth", np.float64), ("angular_velocity_smooth", np.float64),
            ("intask", np.bool_), ("distance_from_stimulus", np.float64),
        ])
        self.fly_dset = self.file.create_dataset("fly_data", shape=(0,), maxshape=(None,), dtype=self.fly_data_dtype, chunks=True)
        self.row_count = 0
        print(f"HDF5 logger initialized at {filepath}")

    def log_fly_data(self, data_payload):
        write_start = time.time()
        
        fly_states = data_payload.get('fly_states', [])
        if not fly_states: 
            return

        num_flies = len(fly_states)
        
        # Time the dataset resize
        resize_start = time.time()
        self.fly_dset.resize((self.row_count + num_flies,))
        resize_time_ms = (time.time() - resize_start) * 1000
        self.perf_monitor.record_resize_time(resize_time_ms)
        
        new_data = np.zeros(num_flies, dtype=self.fly_data_dtype)
        for i, state in enumerate(fly_states):
            new_data[i] = (
                data_payload['frame_number'],
                data_payload['t_capture'],
                data_payload['t_tracking_done'],
                data_payload['t_logic_done'],
                data_payload['phase'].encode('utf-8'),
                state['id'], state['position_x'], state['position_y'],
                state['velocity_smooth'], state['angle_smooth'], state['direction_smooth'],
                state['angular_velocity_smooth'], state['intask'], state['distance_from_stimulus']
            )
        
        # Write the data
        self.fly_dset[self.row_count:] = new_data
        self.row_count += num_flies
        
        # Record timing and data size
        write_time_ms = (time.time() - write_start) * 1000
        data_size = new_data.nbytes
        self.perf_monitor.record_write_time(write_time_ms, num_flies, data_size)

    def close(self): self.file.close(); print("HDF5 logger closed.")

def signal_handler(sig, frame):
    global running
    print("Caught signal, shutting down logger process...")
    running = False

def main(experiment_name: str, session_timestamp: str):
    global running
    setup_process_logging('logger', os.path.join('data', session_timestamp, 'logs'))
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    config = load_config(experiment_name)
    zmq_config = config['zmq_sockets']
    
    h5_filepath = os.path.join('data', session_timestamp, "data.h5")
    
    # Initialize performance monitoring
    perf_monitor = LoggerPerformanceMonitor()
    
    # Start resource monitoring thread
    resource_thread = threading.Thread(target=resource_monitor_thread, args=(perf_monitor,), daemon=True)
    resource_thread.start()
    
    hdf5_logger = HDF5Logger(h5_filepath, perf_monitor)

    context = zmq.Context()

    shutdown_sub = context.socket(zmq.SUB)
    shutdown_sub.connect(zmq_config['shutdown_signal'])
    shutdown_sub.setsockopt(zmq.SUBSCRIBE, b'')

    log_sub = context.socket(zmq.SUB)
    log_sub.connect(zmq_config['log_data'])
    log_sub.setsockopt(zmq.SUBSCRIBE, b'')
    
    print("Logger process started. Subscribed to log_data stream.")
    
    stats_timer = time.time()
    
    try:
        while running:
            socks = dict(poller.poll(timeout=0))
            if shutdown_sub in socks:
                print("Shutdown signal received, stopping camera.")
                running = False
                continue
            log_bytes = log_sub.recv()
            log_payload = unpack_msg(log_bytes)
            hdf5_logger.log_fly_data(log_payload)
            
            # Print stats every 5 seconds
            if time.time() - stats_timer > 5:
                perf_monitor.print_stats()
                stats_timer = time.time()
                
    except (Exception, KeyboardInterrupt):
        pass # Allow finally block to run
    finally:
        print("Logger process shutting down. Finalizing HDF5 file...")
        # Print final performance summary
        final_stats = perf_monitor.get_stats()
        if final_stats:
            print(f"[LOGGER FINAL] Total logs: {final_stats['total_logs']}, "
                  f"Fly records: {final_stats['total_fly_records']}, "
                  f"Rate: {final_stats['log_rate']:.1f}/s, "
                  f"Avg write: {final_stats['avg_write_time_ms']:.1f}ms, "
                  f"Data written: {final_stats['bytes_written']/1024:.1f}KB, "
                  f"Uptime: {final_stats['uptime_seconds']:.1f}s")
        
        hdf5_logger.close()
        shutdown_sub.close()
        log_sub.close()
        context.term()