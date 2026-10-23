"""Tests for the review session: statuses, scanning, deciding, undoing, and path safety."""
import os
import re
import time
from pathlib import Path

import pytest

from tidyarr import calls, config, decisions, leftovers, session as session_module, tmdb, triage
from tests_support import GUESS, IDENTITY, OPINION, SHOW, evaluation  # noqa: F401

@pytest.fixture
def make(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "easy.mkv").write_bytes(b"easy" * 100)
    (inbox / "hard.mkv").write_bytes(b"hard" * 100)
    for video in inbox.iterdir():
        os.utime(video, (0, 0))  # long finished arriving, so nothing waits for it to be done
    monkeypatch.setattr(triage, "evaluate", lambda path, base_dir=None, g=None: evaluation(auto=path == "easy.mkv"))
    searches = []

    def recording_search(query, kind, **kw):
        searches.append(query)
        calls.record("tmdb", "/search/tv", [], "{}", 0.1, 200, command=f"GET https://api.themoviedb.org/3/search/tv?api_key=<redacted>&query={query}", status="HTTP 200")
        return [SHOW]
    monkeypatch.setattr(tmdb, "search", recording_search)
    monkeypatch.setattr(tmdb, "details", lambda kind, tmdb_id: SHOW if tmdb_id == 549 else None)
    monkeypatch.setattr(tmdb, "episode_info", lambda show_id, s, e: {"title": "Into the Mystic", "overview": "Cold open.", "air_date": "1991-01-02", "runtime": 44} if show_id == 549 else None)

    def build(**kwargs):
        sess = session_module.Session(inbox, tmp_path / "decisions.json", **kwargs)
        sess.searches = searches
        return sess
    return build


def by_path(sess):
    return {item["path"]: item for item in sess.list()}


def fid_of(sess, path):
    return by_path(sess)[path]["id"]


def test_files_start_pending_and_scan_accepts_only_what_the_evidence_settles(make):
    sess = make()
    assert {i["status"] for i in sess.list()} == {"pending"}
    items = {i["path"]: i for i in sess.scan()}
    assert items["easy.mkv"]["status"] == "decided" and items["easy.mkv"]["source"] == "auto"
    assert items["easy.mkv"]["name"] == "Law & Order (1990) - S26E02" and items["easy.mkv"]["consensus"] == "50%"
    assert items["hard.mkv"]["status"] == "review"
    assert len(decisions.load(sess.decisions_path)) == 1  # the automatic one was saved to disk


def test_deciding_records_the_choice_with_its_evidence(make):
    sess = make()
    sess.scan()
    summary = sess.decide(fid_of(sess, "hard.mkv"), "tv", 549)
    assert summary["status"] == "decided" and summary["source"] == "human" and summary["name"] == "Law & Order (1990) - S26E02"
    record = next(r for r in decisions.load(sess.decisions_path).values() if r["path_hint"] == "hard.mkv")
    assert record["tmdb"]["id"] == 549 and record["evidence"]["version"] == 1
    assert record["evidence"]["consensus"] == {"agreeing": 1, "spoke": 2}


def test_an_unknown_tmdb_id_is_refused(make):
    sess = make()
    with pytest.raises(ValueError, match="not found"):
        sess.decide(fid_of(sess, "hard.mkv"), "tv", 999999)


def test_undo_forgets_a_decision_and_the_file_is_reviewable_again(make):
    sess = make()
    sess.scan()
    fid = fid_of(sess, "easy.mkv")
    assert sess.undo(fid)["status"] == "pending"
    assert decisions.load(sess.decisions_path) == {}
    assert sess.undo(fid)["status"] == "pending"  # undoing twice is harmless


def test_clients_can_only_name_files_the_session_found(make):
    sess = make()
    for hostile in ("../../etc/passwd", "/etc/passwd", "easy.mkv", ""):
        with pytest.raises(KeyError):
            sess.entry(hostile)


def test_detail_of_a_file_under_review_shows_a_neutral_grid_and_candidates(make):
    sess = make()
    sess.scan()
    detail = sess.detail(fid_of(sess, "hard.mkv"))
    assert detail["status"] == "review" and detail["hints"] == {"query": "LnO", "kind": "tv"}
    assert 'class="known"' in detail["grid_html"] and "tmdb api" not in detail["grid_html"]
    assert detail["candidates"][0]["title"] == "Law & Order" and detail["candidates"][0]["poster"] == "https://image.tmdb.org/t/p/w185/abc123.jpg"
    assert detail["candidates"][0]["url"] == "https://www.themoviedb.org/tv/549"


