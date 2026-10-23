"""Offline tests for the evidence sources, agreement/consensus and TMDB verification (assess)."""
import pytest

from tidyarr import calls as calls_module
from tidyarr import evidence, tmdb, triage

NFO = """
  ░░░ ELiTE ░░░
  TMDB: https://www.themoviedb.org/tv/549
  IMDB: https://www.imdb.com/title/tt0098844/
  Duration : 40 Mins, 54 Secs
"""


def op(title, year=None, season=None, episode=None, kind="tv"):
    return {"kind": kind, "title": title, "year": year, "season": season, "episode": episode}


def test_nfo_urls_become_ids():
    assert evidence.parse_nfo(NFO) == {"tmdb": ("tv", 549), "imdb": "tt0098844"}
    assert evidence.parse_nfo("see https://www.imdb.com/title/tt0133093/ ") == {"tmdb": None, "imdb": "tt0133093"}
    assert evidence.parse_nfo("just ascii art, no links") is None


def test_find_nfo_prefers_same_name_then_a_lone_nfo_beside_a_lone_video(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "video.mkv").write_bytes(b"x")
    (tmp_path / "a" / "release.nfo").write_text("x")
    assert evidence.find_nfo(tmp_path / "a" / "video.mkv").name == "release.nfo"
    (tmp_path / "a" / "video.nfo").write_text("x")
    assert evidence.find_nfo(tmp_path / "a" / "video.mkv").name == "video.nfo"


def test_find_nfo_refuses_to_guess_when_a_folder_holds_several_videos(tmp_path):
    for name in ("one.mkv", "two.mkv", "season.nfo"):
        (tmp_path / name).write_bytes(b"x")
    assert evidence.find_nfo(tmp_path / "one.mkv") is None


def test_opinions_display_and_ignore_list_valued_numbers():
    g = {"type": "episode", "title": "Show", "season": 1, "episode": [1, 2], "year": 2020}
    opinion = evidence.opinion_from_guess(g)
    assert opinion["episode"] is None  # multi-episode files give lists; not a single number to compare
    assert evidence.format_opinion(op("Show", 2020, 1, 2)) == "Show (2020) S01E02"
    assert evidence.format_opinion(op("stuff", episode=4)) == "stuff E04"
    assert evidence.format_opinion(None) is None
    assert evidence.opinion_from_guess({"type": "movie"}) is None


@pytest.mark.parametrize("opinion, expected", [
    (op("Law and Order Toronto Criminal Intent", season=4, episode=4), True),   # '&' vs 'and', punctuation: same title
    (op("law & order toronto: criminal intent"), True),
    (op("LnO", season=4, episode=4), False),                                     # different title
    (op("Law & Order Toronto: Criminal Intent", season=4, episode=5), False),   # same title, wrong episode
    (op("Law & Order Toronto: Criminal Intent", year=1999), False),             # same title, wrong year
    (op("Law & Order Toronto: Criminal Intent", season=4, episode=4, kind="movie"), False),   # same title, wrong type
    (op("Law & Order Toronto: Criminal Intent", season=4, episode=4, kind=None), True),       # no type given: not held against it
])
def test_agreement_with_the_final_identity(opinion, expected):
    identity = {"kind": "tv", "id": 1, "title": "Law & Order Toronto: Criminal Intent", "year": 2024, "season": 4, "episode": 4}
    assert evidence.agrees(opinion, identity) is expected


def test_consensus_counts_only_sources_that_spoke():
    identity = {"kind": "movie", "id": 1, "title": "The Matrix", "year": 1999}
    opinions = {"nfo": None, "metadata": None, "guessit": op("the matrix", 1999, kind="movie")}
    assert evidence.consensus(opinions, identity) == (1, 1)
    opinions["metadata"] = op("Matrix Reloaded", kind="movie")
    assert evidence.consensus(opinions, identity) == (1, 2)


def fake_catalog(monkeypatch, shows, episodes):
    monkeypatch.setattr(tmdb, "search", lambda title, kind, **kw: shows)
    monkeypatch.setattr(tmdb, "details", lambda kind, tmdb_id: next((s for s in shows if s["id"] == tmdb_id), None))
    monkeypatch.setattr(tmdb, "find_by_imdb", lambda imdb: None)
    monkeypatch.setattr(tmdb, "episode_title", lambda show_id, s, e: "Title" if (show_id, s, e) in episodes else None)


def show(id_, title, year):
    return {"id": id_, "kind": "tv", "title": title, "year": year, "countries": ["US"], "overview": ""}


def ev(**kw):
    base = {"guessit": None, "guessit_parse": None, "metadata": None, "metadata_parse": None, "nfo_ids": None}
    return {**base, **kw}


def test_nfo_id_verified_with_episode_numbers_from_the_other_sources(monkeypatch):
    fake_catalog(monkeypatch, [show(549, "Law & Order", 1990)], episodes={(549, 26, 2)})
    verdict = triage.assess(ev(nfo_ids={"tmdb": ("tv", 549), "imdb": None}, guessit=op("LnO", season=26, episode=2)))
    assert (verdict["identity"]["id"], verdict["identity"]["season"], verdict["identity"]["episode"]) == (549, 26, 2)
    assert verdict["nfo"]["title"] == "Law & Order" and not verdict["conflict"]


