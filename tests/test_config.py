"""Tests for the settings file: defaults, merging, and that it is private to its owner."""
import json
import os

import pytest

from tidyarr import config


def test_the_file_overrides_the_defaults_and_holds_the_key_only_once_saved(settings_file):
    assert config.load() == config.DEFAULTS
    settings_file.parent.mkdir()
    settings_file.write_text('{"output_root": "/mnt/x", "other": 1, "tv_format": 7}')
    assert config.load() == {**config.DEFAULTS, "output_root": "/mnt/x"}  # unknown keys and non-strings are ignored


def test_saving_merges_and_keeps_what_the_page_cannot_set(settings_file):
    config.save({"input_root": "/media/in", "something": "else"})
    config.save({"tv_format": "TV/{SHOW_NAME}.{EXTENSION}", config.KEY: "k" * 32})
    assert json.loads(settings_file.read_text()) == {"input_root": "/media/in", "something": "else",
                                                     "tv_format": "TV/{SHOW_NAME}.{EXTENSION}", config.KEY: "k" * 32}
    assert [p.name for p in settings_file.parent.iterdir()] == ["config.json"]  # no temporary file left behind


@pytest.mark.skipif(os.name != "posix", reason="Windows keeps %APPDATA% private by itself")
def test_the_file_is_readable_by_its_owner_only(settings_file):
    config.save({config.KEY: "k" * 32})
    assert settings_file.stat().st_mode & 0o777 == 0o600
    assert settings_file.parent.stat().st_mode & 0o777 == 0o700
    settings_file.chmod(0o644)  # copied or edited with looser permissions
    config.load()
    assert settings_file.stat().st_mode & 0o777 == 0o600


def test_the_junk_types_are_a_list_of_extensions_and_anything_else_is_ignored(settings_file):
    assert config.load()["junk_extensions"] == config.JUNK
    config.save({"junk_extensions": ["TXT", ".nfo", " ", "jpg "]})
    assert config.load()["junk_extensions"] == [".txt", ".nfo", ".jpg"]
    config.save({"junk_extensions": ".txt"})
    assert config.load()["junk_extensions"] == config.JUNK


def test_removing_empty_folders_is_off_unless_the_file_says_true_or_false(settings_file):
    assert config.load()["remove_empty_folders"] is False
    config.save({"remove_empty_folders": True})
    assert config.load()["remove_empty_folders"] is True
    config.save({"remove_empty_folders": "yes"})
    assert config.load()["remove_empty_folders"] is False