def test_detail_of_a_decided_file_colours_the_grid_against_the_answer(make):
    sess = make()
    sess.scan()
    detail = sess.detail(fid_of(sess, "easy.mkv"))
    assert detail["decision"]["name"] == "Law & Order (1990) - S26E02" and "tmdb api" in detail["grid_html"]
    assert 'class="bad">LnO<' in detail["grid_html"]


def test_a_decided_file_shows_the_matched_posters_and_survives_a_failed_lookup(make, monkeypatch):
    sess = make()
    sess.scan()
    fid = fid_of(sess, "easy.mkv")
    assert sess.detail(fid)["decision"]["poster"] == "https://image.tmdb.org/t/p/w342/abc123.jpg"

    def down(kind, tmdb_id):
        raise RuntimeError("Could not reach TMDB")
    monkeypatch.setattr(tmdb, "details", down)
    assert sess.detail(fid)["decision"]["poster"] is None


@pytest.mark.parametrize("path, ok", [("/abc123.jpg", True), ("/a-b_c.d.png", True), ("/../../x.jpg", False), ("javascript:alert(1)", False),
                                      ("/x.jpg?evil=1", False), (None, False), (5, False)])
def test_poster_urls_only_accept_the_shape_of_a_tmdb_image_path(path, ok):
    assert (session_module.poster_url(path) is not None) is ok


def test_each_step_of_the_flow_has_a_hover_card_of_what_it_returned(make):
    sess = make()
    sess.scan()
    for path in ("hard.mkv", "easy.mkv"):  # one under review, one decided
        tips = sess.detail(fid_of(sess, path))["tips"]
        assert "guessit -j -- LnO.26x02.mkv" in tips["guessit"] and 'class="prompt">$</span>' in tips["guessit"]  # the command, drawn like a terminal
        assert all(re.fullmatch(r"guessit|metadata|probe|nfo|tmdb\d*", name) for name in tips)


def test_the_first_search_is_made_once_and_kept(make):
    sess = make()
    sess.scan()
    fid = fid_of(sess, "hard.mkv")
    sess.detail(fid)
    sess.detail(fid)
    assert sess.searches == ["LnO"]  # reopening the file did not search again


def test_searches_and_the_choice_are_kept_in_the_transcript_saved_with_the_decision(make, monkeypatch):
    sess = make()
    sess.scan()
    fid = fid_of(sess, "hard.mkv")
    sess.detail(fid)
    sess.search(fid, "law and order", "tv")
    html = sess.detail(fid)["tips"]["tmdb"]
    assert html.count("GET https://api.themoviedb.org/3/search/tv") == 2 and "query=law and order" in html and "api_key=&lt;redacted&gt;" in html
    sess.decide(fid, "tv", 549)
    record = next(r for r in decisions.load(sess.decisions_path).values() if r["path_hint"] == "hard.mkv")
    tmdb_calls = [c for c in record["evidence"]["raw"]["calls"] if c["tool"] == "tmdb"]
    assert [c["command"].split("query=")[-1] for c in tmdb_calls] == ["LnO", "law and order"]


def test_undoing_forgets_the_searches_so_the_next_review_starts_clean(make):
    sess = make()
    sess.scan()
    fid = fid_of(sess, "hard.mkv")
    sess.detail(fid)
    sess.undo(fid)
    assert sess.entry(fid)["extra_calls"] == [] and sess.entry(fid)["initial"] is None


def test_stats_report_tmdb_requests(make):
    assert set(make().stats()) == {"tmdb_requests"}


FORMATS = {"tv": "TV/{SHOW_NAME}/Season {SEASON_NUMBER}/{SHOW_NAME} - S{SEASON_NUMBER}E{EPISODE_NUMBER} - {EPISODE_NAME}.{EXTENSION}",
           "movie": "Movies/{MOVIE_NAME} ({MOVIE_YEAR}).{EXTENSION}"}


@pytest.fixture
def moving(make, tmp_path):
    sess = make()
    sess.output_dir = tmp_path / "out"
    sess.output_dir.mkdir()
    sess.scan()
    return sess