def test_nfo_show_without_a_real_episode_is_not_accepted(monkeypatch):
    fake_catalog(monkeypatch, [show(549, "Law & Order", 1990)], episodes=set())
    verdict = triage.assess(ev(nfo_ids={"tmdb": ("tv", 549), "imdb": None}, guessit=op("LnO", season=26, episode=2)))
    assert verdict["identity"] is None and verdict["nfo"] is not None  # still shown as the NFO's opinion


def test_sources_that_verify_to_different_shows_are_a_conflict(monkeypatch):
    fake_catalog(monkeypatch, [show(1, "Show A", 2000), show(2, "Show B", 2010)], episodes={(1, 1, 1), (2, 1, 1)})
    metadata_parse = {"type": "episode", "title": "Show A", "season": 1, "episode": 1}
    filename_parse = {"type": "episode", "title": "Show B", "season": 1, "episode": 1}
    monkeypatch.setattr(tmdb, "search", lambda title, kind, **kw: [s for s in (show(1, "Show A", 2000), show(2, "Show B", 2010)) if s["title"] == title])
    verdict = triage.assess(ev(metadata_parse=metadata_parse, guessit_parse=filename_parse))
    assert verdict["conflict"] and verdict["identity"] is None  # a mislabeled file: send to a human


def test_the_container_title_rescues_a_useless_filename(monkeypatch):
    fake_catalog(monkeypatch, [show(7, "Law & Order Toronto: Criminal Intent", 2024)], episodes={(7, 4, 4)})
    metadata_parse = {"type": "episode", "title": "Law and Order Toronto Criminal Intent", "season": 4, "episode": 4}
    garbage_parse = {"type": "episode", "title": "stuff", "episode": 4}
    # the garbage filename does not even resolve ('stuff' is not a title); the tag alone carries it
    monkeypatch.setattr(tmdb, "search", lambda title, kind, **kw: [show(7, "Law & Order Toronto: Criminal Intent", 2024)] if "Law" in title else [])
    verdict = triage.assess(ev(metadata_parse=metadata_parse, guessit_parse=garbage_parse))
    assert verdict["identity"]["id"] == 7 and not verdict["conflict"]


def test_gather_without_a_file_on_disk_uses_only_the_filename(monkeypatch):
    monkeypatch.setattr(evidence, "guess", lambda path, label="filename": {"type": "movie", "title": "The Matrix", "year": 1999})
    result = evidence.gather("not/on/disk.mkv", g=None)
    assert result["guessit"]["title"] == "The Matrix" and result["metadata"] is None and result["nfo_ids"] is None


def test_gather_reads_the_title_tag_and_nfo(tmp_path, monkeypatch):
    folder = tmp_path / "rel"
    folder.mkdir()
    (folder / "video.mkv").write_bytes(b"x")
    (folder / "video.nfo").write_bytes(NFO.encode("cp437", errors="replace"))
    monkeypatch.setattr(evidence.probe, "probe", lambda file: {"title": "Law.and.Order.S26E02", "duration": 2455.0})
    monkeypatch.setattr(evidence, "guess", lambda text, label="filename": {"type": "episode", "title": "Law and Order", "season": 26, "episode": 2})
    result = evidence.gather("rel/video.mkv", base_dir=tmp_path, g={"type": "episode", "title": "video"})
    assert result["nfo_ids"] == {"tmdb": ("tv", 549), "imdb": "tt0098844"}
    assert result["metadata_title"] == "Law.and.Order.S26E02" and result["metadata"]["season"] == 26
    assert result["duration"] == 2455.0


def test_an_unreadable_file_is_noted_not_fatal(tmp_path, monkeypatch):
    (tmp_path / "bad.mkv").write_bytes(b"not a video")

    def broken(file):
        raise RuntimeError("ffprobe could not read the file: Invalid data")
    monkeypatch.setattr(evidence.probe, "probe", broken)
    result = evidence.gather("bad.mkv", base_dir=tmp_path, g={"type": "movie", "title": "bad"})
    assert result["errors"] and result["metadata"] is None


def test_evidence_record_keeps_the_nfo_ids():
    ev = {"guessit_parse": {"title": "x"}, "metadata_parse": None, "nfo_file": "r.nfo",
          "nfo_ids": {"tmdb": ("tv", 549), "imdb": "tt0098844"}, "probe": None}
    opinions = {"nfo": op("Law & Order", 1990), "metadata": None, "guessit": None}
    record = triage.evidence_record(ev, opinions, 1, 1)
    assert record["raw"]["nfo"] == {"file": "r.nfo", "tmdb": {"kind": "tv", "id": 549}, "imdb": "tt0098844"}
    assert record["sources"] == opinions


