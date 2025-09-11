import yaml
import os
from typing import Dict, Any


def deep_merge(source: Dict, destination: Dict) -> Dict:
    """
    Recursively merges source dict into destination dict.
    If a key exists in both and both values are dicts, it merges them.
    Otherwise, the value from the source dict overwrites the destination.
    """
    for key, value in source.items():
        if (
            isinstance(value, dict)
            and key in destination
            and isinstance(destination[key], dict)
        ):
            destination[key] = deep_merge(value, destination[key])
        else:
            destination[key] = value
    return destination


def load_config(experiment_name: str) -> Dict[str, Any]:
    """
    Loads and merges configuration files.

    It loads the base `config.yaml` first, then loads the experiment-specific
    config from `experiments/{experiment_name}/config.yaml`, and merges
    the experiment config on top of the base config.

    Args:
        experiment_name (str): The name of the experiment directory.

    Returns:
        Dict[str, Any]: A single, unified configuration dictionary.

    Raises:
        FileNotFoundError: If either the base or experiment config file is not found.
    """
    base_config_path = "config.yaml"
    experiment_config_path = os.path.join("experiments", experiment_name, "config.yaml")

    if not os.path.exists(base_config_path):
        raise FileNotFoundError(
            f"Base configuration file not found at: {base_config_path}"
        )
    if not os.path.exists(experiment_config_path):
        raise FileNotFoundError(
            f"Experiment configuration file not found at: {experiment_config_path}"
        )

    # Load base configuration
    with open(base_config_path, "r") as f:
        base_config = yaml.safe_load(f)

    # Load experiment-specific configuration
    with open(experiment_config_path, "r") as f:
        experiment_config = yaml.safe_load(f)

    # Merge configurations, with experiment-specific values taking precedence
    unified_config = deep_merge(experiment_config, base_config)

    print(
        f"Successfully loaded and merged configuration for experiment: '{experiment_name}'"
    )
    return unified_config
