import logging
import sys
import os
from logging.handlers import RotatingFileHandler


def setup_process_logging(process_name: str, log_dir: str):
    """
    Configures the Python logging system to redirect all output (including print)
    to a dedicated file for a specific process.

    Args:
        process_name (str): The name of the process (e.g., 'camera').
        log_dir (str): The directory where the log file will be created.
    """
    os.makedirs(log_dir, exist_ok=True)
    log_filepath = os.path.join(log_dir, f"{process_name}.log")

    # Configure the root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # Remove any existing handlers to avoid duplicate logs
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Create a rotating file handler
    # Max 5MB per file, keep 2 backup files.
    file_handler = RotatingFileHandler(
        log_filepath, maxBytes=5 * 1024 * 1024, backupCount=2
    )
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # Redirect stdout and stderr to the logging system
    class StreamToLogger:
        def __init__(self, logger, level):
            self.logger = logger
            self.level = level
            self.linebuf = ""

        def write(self, buf):
            for line in buf.rstrip().splitlines():
                self.logger.log(self.level, line.rstrip())

        def flush(self):
            pass

    sys.stdout = StreamToLogger(root_logger, logging.INFO)
    sys.stderr = StreamToLogger(root_logger, logging.ERROR)

    print(
        f"Logging for '{process_name}' process configured. Output will be in {log_filepath}"
    )
