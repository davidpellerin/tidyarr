"""Tests for the FastAPI app: the local-only guards first, then the endpoints."""
import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tidyarr import folders, leftovers, naming, server, session as session_module, tmdb, triage
from tests_support import SHOW, evaluation

TOKEN = "t" * 32
HOSTS = {"127.0.0.1:8765", "localhost:8765"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "easy.mkv").write_bytes(b"easy" * 100)
    (inbox / "hard.mkv").write_bytes(b"hard" * 100)
    for video in inbox.iterdir():
        os.utime(video, (0, 0))  # long finished arriving, so nothing waits for it to be done
    monkeypatch.setattr(triage, "evaluate", lambda path, base_dir=None, g=None: evaluation(auto=path == "easy.mkv"))
    monkeypatch.setattr(tmdb, "search", lambda *a, **kw: [SHOW])
    monkeypatch.setattr(tmdb, "details", lambda kind, tmdb_id: SHOW if tmdb_id == 549 else None)
    monkeypatch.setattr(tmdb, "episode_info", lambda *a: {"title": "Into the Mystic", "overview": "", "air_date": None, "runtime": None})
    sess = session_module.Session(inbox, tmp_path / "decisions.json")
    sess.output_dir = tmp_path / "out"
    sess.output_dir.mkdir()
    app = server.create_app(sess, TOKEN, HOSTS)
    return TestClient(app, base_url="http://127.0.0.1:8765", headers={"X-Token": TOKEN})


def fid(client, path):
    return next(f["id"] for f in client.get("/api/files").json() if f["path"] == path)


def test_the_api_refuses_requests_without_the_right_token(client):
    assert client.get("/api/files", headers={"X-Token": ""}).status_code == 401
    assert client.get("/api/files", headers={"X-Token": "wrong" * 7}).status_code == 401
    assert client.post("/api/scan", headers={"X-Token": "wrong"}).status_code == 401
    assert client.get("/api/files").status_code == 200


def test_requests_for_another_host_name_are_refused_against_dns_rebinding(client):
    assert client.get("/api/files", headers={"Host": "evil.example:8765"}).status_code == 403
    assert client.get("/", headers={"Host": "evil.example"}).status_code == 403
    assert client.get("/api/files", headers={"Host": "localhost:8765"}).status_code == 200