def test_approving_moves_the_file_to_its_new_name_and_remembers_it(moving, tmp_path):
    fid = fid_of(moving, "easy.mkv")
    summary = moving.approve(fid, FORMATS)
    target = tmp_path / "out" / "TV/Law & Order/Season 26/Law & Order - S26E02 - Into the Mystic.mkv"
    assert summary["status"] == "moved" and target.read_bytes() == b"easy" * 100
    assert not (tmp_path / "inbox" / "easy.mkv").exists()
    record = decisions.load(moving.decisions_path)[moving.entry(fid)["key"]]
    assert record["moved"]["to"] == str(target) and record["moved"]["from"] == str(tmp_path / "inbox" / "easy.mkv")
    moving.refresh()  # the inbox no longer has it, but it stays listed so it can be put back
    assert by_path(moving)["easy.mkv"]["status"] == "moved" and moving.original_path(fid) == target


def test_approving_never_replaces_a_file_already_there(moving, tmp_path):
    taken = tmp_path / "out" / "TV/Law & Order/Season 26/Law & Order - S26E02 - Into the Mystic.mkv"
    taken.parent.mkdir(parents=True)
    taken.write_bytes(b"precious")
    with pytest.raises(ValueError, match="already there"):
        moving.approve(fid_of(moving, "easy.mkv"), FORMATS)
    assert taken.read_bytes() == b"precious" and (tmp_path / "inbox" / "easy.mkv").exists()
    assert by_path(moving)["easy.mkv"]["status"] == "decided"


def test_only_decided_unmoved_files_can_be_approved(moving):
    with pytest.raises(ValueError, match="decide what this file is"):
        moving.approve(fid_of(moving, "hard.mkv"), FORMATS)
    fid = fid_of(moving, "easy.mkv")
    moving.approve(fid, FORMATS)
    for action in (lambda: moving.approve(fid, FORMATS), lambda: moving.undo(fid), lambda: moving.decide(fid, "tv", 549)):
        with pytest.raises(ValueError, match="put it back first"):
            action()


def test_restoring_puts_the_file_back_and_tidies_the_empty_folders(moving, tmp_path):
    fid = fid_of(moving, "easy.mkv")
    moving.approve(fid, FORMATS)
    assert moving.restore(fid)["status"] == "decided"  # the decision is kept; only the move is undone
    assert (tmp_path / "inbox" / "easy.mkv").read_bytes() == b"easy" * 100
    assert list((tmp_path / "out").iterdir()) == []
    assert "moved" not in decisions.load(moving.decisions_path)[moving.entry(fid)["key"]]
    with pytest.raises(ValueError, match="hasn't been moved"):
        moving.restore(fid)


def test_detail_explains_the_match_in_plain_words(moving):
    clues = {c["source"]: c for c in moving.detail(fid_of(moving, "easy.mkv"))["clues"]}
    assert clues["File name"] == {"source": "File name", "said": "LnO · S26E02", "agrees": False}
    assert clues["Title inside the video"]["agrees"] is True and clues["Info file (.nfo)"]["said"] == "Nothing found"
    assert "AI guess" not in clues
    assert all(c["agrees"] is None for c in moving.detail(fid_of(moving, "hard.mkv"))["clues"])  # no answer yet to agree with


def test_subtitles_move_with_the_video_and_come_back_with_it(moving, tmp_path):
    inbox = tmp_path / "inbox"
    (inbox / "easy.en.srt").write_text("1")
    (inbox / "Subs/easy").mkdir(parents=True)
    (inbox / "Subs/easy/2_French.srt").write_text("2")
    fid = fid_of(moving, "easy.mkv")
    relative = moving.destination(fid, FORMATS)
    season = tmp_path / "out/TV/Law & Order/Season 26"
    assert moving.subtitle_destinations(fid, relative) == [f"{Path(relative).parent}/Law & Order - S26E02 - Into the Mystic{t}" for t in (".en.srt", ".French.srt")]
    moving.approve(fid, FORMATS)
    assert (season / "Law & Order - S26E02 - Into the Mystic.en.srt").read_text() == "1"
    assert (season / "Law & Order - S26E02 - Into the Mystic.French.srt").read_text() == "2"
    record = decisions.load(moving.decisions_path)[moving.entry(fid)["key"]]
    assert record["moved"]["subtitles"][0] == {"from": str(inbox / "easy.en.srt"), "to": str(season / "Law & Order - S26E02 - Into the Mystic.en.srt")}
    moving.restore(fid)
    assert (inbox / "easy.en.srt").read_text() == "1" and (inbox / "Subs/easy/2_French.srt").read_text() == "2"
    assert list((tmp_path / "out").iterdir()) == []


