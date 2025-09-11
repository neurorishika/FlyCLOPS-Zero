import cv2
import numpy as np
import time
from scipy.optimize import linear_sum_assignment


def process_contours(contours_list, n_targets, min_contour_area, max_contour_area):
    """
    Extract valid target measurements (x, y, angle) from contours based on size and shape.
    Keeps only the largest n_targets contours if more are found.
    (This function is a dependency and remains unchanged).
    """
    measurements = []
    sizes = []
    for cnt in contours_list:
        size = cv2.contourArea(cnt)
        if size < min_contour_area or size > max_contour_area:
            continue
        if len(cnt) >= 5:
            ellipse = cv2.fitEllipse(cnt)
            (x, y), _, angle = ellipse
            angle_radians = np.deg2rad(angle)
            measurements.append(np.array([x, y, angle_radians], dtype=np.float32))
            sizes.append(size)

    if len(measurements) > n_targets:
        sorted_indices = np.argsort(sizes)[::-1][:n_targets]
        measurements = [measurements[i] for i in sorted_indices]
    return measurements


class FastTracker:
    def __init__(
        self,
        width: int,
        height: int,
        n_targets: int,
        kalman_noise_covariance: float = 0.03,
        kalman_measurement_noise_covariance: float = 0.1,
        morph_kernel_size: int = 5,
        threshold_value: int = 50,
        trajectory_history_seconds: float = 5,
        min_contour_area: int = 50,
        max_contour_area: int = 500,
        max_distance_threshold: float = 25,
        min_detection_counts: int = 10,
        min_tracking_counts: int = 10,
        smoothing_alpha: float = 0.5,
        moving_threshold: float = 5,
        fallback_to_last_position: bool = False,
        debug: bool = False,
    ):
        """
        Initialize the FastTracker. This version is decoupled from any camera hardware.

        Args:
            width (int): Width of the frames to be processed.
            height (int): Height of the frames to be processed.
            n_targets (int): The number of targets to track.
            ... (other parameters remain the same)
        """
        self.WIDTH = width
        self.HEIGHT = height
        self.n_targets = n_targets
        self.kalman_noise_covariance = kalman_noise_covariance
        self.kalman_measurement_noise_covariance = kalman_measurement_noise_covariance
        self.morph_kernel_size = morph_kernel_size
        self.threshold_value = threshold_value
        self.trajectory_history_seconds = trajectory_history_seconds
        self.min_contour_area = min_contour_area
        self.max_contour_area = max_contour_area
        self.max_distance_threshold = max_distance_threshold
        self.min_detection_counts = min_detection_counts
        self.min_tracking_counts = min_tracking_counts
        self.smoothing_alpha = smoothing_alpha
        self.moving_threshold = moving_threshold
        self.fallback_to_last_position = fallback_to_last_position
        self.debug = debug
        # Debug-related parameters (previously in __init__)
        self.trajectory_thickness = 2

        self.background_model_lightest = None
        self.detection_initialized = False
        self.tracking_stabilized = False
        self.detection_counts = 0
        self.tracking_counts = 0
        self.conservative_used = False
        self.frame_count = 0
        self.start_time = time.time()

        self.trajectories = [[] for _ in range(self.n_targets)]
        self.track_ids = np.arange(self.n_targets)
        self.kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (self.morph_kernel_size, self.morph_kernel_size)
        )

        self.kalman_filters = [cv2.KalmanFilter(5, 3) for _ in range(self.n_targets)]
        for kf in self.kalman_filters:
            kf.measurementMatrix = np.array(
                [[1, 0, 0, 0, 0], [0, 1, 0, 0, 0], [0, 0, 1, 0, 0]], np.float32
            )
            kf.transitionMatrix = np.array(
                [
                    [1, 0, 0, 1, 0],
                    [0, 1, 0, 0, 1],
                    [0, 0, 1, 0, 0],
                    [0, 0, 0, 1, 0],
                    [0, 0, 0, 0, 1],
                ],
                np.float32,
            )
            kf.processNoiseCov = (
                np.eye(5, dtype=np.float32) * self.kalman_noise_covariance
            )
            kf.measurementNoiseCov = (
                np.eye(3, dtype=np.float32) * self.kalman_measurement_noise_covariance
            )
            kf.statePre = np.array(
                [
                    np.random.randint(0, self.WIDTH),
                    np.random.randint(0, self.HEIGHT),
                    0,
                    0,
                    0,
                ],
                np.float32,
            )
            kf.statePost = kf.statePre.copy()
            kf.errorCovPre = np.eye(5, dtype=np.float32)

        self.trajectory_colors = [
            tuple(c)
            for c in np.random.randint(
                0, 255, (self.n_targets, 3), dtype=np.uint8
            ).tolist()
        ]

    def process_frame(self, gray_frame: np.ndarray, return_debug_image: bool = False):
        """
        Processes a single grayscale frame to update tracking estimates.

        Args:
            gray_frame (np.ndarray): The input frame to process.
            return_debug_image (bool): If True, returns a BGR image with tracking overlays.

        Returns:
            Tuple[Optional[List[Dict]], Optional[np.ndarray]]:
            A tuple containing:
            - A list of dictionaries with tracking info for each target, or None.
            - The debug image if requested, otherwise None.
        """
        if gray_frame is None:
            return None, None

        current_time = time.time()

        if self.background_model_lightest is None:
            self.background_model_lightest = gray_frame.astype(np.float32)
            return None, (
                cv2.cvtColor(gray_frame, cv2.COLOR_GRAY2BGR)
                if return_debug_image
                else None
            )

        np.maximum(
            self.background_model_lightest,
            gray_frame.astype(np.float32),
            out=self.background_model_lightest,
        )
        background_model_uint8 = cv2.convertScaleAbs(self.background_model_lightest)

        fg_mask = cv2.absdiff(background_model_uint8, gray_frame)
        _, fg_mask = cv2.threshold(
            fg_mask, self.threshold_value, 255, cv2.THRESH_BINARY
        )
        fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_OPEN, self.kernel)
        fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_CLOSE, self.kernel)

        contours, _ = cv2.findContours(
            fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        measurements = process_contours(
            contours, self.n_targets, self.min_contour_area, self.max_contour_area
        )

        if self.detection_initialized and len(measurements) < self.n_targets:
            split_mask = cv2.erode(fg_mask, self.kernel, iterations=2)
            conservative_contours, _ = cv2.findContours(
                split_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            measurements = process_contours(
                conservative_contours,
                self.n_targets,
                self.min_contour_area,
                self.max_contour_area,
            )

        self.detection_counts = (
            self.detection_counts + 1 if len(measurements) == self.n_targets else 0
        )
        if self.detection_counts >= self.min_detection_counts:
            self.detection_initialized = True

        if self.detection_initialized and measurements:
            predicted_positions = np.array(
                [kf.predict()[:3] for kf in self.kalman_filters]
            )
            measurements_array = np.array(measurements)

            diffs = (
                predicted_positions[:, np.newaxis, :2]
                - measurements_array[np.newaxis, :, :2]
            )
            position_costs = np.linalg.norm(diffs, axis=2)
            angle_diffs = np.abs(
                predicted_positions[:, np.newaxis, 2]
                - measurements_array[np.newaxis, :, 2]
            )
            angle_diffs = np.minimum(angle_diffs, 2 * np.pi - angle_diffs)
            cost_matrix = position_costs + angle_diffs

            row_indices, col_indices = linear_sum_assignment(cost_matrix)

            avg_cost = 0
            for row, col in zip(row_indices, col_indices):
                if row < self.n_targets and col < len(measurements):
                    if (
                        not self.tracking_stabilized
                        or cost_matrix[row, col] < self.max_distance_threshold
                    ):
                        self.kalman_filters[row].correct(
                            measurements_array[col].reshape(3, 1)
                        )
                        self.trajectories[row].append(
                            {
                                "x": measurements_array[col][0],
                                "y": measurements_array[col][1],
                                "theta": measurements_array[col][2],
                                "time": current_time,
                            }
                        )
                        avg_cost += cost_matrix[row, col]
                    else:
                        self.trajectories[row].append(
                            {
                                "x": np.nan,
                                "y": np.nan,
                                "theta": np.nan,
                                "time": current_time,
                            }
                        )

            self.trajectories[row] = [
                entry
                for entry in self.trajectories[row]
                if current_time - entry["time"] <= self.trajectory_history_seconds
            ]

            avg_cost /= len(row_indices) if len(row_indices) > 0 else 1
            self.tracking_counts = (
                self.tracking_counts + 1
                if avg_cost < self.max_distance_threshold
                else 0
            )
            if (
                self.tracking_counts >= self.min_tracking_counts
                and not self.tracking_stabilized
            ):
                self.tracking_stabilized = True

        estimates = self._compute_estimates(current_time)
        self.frame_count += 1

        debug_image = None
        if self.debug or return_debug_image:
            debug_image = self._create_debug_image(gray_frame, fg_mask)

        return estimates, debug_image

    def _compute_estimates(self, current_time):
        if not self.detection_initialized:
            return None

        estimates = []
        for i, kf in enumerate(self.kalman_filters):
            est_dict = {
                "id": self.track_ids[i],
                "position": (np.nan, np.nan),
                "velocity": np.nan,
                "velocity_smooth": np.nan,
                "angle": np.nan,
                "angle_smooth": np.nan,
                "direction": np.nan,
                "direction_smooth": np.nan,
                "angular_velocity_smooth": np.nan,
                "time_since_start": current_time - self.start_time,
            }
            if len(self.trajectories[i]) > 0:
                current_entry = self.trajectories[i][-1]
                est_dict["position"] = (current_entry["x"], current_entry["y"])
                est_dict["angle"] = current_entry["theta"]
                est_dict["angle_smooth"] = current_entry.get(
                    "smoothed_angle", current_entry["theta"]
                )

                if len(self.trajectories[i]) >= 2:
                    self._calculate_velocities(i, current_time, est_dict)
            estimates.append(est_dict)
        return estimates

    def _calculate_velocities(self, i, current_time, est_dict):
        current_entry = self.trajectories[i][-1]
        prev_entry = self.trajectories[i][-2]
        delta_time = current_entry["time"] - prev_entry["time"]

        if (
            delta_time > 0
            and not np.isnan(
                [
                    current_entry["x"],
                    current_entry["y"],
                    prev_entry["x"],
                    prev_entry["y"],
                ]
            ).any()
        ):
            dx = current_entry["x"] - prev_entry["x"]
            dy = current_entry["y"] - prev_entry["y"]
            vx_raw, vy_raw = dx / delta_time, dy / delta_time
            est_dict["velocity"] = np.sqrt(vx_raw**2 + vy_raw**2)
            est_dict["direction"] = (
                np.arctan2(vy_raw, vx_raw) if est_dict["velocity"] > 0 else np.nan
            )

            alpha = self.smoothing_alpha
            prev_smoothed_vx = prev_entry.get("smoothed_vx", vx_raw)
            prev_smoothed_vy = prev_entry.get("smoothed_vy", vy_raw)

            smoothed_vx = prev_smoothed_vx * (1 - alpha) + vx_raw * alpha
            smoothed_vy = prev_smoothed_vy * (1 - alpha) + vy_raw * alpha

            est_dict["velocity_smooth"] = np.sqrt(smoothed_vx**2 + smoothed_vy**2)
            if est_dict["velocity_smooth"] > self.moving_threshold:
                est_dict["direction_smooth"] = np.arctan2(smoothed_vy, smoothed_vx)
            else:
                est_dict["direction_smooth"] = prev_entry.get(
                    "smoothed_direction", np.nan
                )

            prev_smoothed_angle = prev_entry.get("smoothed_angle", prev_entry["theta"])
            smoothed_angle = (
                prev_smoothed_angle * (1 - alpha) + current_entry["theta"] * alpha
            )
            est_dict["angle_smooth"] = smoothed_angle
            est_dict["angular_velocity_smooth"] = (
                smoothed_angle - prev_smoothed_angle
            ) / delta_time

            current_entry["smoothed_vx"] = smoothed_vx
            current_entry["smoothed_vy"] = smoothed_vy
            current_entry["smoothed_angle"] = smoothed_angle

    def _create_debug_image(self, gray_frame, fg_mask):
        color_frame = cv2.cvtColor(gray_frame, cv2.COLOR_GRAY2BGR)
        if self.detection_initialized:
            for i in range(self.n_targets):
                if len(self.trajectories[i]) > 0:
                    entry = self.trajectories[i][-1]
                    x, y, theta = entry["x"], entry["y"], entry["theta"]
                    if not np.isnan([x, y]).any():
                        pt = (int(x), int(y))
                        color = self.trajectory_colors[i]
                        cv2.circle(color_frame, pt, 10, color, -1)
                        end_pt = (
                            int(x + 20 * np.cos(theta)),
                            int(y + 20 * np.sin(theta)),
                        )
                        cv2.line(color_frame, pt, end_pt, color, 2)
                        cv2.putText(
                            color_frame,
                            f"ID:{self.track_ids[i]}",
                            (pt[0] + 15, pt[1] - 15),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.6,
                            (255, 0, 0),
                            2,
                        )
        # place the fg_mask and color_frame side by side
        fg_mask_bgr = cv2.cvtColor(fg_mask, cv2.COLOR_GRAY2BGR)
        combined = np.hstack((color_frame, fg_mask_bgr))
        return combined

    def close(self):
        """Release any OpenCV windows if in debug mode."""
        pass
