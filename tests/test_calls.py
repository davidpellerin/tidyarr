"""Tests for the command transcript: recording, display, and that the tool wrappers feed it."""
import json
import subprocess

from tidyarr import calls, guess, probe, report


def completed(stdout, code=0):
    return lambda *a, **kw: subprocess.CompletedProcess(a, code, stdout, "")


def test_record_does_nothing_unless_a_capture_is_active():
    calls.record("guessit", "filename", ["guessit", "x"], "{}", 0.1, 0)  # no error, nothing kept
    with calls.capture() as log:
        calls.record("guessit", "filename", ["guessit", "-j", "--", "a file.mkv"], '{"title":"x"}', 0.123456, 0)
    assert log == [{"tool": "guessit", "label": "filename", "command": "guessit -j -- 'a file.mkv'",
                    "output": '{\n  "title": "x"\n}', "seconds": 0.12, "exit_code": 0}]


def test_captures_are_separate_and_nest_cleanly():
    with calls.capture() as outer:
        calls.record("a", "", ["a"], "1", 0, 0)
        with calls.capture() as inner:
            calls.record("b", "", ["b"], "2", 0, 0)
        calls.record("c", "", ["c"], "3", 0, 0)
    assert [c["tool"] for c in outer] == ["a", "c"] and [c["tool"] for c in inner] == ["b"]


def test_only_very_long_arguments_are_elided_and_json_is_reindented():
    schema = json.dumps({"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"], "additionalProperties": False})
    shown = calls.display_command(["tool", "--json-schema", schema, "short"])
    assert shown.startswith("tool --json-schema ") and "…" in shown and shown.endswith("short") and len(shown) < len(schema)
    assert calls.display_command(["ffprobe", "-v", "error"]) == "ffprobe -v error"
    assert calls.pretty("not json\n") == "not json" and calls.pretty('{"a":1}') == '{\n  "a": 1\n}'
    assert calls.pretty("x" * 30_000).endswith("(output truncated)")


def test_the_wrappers_record_what_they_ran_and_what_came_back(monkeypatch):
    outputs = {"ffprobe": json.dumps({"format": {"tags": {"title": "T"}}, "streams": []})}
    monkeypatch.setattr(guess, "guessit", lambda path: {"title": "Law and Order", "type": "episode"})
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, outputs[argv[0]], ""))
    with calls.capture() as log:
        guess.guess("stuff/ep4.mkv")
        guess.guess("Law.and.Order.S26E02", label="container title")
        probe.probe("stuff/ep4.mkv")
    assert [(c["tool"], c["label"]) for c in log] == [("guessit", "filename"), ("guessit", "container title"),
                                                       ("ffprobe", "file info")]
    assert log[0]["command"] == "guessit -j -- stuff/ep4.mkv" and '"title": "Law and Order"' in log[0]["output"]
    assert log[2]["command"].startswith("ffprobe -v error -print_format json -show_format -show_streams -i /")


def test_a_failing_command_is_recorded_before_the_error_is_raised(monkeypatch):
    monkeypatch.setattr(probe.subprocess, "run", completed("not json at all", code=1))
    with calls.capture() as log:
        try:
            probe.probe("x.mkv")
        except RuntimeError:
            pass
    assert log and log[0]["exit_code"] == 1 and log[0]["output"] == "not json at all"


def test_the_report_draws_each_command_like_a_terminal_and_escapes_it():
    hostile = '<script>alert(1)</script>'
    call = {"tool": "guessit", "label": "filename", "command": f"guessit -j -- '{hostile}'", "output": f'{{"title": "{hostile}"}}', "seconds": 0.2, "exit_code": 0}
    html = report.terminal(call)
    assert '<span class="prompt">$</span>' in html and "# exit 0 · 0.2s" in html
    assert "<script>alert" not in html and "&lt;script&gt;" in html