def test_writes_from_another_origin_are_refused_but_same_origin_and_no_origin_work(client):
    assert client.post("/api/scan", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/scan", headers={"Origin": "http://127.0.0.1:8765"}).status_code == 200
    assert client.post("/api/scan").status_code == 200


def test_every_response_carries_the_security_headers_and_no_api_docs_exist(client):
    for response in (client.get("/"), client.get("/api/files"), client.get("/api/files", headers={"X-Token": ""})):
        assert "script-src 'self'" in response.headers["content-security-policy"] and "frame-ancestors 'none'" in response.headers["content-security-policy"]
        assert response.headers["x-content-type-options"] == "nosniff" and response.headers["cache-control"] == "no-store"
    assert [client.get(path).status_code for path in ("/docs", "/redoc", "/openapi.json")] == [404, 404, 404]


def test_the_page_can_ask_which_files_are_still_being_written(client, tmp_path):
    os.utime(tmp_path / "inbox" / "hard.mkv", (time.time() + 60,) * 2)
    assert client.get("/api/files/writing").json() == {fid(client, "easy.mkv"): False, fid(client, "hard.mkv"): True}


def test_a_file_still_being_written_is_not_served_to_the_player(client, tmp_path):
    os.utime(tmp_path / "inbox" / "hard.mkv", (time.time() + 60,) * 2)
    response = client.get(f"/api/files/{fid(client, 'hard.mkv')}/original")
    assert response.status_code == 400 and "still being written" in response.json()["detail"]
    assert client.head(f"/api/files/{fid(client, 'easy.mkv')}/original").status_code == 200


def test_the_page_and_its_assets_are_served(client):
    assert "<title>Tidyarr</title>" in client.get("/").text
    assert client.get("/app.js").headers["content-type"].startswith("text/javascript")
    assert ".rv-app" in client.get("/app.css").text and "table.matrix" in client.get("/report.css").text


def test_scan_then_review_then_decide_over_http(client):
    items = {i["path"]: i for i in client.post("/api/scan").json()}
    assert items["easy.mkv"]["status"] == "decided" and items["hard.mkv"]["status"] == "review"
    detail = client.get(f"/api/files/{items['hard.mkv']['id']}").json()
    assert detail["candidates"][0]["episode"]["title"] == "Into the Mystic"
    decided = client.post(f"/api/files/{items['hard.mkv']['id']}/decide", json={"kind": "tv", "id": 549})
    assert decided.status_code == 200 and decided.json()["source"] == "human"
    assert client.post(f"/api/files/{items['hard.mkv']['id']}/undo").json()["status"] == "pending"


def test_unknown_files_and_bad_input_get_clean_errors(client):
    assert client.get("/api/files/nope").status_code == 404
    assert client.get("/api/files/..%2F..%2Fetc%2Fpasswd").status_code == 404
    f = fid(client, "hard.mkv")
    assert client.post(f"/api/files/{f}/search", json={"query": "", "kind": "tv"}).status_code == 422
    assert client.post(f"/api/files/{f}/search", json={"query": "x" * 201, "kind": "tv"}).status_code == 422
    assert client.post(f"/api/files/{f}/search", json={"query": "x", "kind": "book"}).status_code == 422
    assert client.post(f"/api/files/{f}/decide", json={"kind": "tv", "id": -1}).status_code == 422
    assert client.post(f"/api/files/{f}/decide", json={"kind": "tv", "id": 999999}).status_code == 400


def test_a_failing_upstream_becomes_a_502_with_a_safe_message(client, monkeypatch):
    def down(*a, **kw):
        raise RuntimeError("Could not reach TMDB: timed out")
    monkeypatch.setattr(tmdb, "search", down)
    response = client.post(f"/api/files/{fid(client, 'hard.mkv')}/search", json={"query": "x", "kind": "tv"})
    assert response.status_code == 502 and response.json()["detail"] == "Could not reach TMDB: timed out"


def test_the_stats_endpoint_needs_the_token_like_everything_else(client):
    assert client.get("/api/stats", headers={"X-Token": ""}).status_code == 401
    body = client.get("/api/stats").json()
    assert set(body) == {"tmdb_requests"}


def test_the_page_may_play_video_only_from_memory_never_from_other_places(client):
    csp = client.get("/").headers["content-security-policy"]
    assert "media-src blob:" in csp and "media-src *" not in csp and "default-src 'none'" in csp


def test_the_original_file_is_served_with_ranges_and_head_for_the_in_browser_player(client):
    f = fid(client, "hard.mkv")
    assert client.get(f"/api/files/{f}/original", headers={"X-Token": ""}).status_code == 401
    assert client.head(f"/api/files/{f}/original").status_code == 200
    part = client.get(f"/api/files/{f}/original", headers={"Range": "bytes=4-7"})
    assert part.status_code == 206 and part.content == b"hard"
    assert client.get("/api/files/nope/original").status_code == 404


def test_frames_are_picked_once_and_returned_as_images_the_page_may_show(client, monkeypatch):
    from tidyarr import frames, probe
    calls = []
    monkeypatch.setattr(probe, "probe", lambda path: {"duration": 600.0})
    monkeypatch.setattr(frames, "pick", lambda path, duration: calls.append(duration) or [{"time": 75.0, "jpeg": b"jpg"}])
    f = fid(client, "hard.mkv")
    assert client.get(f"/api/files/{f}/frames", headers={"X-Token": ""}).status_code == 401
    body = client.get(f"/api/files/{f}/frames").json()
    assert body == [{"time": 75.0, "at": "1m 15s", "jpeg": "anBn"}]
    assert client.get(f"/api/files/{f}/frames").status_code == 200 and calls == [600.0]
    assert client.get("/api/files/nope/frames").status_code == 404
    assert "img-src https://image.tmdb.org blob:" in client.get("/").headers["content-security-policy"]  # the page shows them from blob: URLs


def test_images_come_from_the_top_suggestion_while_under_review_and_from_the_match_once_decided(client, monkeypatch):
    asked = []
    art = [{"kind": "still", "path": "/still0abc.jpg", "width": 1280, "height": 720}, {"kind": "poster", "path": "/poster1abc.jpg", "width": 500, "height": 750},
           {"kind": "logo", "path": "../../etc/passwd", "width": 1, "height": 1}]   # a path of the wrong shape is never turned into a URL
    monkeypatch.setattr(tmdb, "images", lambda kind, tmdb_id, season=None, episode=None: asked.append((kind, tmdb_id, season, episode)) or art)
    f = fid(client, "hard.mkv")
    assert client.get(f"/api/files/{f}/images", headers={"X-Token": ""}).status_code == 401
    assert client.get("/api/files/nope/images").status_code == 404
    assert client.get(f"/api/files/{f}/images").json() == {"title": None, "decided": False, "images": []}   # nothing suggested yet
    client.get(f"/api/files/{f}")   # opening the file makes the first search
    body = client.get(f"/api/files/{f}/images").json()
    assert body["decided"] is False and body["title"] == "Law & Order (1990) - S26E02"
    assert [i["label"] for i in body["images"]] == ["Episode still", "Poster"]
    assert body["images"][0] == {"label": "Episode still", "thumb": "https://image.tmdb.org/t/p/w300/still0abc.jpg",
                                 "full": "https://image.tmdb.org/t/p/original/still0abc.jpg", "width": 1280, "height": 720}
    assert body["images"][1]["thumb"].startswith("https://image.tmdb.org/t/p/w342/")
    assert asked[-1] == ("tv", 549, 26, 2)   # the suggestion's own episode, not just the show
    client.post(f"/api/files/{f}/decide", json={"kind": "tv", "id": 549})
    assert client.get(f"/api/files/{f}/images").json()["decided"] is True


def test_an_upstream_failure_fetching_images_is_a_502_with_a_safe_message(client, monkeypatch):
    def down(*a, **kw):
        raise RuntimeError("Could not reach TMDB: timed out")
    monkeypatch.setattr(tmdb, "images", down)
    f = fid(client, "hard.mkv")
    client.get(f"/api/files/{f}")
    response = client.get(f"/api/files/{f}/images")
    assert response.status_code == 502 and response.json()["detail"] == "Could not reach TMDB: timed out"


def test_only_the_named_files_are_served_from_vendor(client):
    assert client.get("/vendor/../server.py").status_code == 404
    assert client.get("/vendor/anything.js").status_code == 404


def test_approve_moves_the_file_and_restore_puts_it_back_over_http(client, tmp_path):
    client.post("/api/scan")
    f = fid(client, "easy.mkv")
    formats = {"tv": "TV/{SHOW_NAME}/S{SEASON_NUMBER}E{EPISODE_NUMBER}.{EXTENSION}", "movie": "Movies/{MOVIE_NAME}.{EXTENSION}"}
    (tmp_path / "inbox/easy.en.srt").write_text("subs")
    assert client.post(f"/api/files/{f}/destination", json=formats).json() == {"path": f"{folders.Roots.shown(tmp_path / 'out')}/TV/Law & Order/S26E02.mkv",
                                                                               "relative": "TV/Law & Order/S26E02.mkv", "subtitles": ["S26E02.en.srt"], "taken": False}
    assert client.post(f"/api/files/{f}/approve", json=formats).json()["status"] == "moved"
    assert (tmp_path / "out/TV/Law & Order/S26E02.mkv").is_file() and (tmp_path / "out/TV/Law & Order/S26E02.en.srt").is_file()
    assert client.post(f"/api/files/{f}/approve", json=formats).status_code == 400
    assert client.post(f"/api/files/{f}/restore").json()["status"] == "decided"
    assert (tmp_path / "inbox/easy.mkv").is_file()
    assert client.post(f"/api/files/{f}/approve", json={"tv": "/etc/x", "movie": "x"}).status_code == 400
    assert client.post(f"/api/files/{f}/approve", json=formats, headers={"Origin": "https://evil.example"}).status_code == 403


def test_delete_trashes_the_file_over_http(client, tmp_path, monkeypatch):
    monkeypatch.setattr(leftovers, "send2trash", lambda path: Path(path).unlink())
    client.post("/api/scan")
    f = fid(client, "easy.mkv")
    assert client.post(f"/api/files/{f}/delete", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post(f"/api/files/{f}/delete").json() == {"path": "easy.mkv"}
    assert not (tmp_path / "inbox/easy.mkv").exists()
    assert client.post(f"/api/files/{f}/delete").status_code == 404


GOOD_FORMATS = {"tv_format": "TV/{SHOW_NAME}/S{SEASON_NUMBER}E{EPISODE_NUMBER}.{EXTENSION}", "movie_format": "Movies/{MOVIE_NAME}.{EXTENSION}"}


def test_settings_are_saved_to_the_settings_file_and_the_key_is_sent_back_for_the_field(client, settings_file, monkeypatch):
    monkeypatch.delenv("TMDB_API_KEY", raising=False)
    checked = []
    monkeypatch.setattr(tmdb, "check_key", checked.append)
    assert client.get("/api/folders").json()["tmdb_key"] == {"source": None, "key": None}
    key = "0123456789abcdef0123456789abcdef"
    reply = client.post("/api/settings", json={**GOOD_FORMATS, "tmdb_api_key": f"  {key} "})
    assert reply.status_code == 200 and checked == [key]
    assert reply.json() == {"formats": GOOD_FORMATS, "tmdb_key": {"source": "settings", "key": key}, "remove_empty_folders": False}
    assert json.loads(settings_file.read_text()) == {**GOOD_FORMATS, "tmdb_api_key": key}
    assert client.get("/api/folders").json()["formats"] == GOOD_FORMATS and client.get("/api/folders").json()["tmdb_key"]["key"] == key
    assert client.post("/api/settings", json=GOOD_FORMATS).status_code == 200  # no key typed: the saved one stays
    assert json.loads(settings_file.read_text())["tmdb_api_key"] == key
    assert client.get("/api/folders").json()["remove_empty_folders"] is False
    assert client.post("/api/settings", json={**GOOD_FORMATS, "remove_empty_folders": True}).json()["remove_empty_folders"] is True
    assert client.post("/api/settings", json=GOOD_FORMATS).json()["remove_empty_folders"] is True  # not sent: kept
    assert client.get("/api/folders").json()["remove_empty_folders"] is True


def test_the_default_formats_are_offered_for_the_reset_button(client):
    assert client.get("/api/folders").json()["default_formats"] == {"tv_format": naming.DEFAULT_TV, "movie_format": naming.DEFAULT_MOVIE}


def test_settings_that_dont_check_out_are_not_saved(client, settings_file, monkeypatch):
    def refuse(key):
        raise ValueError("TMDB doesn't accept that key")
    monkeypatch.setattr(tmdb, "check_key", refuse)
    reply = client.post("/api/settings", json={**GOOD_FORMATS, "tmdb_api_key": "secret-but-wrong"})
    assert reply.status_code == 400 and "secret-but-wrong" not in reply.text
    assert client.post("/api/settings", json={**GOOD_FORMATS, "tv_format": "/etc/{SHOW_NAME}"}).status_code == 400
    assert not settings_file.exists()
    other_site = client.post("/api/settings", json=GOOD_FORMATS, headers={"Origin": "https://evil.example"})
    assert other_site.status_code == 403 and not settings_file.exists()


def test_preview_shows_the_example_path_for_each_format_or_why_it_cant(client):
    reply = client.post("/api/preview", json={"tv": "Series/{SHOW_NAME} ({SHOW_YEAR})/Season {SEASON_NUMBER}/{SHOW_NAME} S{SEASON_NUMBER}E{EPISODE_NUMBER}.{EXTENSION}",
                                              "movie": "/etc/{MOVIE_NAME}"})
    assert reply.status_code == 200
    assert reply.json()["tv"] == {"path": "Series/Example (2024)/Season 01/Example S01E01.mkv"}
    assert "error" in reply.json()["movie"]


def test_ping_answers_with_the_pid_and_any_authenticated_request_counts_as_the_page_being_open(client):
    client.app.state.last_seen = 0
    assert client.get("/api/ping", headers={"X-Token": ""}).status_code == 401
    assert client.app.state.last_seen == 0
    assert client.get("/api/ping").json() == {"pid": os.getpid()}
    assert client.app.state.last_seen > 0


def test_quit_stops_the_server_only_if_it_was_given_a_way_to(tmp_path, client):
    assert client.post("/api/quit").status_code == 404
    stopped = []
    app = server.create_app(session_module.Session(tmp_path, tmp_path / "decisions.json"), TOKEN, HOSTS, quit=lambda: stopped.append(True))
    quitter = TestClient(app, base_url="http://127.0.0.1:8765", headers={"X-Token": TOKEN})
    assert quitter.post("/api/quit", headers={"X-Token": "wrong"}).status_code == 401 and not stopped
    assert quitter.post("/api/quit").status_code == 200 and stopped == [True]


def test_leftovers_are_listed_and_cleaned_only_with_the_list_that_was_shown(client):
    assert client.get("/api/leftovers").json() == []
    assert client.post("/api/leftovers/nope/clean", json={"junk": []}).status_code == 404
    assert client.post("/api/leftovers/nope/clean", json={"junk": []}, headers={"Origin": "https://evil.example"}).status_code == 403
