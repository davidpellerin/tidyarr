"""Independent evidence for what a file is: its filename, its container title tag and a sidecar .nfo.

Each source yields an *opinion* {kind, title, year, season, episode} (any field may be None). TMDB verifies
them (see triage.assess); the share of sources that back the final answer is the consensus score.
All text from files is untrusted: it is compared and displayed, never obeyed.
"""
import re
from pathlib import Path

from tidyarr import probe
from tidyarr.guess import guess
from tidyarr.inbox import VIDEO_EXTENSIONS

IMDB_URL = re.compile(r"imdb\.com/title/(tt\d{5,10})", re.I)
TMDB_URL = re.compile(r"themoviedb\.org/(movie|tv)/(\d{1,9})", re.I)
NFO_LIMIT = 200_000


def norm_title(title):
    """Loose title comparison: case, punctuation and a leading 'The' don't matter."""
    words = re.sub(r"[^a-z0-9 ]", "", title.casefold().replace("&", "and")).split()
    return " ".join(words[1:] if words[:1] == ["the"] else words)


def find_nfo(file):
    """The .nfo that belongs to this video: same name, or the only .nfo beside the only video in its folder."""
    file = Path(file)
    same = file.with_suffix(".nfo")
    if same.is_file():
        return same
    siblings = [f for f in file.parent.iterdir() if f.is_file()]
    videos = [f for f in siblings if f.suffix.lower() in VIDEO_EXTENSIONS]
    nfos = [f for f in siblings if f.suffix.lower() == ".nfo"]
    return nfos[0] if len(videos) == 1 and len(nfos) == 1 else None


def parse_nfo(text):
    """Return {'tmdb': (kind, id) or None, 'imdb': 'tt...' or None}, or None if the text has neither."""
    tmdb_match, imdb_match = TMDB_URL.search(text), IMDB_URL.search(text)
    if not tmdb_match and not imdb_match:
        return None
    return {
        "tmdb": (tmdb_match.group(1).lower(), int(tmdb_match.group(2))) if tmdb_match else None,
        "imdb": imdb_match.group(1).lower() if imdb_match else None,
    }


def read_nfo(path):
    return Path(path).read_bytes()[:NFO_LIMIT].decode("latin-1")  # NFOs are usually CP437 art; the URLs are plain ASCII


def opinion_from_guess(g):
    if not g or not isinstance(g.get("title"), str):
        return None
    number = lambda v: v if isinstance(v, int) else None  # noqa: E731  (guessit gives lists for multi-episode files)
    return {"kind": "tv" if g.get("type") == "episode" else "movie", "title": g["title"], "year": number(g.get("year")),
            "season": number(g.get("season")), "episode": number(g.get("episode"))}


def format_opinion(op):
    """'Title (Year) S01E02' for display; None if there is no opinion."""
    if not op:
        return None
    text = op["title"] + (f" ({op['year']})" if op.get("year") else "")
    if op.get("season") is not None and op.get("episode") is not None:
        text += f" S{op['season']:02d}E{op['episode']:02d}"
    elif op.get("episode") is not None:
        text += f" E{op['episode']:02d}"
    return text


def agrees(op, identity):
    """Does this opinion back the final identity? Titles must match; the type (episode or movie) and the numbers only
    count if both sides have them."""
    if norm_title(op["title"]) != norm_title(identity["title"]):
        return False
    return all(op.get(k) is None or identity.get(k) is None or op[k] == identity[k] for k in ("kind", "year", "season", "episode"))


def consensus(opinions, identity):
    """(agreeing, spoke) over the sources that offered an opinion; the score is agreeing / spoke."""
    spoke = [op for op in opinions.values() if isinstance(op, dict)]  # a source with nothing to say is None
    return sum(agrees(op, identity) for op in spoke), len(spoke)


def gather(path, base_dir=None, g=None):
    """Collect the local evidence for one file (no network). `g` is guessit's parse of the filename if known."""
    file = Path(base_dir or ".") / path
    filename_parse = g or guess(path)
    ev = {"guessit_parse": filename_parse, "guessit": opinion_from_guess(filename_parse), "metadata_title": None,
          "metadata_parse": None, "metadata": None, "nfo_file": None, "nfo_ids": None, "duration": None, "probe": None, "errors": []}
    if not file.is_file():
        return ev
    nfo = find_nfo(file)
    if nfo:
        ev["nfo_file"], ev["nfo_ids"] = nfo.name, parse_nfo(read_nfo(nfo))
    try:
        info = probe.probe(file)
    except RuntimeError as e:
        ev["errors"].append(str(e))
        return ev
    ev["duration"], ev["probe"] = info["duration"], info
    if info["title"]:
        ev["metadata_title"] = info["title"]
        ev["metadata_parse"] = guess(info["title"], label="container title")
        ev["metadata"] = opinion_from_guess(ev["metadata_parse"])
    return ev
