"""The TMDB HTTP layer: what gets recorded for display, that the API key never does, and the request counter."""
import io
import json
import urllib.error

import pytest

from tidyarr import calls, report, tmdb

SECRET = "SECRETKEY0123456789abcdef"


class FakeResponse:
    def __init__(self, body, status=200, headers=None):
        self._body, self.status, self.headers = body.encode(), status, headers or {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def secret_key(monkeypatch):
    monkeypatch.setenv("TMDB_API_KEY", SECRET)
    tmdb.clear_cache()      # every test starts with nothing reusable
    yield
    tmdb.clear_cache()


def serve(monkeypatch, response=None, error=None):
    sent = []

    def urlopen(url, timeout=None):
        sent.append(url)
        if error:
            raise error
        return response
    monkeypatch.setattr(tmdb.urllib.request, "urlopen", urlopen)
    return sent


def http_error(code, body="{}", headers=None):
    return urllib.error.HTTPError("https://x", code, "err", headers or {}, io.BytesIO(body.encode()))


def test_the_key_is_sent_to_tmdb_but_never_appears_in_anything_recorded(monkeypatch):
    sent = serve(monkeypatch, FakeResponse(json.dumps({"results": []}), headers={"X-Cache": "Hit from cloudfront"}))
    with calls.capture() as log:
        tmdb._get("/search/tv", query="911 nash", first_air_date_year=None)
    assert f"api_key={SECRET}" in sent[0]                       # it really was sent
    assert SECRET not in json.dumps(log)                         # ...and is nowhere in the transcript
    assert log[0]["command"] == "GET https://api.themoviedb.org/3/search/tv?api_key=<redacted>&query=911+nash"
    assert (log[0]["tool"], log[0]["label"], log[0]["status"]) == ("tmdb", "/search/tv", "HTTP 200")


def test_the_key_stays_out_of_the_transcript_when_requests_fail_too(monkeypatch):
    for error in (http_error(404), http_error(500, "boom"), urllib.error.URLError("timed out")):
        serve(monkeypatch, error=error)
        with calls.capture() as log:
            try:
                tmdb._get("/tv/1")
            except RuntimeError as e:
                assert SECRET not in str(e)
        assert SECRET not in json.dumps(log) and log


def test_long_replies_are_trimmed_for_display_only(monkeypatch):
    body = {"page": 1, "results": [{"id": i, "overview": "x" * 500} for i in range(20)]}
    serve(monkeypatch, FakeResponse(json.dumps(body)))
    with calls.capture() as log:
        data = tmdb._get("/search/tv", query="a")
    assert len(data["results"]) == 20 and len(data["results"][0]["overview"]) == 500  # the code still gets everything
    assert "… and 15 more" in log[0]["output"] and "x" * 301 not in log[0]["output"]


def test_cache_status_and_age_are_noted_and_rate_limit_headers_would_be_shown_verbatim(monkeypatch):
    serve(monkeypatch, FakeResponse("{}", headers={"X-Cache": "Hit from cloudfront", "Age": "10646"}))
    with calls.capture() as log:
        tmdb._get("/configuration")
    assert log[0]["note"] == "cache hit (cached 2h57m ago)"
    serve(monkeypatch, FakeResponse("{}", headers={"X-Cache": "Miss from cloudfront", "X-RateLimit-Remaining": "39"}))
    with calls.capture() as log:
        tmdb._get("/configuration", language="en")  # different parameters: a separate request, not a reused answer
    assert log[0]["note"] == "cache miss · x-ratelimit-remaining: 39"


def test_a_missing_item_returns_none_and_is_recorded(monkeypatch):
    serve(monkeypatch, error=http_error(404, '{"success": false, "status_message": "not found"}'))
    with calls.capture() as log:
        assert tmdb._get("/tv/999/season/1/episode/1") is None
    assert log[0]["status"] == "HTTP 404" and "not found" in log[0]["output"]


def test_rate_limiting_gets_a_clear_message_and_shows_retry_after(monkeypatch):
    serve(monkeypatch, error=http_error(429, "{}", headers={"Retry-After": "5"}))
    with calls.capture() as log, pytest.raises(RuntimeError, match="rate-limiting"):
        tmdb._get("/search/tv", query="x")
    assert log[0]["status"] == "HTTP 429" and "retry-after: 5" in log[0]["note"]


def test_an_unreachable_tmdb_is_recorded_without_a_response(monkeypatch):
    serve(monkeypatch, error=urllib.error.URLError("timed out"))
    with calls.capture() as log, pytest.raises(RuntimeError, match="Could not reach TMDB"):
        tmdb._get("/search/tv", query="x")
    assert log[0]["status"] == "no response" and log[0]["note"] == "timed out" and log[0]["output"] == ""


def test_the_request_counter_counts_requests_actually_sent_not_reused_answers(monkeypatch):
    serve(monkeypatch, FakeResponse("{}"))
    before = tmdb.request_count()
    tmdb._get("/configuration")
    tmdb._get("/configuration")          # identical: answered from memory
    serve(monkeypatch, error=http_error(404))
    tmdb._get("/tv/1")
    assert tmdb.request_count() == before + 2


@pytest.mark.parametrize("seconds, text", [(5, "5s"), (59, "59s"), (60, "1m"), (3599, "59m"), (3600, "1h00m"), (10646, "2h57m")])
def test_ages_are_shown_in_a_short_human_form(seconds, text):
    assert tmdb._age(seconds) == text


def test_trim_json_cuts_lists_and_long_text_but_leaves_small_things_alone():
    assert calls.trim_json({"a": [1, 2, 3], "b": "short"}) == {"a": [1, 2, 3], "b": "short"}
    assert calls.trim_json(list(range(7))) == [0, 1, 2, 3, 4, "… and 2 more"]
    assert calls.trim_json("y" * 400) == "y" * 300 + "…"


def test_the_terminal_footer_shows_http_status_and_the_note():
    call = {"tool": "tmdb", "label": "/search/tv", "command": "GET https://api.themoviedb.org/3/search/tv?api_key=<redacted>&query=x",
            "output": "{}", "seconds": 0.21, "exit_code": 200, "status": "HTTP 200", "note": "cache hit (cached 2h57m ago)"}
    html = report.terminal(call)
    assert "# HTTP 200 · 0.21s · cache hit (cached 2h57m ago)" in html
    assert "api_key=&lt;redacted&gt;" in html and "# exit" not in html


def counting(monkeypatch, response):
    """A fake urlopen that remembers every URL it was asked for."""
    sent = []

    def urlopen(url, timeout=None):
        sent.append(url)
        if isinstance(response, Exception):
            raise response
        return response() if callable(response) else response
    monkeypatch.setattr(tmdb.urllib.request, "urlopen", urlopen)
    return sent


def test_an_identical_request_is_sent_once_and_the_second_is_marked_reused(monkeypatch):
    sent = counting(monkeypatch, lambda: FakeResponse(json.dumps({"results": [{"id": 1}]}), headers={"X-Cache": "Hit from cloudfront"}))
    with calls.capture() as log:
        first = tmdb._get("/search/tv", query="911 nash")
        second = tmdb._get("/search/tv", query="911 nash")
    assert len(sent) == 1 and first == second == {"results": [{"id": 1}]}
    assert len(log) == 2                                                          # both are in the transcript...
    assert "reused" not in (log[0].get("note") or "") and log[1]["note"] == "reused an earlier response: no request sent"
    assert log[1]["command"] == log[0]["command"] and log[1]["output"] == log[0]["output"] and log[1]["seconds"] == 0.0
    assert SECRET not in json.dumps(log)                                         # ...and the key is still absent from the reused one


def test_different_parameters_or_paths_are_separate_requests(monkeypatch):
    sent = counting(monkeypatch, lambda: FakeResponse("{}"))
    tmdb._get("/search/tv", query="a")
    tmdb._get("/search/tv", query="b")
    tmdb._get("/search/movie", query="a")
    tmdb._get("/search/tv", query="a", first_air_date_year=2020)
    assert len(sent) == 4


def test_a_missing_item_is_remembered_too_so_it_is_not_asked_for_again(monkeypatch):
    sent = counting(monkeypatch, http_error(404))
    with calls.capture() as log:
        assert tmdb._get("/tv/1/season/9/episode/9") is None
        assert tmdb._get("/tv/1/season/9/episode/9") is None
    assert len(sent) == 1 and log[1]["status"] == "HTTP 404" and "reused" in log[1]["note"]


@pytest.mark.parametrize("failure", [http_error(500, "boom"), http_error(429, "{}", headers={"Retry-After": "5"}), urllib.error.URLError("timed out")])
def test_failures_are_never_remembered_so_the_next_try_really_asks_again(monkeypatch, failure):
    sent = counting(monkeypatch, failure)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            tmdb._get("/search/tv", query="x")
    assert len(sent) == 2
    sent2 = counting(monkeypatch, lambda: FakeResponse('{"ok": true}'))
    assert tmdb._get("/search/tv", query="x") == {"ok": True} and len(sent2) == 1   # and it works once TMDB is back


def test_a_remembered_answer_expires(monkeypatch):
    sent = counting(monkeypatch, lambda: FakeResponse("{}"))
    tmdb._get("/configuration")
    monkeypatch.setattr(tmdb, "CACHE_SECONDS", -1)          # everything is already too old
    tmdb._get("/configuration")
    assert len(sent) == 2


def test_the_cache_is_bounded_and_drops_the_oldest(monkeypatch):
    sent = counting(monkeypatch, lambda: FakeResponse("{}"))
    monkeypatch.setattr(tmdb, "CACHE_ENTRIES", 2)
    for path in ("/tv/1", "/tv/2", "/tv/3"):
        tmdb._get(path)
    assert len(tmdb._cache) == 2 and ("/tv/1", ()) not in tmdb._cache
    tmdb._get("/tv/3")                                       # still remembered
    assert len(sent) == 3
    tmdb._get("/tv/1")                                       # was dropped, so it is asked for again
    assert len(sent) == 4


def test_the_search_that_verifies_a_title_and_the_search_that_lists_candidates_are_one_request(monkeypatch):
    from tidyarr import triage
    body = json.dumps({"results": [{"id": 549, "name": "Law & Order", "first_air_date": "1990-09-13", "origin_country": ["US"], "overview": ""}]})
    sent = counting(monkeypatch, lambda: FakeResponse(body))
    parse = {"type": "episode", "title": "Law and Order", "season": 26, "episode": 2, "country_code": "US"}
    triage.auto_resolve(parse)                                        # the verification search (and its episode check)
    tmdb.search("Law and Order", "tv", year=None, country="US")        # the candidate list: the same search
    assert sum("/search/tv" in url for url in sent) == 1
