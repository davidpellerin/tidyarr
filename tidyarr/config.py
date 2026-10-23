"""The settings file: the two folder roots, the naming formats, the junk file types, whether to remove the release folders
moves leave empty, and the TMDB API key, in one JSON file.

It is ~/.config/tidyarr/config.json on Linux (paths.py has the macOS and Windows places). Because it holds the key,
it is written readable by its owner only (0600, in a 0700 folder; on Windows the folder is already private to the user)
and replaced in one step, so a crash never leaves half a file. The page may save the naming formats and the key; the
roots are only ever set in this file or on the command line, never from the browser (see folders.py). The page may also
switch removing empty folders on and off.
"""
import json
import os
import tempfile
from pathlib import Path

from tidyarr import naming, paths

PATH = paths.CONFIG_DIR / "config.json"  # serve.py replaces this with --config
# What may go to the trash from a release folder once its videos are moved out (see leftovers.py); other files are kept.
JUNK = [".txt", ".nfo", ".jpg", ".jpeg", ".png", ".gif", ".url", ".website", ".lnk", ".exe", ".sfv", ".md5", ".torrent", ".nzb"]
DEFAULTS = {"input_root": "~/Downloads/input", "output_root": "~/Videos", "tv_format": naming.DEFAULT_TV, "movie_format": naming.DEFAULT_MOVIE,
            "junk_extensions": JUNK, "remove_empty_folders": False}
KEY = "tmdb_api_key"


def _read(path):
    if not path.exists():
        return {}
    if os.name == "posix" and path.stat().st_mode & 0o077:
        path.chmod(0o600)  # copied or edited with looser permissions; it may hold the key
    stored = json.loads(path.read_text())
    return stored if isinstance(stored, dict) else {}


def load(path=None):
    """The settings: the defaults, overridden by the file if there is one. The key is present only if one is saved."""
    stored = _read(Path(path or PATH))
    config = dict(DEFAULTS)
    config.update({key: stored[key] for key in (*DEFAULTS, KEY) if isinstance(DEFAULTS.get(key, ""), str) and isinstance(stored.get(key), str)})
    if isinstance(stored.get("remove_empty_folders"), bool):
        config["remove_empty_folders"] = stored["remove_empty_folders"]
    junk = stored.get("junk_extensions")
    if isinstance(junk, list) and all(isinstance(e, str) for e in junk):
        config["junk_extensions"] = ["." + e.strip().lstrip(".").lower() for e in junk if e.strip().lstrip(".")]
    return config


def save(updates, path=None):
    """Merge `updates` into the file; whatever they don't name (the roots, keys we don't know) is kept as it was."""
    path = Path(path or PATH)
    stored = {**_read(path), **updates}
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".config-", suffix=".json")  # created 0600
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(stored, indent=2, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise
