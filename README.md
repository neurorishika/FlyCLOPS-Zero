
# FlyClopsZero

**A real-time, process-based, closed-loop visual stimulus system for behavioral experiments.**

FlyClopsZero is designed for robustness, scalability, and modularity. It uses a ZeroMQ-based publish-subscribe architecture to decouple core components like camera acquisition, object tracking, experiment logic, and stimulus rendering into independent processes. This design eliminates single points of failure, leverages multi-core CPUs, and enables rapid development of new experimental paradigms.

## Key Features

- **Process-Based Architecture:** Each major component (camera, tracker, renderer) runs in its own process, preventing the Global Interpreter Lock (GIL) from becoming a bottleneck and ensuring system stability.
- **Real-Time Performance:** Utilizes ZeroMQ with Inter-Process Communication (IPC) for microsecond-latency messaging, ensuring a tight closed-loop for high-frequency experiments.
- **Modular Experiment Design:** A base `Experiment` class allows researchers to implement new behavioral paradigms by focusing solely on the scientific logic, without touching the underlying engineering.
- **Centralized Configuration:** All system, hardware, and experiment parameters are managed through a single, human-readable `config.yaml` file.
- **Hardware Accelerated:** Natively supports NVIDIA GPU acceleration for video encoding (via FFMPEG) and can be extended for tracking algorithms.
- **Reproducible Environments:** Uses **Conda** for managing the complex CUDA backend and **Poetry** for deterministic, lock-file-based management of all Python dependencies.

## System Architecture

The system is composed of several independent processes that communicate over ZeroMQ sockets:

<!-- It's highly recommended to create a simple diagram for this -->

1. **Camera Process (`camera_process.py`):**
    - Captures frames from a Basler camera using the Pylon SDK.
    - Publishes raw frame data and metadata to the `camera/frames` topic.

2. **Tracker Process (`tracker_process.py`):**
    - Subscribes to `camera/frames`.
    - Performs multi-target tracking using the `FastTracker` algorithm.
    - Publishes tracking `estimates` to the `tracking/estimates` topic.

3. **Experiment Process (`experiment_process.py`):**
    - The "brain" of the system.
    - Subscribes to `tracking/estimates`.
    - Instantiates and runs a specific `Experiment` class (e.g., `TrailExperiment`).
    - Publishes drawing commands to `stimulus/draw` and experiment-specific data to `experiment/events`.

4. **Artist Process (`artist_process.py`):**
    - A "dumb" renderer.
    - Subscribes to `stimulus/draw`.
    - Receives abstract drawing commands and renders them to the projector screen using Pygame.

5. **Datalogger Process (`datalogger_process.py`):**
    - The central scribe.
    - Subscribes to all major data topics (`camera/frames`, `tracking/estimates`, `experiment/events`).
    - Asynchronously writes video streams and structured HDF5 data to disk without blocking the main experimental loop.

---

## Installation Guide

This guide assumes a system running **Ubuntu 22.04 LTS** with an **NVIDIA GPU**.

### Part 0: System-Level Prerequisites

These steps install drivers and libraries that must be available system-wide.

