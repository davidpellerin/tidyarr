"""Where a classified file would go: a naming format like Sonarr's and Radarr's, filled in from the decision.

    Series/{SHOW_NAME}/Season {SEASON_NUMBER}/S{SEASON_NUMBER}E{EPISODE_NUMBER} - {EPISODE_NAME}.{EXTENSION}

The format is relative to the output folder and is typed by a person, so it is checked before use: no absolute or `~`
paths, no `..`, only known {TOKENS}. Values come from TMDB and the filename, so they are cleaned (no slashes or other
characters a file name can't hold) before they are put in; the result is always a path of plain names below the output
folder. Nothing here touches the disk.
"""
import re
from pathlib import PurePosixPath

DEFAULT_TV = "Series/{SHOW_NAME}/Season {SEASON_NUMBER}/S{SEASON_NUMBER}E{EPISODE_NUMBER} - {EPISODE_NAME}.{EXTENSION}"
DEFAULT_MOVIE = "Movies/{MOVIE_NAME} ({MOVIE_YEAR})/{MOVIE_NAME} ({MOVIE_YEAR}).{EXTENSION}"
TOKENS = {
    "tv": ("SHOW_NAME", "SHOW_YEAR", "SEASON_NUMBER", "EPISODE_NUMBER", "EPISODE_NAME", "EXTENSION"),
    "movie": ("MOVIE_NAME", "MOVIE_YEAR", "EXTENSION"),
}
MAX_FORMAT = 300
MAX_NAME = 255
FORBIDDEN = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
TOKEN = re.compile(r"\{([^{}]*)\}")


def braced(tokens):
    return ", ".join("{" + t + "}" for t in tokens)


def check(template, kind):
    """Raise ValueError (with a message fit to show) unless `template` is a usable format for this kind."""
    if not template.strip():
        raise ValueError("the naming format is empty")
    if len(template) > MAX_FORMAT:
        raise ValueError("the naming format is too long")
    if template.startswith(("/", "~")) or "\\" in template:
        raise ValueError("the naming format is relative to the output folder: don't start it with / or ~")
    if ".." in template.split("/"):
        raise ValueError("the naming format may not contain '..'")
    unknown = [t for t in TOKEN.findall(template) if t not in TOKENS[kind]]
    if unknown:
        raise ValueError(f"unknown {{{unknown[0]}}} for this kind of file; use {braced(TOKENS[kind])}")
    if re.search(r"[{}]", TOKEN.sub("", template)):
        raise ValueError("the naming format has a { without a matching } (or the other way round)")
    dollar = re.search(r"\$([A-Z_]+)", template)
    if dollar:
        raise ValueError(f"write ${dollar.group(1)} as {{{dollar.group(1)}}}")


def clean(value):
    """One value made safe as part of a file or folder name."""
    value = FORBIDDEN.sub("", str(value).replace(":", " -"))
    return " ".join(value.split()).strip(". ")


def values(ident, extension, episode_name=None):
    """The {TOKEN} values for one decision (`ident` is the stored TMDB identity)."""
    year = "" if ident.get("year") is None else str(ident["year"])
    common = {"EXTENSION": clean(extension.lstrip("."))}
    if ident["kind"] == "tv":
        return {**common, "SHOW_NAME": clean(ident["title"]), "SHOW_YEAR": year,
                "SEASON_NUMBER": f"{ident['season']:02d}" if ident.get("season") is not None else "",
                "EPISODE_NUMBER": f"{ident['episode']:02d}" if ident.get("episode") is not None else "",
                "EPISODE_NAME": clean(episode_name or "")}
    return {**common, "MOVIE_NAME": clean(ident["title"]), "MOVIE_YEAR": year}


def uses(template, kind, token):
    return token in TOKEN.findall(template)


def render(template, kind, filled):
    """The relative path `template` gives for these values, as a PurePosixPath. ValueError if it can't be a safe path."""
    check(template, kind)
    missing = [t for t in TOKEN.findall(template) if not filled.get(t)]
    if missing:
        raise ValueError(f"no value for {{{missing[0]}}} for this file")
    path = TOKEN.sub(lambda m: filled[m.group(1)], template)
    parts = [part.strip() for part in path.split("/")]
    if any(part in ("", ".", "..") or len(part.encode()) > MAX_NAME for part in parts):
        raise ValueError("the naming format gives an empty or too-long folder or file name")
    return PurePosixPath(*parts)
