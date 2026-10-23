"""The Mermaid diagram of how a file's metadata was built up."""
import json

from tidyarr import flow

FINAL = {"kind": "tv", "id": 549, "title": "Law & Order", "year": 1990, "season": 26, "episode": 2}
GUESSIT = {"kind": "tv", "title": "Law & Order", "year": 1990, "season": 26, "episode": 2}
LNO = {"kind": "tv", "title": "LnO", "year": None, "season": 26, "episode": 2}
RAW = {"probe": {"title": "Law.and.Order.S26E02"}, "nfo": {"file": "x.nfo", "tmdb": {"kind": "tv", "id": 549}, "imdb": None}}


def test_a_decided_file_shows_each_tool_and_who_agreed_with_the_answer():
    text = flow.diagram("a/Law.and.Order.S26E02.mkv", {"nfo": GUESSIT, "metadata": LNO, "guessit": GUESSIT}, FINAL, RAW, "auto")
    assert text.startswith("flowchart TD")
    for stage in ("guessit + filename", "ffprobe + filename", "guessit + title tag", "Sidecar x.nfo", "TMDB verifies", "Accepted automatically: the sources agreed"):
        assert stage in text
    assert "class guessit,nfo agree" in text and "class metadata conflict" in text      # LnO disagrees with the final title
    assert "Agreed: guessit + filename, the .nfo<br/>Disagreed: guessit + title tag" in text


def test_a_file_under_review_has_no_decision_yet():
    pending = flow.diagram("a.mkv", {"nfo": None, "metadata": None, "guessit": GUESSIT}, None, {"probe": None, "nfo": None})
    assert "Needs a decision" in pending and "Sidecar" not in pending and "guessit + title tag" not in pending


def test_text_from_files_and_services_cannot_break_out_of_the_diagram():
    hostile = 'x"]) --> evil["<img src=x onerror=alert(1)>\n%%{init}%% `'
    text = flow.diagram(hostile, {"guessit": {"kind": "movie", "title": hostile, "year": None, "season": None, "episode": None}}, None, {})
    for line in text.splitlines():
        if "<b>" in line:
            inner = line.split('"', 1)[1].rsplit('"', 1)[0]
            assert '"' not in inner and "<img" not in inner and "[" not in inner and "`" not in inner
    assert len(flow.label("a" * 500)) <= flow.MAX_LABEL + 1


def test_long_names_are_shown_whole_and_each_source_is_named_by_its_verdict():
    long_final = dict(FINAL, title="Law & Order Toronto: Criminal Intent", year=2024, season=4, episode=4)
    text = flow.diagram("a.mkv", {"guessit": GUESSIT, "metadata": None, "nfo": None}, long_final, {})
    assert "Law #38; Order Toronto: Criminal Intent #40;2024#41;<br/>Season 4 episode 4, taken from the chosen TMDB episode<br/>Disagreed: guessit + filename<br/>No opinion: guessit + title tag, the .nfo" in text
    assert "…" not in text and "sources agree with this" not in text


def test_a_person_picks_the_show_and_the_episode_is_credited_to_where_it_was_read():
    sources = {"guessit": GUESSIT, "metadata": dict(LNO, season=26, episode=2), "nfo": None}
    text = flow.diagram("a.mkv", sources, FINAL, RAW, "human")
    assert "Human chose from TMDB<" in text and "Law #38; Order #40;1990#41;<br/>Season 26 episode 2, taken from the title tag" in text
    filename_only = flow.diagram("a.mkv", dict(sources, metadata=None), FINAL, {}, "human")
    assert "Human chose from TMDB<" in filename_only and "taken from the filename" in filename_only
    film = flow.diagram("a.mkv", {"guessit": None}, {"kind": "movie", "id": 1, "title": "Dune", "year": 2021}, {}, "human")
    assert "Dune #40;2021#41;" in film and "Season" not in film


def tmdb_call(path, query, output, status="HTTP 200"):
    command = f"GET https://api.themoviedb.org/3{path}?api_key=<redacted>" + (f"&query={query}" if query else "")
    return {"tool": "tmdb", "label": path, "command": command, "output": json.dumps(output), "status": status}


def test_every_tmdb_request_gets_its_own_box_in_the_order_it_was_made():
    calls = [tmdb_call("/search/tv", "Law+and+Order", {"results": [{"id": 549, "name": "Law & Order", "first_air_date": "1990-09-13"}], "total_results": 1}),
             tmdb_call("/tv/549/season/26/episode/2", None, {"name": "Into the Mystic"}),
             tmdb_call("/tv/549/season/26/episode/9", None, {}, status="HTTP 404"),
             tmdb_call("/tv/549", None, {"id": 549, "name": "Law & Order", "first_air_date": "1990-09-13"})]
    text = flow.diagram("a.mkv", {"guessit": GUESSIT}, FINAL, {"calls": calls})
    assert "TMDB: Found the show ID from the name</b><br/>searched for “Law and Order”<br/>Law #38; Order #40;1990#41; #91;id 549#93;" in text
    assert "TMDB: Checked the episode ID exists</b><br/>show id 549, season 26, episode 2<br/>“Into the Mystic”" in text
    assert "no such episode" in text and "TMDB: Fetched the show details</b><br/>id 549" in text
    assert "guessit --> tmdb1" in text and "tmdb1 --> tmdb2" in text and "tmdb3 --> tmdb4" in text and "tmdb4 --> final" in text
    assert "TMDB verifies" not in text


def test_a_search_with_no_results_and_a_cut_off_reply_are_both_described():
    empty = flow.describe_tmdb(tmdb_call("/search/movie", "Nope", {"results": [], "total_results": 0}))
    assert empty == ("TMDB: Found the film ID from the name", ["searched for “Nope”", "nothing found"])
    cut = dict(tmdb_call("/search/tv", "X", {}), output='{"page": 1, "results": [{"id"')
    assert flow.describe_tmdb(cut)[1][1] == "nothing found"
    assert flow.describe_tmdb({"tool": "tmdb", "label": "/odd"})[0] == "TMDB request"
