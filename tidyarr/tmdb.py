"""Catalog lookup: search TMDB for candidate titles and check that an episode exists.

The API key comes from the TMDB_API_KEY environment variable if set, otherwise from the settings file (where the
page saves it, see config.py). It is never logged, put in an error message, or sent back to the page: the page only
learns where the key comes from and its last four characters.
"""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from tidyarr import calls, config

API = "https://api.themoviedb.org/3"


KEY_PATTERN = re.compile(r"[0-9a-fA-F]{32}")  # a v3 "API Key"; the longer "Read Access Token" is a different thing


def find_key():
    """(key, where it came from), or ("", None). The settings file is read each time, so a key saved there applies at once."""
    for source, read in (("TMDB_API_KEY", lambda: os.environ.get("TMDB_API_KEY", "")),
                         ("settings", lambda: config.load().get(config.KEY, ""))):
        key = read().strip()
        if key:
            return key, source
    return "", None


def api_key():
    key, _ = find_key()
    if not key:
        raise RuntimeError("No TMDB API key: add one in Settings (or set TMDB_API_KEY)")
    return key


def key_status():
    """The key and where it comes from, for the Settings dialog (which keeps it masked until asked to show it)."""
    key, source = find_key()
    return {"source": source, "key": key or None}


def check_key(key):
    """Raise ValueError (fit to show, never repeating the key) unless TMDB accepts `key`. One request, not recorded."""
    global _request_count
    if not KEY_PATTERN.fullmatch(key):
        raise ValueError("that doesn't look like a TMDB API key: it is 32 letters and digits (the \"API Key\", not the Read Access Token)")
    with _count_lock:
        _request_count += 1
    try:
        with urllib.request.urlopen(f"{API}/configuration?{urllib.parse.urlencode({'api_key': key})}", timeout=10) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise ValueError("TMDB doesn't accept that key; copy the \"API Key\" from your TMDB account's API settings") from None
        raise RuntimeError(f"TMDB returned HTTP {e.code} while checking the key") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach TMDB to check the key: {e.reason}") from None


_request_count = 0
_count_lock = threading.Lock()


def request_count():
    """How many requests this process has sent to TMDB (TMDB itself reports no quota to compare against)."""
    return _request_count


def _age(seconds):
    hours, rest = divmod(int(seconds), 3600)
    return f"{hours}h{rest // 60:02d}m" if hours else f"{rest // 60}m" if rest >= 60 else f"{rest}s"


def _response_note(headers):
    """What a response says about caching and rate limits. TMDB sends no quota, so rate-limit headers (and a 429's
    Retry-After) are shown verbatim if they ever appear."""
    parts = []
    cache = headers.get("X-Cache", "")
    if cache:
        parts.append("cache " + ("hit" if cache.lower().startswith("hit") else "miss"))
        if headers.get("Age", "").isdigit() and int(headers["Age"]) > 0:
            parts[-1] += f" (cached {_age(int(headers['Age']))} ago)"
    parts += [f"{name.lower()}: {value}" for name, value in headers.items() if any(w in name.lower() for w in ("ratelimit", "rate-limit", "retry-after"))]
    return " · ".join(parts)


CACHE_SECONDS = 600     # how long an answer is reused; TMDB's data changes rarely, and this process is short-lived
CACHE_ENTRIES = 500
_cache = {}             # (path, params) -> (stored_at, status, body); only successful answers and 404s are kept
_cache_lock = threading.Lock()


def clear_cache():
    with _cache_lock:
        _cache.clear()


def _get(path, **params):
    """GET a TMDB endpoint and return the parsed JSON, or None if it doesn't exist (404).
    Every request is recorded for display with the API key replaced by <redacted>. An identical request made again
    within CACHE_SECONDS is answered from memory: nothing is sent and it is not counted, but it is still recorded, marked
    as reused, so each file's transcript stays complete."""
    global _request_count
    shown = {k: v for k, v in params.items() if v is not None}
    command = f"GET {API}{path}?api_key=<redacted>" + (f"&{urllib.parse.urlencode(shown)}" if shown else "")
    cache_key = (path, tuple(sorted(shown.items())))
    with _cache_lock:
        hit = _cache.get(cache_key)
        if hit and time.monotonic() - hit[0] > CACHE_SECONDS:
            del _cache[cache_key]
            hit = None
    if hit:
        _, status, body = hit
        calls.record("tmdb", path, [], body, 0.0, status, command=command, status=f"HTTP {status}",
                     note="reused an earlier response: no request sent", trim=True)
        return None if status == 404 else json.loads(body)

    url = f"{API}{path}?{urllib.parse.urlencode({'api_key': api_key(), **shown})}"
    started = time.monotonic()
    with _count_lock:
        _request_count += 1
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            body, status, note = resp.read().decode("utf-8", "replace"), resp.status, _response_note(resp.headers)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        calls.record("tmdb", path, [], body, time.monotonic() - started, e.code, command=command, status=f"HTTP {e.code}",
                     note=_response_note(e.headers), trim=True)
        if e.code == 404:
            _remember(cache_key, 404, body)  # "this episode does not exist" is as reusable as an answer
            return None
        if e.code == 429:
            raise RuntimeError("TMDB is rate-limiting requests (HTTP 429); wait a moment and try again") from None
        raise RuntimeError(f"TMDB returned HTTP {e.code} for {path}") from None  # `from None`: don't chain the URL
    except urllib.error.URLError as e:
        calls.record("tmdb", path, [], "", time.monotonic() - started, None, command=command, status="no response", note=str(e.reason))
        raise RuntimeError(f"Could not reach TMDB: {e.reason}") from None
    calls.record("tmdb", path, [], body, time.monotonic() - started, status, command=command, status=f"HTTP {status}", note=note, trim=True)
    _remember(cache_key, status, body)
    return json.loads(body)


