from typing import List, Dict, Tuple, Any
import numpy as np


class Drawing:
    """
    A class to create a serializable description of a scene to be rendered.
    Instructions are stored as a list of dictionaries, ready for ZMQ.
    This class mimics the API of the legacy version for easy migration.
    """

    def __init__(self):
        self.instructions: List[Dict[str, Any]] = []

    def _to_list(self, data):
        """Helper to convert tuples/numpy arrays to lists for JSON/MsgPack compatibility."""
        if isinstance(data, (tuple, np.ndarray)):
            return list(data)
        return data

    def add_circle(
        self,
        center: Tuple,
        radius: float,
        color: Tuple = (0, 0, 0),
        line_width: int = 1,
        fill: bool = False,
    ):
        self.instructions.append(
            {
                "type": "circle",
                "center": self._to_list(center),
                "radius": radius,
                "color": self._to_list(color),
                "line_width": line_width,
                "fill": fill,
            }
        )

    def add_path(
        self,
        points: List[Tuple],
        color: Tuple = (0, 0, 0),
        line_width: int = 1,
        close: bool = False,
        fill: bool = False,
    ):
        self.instructions.append(
            {
                "type": "path",
                "points": [self._to_list(p) for p in points],
                "color": self._to_list(color),
                "line_width": line_width,
                "close": close,
                "fill": fill,
            }
        )

    def add_rectangle(
        self,
        top_left: Tuple,
        width: float,
        height: float,
        color: Tuple = (0, 0, 0),
        line_width: int = 1,
        fill: bool = False,
    ):
        self.instructions.append(
            {
                "type": "rectangle",
                "top_left": self._to_list(top_left),
                "width": width,
                "height": height,
                "color": self._to_list(color),
                "line_width": line_width,
                "fill": fill,
            }
        )

    # Add other drawing primitives here (ellipse, polygon, etc.) following the same pattern if needed.

    def to_dict_list(self) -> List[Dict[str, Any]]:
        """Returns the list of drawing instructions."""
        return self.instructions

    def clear(self):
        """Clears all drawing instructions."""
        self.instructions = []
