"""Finding the files to classify, and telling which are still being written (a download in progress)."""
import os
import time
from pathlib import Path

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".ts", ".wmv"}


def video_files(folder):
    """Relative paths of the video files under `folder`, sorted."""
    folder = Path(folder)
    return sorted(str(f.relative_to(folder)) for f in folder.rglob("*") if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS)


RECENTLY_WRITTEN_SECONDS = 3  # changed this recently: still arriving, even if no writer is seen (one we can't see writes steadily)


def open_for_writing(paths):
    """Those of `paths` that some process has open for writing, from /proc (Linux). Only processes this user may look
    into are seen, so a writer running as another user, in a container or on another machine is missed."""
    wanted, found = {os.path.realpath(p) for p in paths}, set()
    try:
        pids = [pid for pid in os.listdir("/proc") if pid.isdigit()]
    except OSError:
        return found  # no /proc here
    for pid in pids:
        try:
            fds = os.listdir(f"/proc/{pid}/fd")
        except OSError:
            continue  # gone, or not ours to look into
        for fd in fds:
            try:
                target = os.readlink(f"/proc/{pid}/fd/{fd}")
                if target not in wanted or target in found:
                    continue
                with open(f"/proc/{pid}/fdinfo/{fd}") as info:
                    flags = next(int(line.split()[1], 8) for line in info if line.startswith("flags:"))
            except (OSError, StopIteration, ValueError):
                continue
            if flags & (os.O_WRONLY | os.O_RDWR):
                found.add(target)
    return found


def being_written(paths, now=None):
    """Those of `paths` we think are still being written: open for writing somewhere, or changed in the last few seconds."""
    now = time.time() if now is None else now
    writing = open_for_writing(paths)
    result = set()
    for path in paths:
        try:
            recent = now - os.stat(path).st_mtime < RECENTLY_WRITTEN_SECONDS
        except OSError:
            continue
        if recent or os.path.realpath(path) in writing:
            result.add(path)
    return result
