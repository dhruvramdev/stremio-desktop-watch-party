"""
Configuration management for Stremio Desktop Watch Party controller.
"""

import os
import json
import argparse
import logging

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_DIR = os.path.expanduser("~/.config/stremio-watch-party")
DEFAULT_CONFIG_FILE = os.path.join(DEFAULT_CONFIG_DIR, "config.json")

def load_config(args=None) -> dict:
    """
    Loads configuration from:
    1. Default values
    2. Optional config file (~/.config/stremio-watch-party/config.json)
    3. Environment variables (STREMIO_WP_*)
    4. Explicit CLI arguments/passed dict
    """
    config = {
        "room": "default-room",
        "secret": "default-secret",
        "relay": "ws://localhost:8765",
        "role": "guest",
        "socket": None
    }

    # Load from file if exists
    if os.path.exists(DEFAULT_CONFIG_FILE):
        try:
            with open(DEFAULT_CONFIG_FILE, "r", encoding="utf-8") as f:
                file_cfg = json.load(f)
                config.update(file_cfg)
        except Exception as e:
            logger.warning(f"Failed to load config file {DEFAULT_CONFIG_FILE}: {e}")

    # Load from environment variables
    if os.environ.get("STREMIO_WP_ROOM"):
        config["room"] = os.environ.get("STREMIO_WP_ROOM")
    if os.environ.get("STREMIO_WP_SECRET"):
        config["secret"] = os.environ.get("STREMIO_WP_SECRET")
    if os.environ.get("STREMIO_WP_RELAY"):
        config["relay"] = os.environ.get("STREMIO_WP_RELAY")
    if os.environ.get("STREMIO_WP_ROLE"):
        config["role"] = os.environ.get("STREMIO_WP_ROLE")
    if os.environ.get("STREMIO_WP_SOCKET"):
        config["socket"] = os.environ.get("STREMIO_WP_SOCKET")

    # Override with explicit args if passed
    if args:
        if isinstance(args, argparse.Namespace):
            args_dict = vars(args)
        elif isinstance(args, dict):
            args_dict = args
        else:
            args_dict = {}

        for k, v in args_dict.items():
            if v is not None:
                config[k] = v

    return config