def _remember(cache_key, status, body):
    with _cache_lock:
        if len(_cache) >= CACHE_ENTRIES:
            del _cache[min(_cache, key=lambda k: _cache[k][0])]  # drop the oldest
        _cache[cache_key] = (time.monotonic(), status, body)


def _candidate(result, kind):
    if kind == "tv":
        title, date, countries = result["name"], result.get("first_air_date"), result.get("origin_country", [])
        original = result.get("original_name")
    else:
        title, date, countries = result["title"], result.get("release_date"), []
        original = result.get("original_title")
    return {
        "id": result["id"],
        "kind": kind,
        "title": title,
        "year": int(date[:4]) if date else None,
        "countries": countries,
        "overview": result.get("overview", ""),
        "poster": result.get("poster_path"),
        "date": date or None,
        "original_title": original if original and original != title else None,
        "language": result.get("original_language"),
        "rating": result.get("vote_average") if result.get("vote_count") else None,  # 0 votes means no rating, not a bad one
        "votes": result.get("vote_count") or 0,
        **_extras(result, kind),
    }


CERT_COUNTRY = "US"     # whose age rating is shown when TMDB has one; otherwise the first it lists
CAST_SHOWN = 5


def _certification(result, kind):
    """The age rating (e.g. 'R', 'TV-MA'), preferring CERT_COUNTRY's, or None."""
    if kind == "tv":
        entries = [(r.get("iso_3166_1"), r.get("rating")) for r in (result.get("content_ratings") or {}).get("results", [])]
    else:
        entries = [(r.get("iso_3166_1"), next((d["certification"] for d in r.get("release_dates", []) if d.get("certification")), None))
                   for r in (result.get("release_dates") or {}).get("results", [])]
    entries = [(country, rating) for country, rating in entries if rating]
    return next((rating for country, rating in entries if country == CERT_COUNTRY), entries[0][1] if entries else None)


