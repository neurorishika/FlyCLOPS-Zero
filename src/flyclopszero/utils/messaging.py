import msgpack
import numpy as np
from typing import Dict, Any, List, Tuple

# Explanation of the protocol:
# We use multipart messages for efficiency.
# For NumPy arrays (like camera frames), the message is a list of two byte strings:
#   1. Metadata (shape, dtype, timestamp) serialized with msgpack.
#   2. The raw bytes of the NumPy array itself, which is extremely fast.
# For other data (dictionaries, lists), we use a single msgpack-serialized byte string.


def pack_frame(frame: np.ndarray, metadata: Dict[str, Any]) -> List[bytes]:
    """
    Serializes a NumPy frame and its metadata for multipart ZMQ sending.

    Args:
        frame (np.ndarray): The camera frame.
        metadata (Dict[str, Any]): A dictionary of metadata (e.g., timestamp, frame_number).

    Returns:
        List[bytes]: A list of byte strings ready for `socket.send_multipart()`.
    """
    metadata["dtype"] = str(frame.dtype)
    metadata["shape"] = frame.shape

    metadata_bytes = msgpack.packb(metadata, use_bin_type=True)
    frame_bytes = frame.tobytes()

    return [metadata_bytes, frame_bytes]


def unpack_frame(multipart_message: List[bytes]) -> Tuple[np.ndarray, Dict[str, Any]]:
    """
    Deserializes a multipart ZMQ message back into a NumPy frame and metadata.

    Args:
        multipart_message (List[bytes]): The list of byte strings from `socket.recv_multipart()`.

    Returns:
        Tuple[np.ndarray, Dict[str, Any]]: The reconstructed frame and its metadata dictionary.
    """
    metadata_bytes, frame_bytes = multipart_message
    metadata = msgpack.unpackb(metadata_bytes, raw=False)

    frame = np.frombuffer(frame_bytes, dtype=np.dtype(metadata["dtype"])).reshape(
        metadata["shape"]
    )

    return frame, metadata


def numpy_encoder(obj: Any) -> Any:
    """
    A custom encoder for msgpack to handle NumPy data types.
    """
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, np.bool_):
        return bool(obj)
    # Let msgpack raise a TypeError for other unserializable types
    return obj


def pack_msg(data: Any) -> bytes:
    """
    Serializes a Python object (dict, list, etc.) using msgpack, with support for NumPy types.

    Args:
        data (Any): The Python object to serialize.

    Returns:
        bytes: A single byte string representing the object.
    """
    return msgpack.packb(data, default=numpy_encoder, use_bin_type=True)


def unpack_msg(message: bytes) -> Any:
    """
    Deserializes a byte string back into a Python object using msgpack.

    Args:
        message (bytes): The byte string from `socket.recv()`.

    Returns:
        Any: The reconstructed Python object.
    """
    return msgpack.unpackb(message, raw=False)
