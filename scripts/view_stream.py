import zmq
import argparse
from flyclopszero.utils.config_loader import load_config
from flyclopszero.utils.messaging import unpack_frame, unpack_msg


def main():
    """
    A utility to subscribe to a ZMQ topic and print the contents of messages.
    Essential for debugging the distributed system.
    """
    parser = argparse.ArgumentParser(
        description="Subscribe to a ZMQ topic and view the stream."
    )
    parser.add_argument(
        "topic",
        type=str,
        help="The name of the ZMQ socket to listen to (e.g., 'camera_frames', 'tracking_estimates').",
    )
    parser.add_argument(
        "--type",
        type=str,
        default="frame",
        choices=["frame", "msg"],
        help="The type of message to unpack ('frame' for camera data, 'msg' for generic msgpack).",
    )
    args = parser.parse_args()

    # Load config to get the socket address
    config = load_config("sample")
    zmq_sockets = config["zmq_sockets"]

    if args.topic not in zmq_sockets:
        print(
            f"Error: Topic '{args.topic}' not found in config.yaml's zmq_sockets section."
        )
        return

    address = zmq_sockets[args.topic]

    # Setup ZMQ Subscriber
    context = zmq.Context()
    socket = context.socket(zmq.SUB)
    socket.connect(address)
    socket.setsockopt(zmq.SUBSCRIBE, b"")  # Subscribe to all messages on this topic
    print(f"Listening for '{args.topic}' messages on {address}...")

    try:
        while True:
            multipart_message = socket.recv_multipart()

            print("--- New Message ---")
            if args.type == "frame":
                frame, metadata = unpack_frame(multipart_message)
                print(f"Metadata: {metadata}")
                print(f"Frame Shape: {frame.shape}, Dtype: {frame.dtype}")
            else:  # 'msg'
                # Assumes single-part message for now
                payload = unpack_msg(multipart_message[0])
                print(f"Payload: {payload}")

    except KeyboardInterrupt:
        print("\nStopping viewer.")
    finally:
        socket.close()
        context.term()


if __name__ == "__main__":
    main()
