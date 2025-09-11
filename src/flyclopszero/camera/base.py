from abc import ABC, abstractmethod


class AbstractCamera(ABC):
    """
    An abstract base class for camera objects.
    Defines the essential interface that all camera implementations must follow.
    """

    def __init__(self, **kwargs):
        """Initializes the camera using parameters from the config."""
        self.params = kwargs
        super().__init__()

    @abstractmethod
    def start(self):
        """Starts the image acquisition."""
        pass

    @abstractmethod
    def stop(self):
        """Stops the image acquisition and cleans up resources."""
        pass

    @abstractmethod
    def get_array(self) -> "np.ndarray":
        """
        Retrieves a single frame from the camera as a NumPy array.
        """
        pass

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit. Ensures the camera is stopped."""
        self.stop()