def _extras(result, kind):
    """What only a title's own page carries (a search result has none of it, so these come back empty there): genres, the
    runtime (a show's usual episode length), director (movie) or creator (show), top-billed cast, age rating, and a show's season and episode counts."""
    if kind == "tv":
        people = [p["name"] for p in result.get("created_by", []) if p.get("name")]
    else:
        people = [p["name"] for p in (result.get("credits") or {}).get("crew", []) if p.get("job") == "Director" and p.get("name")]
    cast = [p["name"] for p in (result.get("credits") or {}).get("cast", []) if p.get("name")][:CAST_SHOWN]
    usual = sorted(r for r in (result.get("episode_run_time") or []) if r)
    runtime = result.get("runtime") or None if kind == "movie" else (usual[len(usual) // 2] if usual else None)
    return {"runtime": runtime, "genres": [g["name"] for g in result.get("genres", []) if g.get("name")], "director": people, "cast": cast,
            "certification": _certification(result, kind),
            "seasons": result.get("number_of_seasons") if kind == "tv" else None,
            "episodes": result.get("number_of_episodes") if kind == "tv" else None}


def search(title, kind, year=None, country=None, limit=5):
    """Return up to `limit` candidates for `title`; kind is 'tv' or 'movie'.

    If `country` is given (ISO code, e.g. 'US'), TV results from that country come first.
    TMDB's own popularity order is otherwise kept.
    """
    if kind == "tv":
        data = _get("/search/tv", query=title, first_air_date_year=year)
    else:
        data = _get("/search/movie", query=title, year=year)
    candidates = [_candidate(r, kind) for r in (data or {}).get("results", [])]
    if country:
        candidates.sort(key=lambda c: country not in c["countries"])  # stable: False (match) sorts first
    return candidates[:limit]


def episode_info(tv_id, season, episode):
    """The episode's title, summary, air date and runtime (minutes), or None if that show has no such season/episode."""
    data = _get(f"/tv/{tv_id}/season/{season}/episode/{episode}")
    if not data:
        return None
    return {"title": data.get("name"), "overview": data.get("overview") or "", "air_date": data.get("air_date") or None,
            "runtime": data.get("runtime") or None}


def episode_title(tv_id, season, episode):
    """Return the episode's title, or None if that show has no such season/episode."""
    info = episode_info(tv_id, season, episode)
    return info["title"] if info else None


def runtime_minutes(ident):
    """TMDB's runtime in minutes for a movie or episode identity, or None if TMDB doesn't say."""
    if ident["kind"] == "tv":
        if ident.get("season") is None or ident.get("episode") is None:
            return None
        info = episode_info(ident["id"], ident["season"], ident["episode"])
        return info["runtime"] if info else None
    data = _get(f"/movie/{int(ident['id'])}")
    return (data or {}).get("runtime") or None


def runtime_estimate(ident):
    """A stand-in runtime for a TV episode TMDB has no runtime for (new episodes often lack one): the median of the
    season's other episodes, else the show's usual episode length. Returns (minutes, 'season' or 'show'), or None."""
    if ident["kind"] != "tv" or ident.get("season") is None:
        return None
    season = _get(f"/tv/{ident['id']}/season/{ident['season']}") or {}
    others = sorted(e["runtime"] for e in season.get("episodes", []) if e.get("runtime") and e.get("episode_number") != ident.get("episode"))
    if others:
        return others[len(others) // 2], "season"
    usual = sorted(r for r in ((_get(f"/tv/{ident['id']}") or {}).get("episode_run_time") or []) if r)
    return (usual[len(usual) // 2], "show") if usual else None


def identity(candidate, season=None, episode=None):
    """The stable facts behind a decision. Display names are derived from these, never stored."""
    ident = {"kind": candidate["kind"], "id": candidate["id"], "title": candidate["title"], "year": candidate["year"]}
    if candidate["kind"] == "tv" and season is not None and episode is not None:
        ident.update(season=season, episode=episode)
    return ident


def render(ident):
    """'Title (Year)' or 'Title (Year) - S03E04', derived from an identity."""
    return clean_name(ident, ident.get("season"), ident.get("episode"))


def clean_name(candidate, season=None, episode=None):
    """'Title (Year)' for a movie; 'Title (Year) - S03E04' for an episode."""
    name = candidate["title"] + (f" ({candidate['year']})" if candidate["year"] else "")
    if candidate.get("kind") == "tv" and season is not None and episode is not None:
        name += f" - S{season:02d}E{episode:02d}"
    return name


def details(kind, tmdb_id):
    """The catalog entry for a known TMDB id (kind is 'tv' or 'movie'), or None."""
    extra = "credits,content_ratings" if kind == "tv" else "credits,release_dates"
    data = _get(f"/{kind}/{int(tmdb_id)}", append_to_response=extra)
    return _candidate(data, kind) if data else None


ARTWORK = (("backdrops", "backdrop", 6), ("logos", "logo", 2), ("posters", "poster", 3))   # what TMDB calls them, what we call them, how many


def images(kind, tmdb_id, season=None, episode=None):
    """Artwork for a title, the best rated first within each kind: [{'kind', 'path', 'width', 'height'}] where kind is 'still'
    (the episode's own, for a TV show with season and episode), 'backdrop', 'logo' or 'poster'. Images with no language (or
    English) are kept, so a logo or poster is readable. [] if TMDB has none; None if the title does not exist."""
    data = _get(f"/{kind}/{int(tmdb_id)}/images", include_image_language="en,null")
    if data is None:
        return None
    def best(items, count):
        ranked = sorted((i for i in items if i.get("file_path")), key=lambda i: i.get("vote_average") or 0, reverse=True)
        return ranked[:count]
    found = []
    if kind == "tv" and season is not None and episode is not None:
        stills = (_get(f"/tv/{int(tmdb_id)}/season/{int(season)}/episode/{int(episode)}/images") or {}).get("stills", [])
        found += [("still", i) for i in best(stills, 4)]
    for key, name, count in ARTWORK:
        found += [(name, i) for i in best(data.get(key, []), count)]
    return [{"kind": name, "path": i["file_path"], "width": i.get("width"), "height": i.get("height")} for name, i in found]


def find_by_imdb(imdb_id):
    """The movie or TV show with this IMDb id (tt...), or None. Episode-level ids are not resolved."""
    data = _get(f"/find/{imdb_id}", external_source="imdb_id") or {}
    for kind, key in (("tv", "tv_results"), ("movie", "movie_results")):
        if data.get(key):
            return _candidate(data[key][0], kind)
    return None