1. **NVIDIA Drivers:**
   Ensure your system's NVIDIA drivers are installed and functional. Verify by running `nvidia-smi`.

   ```bash
   nvidia-smi
   ```

   If not installed, follow the official Ubuntu documentation: [NVIDIA Drivers Installation](https://ubuntu.com/server/docs/nvidia-drivers-installation).

2. **Custom FFMPEG with NVIDIA Acceleration:**
   This project requires a custom build of FFMPEG to enable hardware-accelerated video encoding.
   Follow the official NVIDIA guide to build and install FFMPEG from source:  
   [FFMPEG with NVIDIA GPU Guide](https://docs.nvidia.com/video-technologies/video-codec-sdk/12.1/ffmpeg-with-nvidia-gpu/index.html)

   After installation, verify that the build includes NVIDIA support:

   ```bash
   ffmpeg -version | grep "enable-cuda-nvcc"
   ```

   If you encounter errors about missing shared libraries (`.so` files), ensure the installation path (e.g., `/usr/local/lib`) is known to the dynamic linker.

3. **Basler Pylon SDK:**
   Install the Pylon SDK for your camera from the official Basler website. Download and install the `.deb` packages for both the **Pylon Software** and the **MPEG-4 Supplementary Package**.
   - [Basler Software Downloads](https://www.baslerweb.com/en/downloads/software-downloads/)

   Before installing, run the following to satisfy dependencies:

   ```bash
   sudo apt-get update
   sudo apt-get install libgl1-mesa-dri libgl1-mesa-glx libxcb-xinerama0 libxcb-xinput0 libxcb-cursor0 libcairo2-dev
   ```

   Follow all installation instructions provided by Basler for your specific camera type (GigE or CoaXPress). For GigE cameras, remember to configure the network adapter for "Link-Local Only".

### Part 1: Project Setup and Environment

This section uses **Mamba** (a fast Conda implementation) and **Poetry** to create a reproducible environment.

1. **Clone the Repository:**

```bash
git clone https://github.com/your-username/flyprojection-v2.git
cd flyprojection-v2
```

2. **Install Mambaforge:** This provides the `mamba` package manager, which will be used to create our base environment.

```bash
wget -nc https://github.com/conda-forge/miniforge/releases/latest/download/Mambaforge-Linux-x86_64.sh
bash Mambaforge-Linux-x86_64.sh -b
source ~/mambaforge/bin/activate
conda init bash
```

Close and reopen your terminal for the changes to take effect.

3. **Create and Activate the Conda Environment:**
We use `mamba` to create an environment with only Python and the CUDA Toolkit. All other dependencies will be handled by Poetry.

```bash
mamba create --name flyprojection-v2 python=3.9 cudatoolkit=11.8 -c conda-forge -y
conda activate flyprojection-v2
```

4. **Install Project Dependencies with Poetry:**
With the Conda environment active, Poetry will install all Python packages into it.

```bash
pip install poetry
poetry install
```

This command reads the `pyproject.toml` file, resolves all dependencies, and installs them according to the `poetry.lock` file, ensuring a fully reproducible setup.

---

## Usage

### 1. Configure Your Experiment

Before running, all parameters must be set in the `config.yaml` file. This includes:

- Camera and projector hardware settings.
- ZMQ socket addresses (IPC is recommended for single-machine use).
- Tracker parameters.
- Experiment-specific variables (stimulus colors, engagement rules, etc.).

### 2. Running an Experiment

The entire system is launched from a single master script. It starts all the required processes in the correct order.

```bash
python run_experiment.py
```

To stop the experiment gracefully, press `Ctrl+C` in the terminal. The master script will catch the signal and terminate all child processes.

### 3. Developing a New Experiment

The modular architecture makes adding new experiments straightforward:

1.**Create a New Experiment Class:** In the `src/flyprojection_v2/experiments/` directory, create a new file (e.g., `my_experiment.py`). Inside, define a class that inherits from the `Experiment` base class.

```python
# src/flyprojection_v2/experiments/my_experiment.py
from .base import Experiment

class MyExperiment(Experiment):
    def __init__(self, config):
        super().__init__(config)
        # Initialize experiment-specific state here

    def update(self, estimates):
        # Main logic goes here:
        # 1. Update fly states based on estimates.
        # 2. Decide on the stimulus to show.
        # 3. Return drawing commands, hardware commands, and events.
        drawing_commands = [...]
        return drawing_commands, None, None
```

2.**Add Configuration:** Add a section for your new experiment's parameters to `config.yaml`.
3.**Update the Launcher:** Modify `run_experiment.py` or `experiment_process.py` to import and instantiate your new `MyExperiment` class based on the configuration.

---

## Contributing

Contributions are welcome! Please open an issue to discuss a new feature or bug fix. For development, it's recommended to install dependencies in editable mode:

```bash
# After activating the conda environment
poetry install
```

This ensures that any changes you make to the source code are immediately reflected when you run the application.
