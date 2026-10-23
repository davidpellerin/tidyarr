"""Evidence from inside the file: container tags, duration and streams, read with `ffprobe`.

Everything returned is untrusted text from the file. It is compared and displayed, never obeyed.
"""
import json
import subprocess
import time
from pathlib import Path

from tidyarr import calls


def probe(file, timeout=30):
    """Return {'format', 'duration', 'size', 'title', 'tags', 'video', 'audio', 'subtitles'} for a media file.

    `title` is the container's title tag or None; `duration` is seconds or None; `video` is e.g.
    '1920x1080 hevc'; `audio` and `subtitles` are lists of language codes ('und' when unlabeled).
    Raises RuntimeError (with a message that is safe to print) if ffprobe is missing or fails.
    """
    command = ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", "-i", str(Path(file).resolve())]
    started = time.monotonic()
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        calls.record("ffprobe", "file info", command, proc.stdout, time.monotonic() - started, proc.returncode)
    except FileNotFoundError:
        raise RuntimeError("ffprobe is not installed (it comes with ffmpeg)") from None
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"ffprobe took longer than {timeout}s") from None
    if proc.returncode != 0:
        detail = proc.stderr.strip().splitlines()[0] if proc.stderr.strip() else f"exit {proc.returncode}"
        raise RuntimeError(f"ffprobe could not read the file: {detail}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise RuntimeError("ffprobe returned output that is not JSON") from None

    fmt = data.get("format", {})
    tags = {str(k).casefold(): str(v) for k, v in (fmt.get("tags") or {}).items()}  # tag-name case varies by container
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    language = lambda s: (s.get("tags") or {}).get("language", "und")  # noqa: E731
    return {
        "format": fmt.get("format_name"),
        "duration": float(fmt["duration"]) if fmt.get("duration") else None,
        "size": int(fmt["size"]) if fmt.get("size") else None,
        "title": tags.get("title", "").strip() or None,
        "tags": tags,
        "video": f"{video.get('width', '?')}x{video.get('height', '?')} {video.get('codec_name', '?')}" if video else None,
        "audio": [language(s) for s in streams if s.get("codec_type") == "audio"],
        "subtitles": [language(s) for s in streams if s.get("codec_type") == "subtitle"],
    }


def format_duration(seconds):
    """'44m 00s', or '1h 02m 03s' for long files; '?' when unknown."""
    if seconds is None:
        return "?"
    minutes, secs = divmod(int(round(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m {secs:02d}s" if hours else f"{minutes}m {secs:02d}s"
