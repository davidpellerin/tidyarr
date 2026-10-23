"""Tests for the folder roots: they are scanned whole, shown to the page, and may not nest."""
import pytest
from fastapi.testclient import TestClient

from tidyarr import folders, server, session as session_module, tmdb, triage
from tests_support import SHOW, evaluation

TOKEN = "t" * 32
HOSTS = {"127.0.0.1:8765"}


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "in" / "season1").mkdir(parents=True)
    (tmp_path / "in" / ".hidden").mkdir()
    (tmp_path / "in" / "season1" / "ep.mkv").write_bytes(b"ep" * 100)
    (tmp_path / "in" / "top.mkv").write_bytes(b"top" * 100)
    (tmp_path / "out" / "tv").mkdir(parents=True)
    (tmp_path / "secret").mkdir()
    (tmp_path / "in" / "link").symlink_to(tmp_path / "secret")
    return tmp_path


@pytest.fixture
def roots(tree):
    return folders.Roots(tree / "in", tree / "out")


@pytest.fixture
def client(tree, roots, monkeypatch):
    monkeypatch.setattr(triage, "evaluate", lambda path, base_dir=None, g=None: evaluation(auto=False))
    monkeypatch.setattr(tmdb, "search", lambda *a, **kw: [SHOW])
    app = server.create_app(session_module.Session(None, tree / "decisions.json"), TOKEN, HOSTS, roots)
    return TestClient(app, base_url="http://127.0.0.1:8765", headers={"X-Token": TOKEN})


def test_roots_may_not_nest(tree):
    with pytest.raises(ValueError, match="contain"):
        folders.Roots(tree, tree / "out")
    with pytest.raises(ValueError, match="contain"):
        folders.Roots(tree / "in", tree / "in" / "season1")


def test_nothing_is_listed_until_scan_is_pressed_then_the_whole_input_root_is(client, tree):
    assert client.get("/api/files").json() == []
    assert client.get("/api/folders").json()["output"] == str(tree / "out")
    assert [f["path"] for f in client.post("/api/scan").json()] == ["season1/ep.mkv", "top.mkv"]


def test_a_symlinked_video_pointing_outside_the_input_is_not_served(client, tree):
    (tree / "secret" / "private.mkv").write_bytes(b"private")
    (tree / "in" / "leak.mkv").symlink_to(tree / "secret" / "private.mkv")
    files = client.post("/api/scan").json()
    leak = next(f["id"] for f in files if f["path"] == "leak.mkv")
    assert client.get(f"/api/files/{leak}/original").status_code == 400


def test_roots_are_shown_with_the_home_folder_abbreviated(roots, monkeypatch, tree):
    monkeypatch.setattr(folders.Path, "home", classmethod(lambda cls: tree))
    assert roots.choices() == {"input": "~/in", "output": "~/out"}
    assert roots.shown(tree) == "~"
    assert roots.shown(tree.parent) == str(tree.parent)


def test_moving_a_file_never_replaces_another(tmp_path):
    (tmp_path / "a.mkv").write_bytes(b"a")
    (tmp_path / "b.mkv").write_bytes(b"b")
    with pytest.raises(ValueError, match="already there"):
        folders.move_file(tmp_path / "a.mkv", tmp_path / "b.mkv")
    assert (tmp_path / "a.mkv").read_bytes() == b"a" and (tmp_path / "b.mkv").read_bytes() == b"b"
    folders.move_file(tmp_path / "a.mkv", tmp_path / "deep" / "er" / "c.mkv")
    assert (tmp_path / "deep/er/c.mkv").read_bytes() == b"a" and not (tmp_path / "a.mkv").exists()


def test_moving_across_filesystems_copies_then_removes(tmp_path, monkeypatch):
    def no_links(*a):
        raise OSError(18, "Invalid cross-device link")
    monkeypatch.setattr(folders.os, "link", no_links)
    (tmp_path / "a.mkv").write_bytes(b"a" * 5000)
    (tmp_path / "b.mkv").write_bytes(b"b")
    folders.move_file(tmp_path / "a.mkv", tmp_path / "c.mkv")
    assert (tmp_path / "c.mkv").read_bytes() == b"a" * 5000 and not (tmp_path / "a.mkv").exists()
    with pytest.raises(ValueError, match="already there"):
        folders.move_file(tmp_path / "c.mkv", tmp_path / "b.mkv")
    assert (tmp_path / "b.mkv").read_bytes() == b"b" and (tmp_path / "c.mkv").exists()


def test_pruning_removes_only_empty_folders_and_stops_at_the_root(tmp_path):
    (tmp_path / "out" / "TV" / "Show" / "Season 01").mkdir(parents=True)
    (tmp_path / "out" / "TV" / "keep.mkv").write_bytes(b"k")
    folders.prune_empty(tmp_path / "out" / "TV" / "Show" / "Season 01", tmp_path / "out")
    assert not (tmp_path / "out" / "TV" / "Show").exists() and (tmp_path / "out" / "TV").is_dir()
    folders.prune_empty(tmp_path / "out", tmp_path / "out")
    assert (tmp_path / "out").is_dir()
