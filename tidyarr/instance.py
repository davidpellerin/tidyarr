"""The one running server: where it is, so a second launch can reopen it instead of starting another.

The server writes its pid, port and access token to instance.json in the state folder (paths.py), readable by its owner
only, and removes it on the way out. A file left behind by a crash, or a pid since reused by another program, is
caught by asking the server at that port for its pid with the token.
"""
import json
import os
import urllib.request
from pathlib import Path

from tidyarr import paths

PATH = paths.STATE_DIR / "instance.json"
LOG = paths.STATE_DIR / "server.log"


def url(info):
    return f"http://127.0.0.1:{info['port']}/#token={info['token']}"


def write(port, token, path=None):
    path = Path(path or PATH)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"pid": os.getpid(), "port": port, "token": token}, f)


def clear(path=None):
    """Remove the file, if it is this process's (a newer server may have replaced it)."""
    path = Path(path or PATH)
    try:
        if json.loads(path.read_text()).get("pid") == os.getpid():
            path.unlink()
    except (OSError, ValueError, AttributeError):
        pass


def ask(info, endpoint, method="GET", timeout=2):
    request = urllib.request.Request(f"http://127.0.0.1:{info['port']}/api/{endpoint}", method=method,
                                     headers={"X-Token": info["token"]})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read() or b"{}")


def find(path=None):
    """The running server's {pid, port, token}, or None if there isn't one."""
    try:
        info = json.loads(Path(path or PATH).read_text())
        return info if ask(info, "ping").get("pid") == info["pid"] else None
    except (OSError, ValueError, KeyError, TypeError, AttributeError):  # no file, a bad one, or nothing answering
        return None
