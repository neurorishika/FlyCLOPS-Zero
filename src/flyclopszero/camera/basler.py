from pypylon import pylon
import numpy as np
import time
from .base import AbstractCamera


def list_basler_cameras():
    """Returns a list of connected Basler camera device info objects."""
    tlf = pylon.TlFactory.GetInstance()
    devices = tlf.EnumerateDevices()
    for i, dev in enumerate(devices):
        print(f"  {i}: {dev.GetModelName()} ({dev.GetSerialNumber()})")
    return devices


class BaslerCamera(AbstractCamera):
    """
    A class to encapsulate a Basler camera, conforming to the AbstractCamera interface.
    This version is streamlined for the ZMQ architecture, focusing only on frame acquisition.
    """

    def __init__(self, **kwargs):
        """
        Initializes the Basler camera.

        Args:
            **kwargs: Keyword arguments matching the `camera` section of the config.
                      Expected keys: 'index', 'exposure_time', 'gain', 'width',
                                     'height', 'offset_x', 'offset_y', 'pixel_format'.
        """
        super().__init__(**kwargs)
        self.cam = None
        self.running = False
        self.initialized = False
        self.grab_result = None

        # Discover and select the camera
        print("Searching for Basler cameras...")
        devices = list_basler_cameras()
        if not devices:
            raise RuntimeError("No Basler cameras detected.")

        index = self.params.get("index", 0)
        if index >= len(devices):
            raise IndexError(
                f"Camera index {index} is out of range. Only {len(devices)} cameras found."
            )

        self.cam = pylon.InstantCamera(
            pylon.TlFactory.GetInstance().CreateDevice(devices[index])
        )
        device_info = self.cam.GetDeviceInfo()
        print(
            f"Selected camera: {device_info.GetModelName()} ({device_info.GetSerialNumber()})"
        )

        # Initialize camera settings
        self._init_camera()

    def _init_camera(self):
        """Initializes the camera hardware settings."""
        self.cam.Open()
        print("Camera device opened.")

        # --- PARAMETER CONFIGURATION ---
        # All parameters must be set BEFORE StartGrabbing() is called.

        # Set camera parameters from config
        print("Setting camera parameters...")
        self.cam.ExposureAuto.SetValue("Off")
        self.cam.ExposureTime.SetValue(self.params.get("exposure_time", 9000))

        self.cam.GainAuto.SetValue("Off")
        self.cam.Gain.SetValue(self.params.get("gain", 0))

        # Set ROI
        self.cam.Width.SetValue(self.params.get("width", 2048))
        self.cam.Height.SetValue(self.params.get("height", 2048))
        self.cam.OffsetX.SetValue(self.params.get("offset_x", 224))
        self.cam.OffsetY.SetValue(self.params.get("offset_y", 0))

        pixel_format = self.params.get("pixel_format", "Mono8")
        if pixel_format not in self.cam.PixelFormat.Symbolics:
            raise ValueError(
                f"Invalid pixel format '{pixel_format}'. Available: {self.cam.PixelFormat.Symbolics}"
            )
        self.cam.PixelFormat.SetValue(pixel_format)
        print("Parameters set successfully.")

        # --- START GRABBING ---
        # We assume continuous grabbing for this high-speed, closed-loop system.
        # This is the most performant mode.
        self.cam.RegisterConfiguration(
            pylon.AcquireContinuousConfiguration(),
            pylon.RegistrationMode_ReplaceAll,
            pylon.Cleanup_Delete,
        )

        # Set grabbing strategy to ensure we get the latest frame, critical for closed-loop.
        self.cam.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)
        self.running = True

        self.initialized = True
        print("BaslerCamera initialized and grabbing.")

    def start(self):
        """Starts image acquisition (already started in init for Basler)."""
        if not self.running:
            self.cam.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)
            self.running = True
            print("Camera grabbing restarted.")

    def stop(self):
        """Stops image acquisition and cleans up."""
        if self.running:
            self.cam.StopGrabbing()
            self.running = False
            print("Camera grabbing stopped.")
        if self.cam.IsOpen():
            self.cam.Close()
            print("Camera device closed.")

    def get_array(self, timeout=1000) -> np.ndarray:
        """
        Retrieves a single frame from the camera as a NumPy array.
        This is a blocking call.

        Args:
            timeout (int): Timeout in milliseconds to wait for a frame.

        Returns:
            np.ndarray: The captured frame.
        """
        if not self.running:
            raise RuntimeError(
                "Camera is not running. Call start() before getting an array."
            )

        try:
            # Re-using the grab_result object is more efficient
            self.grab_result = self.cam.RetrieveResult(
                timeout, pylon.TimeoutHandling_ThrowException
            )
            if self.grab_result.GrabSucceeded():
                # No copy is made here, the array points to the camera buffer.
                # The user must copy it if they need to hold onto it.
                return self.grab_result.Array
            else:
                print(f"Error grabbing frame: {self.grab_result.GetErrorDescription()}")
                return None
        finally:
            # Release the buffer back to the camera
            if self.grab_result and self.grab_result.IsValid():
                self.grab_result.Release()
