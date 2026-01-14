import numpy as np
import sys
import os
import cv2
from typing import List, Dict
from flyclopszero.utils.calibration_loader import Calibration
from flyclopszero.projection.renderer import (
    SceneRenderer,
)  # Import our new headless renderer


class Artist:
    """
    Manages the Pygame window and displays scenes after transforming them
    from camera space to projector space.
    """

    def __init__(
        self, projector_config: Dict, camera_config: Dict, calibration: Calibration
    ):
        import pygame

        self.pygame = pygame

        self.projector_width = projector_config["width"]
        self.projector_height = projector_config["height"]
        self.calibration = calibration

        # Create an instance of our headless renderer
        self.renderer = SceneRenderer(camera_config["width"], camera_config["height"])

        os.environ["SDL_VIDEO_WINDOW_POS"] = "0,0"
        self.pygame.init()
        self.screen = self.pygame.display.set_mode(
            (self.projector_width, self.projector_height), self.pygame.NOFRAME
        )
        self.pygame.mouse.set_visible(False)
        self.clock = self.pygame.time.Clock()

    def render(self, instructions: List[Dict]):
        """Renders instructions and displays them on the projector window."""
        # 1. Use the headless renderer to get the camera-space image
        camera_image_bgr = self.renderer.render_to_image(instructions)

        # 2. Warp the image using calibration maps
        projector_image_bgr = self.transform_camera_to_projector_map(camera_image_bgr)

        # 3. Convert to RGB and display
        projector_image_rgb = cv2.cvtColor(projector_image_bgr, cv2.COLOR_BGR2RGB)
        pygame_surface = self.pygame.surfarray.make_surface(
            projector_image_rgb.swapaxes(0, 1)
        )
        self.screen.blit(pygame_surface, (0, 0))
        self.pygame.display.flip()

        for event in self.pygame.event.get():
            if event.type == self.pygame.QUIT or (
                event.type == self.pygame.KEYDOWN and event.key == self.pygame.K_ESCAPE
            ):
                self.close()
                sys.exit()
        return camera_image_bgr  # Return the camera-space image for logging/debugging

    def transform_camera_to_projector_map(self, camera_image: np.ndarray) -> np.ndarray:
        # ... (This method remains exactly the same as before) ...
        map1x = self.calibration["H_refined_mapx"].astype(np.float32)
        map1y = self.calibration["H_refined_mapy"].astype(np.float32)
        projection_space_image = cv2.remap(
            camera_image, map1x, map1y, interpolation=cv2.INTER_LINEAR
        )

        correction_method = self.calibration.get(
            "projector_correction_method", "distortion"
        )
        if correction_method == "homography":
            mapx_key = "H_projector_distortion_corrected_homography_mapx"
            mapy_key = "H_projector_distortion_corrected_homography_mapy"
        else:
            mapx_key = "H_projector_distortion_corrected_distortion_mapx"
            mapy_key = "H_projector_distortion_corrected_distortion_mapy"

        map2x = self.calibration[mapx_key].astype(np.float32)
        map2y = self.calibration[mapy_key].astype(np.float32)
        corrected_projector_image = cv2.remap(
            projection_space_image, map2x, map2y, interpolation=cv2.INTER_LINEAR
        )
        return corrected_projector_image

    def close(self):
        if self.pygame.get_init():
            self.pygame.quit()