def test_nothing_moves_when_a_subtitle_name_is_taken(moving, tmp_path):
    (tmp_path / "inbox/easy.en.srt").write_text("1")
    taken = tmp_path / "out/TV/Law & Order/Season 26/Law & Order - S26E02 - Into the Mystic.en.srt"
    taken.parent.mkdir(parents=True)
    taken.write_text("precious")
    with pytest.raises(ValueError, match="already there"):
        moving.approve(fid_of(moving, "easy.mkv"), FORMATS)
    assert taken.read_text() == "precious" and (tmp_path / "inbox/easy.mkv").exists() and (tmp_path / "inbox/easy.en.srt").exists()


def test_leftovers_are_offered_for_a_release_folder_once_its_video_is_moved(make, tmp_path, monkeypatch):
    release = tmp_path / "inbox/Law.and.Order.S26E02"
    release.mkdir()
    (tmp_path / "inbox/easy.mkv").rename(release / "easy.mkv")
    (release / "site.jpg").write_bytes(b"x")
    (release / "Subs").mkdir()
    sess = make()
    sess.output_dir = tmp_path / "out"
    sess.output_dir.mkdir()
    sess.scan()
    assert sess.leftovers(config.JUNK) == []  # the video is still there
    fid = fid_of(sess, "Law.and.Order.S26E02/easy.mkv")
    sess.decide(fid, "tv", 549)
    sess.approve(fid, FORMATS)
    [offer] = sess.leftovers(config.JUNK)
    assert offer["folder"] == "Law.and.Order.S26E02" and offer["junk"] == ["site.jpg"] and offer["kept"] == []
    trashed = []
    monkeypatch.setattr(leftovers, "send2trash", lambda path: (trashed.append(path), Path(path).unlink()))
    assert sess.clean_leftovers(offer["id"], config.JUNK, ["site.jpg"])["trashed"] == 1
    assert not release.exists() and sess.leftovers(config.JUNK) == []
    sess.restore(fid)  # putting the video back recreates its folder
    assert (release / "easy.mkv").is_file()
    with pytest.raises(KeyError):
        sess.clean_leftovers("nope", config.JUNK, [])


def test_the_setting_removes_a_release_folder_the_move_left_empty_and_nothing_else(make, tmp_path):
    inbox = tmp_path / "inbox"
    for name, video in (("Empty.Release", "easy.mkv"), ("Busy.Release", "hard.mkv")):
        (inbox / name / "Subs").mkdir(parents=True)
        (inbox / video).rename(inbox / name / video)
    (inbox / "Empty.Release/Subs/easy.en.srt").write_text("1")  # moves with the video, so the folder ends up empty
    (inbox / "Busy.Release/site.jpg").write_bytes(b"x")
    sess = make()
    sess.output_dir = tmp_path / "out"
    sess.output_dir.mkdir()
    sess.scan()
    easy, hard = fid_of(sess, "Empty.Release/easy.mkv"), fid_of(sess, "Busy.Release/hard.mkv")
    sess.decide(easy, "tv", 549)
    sess.decide(hard, "tv", 549)
    sess.approve(easy, FORMATS, remove_empty=True)
    assert not (inbox / "Empty.Release").exists()
    sess.approve(hard, {**FORMATS, "tv": "Other/{SHOW_NAME}.{EXTENSION}"}, remove_empty=True)
    assert (inbox / "Busy.Release/site.jpg").is_file()  # junk is only ever trashed on a click
    sess.restore(easy)  # putting it back recreates the folder
    assert (inbox / "Empty.Release/easy.mkv").is_file() and (inbox / "Empty.Release/Subs/easy.en.srt").is_file()


