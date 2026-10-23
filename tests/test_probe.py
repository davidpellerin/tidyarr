"""Offline tests for tidyarr.probe: subprocess.run is replaced, so ffprobe is never called."""
import json
import subprocess

import pytest

from tidyarr import probe

SAMPLE = {
    "format": {"format_name": "matroska,webm", "duration": "2639.737000", "size": "576716800",
               "tags": {"TITLE": " Law.and.Order.Toronto.Criminal.Intent.S04E04 ", "ENCODER": "Lavf61.7.100"}},
    "streams": [
        {"codec_type": "video", "codec_name": "hevc", "width": 1920, "height": 1080},
        {"codec_type": "audio", "codec_name": "aac", "tags": {"language": "eng"}},
        {"codec_type": "audio", "codec_name": "aac"},
        {"codec_type": "subtitle", "codec_name": "subrip", "tags": {"language": "fre"}},
    ],
}


def fake_ffprobe(monkeypatch, payload, returncode=0, stderr=""):
    stdout = payload if isinstance(payload, str) else json.dumps(payload)
    monkeypatch.setattr(probe.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, returncode, stdout, stderr))


def test_probe_extracts_title_duration_and_streams(monkeypatch):
    fake_ffprobe(monkeypatch, SAMPLE)
    info = probe.probe("x.mkv")
    assert info["title"] == "Law.and.Order.Toronto.Criminal.Intent.S04E04"  # tag-name case varies; value is trimmed
    assert info["duration"] == pytest.approx(2639.737)
    assert (info["video"], info["audio"], info["subtitles"]) == ("1920x1080 hevc", ["eng", "und"], ["fre"])


def test_a_file_without_a_title_tag_has_title_none(monkeypatch):
    fake_ffprobe(monkeypatch, {"format": {"format_name": "mov,mp4", "duration": "60.0", "tags": {"encoder": "x"}}, "streams": []})
    info = probe.probe("x.mp4")
    assert info["title"] is None and info["video"] is None and info["audio"] == []


def test_a_blank_title_tag_counts_as_none(monkeypatch):
    fake_ffprobe(monkeypatch, {"format": {"tags": {"title": "   "}}, "streams": []})
    assert probe.probe("x.mkv")["title"] is None


def test_ffprobe_failures_become_clear_errors(monkeypatch):
    fake_ffprobe(monkeypatch, "", returncode=1, stderr="x.mkv: Invalid data found when processing input\n")
    with pytest.raises(RuntimeError, match="Invalid data"):
        probe.probe("x.mkv")
    fake_ffprobe(monkeypatch, "not json")
    with pytest.raises(RuntimeError, match="not JSON"):
        probe.probe("x.mkv")

    def missing(*a, **kw):
        raise FileNotFoundError
    monkeypatch.setattr(probe.subprocess, "run", missing)
    with pytest.raises(RuntimeError, match="not installed"):
        probe.probe("x.mkv")


def test_the_path_is_passed_as_an_absolute_argument_after_dash_i(monkeypatch):
    seen = []
    monkeypatch.setattr(probe.subprocess, "run", lambda cmd, **kw: (seen.append(cmd), subprocess.CompletedProcess(cmd, 0, json.dumps(SAMPLE), ""))[1])
    probe.probe("-weird name.mkv")
    assert seen[0][seen[0].index("-i") + 1].startswith("/")  # never mistaken for an option


@pytest.mark.parametrize("seconds, expected", [(None, "?"), (2639.737, "44m 00s"), (59.2, "0m 59s"), (3723, "1h 02m 03s")])
def test_format_duration(seconds, expected):
    assert probe.format_duration(seconds) == expected
