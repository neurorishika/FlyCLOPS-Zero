import time
import numpy as np
from typing import Dict, Any
from flyclopszero.experiments.base import AbstractExperiment
from flyclopszero.behavior.fly_state import Fly
from flyclopszero.projection.drawing import Drawing


class SampleExperiment(AbstractExperiment):
    """
    Implements a pre-stimulus, stimulus, and post-stimulus experiment.
    This version includes verbose status updates for live monitoring.
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

        exp_config = self.config["experiment"]
        self.duration_pre = exp_config["duration_pre"] * 60
        self.duration_stim = exp_config["duration_stim"] * 60
        self.duration_post = exp_config["duration_post"] * 60
        self.total_duration = (
            self.duration_pre + self.duration_stim + self.duration_post
        )
        self.max_time_outside = exp_config["max_time_outside_intask"]

        self.stimulus_center_px = np.array([1024, 1024])
        self.arena_radius_px = 1000.0
        self.stimulus_radius_px = 800.0
        self.stimulus_thickness_px = 100.0

        self.background_color = tuple(exp_config["colors"]["background"])
        self.stimulus_color = tuple(exp_config["colors"]["stimulus"])

        self.start_time = None
        self.last_update_time = None
        self.flies: Dict[int, Fly] = {}

        # --- NEW: State for verbose logging ---
        self.last_phase = ""
        self.phase_start_time = None

    def get_phase(self) -> str:
        """Determines the current experimental phase based on elapsed time."""
        if self.start_time is None:
            return "init"

        elapsed = time.time() - self.start_time
        if elapsed < self.duration_pre:
            return "pre"
        elif elapsed < self.duration_pre + self.duration_stim:
            return "stim"
        elif elapsed < self.total_duration:
            return "post"
        else:
            return "finished"

    def update(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        """
        Processes tracking estimates, updates fly states, and generates drawing commands
        and a human-readable status message.
        """
        if self.start_time is None:
            self.start_time = time.time()
            self.last_update_time = self.start_time
            self.phase_start_time = self.start_time
            print("--- Experiment clock started ---")

        current_time = time.time()
        delta_time = current_time - self.last_update_time
        self.last_update_time = current_time

        # --- NEW: Detect and log phase changes ---
        current_phase = self.get_phase()
        if current_phase != self.last_phase:
            print(f"--- Phase Change: -> {current_phase.upper()} ---")
            self.last_phase = current_phase
            self.phase_start_time = current_time

        # --- Update fly states (logic is unchanged) ---
        estimates = msg["estimates"]
        for est in estimates:
            fly_id = est["id"]
            if fly_id not in self.flies:
                self.flies[fly_id] = Fly(fly_id, est)
            else:
                self.flies[fly_id].update_from_estimate(est)
            self.flies[fly_id].update_task_status(
                self.stimulus_center_px,
                self.stimulus_radius_px,
                self.stimulus_thickness_px,
                self.max_time_outside,
                delta_time,
            )

        # --- Generate drawing instructions (logic is unchanged) ---
        drawing = Drawing()
        drawing.add_circle(
            self.stimulus_center_px,
            self.arena_radius_px,
            color=self.background_color,
            fill=True,
        )
        if current_phase == "stim":
            drawing.add_circle(
                self.stimulus_center_px,
                self.stimulus_radius_px,
                color=self.stimulus_color,
                fill=False,
                line_width=self.stimulus_thickness_px,
            )

        # --- Prepare data for HDF5 logger (logic is unchanged) ---
        fly_data_for_logging = [
            {
                "id": fly.id,
                "position_x": fly.position[0],
                "position_y": fly.position[1],
                "velocity_smooth": fly.velocity_smooth,
                "angle_smooth": fly.angle_smooth,
                "direction_smooth": fly.direction_smooth,
                "angular_velocity_smooth": fly.angular_velocity_smooth,
                "intask": fly.in_task,
                "distance_from_stimulus": fly.distance_from_stimulus,
            }
            for fly in self.flies.values()
        ]

        # --- NEW: Generate a verbose status message ---
        total_elapsed = current_time - self.start_time
        phase_elapsed = current_time - self.phase_start_time
        flies_in_task = sum(1 for fly in self.flies.values() if fly.in_task)
        total_flies = len(self.flies)

        status_message = (
            f"Phase: {current_phase.upper()} [{int(phase_elapsed)}s] | "
            f"Total: [{int(total_elapsed)}s / {int(self.total_duration)}s] | "
            f"Flies in Task: {flies_in_task}/{total_flies}"
        )

        # --- Assemble all outputs ---
        outputs = {
            "status_message": status_message,  # Add the new message
            "stimulus_draw": {
                "frame_number": msg["frame_number"],
                "instructions": drawing.to_dict_list(),
            },
            "log_data": {
                "frame_number": msg["frame_number"],
                "phase": current_phase,
                "fly_states": fly_data_for_logging,
                "t_capture": msg["t_capture"],
                "t_tracking_done": msg["t_tracking_done"],
                "t_logic_done": time.time(),
            },
        }

        return outputs
