import h5py
import numpy as np
import os
from typing import Dict, Any


class Calibration:
    """
    A loader and container for rig calibration data stored in an HDF5 file.
    Provides easy, dictionary-like access to calibration parameters.
    """

    def __init__(self, filepath: str):
        """
        Loads calibration data from the specified HDF5 file.

        Args:
            filepath (str): The path to the calibration HDF5 file.

        Raises:
            FileNotFoundError: If the calibration file does not exist.
        """
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Calibration file not found at: {filepath}")

        self.filepath = filepath
        self.data: Dict[str, Any] = {}
        self._load_data()
        print(f"Successfully loaded calibration data from {filepath}")

    def _load_data(self):
        """Internal method to read all datasets and attributes from the HDF5 file."""
        with h5py.File(self.filepath, "r") as f:
            # Load datasets
            for key in f.keys():
                self.data[key] = f[key][()]  # [()] reads the entire dataset into memory

            # Load attributes (for string/scalar values)
            for key in f.attrs.keys():
                value = f.attrs[key]
                # h5py can store strings as bytes, so decode if necessary
                if isinstance(value, bytes):
                    self.data[key] = value.decode("utf-8")
                else:
                    self.data[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        """
        Gets a calibration value by key, with an optional default.

        Args:
            key (str): The name of the calibration parameter.
            default (Any, optional): The value to return if the key is not found.

        Returns:
            Any: The calibration value or the default.
        """
        return self.data.get(key, default)

    def __getitem__(self, key: str) -> Any:
        """Allows dictionary-style access, e.g., calib['H_refined']"""
        if key not in self.data:
            raise KeyError(f"Calibration key '{key}' not found in {self.filepath}")
        return self.data[key]