def test_consensus_counts_only_the_sources_that_spoke():
    identity = {"kind": "movie", "id": 1, "title": "The Matrix", "year": 1999}
    opinions = {"nfo": None, "metadata": None, "guessit": op("the matrix", 1999, kind="movie")}
    assert evidence.consensus(opinions, identity) == (1, 1)


def test_evaluate_captures_the_transcript_into_the_evidence(monkeypatch):
    monkeypatch.setattr(triage, "gather", lambda path, base_dir=None, g=None: (calls_module.record("guessit", "filename", ["guessit", path], "{}", 0.0, 0) or
                                                                                  {"guessit": None, "guessit_parse": {}, "metadata": None, "metadata_parse": None, "nfo_ids": None}))
    monkeypatch.setattr(triage, "guess", lambda path, label="filename": {})
    result = triage.evaluate("a.mkv", None)
    assert [c["tool"] for c in result["ev"]["calls"]] == ["guessit"]
    record = triage.evidence_record(result["ev"], result["opinions"], 0, 0, extra_calls=[{"tool": "tmdb"}])
    assert [c["tool"] for c in record["raw"]["calls"]] == ["guessit", "tmdb"]


def test_evaluate_records_the_tmdb_verification_alongside_the_local_tools(monkeypatch):
    def fake_gather(path, base_dir=None, g=None):
        calls_module.record("guessit", "filename", ["guessit", path], "{}", 0.0, 0)
        return {"guessit": None, "guessit_parse": {}, "metadata": None, "metadata_parse": None, "nfo_ids": None}

    def fake_assess(ev):
        calls_module.record("tmdb", "/search/tv", [], "{}", 0.1, 200, command="GET x", status="HTTP 200")
        return {"identity": None, "conflict": False, "nfo": None}
    monkeypatch.setattr(triage, "gather", fake_gather)
    monkeypatch.setattr(triage, "assess", fake_assess)
    monkeypatch.setattr(triage, "guess", lambda path, label="filename": {})
    result = triage.evaluate("a.mkv", None)
    assert [c["tool"] for c in result["ev"]["calls"]] == ["guessit", "tmdb"]


def _evaluate_with(monkeypatch, duration, expected_minutes):
    identity = {"kind": "movie", "id": 1, "title": "Dune", "year": 2021}
    ev_ = {"guessit": op("Dune", 2021, kind="movie"), "guessit_parse": {}, "metadata": None, "metadata_parse": None,
           "nfo_ids": None, "duration": duration}
    monkeypatch.setattr(triage, "gather", lambda path, base_dir=None, g=None: ev_)
    monkeypatch.setattr(triage, "assess", lambda ev: {"identity": identity, "conflict": False, "nfo": None})
    monkeypatch.setattr(triage, "guess", lambda path, label="filename": {})
    monkeypatch.setattr(triage.tmdb, "runtime_minutes", lambda ident: expected_minutes)
    return triage.evaluate("a.mkv", None)


def test_a_file_whose_length_matches_tmdbs_runtime_is_still_accepted(monkeypatch):
    result = _evaluate_with(monkeypatch, duration=155 * 60, expected_minutes=155)
    assert result["auto"] and result["runtime"]["ok"]


def test_a_file_far_shorter_or_longer_than_tmdbs_runtime_goes_to_a_human(monkeypatch):
    for minutes in (3, 400):
        result = _evaluate_with(monkeypatch, duration=minutes * 60, expected_minutes=155)
        assert not result["auto"] and not result["runtime"]["ok"]


def test_an_unknown_duration_or_runtime_does_not_block_acceptance(monkeypatch):
    assert _evaluate_with(monkeypatch, duration=None, expected_minutes=155)["auto"]
    assert _evaluate_with(monkeypatch, duration=3 * 60, expected_minutes=None)["auto"]


def test_runtime_estimate_uses_the_seasons_other_episodes_then_the_show(monkeypatch):
    ident = {"kind": "tv", "id": 7, "season": 16, "episode": 8}
    season = {"episodes": [{"episode_number": n, "runtime": r} for n, r in ((6, 70), (7, 72), (8, None), (9, 74))]}
    monkeypatch.setattr(tmdb, "_get", lambda path, **kw: season if path.endswith("/season/16") else {"episode_run_time": [60]})
    assert tmdb.runtime_estimate(ident) == (72, "season")
    season["episodes"] = [{"episode_number": 8, "runtime": None}]
    assert tmdb.runtime_estimate(ident) == (60, "show")
    assert tmdb.runtime_estimate({"kind": "movie", "id": 1}) is None


def test_an_estimated_runtime_is_shown_but_never_blocks_acceptance(monkeypatch):
    monkeypatch.setattr(triage.tmdb, "runtime_minutes", lambda ident: None)
    monkeypatch.setattr(triage.tmdb, "runtime_estimate", lambda ident: (72, "season"))
    check = triage.runtime_check(71.6 * 60, {"kind": "tv", "id": 7, "season": 16, "episode": 8})
    assert check == {"expected": 72, "actual": 71.6, "ok": None, "estimate": "season"}
    assert _evaluate_with(monkeypatch, duration=3 * 60, expected_minutes=None)["auto"]
