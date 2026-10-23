"""Subtitle files that belong to a video, so they can be moved with it and given its new name.

A subtitle belongs to a video when it sits beside it and its name starts with the video's name and a dot
(Show.S01E01.en.forced.srt), or when it is in a Subs/Subtitles folder beside it: under a folder named after the video
(Subs/Show.S01E01/2_English.srt, as season packs do), or loose in it when the video is the only one in its folder.
What followed the video's name (.en.forced.srt) is kept; for a file in a Subs folder that is its own name, without a
leading track number (.English.srt). Nothing here touches the disk beyond listing folders.
"""
import re
from pathlib import Path

from tidyarr.inbox import VIDEO_EXTENSIONS

EXTENSIONS = {".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx", ".sup"}
FOLDERS = {"subs", "subtitles"}
TRACK = re.compile(r"^\d+[_ .-]+")


def _files(folder):
    try:
        return sorted(p for p in folder.iterdir() if p.is_file() and not p.is_symlink())
    except OSError:
        return []


def find(video):
    """[(subtitle path, tail)] for the video at `video`, where tail is what follows the name (".en.srt")."""
    video = Path(video)
    siblings = _files(video.parent)
    stems = [p.stem for p in siblings if p.suffix.lower() in VIDEO_EXTENSIONS]
    found = []
    for sub in siblings:
        if sub.suffix.lower() not in EXTENSIONS or not sub.name.startswith(video.stem + "."):
            continue
        # Show.S01E01.Extended.en.srt belongs to Show.S01E01.Extended.mkv, not Show.S01E01.mkv: the longest name wins.
        owner = max((s for s in stems if sub.name.startswith(s + ".")), key=len)
        if owner == video.stem:
            found.append((sub, sub.name[len(video.stem):]))
    for folder in (p for p in sorted(video.parent.iterdir()) if p.name.lower() in FOLDERS and p.is_dir() and not p.is_symlink()):
        own = folder / video.stem
        loose = _files(own) if own.is_dir() and not own.is_symlink() else _files(folder) if stems == [video.stem] else []
        subs = [p for p in loose if p.suffix.lower() in EXTENSIONS]
        tails = ["." + (TRACK.sub("", p.stem) or p.stem) + p.suffix for p in subs]
        # Two tracks that are both "English" once their numbers are gone keep their names whole, so neither is lost.
        found += [(p, tail if tails.count(tail) == 1 else "." + p.name) for p, tail in zip(subs, tails)]
    return found


def renamed(target, tail):
    """Where a subtitle goes when its video goes to `target`: beside it, with the video's new name."""
    return target.with_name(target.stem + tail)
