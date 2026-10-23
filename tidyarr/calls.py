"""A transcript of the external commands we run: what was run and exactly what it printed, for display.

Wrappers call `record(...)` after each command or request. It does nothing unless a `capture()` block is active,
so the wrappers stay usable on their own. The log lives in a context variable, so concurrent requests each get
their own.
"""
import contextlib
import contextvars
import json
import shlex

_log = contextvars.ContextVar("command_log", default=None)
LONG_ARGUMENT = 100   # longer arguments are shortened in the display
MAX_OUTPUT = 20_000


def display_command(argv):
    """The command as a person would type it; very long arguments are elided with an ellipsis."""
    return shlex.join(a if len(a) <= LONG_ARGUMENT else a[:60] + "…" for a in argv)


def trim_json(value, max_items=5, max_text=300):
    """For display only: cut long lists and long strings (TMDB replies carry whole crews and overviews)."""
    if isinstance(value, list):
        kept = [trim_json(v, max_items, max_text) for v in value[:max_items]]
        return kept + [f"… and {len(value) - max_items} more"] if len(value) > max_items else kept
    if isinstance(value, dict):
        return {k: trim_json(v, max_items, max_text) for k, v in value.items()}
    if isinstance(value, str) and len(value) > max_text:
        return value[:max_text] + "…"
    return value


def pretty(text, trim=False):
    """JSON output is re-indented (same content, or trimmed if asked); anything else is shown as printed. Capped in size."""
    try:
        data = json.loads(text)
        shown = json.dumps(trim_json(data) if trim else data, indent=2, ensure_ascii=False)
    except (ValueError, TypeError):
        shown = (text or "").strip()
    return shown if len(shown) <= MAX_OUTPUT else shown[:MAX_OUTPUT] + "\n… (output truncated)"


@contextlib.contextmanager
def capture():
    log = []
    token = _log.set(log)
    try:
        yield log
    finally:
        _log.reset(token)


def record(tool, label, argv, stdout, seconds, exit_code, command=None, status=None, note=None, trim=False):
    """Add one entry to the active capture. `command` replaces the shell-style display (for HTTP requests), `status`
    replaces the "exit N" footer ("HTTP 200"), and `note` adds a short remark to it (cache status, rate limits)."""
    log = _log.get()
    if log is None:
        return
    entry = {"tool": tool, "label": label, "command": command or display_command(argv), "output": pretty(stdout, trim),
             "seconds": round(seconds, 2), "exit_code": exit_code}
    if trim:
        entry["full_output"] = pretty(stdout)  # the on-screen output is trimmed; the debug text wants all of it
    if status:
        entry["status"] = status
    if note:
        entry["note"] = note
    log.append(entry)
