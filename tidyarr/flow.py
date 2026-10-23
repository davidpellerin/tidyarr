"""A Mermaid diagram of how what we know about one file was built up, tool by tool.

The filename is parsed by guessit; ffprobe reads the title tag inside the file, which guessit parses again; an .nfo may
name the title on TMDB; TMDB verifies what the sources claim; a person (or the agreement of the sources) makes the final
call. Every label comes from the file or from an external service, so all text goes through `label` before it reaches the
diagram, and the page renders it in Mermaid's strict mode.
"""
import json
import re
import urllib.parse

from tidyarr import tmdb
from tidyarr.evidence import agrees, format_opinion

MAX_LABEL = 300  # only a guard against absurd input; real titles and paths are shown whole
NAMES = {"guessit": "guessit + filename", "metadata": "guessit + title tag", "nfo": "the .nfo"}


def label(text):
    """Text that is safe inside a quoted Mermaid label: no quotes, brackets or markup, and not unreasonably long."""
    text = " ".join(str(text).split())
    if len(text) > MAX_LABEL:
        text = text[:MAX_LABEL - 1] + "…"
    return "".join(f"#{ord(c)};" if c in "\"'`<>&[](){}|#;\\" else c for c in text)


def node(name, heading, *lines, shape="rect"):
    body = "<br/>".join([f"<b>{label(heading)}</b>", *(label(line) for line in lines)])
    return f'{name}{{{{"{body}"}}}}' if shape == "hex" else f'{name}("{body}")'


SEARCH = re.compile(r"/search/(tv|movie)")
EPISODE = re.compile(r"/tv/(\d+)/season/(\d+)/episode/(\d+)")
DETAILS = re.compile(r"/(tv|movie)/(\d+)")
KIND_WORDS = {"tv": "show", "movie": "film"}


def tmdb_calls(raw):
    """The TMDB requests that were made for a file, in order (the diagram draws one box for each)."""
    return [c for c in (raw or {}).get("calls") or [] if c.get("tool") == "tmdb"]


def reply(call):
    try:
        data = json.loads(call.get("output") or "")
    except ValueError:  # an over-long reply is cut off for display; there is nothing to summarise
        return {}
    return data if isinstance(data, dict) else {}


def named(item):
    year = (item.get("first_air_date") or item.get("release_date") or "")[:4]
    return f"{item.get('name') or item.get('title') or '?'}" + (f" ({year})" if year else "") + f" [id {item.get('id')}]"


def describe_tmdb(call):
    """(heading, lines) saying what one TMDB request asked and what came back, in plain words."""
    path, ok = call.get("label") or "", str(call.get("status")) == "HTTP 200"
    data = reply(call)
    if m := SEARCH.fullmatch(path):
        query = (urllib.parse.parse_qs(urllib.parse.urlparse(call.get("command", "").split(" ", 1)[-1]).query).get("query") or ["?"])[0]
        results = [r for r in data.get("results") or [] if isinstance(r, dict)]
        total = data.get("total_results", len(results))
        found = ", ".join(named(r) for r in results[:3]) + (f" and {total - 3} more" if total > 3 else "") if results else "nothing found"
        return f"TMDB: Found the {KIND_WORDS[m[1]]} ID from the name", [f"searched for “{query}”", found]
    if m := EPISODE.fullmatch(path):
        outcome = f"“{data.get('name')}”" if ok and data.get("name") else "no such episode"
        return "TMDB: Checked the episode ID exists", [f"show id {m[1]}, season {m[2]}, episode {m[3]}", outcome]
    if m := DETAILS.fullmatch(path):
        outcome = named(data) if ok and data else "not found"
        return f"TMDB: Fetched the {KIND_WORDS[m[1]]} details", [f"id {m[2]}", outcome]
    return "TMDB request", [path or "?"]


def episode_origin(sources, final):
    """Where the season and episode numbers came from: TMDB results carry none, so they were read from the title tag or the
    filename (the title tag first, as everywhere else) and then checked against the chosen show. None for a film."""
    if final.get("season") is None or final.get("episode") is None:
        return None
    for name, where in (("metadata", "the title tag"), ("guessit", "the filename")):
        op = sources.get(name)
        if isinstance(op, dict) and (op.get("season"), op.get("episode")) == (final["season"], final["episode"]):
            return where
    return "the chosen TMDB episode"


