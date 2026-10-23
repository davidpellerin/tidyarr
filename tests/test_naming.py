"""Tests for naming formats: token filling, cleaning of values, and refusal of unsafe formats."""
import os

import pytest
from fastapi.testclient import TestClient

from tidyarr import naming, server, session as session_module, tmdb, triage
from tests_support import evaluation

EPISODE = {"kind": "tv", "id": 549, "title": "Mr. Robot", "year": 2015, "season": 2, "episode": 3}
FILM = {"kind": "movie", "id": 9, "title": "Alien: Resurrection", "year": 1997}


def path(template, ident, ext=".mkv", episode_name="Into the Mystic"):
    return naming.render(template, ident["kind"], naming.values(ident, ext, episode_name)).as_posix()


def test_the_default_formats_give_sonarr_and_radarr_style_paths():
    assert path(naming.DEFAULT_TV, EPISODE) == "Series/Mr. Robot/Season 02/S02E03 - Into the Mystic.mkv"
    assert path(naming.DEFAULT_MOVIE, FILM, ".mp4") == "Movies/Alien - Resurrection (1997)/Alien - Resurrection (1997).mp4"


def test_a_token_followed_by_a_letter_is_still_that_token():
    assert path("S{SEASON_NUMBER}E{EPISODE_NUMBER}.{EXTENSION}", EPISODE) == "S02E03.mkv"


def test_values_cannot_add_folders_or_climb_out():
    ident = {**EPISODE, "title": "../../etc/passwd"}
    assert path("{SHOW_NAME}/x.{EXTENSION}", ident) == "etcpasswd/x.mkv"
    assert path("{SHOW_NAME}.{EXTENSION}", {**EPISODE, "title": "a/b\\c"}) == "abc.mkv"
    with pytest.raises(ValueError, match="no value for \\{SHOW_NAME\\}"):
        path("{SHOW_NAME}/x", {**EPISODE, "title": ".."})


@pytest.mark.parametrize("template", ["", "/abs/{SHOW_NAME}", "~/Videos/{SHOW_NAME}", "../{SHOW_NAME}", "a/../b", "a\\b", "{NOPE}.{EXTENSION}", "{MOVIE_NAME}.{EXTENSION}", "{SHOW_NAME.{EXTENSION}", "SHOW_NAME}.{EXTENSION}", "$SHOW_NAME.{EXTENSION}", "x" * 400])
def test_unsafe_or_unknown_formats_are_refused(template):
    with pytest.raises(ValueError):
        naming.check(template, "tv")


def test_a_missing_value_is_an_error_not_an_empty_name():
    with pytest.raises(ValueError, match="EPISODE_NAME"):
        path(naming.DEFAULT_TV, EPISODE, episode_name=None)
    with pytest.raises(ValueError, match="MOVIE_YEAR"):
        path(naming.DEFAULT_MOVIE, {**FILM, "year": None})


@pytest.fixture
def client(tmp_path, monkeypatch):
    (tmp_path / "in").mkdir()
    (tmp_path / "out").mkdir()
    (tmp_path / "in" / "easy.mkv").write_bytes(b"easy" * 100)
    (tmp_path / "in" / "hard.mkv").write_bytes(b"hard" * 100)
    for video in (tmp_path / "in").iterdir():
        os.utime(video, (0, 0))  # long finished arriving, so the scan doesn't wait for it
    monkeypatch.setattr(triage, "evaluate", lambda p, base_dir=None, g=None: evaluation(auto=p == "easy.mkv"))
    monkeypatch.setattr(tmdb, "episode_title", lambda *a: "Into the Mystic")
    from tidyarr import folders
    roots = folders.Roots(tmp_path / "in", tmp_path / "out")
    app = server.create_app(session_module.Session(None, tmp_path / "d.json"), "t" * 32, {"127.0.0.1:1"}, roots)
    c = TestClient(app, base_url="http://127.0.0.1:1", headers={"X-Token": "t" * 32})
    c.post("/api/scan")
    return c


def test_the_destination_endpoint_only_answers_for_decided_files(client):
    files = {f["path"]: f["id"] for f in client.get("/api/files").json()}
    formats = {"tv": naming.DEFAULT_TV, "movie": naming.DEFAULT_MOVIE}
    assert client.post(f"/api/files/{files['hard.mkv']}/destination", json=formats).status_code == 400
    response = client.post(f"/api/files/{files['easy.mkv']}/destination", json=formats)
    assert response.status_code == 200
    assert response.json()["path"].endswith("/out/Series/Law & Order/Season 26/S26E02 - Into the Mystic.mkv")


def test_the_endpoint_refuses_bad_formats_and_never_writes(client, tmp_path):
    easy = next(f["id"] for f in client.get("/api/files").json() if f["path"] == "easy.mkv")
    for bad in ("/etc/x.{EXTENSION}", "../x.{EXTENSION}", "~/x"):
        assert client.post(f"/api/files/{easy}/destination", json={"tv": bad, "movie": bad}).status_code == 400
    assert list((tmp_path / "out").iterdir()) == []
