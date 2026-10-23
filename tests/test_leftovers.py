from pathlib import Path

import pytest

from tidyarr import config, leftovers

JUNK = config.JUNK


@pytest.fixture
def trash(monkeypatch):
    """Where send2trash would have put things, instead of the real trash."""
    sent = []
    monkeypatch.setattr(leftovers, "send2trash", lambda path: (sent.append(Path(path).name), Path(path).unlink()))
    return sent


def touch(*paths):
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")


def test_the_release_folder_is_the_one_directly_under_the_inbox(tmp_path):
    assert leftovers.release_folder(tmp_path, "Film (2010)/Sub/film.mkv") == tmp_path / "Film (2010)"
    assert leftovers.release_folder(tmp_path, "film.mkv") is None


def test_junk_types_and_orphan_subtitles_are_junk_and_the_rest_is_kept(tmp_path):
    touch(tmp_path / "site.JPG", tmp_path / "info.txt", tmp_path / "Subs/English.srt", tmp_path / "notes.pdf")
    (tmp_path / "Empty").mkdir()
    assert leftovers.survey(tmp_path, JUNK) == {"videos": 0, "junk": ["Subs/English.srt", "info.txt", "site.JPG"], "kept": ["notes.pdf"]}


def test_a_folder_that_still_has_a_video_is_not_offered(tmp_path):
    touch(tmp_path / "Sample/sample.mkv", tmp_path / "info.txt")
    found = leftovers.survey(tmp_path, JUNK)
    assert found["videos"] == 1 and not leftovers.worth_offering(found)


def test_symlinks_are_kept_and_not_followed(tmp_path):
    outside = tmp_path / "outside"
    touch(outside / "precious.txt")
    folder = tmp_path / "release"
    folder.mkdir()
    (folder / "link.txt").symlink_to(outside / "precious.txt")
    (folder / "linked").symlink_to(outside)
    assert leftovers.survey(folder, JUNK) == {"videos": 0, "junk": [], "kept": ["link.txt", "linked"]}


def test_cleaning_trashes_the_junk_and_removes_folders_left_empty(tmp_path, trash):
    folder = tmp_path / "release"
    touch(folder / "site.jpg", folder / "Subs/English.srt", folder / "Extras/notes.pdf")
    (folder / "Empty").mkdir()
    leftovers.clean(folder, JUNK, ["Subs/English.srt", "site.jpg"])
    assert sorted(trash) == ["English.srt", "site.jpg"]
    assert sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*")) == ["Extras", "Extras/notes.pdf"]
    (folder / "Extras/notes.pdf").unlink()
    leftovers.clean(folder, JUNK, [])  # nothing but empty folders left: the release folder goes too
    assert not folder.exists()


def test_only_a_folder_with_nothing_in_it_is_removed_as_empty(tmp_path):
    folder = tmp_path / "release"
    (folder / "Subs/Deeper").mkdir(parents=True)
    (folder / "linked").symlink_to(tmp_path)
    assert not leftovers.remove_if_empty(folder) and (folder / "Subs/Deeper").is_dir()
    (folder / "linked").unlink()
    touch(folder / "Subs/site.jpg")
    assert not leftovers.remove_if_empty(folder)
    (folder / "Subs/site.jpg").unlink()
    assert leftovers.remove_if_empty(folder) and not folder.exists()
    assert not leftovers.remove_if_empty(folder)


def test_nothing_is_touched_if_the_folder_changed_since_it_was_shown(tmp_path, trash):
    folder = tmp_path / "release"
    touch(folder / "site.jpg", folder / "new.txt")
    with pytest.raises(ValueError, match="changed"):
        leftovers.clean(folder, JUNK, ["site.jpg"])
    touch(folder / "film.mkv")
    with pytest.raises(ValueError, match="video"):
        leftovers.clean(folder, JUNK, ["new.txt", "site.jpg"])
    assert trash == [] and (folder / "site.jpg").exists()
