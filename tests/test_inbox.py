"""Tests for finding the inbox's files and telling which are still being written."""
import os
import sys
import time

import pytest

from tidyarr import inbox

linux_only = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc")


@linux_only
def test_a_file_open_for_writing_is_seen_and_one_open_only_for_reading_is_not(tmp_path):
    written, read = tmp_path / "written.mkv", tmp_path / "read.mkv"
    read.write_bytes(b"x")
    with open(written, "ab"), open(read, "rb"):
        assert inbox.open_for_writing([written, read]) == {str(written)}
    assert inbox.open_for_writing([written, read]) == set()


def test_a_file_changed_in_the_last_few_seconds_counts_as_still_being_written(tmp_path):
    fresh, settled = tmp_path / "fresh.mkv", tmp_path / "settled.mkv"
    fresh.write_bytes(b"x")
    settled.write_bytes(b"x")
    os.utime(settled, (0, 0))
    os.utime(fresh, (time.time() + 60,) * 2)  # still "fresh" however slowly the test runs
    assert inbox.being_written([fresh, settled, tmp_path / "gone.mkv"]) == {fresh}
    assert inbox.being_written([fresh, settled], now=fresh.stat().st_mtime + inbox.RECENTLY_WRITTEN_SECONDS) == set()


@linux_only
def test_an_old_file_still_open_for_writing_counts_as_still_being_written(tmp_path):
    stalled = tmp_path / "stalled.mkv"
    stalled.write_bytes(b"x")
    os.utime(stalled, (0, 0))
    with open(stalled, "r+b"):
        assert inbox.being_written([stalled]) == {stalled}
