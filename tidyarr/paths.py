"""Where the app keeps its files.

The settings file is always in the user's own config folder, so the API key it holds can never end up in the project
(or be committed). Decisions live in the project's data/ folder when run from source; in a PyInstaller build the package
is unpacked to a temporary folder that is deleted on exit, so they go to the config folder too. The running server's
address and its log go in the state folder (~/.local/state/tidyarr on Linux, the config folder elsewhere).
"""
import os
import sys
from pathlib import Path


NAME = "tidyarr"


def user_dir(name=NAME):
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / name
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / name
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / name


def state_dir(name=NAME):
    if sys.platform in ("win32", "darwin"):
        return user_dir(name)
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / name


CONFIG_DIR = user_dir()
DATA_DIR = CONFIG_DIR if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent / "data"
STATE_DIR = state_dir()
