import numpy as np
import cairo
from typing import List, Dict

class SceneRenderer:
    """
    A headless renderer for creating scene images from drawing instructions.
    It uses Cairo to draw onto an off-screen surface and returns a NumPy array.
    This class has no dependency on Pygame.
    """
    def __init__(self, camera_width: int, camera_height: int):
        """
        Initializes the headless renderer.

        Args:
            camera_width (int): The width of the camera-space canvas.
            camera_height (int): The height of the camera-space canvas.
        """
        self.camera_width = camera_width
        self.camera_height = camera_height

        self.camera_surface = cairo.ImageSurface(
            cairo.FORMAT_ARGB32, self.camera_width, self.camera_height
        )
        self.camera_context = cairo.Context(self.camera_surface)

    def render_to_image(self, instructions: List[Dict]) -> np.ndarray:
        """
        Renders instructions to the off-screen buffer and returns it as a BGR NumPy array.
        """
        self.camera_context.save()
        self.camera_context.set_operator(cairo.OPERATOR_CLEAR)
        self.camera_context.paint()
        self.camera_context.restore()
        
        for instr in instructions:
            self._execute_instruction(self.camera_context, instr)
            
        buffer = self.camera_surface.get_data()
        image_bgra = np.frombuffer(buffer, dtype=np.uint8).reshape((self.camera_height, self.camera_width, 4))
        
        return image_bgra[:, :, :3] # Return as BGR

    def _execute_instruction(self, ctx: cairo.Context, instr: Dict):
        """Internal helper to dispatch drawing commands to Cairo."""
        instr_type = instr.get('type')
        ctx.save()
        ctx.set_source_rgb(*instr.get('color', (0,0,0)))
        ctx.set_line_width(instr.get('line_width', 1))
        
        if instr_type == 'circle':
            ctx.arc(instr['center'][0], instr['center'][1], instr['radius'], 0, 2 * np.pi)
            if instr.get('fill', False): ctx.fill()
            else: ctx.stroke()
        elif instr_type == "path":
            points = instr["points"]
            if len(points) >= 2:
                ctx.move_to(points[0][0], points[0][1])
                for p in points[1:]:
                    ctx.line_to(p[0], p[1])
                if instr["close"]:
                    ctx.close_path()
                if instr["fill"]:
                    ctx.fill()
                else:
                    ctx.stroke()
        elif instr_type == "rectangle":
            top_left = instr["top_left"]
            ctx.rectangle(top_left[0], top_left[1], instr["width"], instr["height"])
            if instr["fill"]:
                ctx.fill()
            else:
                ctx.stroke()
        ctx.restore()