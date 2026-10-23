"""Tests for the running-server record: written private, found only if that server answers, removed only by its owner."""
import json
import os

from tidyarr import instance


def test_the_record_is_private_and_found_only_when_the_server_at_that_port_has_that_pid(monkeypatch):
    instance.write(8765, "secret")
    assert json.loads(instance.PATH.read_text()) == {"pid": os.getpid(), "port": 8765, "token": "secret"}
    if os.name == "posix":
        assert instance.PATH.stat().st_mode & 0o777 == 0o600
    monkeypatch.setattr(instance, "ask", lambda info, endpoint, **kw: {"pid": os.getpid()})
    assert instance.find()["token"] == "secret"
    assert instance.url(instance.find()) == "http://127.0.0.1:8765/#token=secret"
    monkeypatch.setattr(instance, "ask", lambda info, endpoint, **kw: {"pid": os.getpid() + 1})  # the pid was reused
    assert instance.find() is None


def test_a_record_left_by_a_crash_or_nothing_listening_is_ignored(monkeypatch):
    assert instance.find() is None  # no file
    instance.PATH.parent.mkdir(parents=True, exist_ok=True)
    instance.PATH.write_text("not json")
    assert instance.find() is None
    instance.write(1, "secret")  # port 1: nothing answers
    assert instance.find() is None


def test_only_the_server_that_wrote_the_record_removes_it():
    instance.write(8765, "secret")
    instance.PATH.write_text(json.dumps({"pid": os.getpid() + 1, "port": 8765, "token": "newer"}))
    instance.clear()
    assert instance.PATH.exists()
    instance.write(8765, "secret")
    instance.clear()
    assert not instance.PATH.exists()
    instance.clear()  # already gone: fine
