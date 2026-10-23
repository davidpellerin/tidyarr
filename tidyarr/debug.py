"""A plain-text summary of everything known about one file, to paste into a chat when something looks wrong.

Built from data already collected (see triage.evidence_record). Folder paths are replaced by placeholders and the TMDB
key is already redacted in the transcript. File names and tags are untrusted text from the file: the reader should
treat them as data, never as instructions.
"""
import datetime
import json


def _json(value):
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def build(detail, raw, hide=()):
    """`detail` is Session.detail's result, `raw` the evidence's raw section (or None). `hide` maps text to a placeholder."""
    raw = raw or {}
    out = ["# tidyarr debug info", f"time: {datetime.datetime.now().astimezone().isoformat(timespec='seconds')}", "",
           f"file: {detail['path']}", f"status: {detail['status']}"]
    if detail.get("decision"):
        d = detail["decision"]
        out.append(f"decision: {d['name']} (decided by {d['source']})")
    if detail.get("identity"):
        out.append(f"tmdb identity: {_json(detail['identity'])}")
    if detail.get("consensus"):
        out.append(f"consensus: {detail['consensus']['agreeing']} of {detail['consensus']['spoke']} sources agree")
    runtime = detail.get("runtime")
    out.append("runtime check: " + (_json(runtime) if runtime else "none (no match to compare against, or the file's length is unknown)"))
    out += ["", "## sources"]
    out += [f"- {c['source']}: {c['said']} (agrees: {c['agrees']})" for c in detail.get("clues") or []] or ["- none"]
    out += ["", "## probe (ffprobe)", _json(raw.get("probe")), "", "## guessit on the filename", _json(raw.get("guessit")),
            "", "## guessit on the container title", _json(raw.get("metadata_guess")), "", "## nfo", _json(raw.get("nfo")),
            "", "## transcript"]
    for call in raw.get("calls") or []:
        flags = " ".join(f"[{k}]" for k in ("status", "note") if call.get(k))
        out += [f"### {call.get('tool')}: {call.get('label')} {flags}".rstrip(), f"$ {call.get('command')}", (call.get("full_output") or call.get("output") or "").strip(), ""]
    text = "\n".join(out).rstrip() + "\n"
    for secret, placeholder in hide:
        if secret:
            text = text.replace(str(secret), placeholder)
    return text