def test_deleting_trashes_the_file_and_its_subtitles_and_forgets_its_decision(make, tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    (inbox / "Release").mkdir()
    (inbox / "easy.mkv").rename(inbox / "Release/easy.mkv")
    (inbox / "Release/easy.en.srt").write_text("1")
    sess = make()
    sess.scan()
    fid = fid_of(sess, "Release/easy.mkv")
    trashed = []
    monkeypatch.setattr(leftovers, "send2trash", lambda path: (trashed.append(Path(path).name), Path(path).unlink()))
    assert sess.delete(fid, remove_empty=True) == {"path": "Release/easy.mkv"}
    assert sorted(trashed) == ["easy.en.srt", "easy.mkv"] and not (inbox / "Release").exists()
    assert "Release/easy.mkv" not in by_path(sess) and decisions.load(sess.decisions_path) == {}
    with pytest.raises(KeyError):
        sess.delete(fid)


def test_a_moved_file_is_not_deleted(moving, monkeypatch):
    monkeypatch.setattr(leftovers, "send2trash", lambda path: pytest.fail("nothing should be trashed"))
    fid = fid_of(moving, "easy.mkv")
    moving.approve(fid, FORMATS)
    with pytest.raises(ValueError, match="put it back first"):
        moving.delete(fid)


def test_detail_of_a_file_under_review_carries_the_runtime_comparison(make, monkeypatch):
    sess = make()
    mismatch = {"expected": 44, "actual": 3.0, "ok": False}
    monkeypatch.setattr(triage, "evaluate", lambda path, base_dir=None, g=None: {**evaluation(auto=False), "runtime": mismatch})
    sess.scan()
    assert sess.detail(fid_of(sess, "hard.mkv"))["runtime"] == mismatch


def test_detail_of_a_decided_file_compares_the_stored_duration_with_tmdb(make, monkeypatch):
    sess = make()
    sess.scan()
    record = sess.known[sess.files[fid_of(sess, "easy.mkv")]["key"]]
    record["evidence"] = {"version": 1, "sources": {}, "consensus": {"agreeing": 1, "spoke": 1}, "raw": {"probe": {"duration": 44 * 60}}}
    monkeypatch.setattr(tmdb, "runtime_minutes", lambda ident: 44)
    assert sess.detail(fid_of(sess, "easy.mkv"))["runtime"] == {"expected": 44, "actual": 44.0, "ok": True, "estimate": None}
    monkeypatch.setattr(tmdb, "runtime_minutes", lambda ident: (_ for _ in ()).throw(RuntimeError("down")))
    assert sess.detail(fid_of(sess, "easy.mkv"))["runtime"] is None


def test_debug_text_summarises_the_file_and_hides_the_folders(make, tmp_path):
    sess = make()
    sess.scan()
    for name in ("easy.mkv", "hard.mkv"):  # one decided, one under review
        text = sess.debug_text(fid_of(sess, name))["text"]
        assert f"file: {name}" in text and "## transcript" in text and "guessit -j" in text
        assert str(tmp_path) not in text


def test_writing_tells_which_inbox_files_look_like_downloads_still_in_progress(make, tmp_path):
    sess = make()
    os.utime(tmp_path / "inbox" / "hard.mkv", (time.time() + 60,) * 2)
    assert sess.writing() == {fid_of(sess, "easy.mkv"): False, fid_of(sess, "hard.mkv"): True}


def test_frames_are_not_picked_from_a_file_still_being_written(make, tmp_path, monkeypatch):
    sess = make()
    os.utime(tmp_path / "inbox" / "hard.mkv", (time.time() + 60,) * 2)
    monkeypatch.setattr(session_module.probe, "probe", lambda path: pytest.fail("a part-written file was read"))
    with pytest.raises(ValueError, match="still being written"):
        sess.frames(fid_of(sess, "hard.mkv"))


def test_a_file_still_downloading_is_named_from_its_filename_but_accepted_only_once_it_is_whole(make):
    session = make()
    os.utime(session.base_dir / "easy.mkv", (time.time() + 60,) * 2)  # still being written
    assert {f["path"]: f["status"] for f in session.scan()}["easy.mkv"] == "review"
    session.writing()  # sees it being written
    os.utime(session.base_dir / "easy.mkv", (0, 0))
    session.writing()  # notices it settled
    assert {f["path"]: f["status"] for f in session.scan()}["easy.mkv"] == "decided"
