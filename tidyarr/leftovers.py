"""What a release folder still holds once its videos have been moved out, and putting the junk in it in the trash.

A release folder is the folder directly under the inbox that a video came from; a video loose in the inbox has none, so
nothing beside it is ever offered. Once a release folder holds no video, its files of a junk type (config.py's
junk_extensions, and subtitles, since any still there have no video) can go to the system trash, where they can be got
back, and the folders left empty are removed. Anything else is listed but kept, and so are the folders holding it.
Symlinks are never followed or removed. Only ever done when a person asks, for the exact list they were shown; the one
exception is a release folder holding nothing at all, which the "remove empty folders after a move" setting removes.
"""
import os
from pathlib import Path

from send2trash import send2trash

from tidyarr import subtitles
from tidyarr.inbox import VIDEO_EXTENSIONS


def release_folder(base_dir, relative):
    """The folder under `base_dir` that the file at `relative` (to it) came from, or None for a file loose in it."""
    parts = Path(relative).parts
    return Path(base_dir) / parts[0] if len(parts) > 1 else None


def survey(folder, junk):
    """{'videos': how many are left, 'junk': [...], 'kept': [...]}, as sorted paths relative to `folder`."""
    folder, junk = Path(folder), {e.lower() for e in junk} | subtitles.EXTENSIONS
    out = {"videos": 0, "junk": [], "kept": []}
    for dirpath, dirnames, filenames in os.walk(folder):  # doesn't descend into symlinked folders
        here = Path(dirpath)
        for name in [*filenames, *(d for d in dirnames if (here / d).is_symlink())]:
            path = here / name
            suffix = path.suffix.lower()
            if suffix in VIDEO_EXTENSIONS:
                out["videos"] += 1
            else:
                out["junk" if suffix in junk and not path.is_symlink() else "kept"].append(path.relative_to(folder).as_posix())
    out["junk"].sort()
    out["kept"].sort()
    return out


def worth_offering(found):
    """Whether cleaning would do anything: no video left, and junk to trash or nothing at all to keep (empty folders)."""
    return not found["videos"] and bool(found["junk"] or not found["kept"])


def clean(folder, junk, shown):
    """Trash the junk in `folder` and remove the folders left empty. `shown` is the junk list the person approved;
    if the folder no longer holds exactly that (or a video has appeared), nothing is touched."""
    folder = Path(folder)
    found = survey(folder, junk)
    if found["videos"]:
        raise ValueError("there is a video in that folder again, so it was left alone")
    if found["junk"] != list(shown):
        raise ValueError("that folder has changed since it was shown; look again")
    for name in found["junk"]:
        send2trash(str(folder / name))
    _remove_empty(folder)
    return found


def remove_if_empty(folder):
    """Remove `folder` if nothing but empty folders is left in it (no file of any kind, no symlink). Whether it went."""
    folder = Path(folder)
    if not folder.is_dir() or folder.is_symlink() or survey(folder, []) != {"videos": 0, "junk": [], "kept": []}:
        return False
    _remove_empty(folder)
    return not folder.exists()


def _remove_empty(folder):
    for dirpath, _, _ in os.walk(folder, topdown=False):  # deepest first, and the release folder itself last
        try:
            os.rmdir(dirpath)  # only ever removes an empty folder
        except OSError:
            pass
