from abc import ABC, abstractmethod
from typing import Dict, Any, Tuple


class AbstractExperiment(ABC):
    """
    An abstract base class for experiment logic.
    Defines the structure for managing the state and rules of an experiment.
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initializes the experiment using the unified configuration.

        Args:
            config (Dict[str, Any]): The merged configuration dictionary.
        """
        self.config = config
        super().__init__()

    @abstractmethod
    def update(self, msg: Tuple[Dict, Any]) -> Dict[str, bytes]:
        """
        Processes an incoming message (e.g., tracking estimates) and updates
        the experiment's state.

        Args:
            msg (Tuple[Dict, Any]): A tuple containing the message metadata
                                     and the unpacked message payload.

        Returns:
            Dict[str, bytes]: A dictionary where keys are ZMQ topic strings
                              (e.g., 'stimulus/draw') and values are the
                              serialized messages to be published on those topics.
        """
        pass
