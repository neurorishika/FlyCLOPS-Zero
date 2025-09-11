import numpy as np
from typing import Dict, Any


class Fly:
    """
    A state-management class for a single tracked target (fly).
    Encapsulates all tracking data and experiment-specific state for one fly.
    """

    def __init__(self, fly_id: int, initial_estimate: Dict[str, Any]):
        """
        Initializes a Fly state object.

        Args:
            fly_id (int): The unique ID for this fly.
            initial_estimate (Dict[str, Any]): The first estimate dictionary from the tracker.
        """
        self.id = fly_id

        # Core tracking attributes, initialized to sensible defaults
        self.position = np.array([np.nan, np.nan])
        self.velocity_smooth = np.nan
        self.angle_smooth = np.nan
        self.direction_smooth = np.nan
        self.angular_velocity_smooth = np.nan

        # Experiment-specific state
        self.in_task = False
        self.time_since_last_encounter = 0.0
        self.distance_from_stimulus = np.inf

        # Populate with initial data
        self.update_from_estimate(initial_estimate)

    def update_from_estimate(self, estimate: Dict[str, Any]):
        """Updates the fly's state from a new tracker estimate."""
        pos = estimate.get("position", (np.nan, np.nan))
        self.position = np.array(pos) if pos is not None else np.array([np.nan, np.nan])
        self.velocity_smooth = estimate.get("velocity_smooth", np.nan)
        self.angle_smooth = estimate.get("angle_smooth", np.nan)
        self.direction_smooth = estimate.get("direction_smooth", np.nan)
        self.angular_velocity_smooth = estimate.get("angular_velocity_smooth", np.nan)

    def update_task_status(
        self,
        stimulus_center_px: np.ndarray,
        stimulus_radius_px: float,
        stimulus_thickness_px: float,
        max_time_outside: float,
        delta_time: float,
    ):
        """
        Updates the 'in_task' status based on proximity to the stimulus.

        Args:
            stimulus_center_px (np.ndarray): [x, y] coordinates of the stimulus center.
            stimulus_radius_px (float): Radius of the stimulus circle in pixels.
            stimulus_thickness_px (float): Thickness of the stimulus circle in pixels.
            max_time_outside (float): Seconds a fly can be outside the zone before disengaging.
            delta_time (float): Time elapsed since the last frame.
        """
        if np.isnan(self.position).any():
            self.distance_from_stimulus = np.inf
            return

        dist_from_center = np.linalg.norm(self.position - stimulus_center_px)
        self.distance_from_stimulus = abs(dist_from_center - stimulus_radius_px)

        half_thickness = stimulus_thickness_px / 2.0

        if self.distance_from_stimulus < half_thickness:
            self.in_task = True
            self.time_since_last_encounter = 0.0
        elif self.in_task:
            self.time_since_last_encounter += delta_time
            if self.time_since_last_encounter > max_time_outside:
                self.in_task = False
                self.time_since_last_encounter = 0.0