def diagram(path, sources, final, raw, decision_source=None):
    """`sources` are the opinions by name (nfo, metadata, guessit), `final` the chosen TMDB identity or None,
    `raw` the evidence's raw material (guessit's parse, ffprobe's result, the nfo)."""
    raw = raw or {}
    lines = ["flowchart TD"]
    classes = {"agree": [], "conflict": [], "silent": []}

    def verdict(name, opinion):
        if isinstance(opinion, dict) and final:
            classes["agree" if agrees(opinion, final) else "conflict"].append(name)
        elif not isinstance(opinion, dict):
            classes["silent"].append(name)

    lines.append("  " + node("file", "Filename", path))
    guessit = sources.get("guessit")
    lines.append("  " + node("guessit", "guessit + filename", format_opinion(guessit) or "nothing found"))
    lines.append("  file --> guessit")
    verdict("guessit", guessit)

    tag = (raw.get("probe") or {}).get("title")
    lines.append("  " + node("probe", "ffprobe + filename", f"found title tag: {tag}" if tag else "no title tag found"))
    lines.append("  file --> probe")
    metadata = sources.get("metadata")
    if tag:
        lines.append("  " + node("metadata", "guessit + title tag", format_opinion(metadata) or "nothing found"))
        lines.append("  probe --> metadata")
        verdict("metadata", metadata)
    else:
        classes["silent"].append("probe")

    nfo, nfo_raw = sources.get("nfo"), raw.get("nfo")
    if nfo_raw:
        ref = nfo_raw.get("tmdb")
        ids = f"TMDB {ref['kind']} {ref['id']}" if ref else f"IMDb {nfo_raw['imdb']}" if nfo_raw.get("imdb") else "no ids"
        lines.append("  " + node("nfo", f"Sidecar {nfo_raw['file']}", format_opinion(nfo) or ids))
        lines.append("  file --> nfo")
        verdict("nfo", nfo)

    sources_out = [name for name, present in (("guessit", True), ("metadata", bool(tag)), ("nfo", bool(nfo_raw))) if present]
    steps = tmdb_calls(raw)
    if steps:  # one box per request, in the order they were made: a true picture of how TMDB was asked
        previous = None
        for number, call in enumerate(steps, 1):
            heading, detail = describe_tmdb(call)
            lines.append("  " + node(f"tmdb{number}", heading, *detail, shape="hex"))
            for name in (sources_out if previous is None else [previous]):
                lines.append(f"  {name} --> tmdb{number}")
            previous = f"tmdb{number}"
        last = previous
    else:  # nothing needed TMDB (no title was found to look up)
        lines.append("  " + node("tmdb", "TMDB verifies", tmdb.render(final) if final else "no single match yet", shape="hex"))
        lines += [f"  {name} --> tmdb" for name in sources_out]
        last = "tmdb"

    if final:
        order = ("guessit", "metadata", "nfo")  # the order the steps are drawn in
        verdicts = {name: agrees(sources[name], final) for name in order if isinstance(sources.get(name), dict)}
        heading = "Human chose from TMDB" if decision_source == "human" else "Accepted automatically: the sources agreed"
        shown = [tmdb.clean_name(final)]  # the show or film, as chosen; the episode is not something a person picks
        where = episode_origin(sources, final)
        if where:
            shown.append(f"Season {final['season']} episode {final['episode']}, taken from {where}")
        who = []
        for text, wanted in (("Agreed", True), ("Disagreed", False)):
            names = [NAMES.get(name, name) for name, v in verdicts.items() if v is wanted]
            if names:
                who.append(f"{text}: {', '.join(names)}")
        silent = [NAMES.get(name, name) for name in order if name not in verdicts]
        if silent:
            who.append(f"No opinion: {', '.join(silent)}")
        lines.append("  " + node("final", heading, *shown, *who))
        lines.append(f"  {last} --> final")
    else:
        lines.append("  " + node("final", "Needs a decision", "pick a TMDB result below"))
        lines.append(f"  {last} --> final")

    lines += ["  classDef agree fill:#12301e,stroke:#50c864,color:#d7f5df",
              "  classDef conflict fill:#3a1613,stroke:#eb4034,color:#ffd9d5",
              "  classDef silent fill:#1c1f24,stroke:#555,color:#999,stroke-dasharray:4 3"]
    lines += [f"  class {','.join(names)} {kind}" for kind, names in classes.items() if names]
    return "\n".join(lines)
