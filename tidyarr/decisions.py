"""Remembered decisions: once a file is classified, it is never asked about again.

data/decisions.json maps a key to a record:
    {"path_hint": "Mr.Robot.S02E03.720p.mkv", "source": "auto" | "human",
     "decided_at": "2026-10-16",
     "tmdb": {"kind": "tv", "id": 62560, "title": "Mr. Robot", "year": 2015, "season": 2, "episode": 3}}
The key is a fingerprint of the file's content ("fp:...") so a decision survives the file being moved
or renamed; if the file isn't on disk it falls back to the path ("path:..."). Only the identity is
stored: the display name is derived from it (render), so changing the naming format never means
re-deciding. This file is the human-confirmed ground truth, so it is replaced in one step (a crash never leaves
half a file) and the previous version is kept beside it as decisions.json.bak.
"""
import datetime
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from tidyarr import paths, tmdb

DEFAULT_PATH = paths.DATA_DIR / "decisions.json"
CHUNK = 1024 * 1024


def fingerprint(file):
    """Size plus a hash of the first and last megabyte: cheap, and stable across moves and renames."""
    file = Path(file)
    size = file.stat().st_size
    digest = hashlib.sha256(str(size).encode())
    with open(file, "rb") as f:
        digest.update(f.read(CHUNK))
        if size > CHUNK:
            f.seek(max(CHUNK, size - CHUNK))  # don't re-read bytes already hashed in a small file
            digest.update(f.read(CHUNK))
    return digest.hexdigest()[:32]


def key_for(path, base_dir=None):
    file = Path(base_dir or ".") / path
    return f"fp:{fingerprint(file)}" if file.is_file() else f"path:{path}"


def today():
    return datetime.date.today().isoformat()


def make_record(path, identity, source, decided_at=None, evidence=None):
    record = {
        "path_hint": path,
        "source": source,
        "decided_at": decided_at or today(),
        "tmdb": identity,
    }
    if evidence:  # what each source said, plus the consensus score
        record["evidence"] = evidence
    return record


def render(record):
    return tmdb.render(record["tmdb"])


def backup_path(path):
    path = Path(path)
    return path.with_name(path.name + ".bak")


def load(path=DEFAULT_PATH):
    path = Path(path)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except ValueError:  # bad JSON or bad UTF-8: never start over silently, as the next save would erase every decision
        raise RuntimeError(f"{path} is not valid JSON; the previous version is in {backup_path(path)}") from None


def save(decisions, path=DEFAULT_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".decisions-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(decisions, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
        if path.exists():
            shutil.copyfile(path, backup_path(path))
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise
