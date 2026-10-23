"""Deciding what a file is from its local evidence, accepting an answer only when TMDB independently confirms it.

`evaluate` gathers the evidence for one file, verifies each source through TMDB (`assess`) and scores how many agree.
A clear answer is accepted automatically; anything else waits for a person in the review page.
"""
from tidyarr import calls, tmdb
from tidyarr.evidence import consensus, gather, norm_title as _norm
from tidyarr.guess import guess


def auto_resolve(g, duration=None):
    """`g` is guessit's parse (see tidyarr.guess). Return a TMDB identity (see tmdb.identity) or None.

    `duration` (the file's length in seconds) breaks a tie between movies sharing a title and year: only those whose
    TMDB runtime fits the file count, and one survivor wins."""
    title = g.get("title")
    if not isinstance(title, str):
        return None
    season, episode, year = g.get("season"), g.get("episode"), g.get("year")

    if g.get("type") == "movie" and isinstance(year, int):
        matches = [c for c in tmdb.search(title, "movie", year=year, limit=5) if _norm(c["title"]) == _norm(title) and c["year"] == year]
        if len(matches) > 1 and duration:
            matches = [c for c in matches if _fits_runtime(duration, tmdb.runtime_minutes(tmdb.identity(c)))]
        return tmdb.identity(matches[0]) if len(matches) == 1 else None

    if g.get("type") == "episode" and isinstance(season, int) and isinstance(episode, int):
        candidates = [c for c in tmdb.search(title, "tv", country=g.get("country_code"), limit=5) if _norm(c["title"]) == _norm(title)]
        if g.get("country_code"):
            candidates = [c for c in candidates if g["country_code"] in c["countries"]]
        if isinstance(year, int):
            candidates = [c for c in candidates if c["year"] == year]
        # Several shows can share a name; the one that actually has this episode wins, if exactly one does.
        with_episode = [c for c in candidates if tmdb.episode_title(c["id"], season, episode)]
        return tmdb.identity(with_episode[0], season, episode) if len(with_episode) == 1 else None

    return None


AUTO_THRESHOLD = 0.5  # share of the sources that spoke which must back the answer for it to be accepted without a human


RUNTIME_TOLERANCE = 0.25  # how far the file's length may stray from TMDB's runtime (ads, credits, extended cuts)


def _fits_runtime(duration, expected):
    return bool(expected) and abs(duration / 60 - expected) <= expected * RUNTIME_TOLERANCE


def runtime_check(duration, identity):
    """Compare the file's length with TMDB's runtime for the identity.

    Returns {'expected': minutes or None, 'actual': minutes, 'ok': bool or None, 'estimate': None, 'season' or
    'show'}, or None when the file's length is unknown. When TMDB has no runtime for the episode, `expected` may be an
    estimate from the rest of the season or the show (shown to the person, never used to decide): `ok` is then None.
    `ok` None never blocks acceptance: the check only adds a reason to doubt, it isn't required evidence.
    """
    if not duration:
        return None
    expected = tmdb.runtime_minutes(identity)
    actual = duration / 60
    if not expected:
        guess = tmdb.runtime_estimate(identity)
        return {"expected": guess[0] if guess else None, "actual": round(actual, 1), "ok": None, "estimate": guess[1] if guess else None}
    return {"expected": expected, "actual": round(actual, 1), "ok": abs(actual - expected) <= expected * RUNTIME_TOLERANCE, "estimate": None}


def episode_numbers(ev):
    for name in ("metadata", "guessit"):
        op = ev.get(name)
        if op and op.get("season") is not None and op.get("episode") is not None:
            return op["season"], op["episode"]
    return None, None


def assess(ev):
    """Verify each local source through TMDB. `ev` is evidence.gather's result.

    Returns {'identity': the one identity all verifying sources agree on or None, 'conflict': True if verified
    sources point at different things, 'nfo': the NFO's opinion or None}. Priority when they agree: nfo,
    metadata, filename.
    """
    resolved = {}
    candidate = nfo_candidate(ev)
    nfo_op = nfo_opinion(candidate)
    if candidate:
        if candidate["kind"] == "movie":
            resolved["nfo"] = tmdb.identity(candidate)
        else:  # the NFO names the show; the episode numbers come from the other sources and must exist on TMDB
            season, episode = episode_numbers(ev)
            if season is not None and tmdb.episode_title(candidate["id"], season, episode):
                resolved["nfo"] = tmdb.identity(candidate, season, episode)
    for name in ("metadata", "guessit"):
        parse = ev.get(f"{name}_parse")
        identity = auto_resolve(parse, ev.get("duration")) if parse else None
        if identity:
            resolved[name] = identity
    distinct = {(i["kind"], i["id"], i.get("season"), i.get("episode")) for i in resolved.values()}
    return {"identity": next(iter(resolved.values())) if len(distinct) == 1 else None, "conflict": len(distinct) > 1, "nfo": nfo_op}


def nfo_candidate(ev):
    """The TMDB entry the sidecar NFO points at (by TMDB id, else IMDb id), or None."""
    ids = ev.get("nfo_ids")
    if not ids:
        return None
    candidate = tmdb.details(*ids["tmdb"]) if ids.get("tmdb") else None
    if candidate is None and ids.get("imdb"):
        candidate = tmdb.find_by_imdb(ids["imdb"])
    return candidate


def nfo_opinion(candidate):
    if not candidate:
        return None
    return {"kind": candidate["kind"], "title": candidate["title"], "year": candidate["year"], "season": None, "episode": None}


def percent(agreeing, spoke):
    return f"{100 * agreeing // spoke}%" if spoke else "n/a"


def _nfo_json(ev):
    ids = ev.get("nfo_ids")
    if not ev.get("nfo_file"):
        return None
    tmdb_ref = ids.get("tmdb") if ids else None
    return {"file": ev["nfo_file"], "tmdb": {"kind": tmdb_ref[0], "id": tmdb_ref[1]} if tmdb_ref else None, "imdb": ids.get("imdb") if ids else None}


def evidence_record(ev, opinions, agreeing, spoke, extra_calls=None):
    """Structured evidence stored with a decision (version 1): what every source said, as fields, plus the
    raw material. `extra_calls` is the transcript of anything run after the first evaluation (candidate
    searches). Display strings are derived from this, never stored."""
    return {
        "version": 1,
        "sources": {"nfo": opinions["nfo"], "metadata": opinions["metadata"], "guessit": opinions["guessit"]},
        "consensus": {"agreeing": agreeing, "spoke": spoke},
        "raw": {"guessit": ev.get("guessit_parse"), "metadata_guess": ev.get("metadata_parse"), "nfo": _nfo_json(ev), "probe": ev.get("probe"),
                "calls": list(ev.get("calls") or []) + list(extra_calls or [])},
    }


def evaluate(path, base_dir=None, g=None):
    """Run the whole evaluation for one file: gather, verify through TMDB, score, decide.

    Returns {'ev', 'opinions', 'verdict', 'score': (agreeing, spoke) or None, 'runtime': runtime_check's result,
    'auto': True if it can be accepted without a person}.
    """
    with calls.capture() as log:  # the transcript of guessit, ffprobe and the TMDB verification, for display
        ev = gather(path, base_dir, g=g or guess(path))
        verdict = assess(ev)
        runtime = runtime_check(ev.get("duration"), verdict["identity"]) if verdict["identity"] else None
    ev["calls"] = log
    opinions = {"nfo": verdict["nfo"], "metadata": ev["metadata"], "guessit": ev["guessit"]}
    score, auto = None, False
    if verdict["identity"]:
        agreeing, spoke = consensus(opinions, verdict["identity"])
        score, auto = (agreeing, spoke), agreeing / spoke >= AUTO_THRESHOLD and (runtime is None or runtime["ok"] is not False)
    return {"ev": ev, "opinions": opinions, "verdict": verdict, "score": score, "auto": auto, "runtime": runtime}
