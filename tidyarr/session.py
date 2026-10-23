"""State and actions behind the review web UI. No web framework in here, so it can be tested directly.

A session is one inbox folder (chosen later, from the roots in folders.py) plus the decisions file. Files are addressed
by an opaque id derived from their content fingerprint; the client never supplies a path, so it can never make the
server touch a file outside the inbox.
"""
import base64
import hashlib
import re
import threading
import time
from pathlib import Path, PurePosixPath

from tidyarr import calls, debug, decisions, flow, folders, frames, leftovers, naming, probe, report, subtitles, tmdb, triage
from tidyarr.evidence import consensus
from tidyarr.inbox import being_written, video_files

POSTER_PATH = re.compile(r"/[A-Za-z0-9_.-]{5,60}\.(?:jpg|png)")


def poster_url(path, size="w185"):
    """A TMDB image URL, only if the path has the shape of one (it comes from an external service)."""
    return f"https://image.tmdb.org/t/p/{size}{path}" if isinstance(path, str) and POSTER_PATH.fullmatch(path) else None


class Session:
    def __init__(self, inbox=None, decisions_path=decisions.DEFAULT_PATH):
        self.base_dir = Path(inbox).expanduser().resolve() if inbox else None
        self.output_dir = None  # chosen with the inbox; files are moved here only when a person approves them
        self.decisions_path = Path(decisions_path)
        self.lock = threading.RLock()
        self.files = {}
        self.known = {}
        self.frame_cache = {}  # file key -> the frames picked for it, so reopening a file doesn't run ffmpeg again
        self.last_writing = None  # (when, writing()): the player's many range requests reuse a recent answer
        self.was_busy = set()  # ids last seen being written; when one finishes, what we worked out from the partial file is dropped
        self.refresh()

    # ----- the file list -------------------------------------------------------------------------------------

    def refresh(self):
        """Re-read the decisions and the inbox folder, keeping what we already worked out about each file."""
        with self.lock:
            self.known = decisions.load(self.decisions_path)
            files = {}
            for path in video_files(self.base_dir) if self.base_dir else []:
                key = decisions.key_for(path, self.base_dir)
                fid = hashlib.sha1(key.encode()).hexdigest()[:12]
                entry = self.files.get(fid) or {"evaluation": None, "extra_calls": [], "initial": None,
                                                "moved": None, "lock": threading.Lock()}
                entry.update(path=path, key=key)
                files[fid] = entry
            # Files moved out of the inbox in this session stay listed, so they can be seen and put back.
            files.update({fid: entry for fid, entry in self.files.items() if entry["moved"] and fid not in files})
            self.files = files

    def set_folders(self, inbox, output):
        """Switch to other (already validated) folders. What was worked out about the old folder's files is dropped."""
        with self.lock:
            inbox = Path(inbox).resolve()
            if inbox != self.base_dir:
                self.files = {}
            self.base_dir, self.output_dir = inbox, Path(output).resolve()
            self.refresh()

    def entry(self, fid):
        try:
            return self.files[fid]
        except KeyError:
            raise KeyError("no such file") from None

    def status(self, entry):
        if entry["moved"]:
            return "moved"
        if entry["key"] in self.known:
            return "decided"
        return "review" if entry["evaluation"] else "pending"

    def summary(self, fid, entry):
        record = self.known.get(entry["key"])
        item = {"id": fid, "path": entry["path"], "status": self.status(entry), "name": None, "source": None, "consensus": None,
                "identity": None, "moved_to": None}
        if entry["moved"]:
            item["moved_to"] = folders.Roots.shown(entry["moved"]["to"])
        if record:
            item["name"], item["source"], item["identity"] = decisions.render(record), record.get("source"), record["tmdb"]
            evidence = record.get("evidence")
            if evidence:
                item["consensus"] = triage.percent(evidence["consensus"]["agreeing"], evidence["consensus"]["spoke"])
        return item

    def list(self):
        with self.lock:
            return [self.summary(fid, entry) for fid, entry in self.files.items()]

    def writing(self):
        """For each file still in the inbox, by id: whether we think it is still being written (a download in progress).
        Nothing is refused because of it yet; the page only shows it. It also re-reads the inbox, so a download that
        starts after the page opened is listed (and one that is deleted drops off) without a scan."""
        with self.lock:
            self.refresh()
            paths = {fid: self.base_dir / entry["path"] for fid, entry in self.files.items() if not entry["moved"]}
        busy = being_written(list(paths.values()))
        result = {fid: path in busy for fid, path in paths.items()}
        with self.lock:
            for fid in self.was_busy - {fid for fid, now in result.items() if now}:
                if fid in self.files:
                    self.files[fid]["evaluation"] = None  # read from a part-downloaded file: look again now it is whole
            self.was_busy = {fid for fid, now in result.items() if now}
        self.last_writing = (time.monotonic(), result)
        return result

    WRITING_FRESH_SECONDS = 2

    def require_finished(self, fid):
        """Refuse to read a file that is still being written: a part-downloaded video plays badly and gives wrong frames."""
        self.entry(fid)
        recent = self.last_writing
        result = recent[1] if recent and time.monotonic() - recent[0] < self.WRITING_FRESH_SECONDS else self.writing()
        if result.get(fid):
            raise ValueError("this file is still being written; try again once it's done")

    def playable_path(self, fid):
        """The video for the in-browser player, once it has finished arriving."""
        self.require_finished(fid)
        return self.original_path(fid)

    # ----- evaluating and deciding ---------------------------------------------------------------------------

    def evaluation(self, entry):
        with entry["lock"]:
            if entry["evaluation"] is None:
                entry["evaluation"] = triage.evaluate(entry["path"], self.base_dir)
            return entry["evaluation"]

    def scan(self):
        """Evaluate every undecided file; those the evidence settles are accepted."""
        busy = being_written([self.base_dir / e["path"] for e in self.files.values() if not e["moved"]]) if self.base_dir else set()
        for entry in list(self.files.values()):
            if entry["key"] in self.known or entry["evaluation"]:
                continue
            result = self.evaluation(entry)
            # A download is read from its name for now; the video's own tags and length aren't there yet, so nothing is
            # accepted until it is whole (then it is evaluated again).
            if result["auto"] and self.base_dir / entry["path"] not in busy:
                agreeing, spoke = result["score"]
                self._save(entry, result["verdict"]["identity"], "auto",
                           triage.evidence_record(result["ev"], result["opinions"], agreeing, spoke))
        return self.list()

    def _save(self, entry, identity, source, evidence):
        with self.lock:
            self.known[entry["key"]] = decisions.make_record(entry["path"], identity, source, evidence=evidence)
            decisions.save(self.known, self.decisions_path)

    def decide(self, fid, kind, tmdb_id):
        entry = self.unmoved(fid)
        with calls.capture() as log:
            try:
                candidate = tmdb.details(kind, tmdb_id)
            finally:
                entry["extra_calls"].extend(log)
        if candidate is None:
            raise ValueError("that title was not found on TMDB")
        result = self.evaluation(entry)
        season, episode = triage.episode_numbers(result["ev"]) if kind == "tv" else (None, None)
        identity = tmdb.identity(candidate, season, episode)
        agreeing, spoke = consensus(result["opinions"], identity)
        evidence = triage.evidence_record(result["ev"], result["opinions"], agreeing, spoke, extra_calls=entry["extra_calls"])
        self._save(entry, identity, "human", evidence)
        return self.summary(fid, entry)

    def undo(self, fid):
        """Forget a decision (automatic or not) so the file can be reviewed again."""
        entry = self.unmoved(fid)
        with self.lock:
            if self.known.pop(entry["key"], None) is not None:
                decisions.save(self.known, self.decisions_path)
            entry.update(evaluation=None, extra_calls=[], initial=None)
        return self.summary(fid, entry)

    def unmoved(self, fid):
        entry = self.entry(fid)
        if entry["moved"]:
            raise ValueError("this file has been moved; put it back first")
        return entry

    # ----- moving --------------------------------------------------------------------------------------------

    def approve(self, fid, formats, remove_empty=False):
        """Move a decided file to its place under the output folder. Only ever done when a person asks for it.
        With `remove_empty`, the release folder it came from goes too if the move (with its subtitles) left it empty."""
        entry = self.unmoved(fid)
        with self.lock:
            relative = self.destination(fid, formats)
            source, target = self.original_path(fid), self.output_dir / relative
            subs = [(sub, subtitles.renamed(target, tail)) for sub, tail in subtitles.find(source)]
            ends = [target, *(to for _, to in subs)]
            if len(set(ends)) < len(ends) or any(end.exists() for end in ends):
                raise ValueError("a file with that name is already there")
            folders.move_file(source, target)
            moved = []
            try:
                for sub, to in subs:
                    folders.move_file(sub, to)
                    moved.append((sub, to))
            except BaseException:  # all or nothing: put back what went, so the video and its subtitles stay together
                for sub, to in reversed(moved):
                    folders.move_file(to, sub)
                folders.move_file(target, source)
                folders.prune_empty(target.parent, self.output_dir)
                raise
            entry["moved"] = {"from": source, "to": target, "subtitles": moved}
            record = self.known[entry["key"]]
            record["moved"] = {"from": str(source), "to": str(target), "at": decisions.today(),
                               "subtitles": [{"from": str(sub), "to": str(to)} for sub, to in moved]}
            decisions.save(self.known, self.decisions_path)
            if remove_empty and self.base_dir in source.parents:
                folder = leftovers.release_folder(self.base_dir, source.relative_to(self.base_dir))
                if folder:
                    leftovers.remove_if_empty(folder)
        return self.summary(fid, entry)

    def subtitle_destinations(self, fid, relative):
        """Where the video's subtitles would go if it went to `relative` under the output folder. Nothing is moved."""
        return [subtitles.renamed(PurePosixPath(relative), tail).as_posix() for _, tail in subtitles.find(self.original_path(fid))]

    def restore(self, fid):
        """Put a moved file back where it was found, and tidy away any folders the move left empty."""
        entry = self.entry(fid)
        with self.lock:
            moved = entry["moved"]
            if not moved:
                raise ValueError("this file hasn't been moved")
            if not moved["to"].is_file():
                raise ValueError("the moved file is no longer there")
            folders.move_file(moved["to"], moved["from"])
            for sub, to in moved.get("subtitles", []):
                if to.is_file():  # one deleted from the library since is simply not brought back
                    folders.move_file(to, sub)
            folders.prune_empty(moved["to"].parent, self.output_dir)
            entry["moved"] = None
            record = self.known.get(entry["key"])
            if record and record.pop("moved", None):
                decisions.save(self.known, self.decisions_path)
        return self.summary(fid, entry)

    def delete(self, fid, remove_empty=False):
        """Put a file still in the inbox, and its subtitles, in the system trash, and forget its decision. Only ever done
        when a person asks for it and confirms. With `remove_empty`, the release folder it came from goes too if left empty."""
        entry = self.unmoved(fid)
        self.require_finished(fid)
        with self.lock:
            source = self.original_path(fid)
            for path in [source, *(sub for sub, _ in subtitles.find(source))]:
                leftovers.send2trash(str(path))
            del self.files[fid]
            if self.known.pop(entry["key"], None):
                decisions.save(self.known, self.decisions_path)
            if remove_empty:
                folder = leftovers.release_folder(self.base_dir, source.relative_to(self.base_dir))
                if folder:
                    leftovers.remove_if_empty(folder)
        return {"path": entry["path"]}

    # ----- leftovers -----------------------------------------------------------------------------------------

    def _release_folders(self):
        """{id: folder} for the inbox's release folders that a file was moved out of (in this session or before)."""
        found = {}
        for record in self.known.values():
            source = Path((record.get("moved") or {}).get("from", ""))
            if not self.base_dir or self.base_dir not in source.parents:
                continue
            folder = leftovers.release_folder(self.base_dir, source.relative_to(self.base_dir))
            if folder and folder.is_dir() and not folder.is_symlink():
                found[hashlib.sha1(folder.name.encode()).hexdigest()[:12]] = folder
        return found

    def leftovers(self, junk):
        """The release folders with no video left and something to clean up: their junk, and what would be kept."""
        with self.lock:
            out = []
            for lid, folder in sorted(self._release_folders().items(), key=lambda item: item[1].name.lower()):
                found = leftovers.survey(folder, junk)
                if leftovers.worth_offering(found):
                    out.append({"id": lid, "folder": folder.name, "junk": found["junk"], "kept": found["kept"]})
            return out

    def clean_leftovers(self, lid, junk, shown):
        """Put the junk of one release folder in the trash: the list the person was shown, and only if it still is."""
        with self.lock:
            folder = self._release_folders().get(lid)
            if folder is None:
                raise KeyError(lid)
            found = leftovers.clean(folder, junk, shown)
            return {"folder": folder.name, "trashed": len(found["junk"]), "kept": found["kept"]}

    # ----- candidates ----------------------------------------------------------------------------------------

    EXTRA_FACTS = ("runtime", "genres", "director", "cast", "certification", "seasons", "episodes")

    def candidates(self, entry, query, kind, year=None, country=None):
        """TMDB candidates for a search, each with the episode check for TV (does this show have S/E?)."""
        season, episode = triage.episode_numbers(self.evaluation(entry)["ev"]) if kind == "tv" else (None, None)
        out = []
        for c in tmdb.search(query, kind, year=year, country=country):
            item = {"kind": c["kind"], "id": c["id"], "title": c["title"], "year": c["year"], "countries": c["countries"],
                    "overview": c["overview"], "poster": poster_url(c.get("poster")), "episode": None,
                    "date": c["date"], "original_title": c["original_title"], "language": c["language"], "rating": c["rating"], "votes": c["votes"],
                    "url": f"https://www.themoviedb.org/{c['kind']}/{int(c['id'])}"}
            full = tmdb.details(c["kind"], c["id"]) or {}   # a search result lacks genres, cast and the rest
            item.update({k: full.get(k) for k in self.EXTRA_FACTS})
            if season is not None:
                info = tmdb.episode_info(c["id"], season, episode) or {}
                item["episode"] = {"season": season, "episode": episode, "title": info.get("title"), "overview": info.get("overview", ""),
                                   "air_date": info.get("air_date"), "runtime": info.get("runtime")}
            out.append(item)
        return out

    def _recorded(self, entry, fn, *args, **kwargs):
        """Run something that talks to TMDB, keeping its transcript with the file (even if it fails)."""
        with calls.capture() as log:
            try:
                return fn(*args, **kwargs)
            finally:
                entry["extra_calls"].extend(log)

    def search(self, fid, query, kind):
        entry = self.entry(fid)
        return {"candidates": self._recorded(entry, self.candidates, entry, query.strip(), kind)}

    def stats(self):
        """What this session has used: requests sent to TMDB (it reports no quota)."""
        return {"tmdb_requests": tmdb.request_count()}

    def destination(self, fid, formats):
        """Where a decided file would go under the output folder, as a relative path. Only computed; nothing is moved.
        `formats` maps 'tv' and 'movie' to naming formats."""
        entry = self.entry(fid)
        record = self.known.get(entry["key"])
        if not self.output_dir:
            raise ValueError("choose an output folder first")
        if not record:
            raise ValueError("decide what this file is to see where it will go")
        ident = record["tmdb"]
        template = formats[ident["kind"]]
        naming.check(template, ident["kind"])
        episode_name = None
        if ident["kind"] == "tv" and naming.uses(template, "tv", "EPISODE_NAME"):
            episode_name = tmdb.episode_title(ident["id"], ident.get("season"), ident.get("episode"))
        filled = naming.values(ident, Path(entry["path"]).suffix, episode_name)
        relative = naming.render(template, ident["kind"], filled)
        if self.output_dir not in (self.output_dir / relative).resolve().parents:  # a symlink inside the output folder
            raise ValueError("that would leave the output folder")
        return relative.as_posix()

    # ----- the detail view -----------------------------------------------------------------------------------

    def original_path(self, fid):
        entry = self.entry(fid)
        if entry["moved"]:  # once moved, the player plays it from its new place
            if not entry["moved"]["to"].is_file():
                raise ValueError("the file is no longer there")
            return entry["moved"]["to"]
        path = self.base_dir / entry["path"]
        if not path.is_file() or self.base_dir not in path.resolve().parents:  # a symlink may point out of the inbox
            raise ValueError("the file is no longer there")
        return path

    def poster_for(self, ident):
        """A small poster URL for the file list, or None."""
        try:
            found = tmdb.details(ident["kind"], ident["id"])
        except RuntimeError:
            return None
        return poster_url(found.get("poster")) if found else None

    def frames(self, fid):
        """[{'time', 'at', 'jpeg'}]: good still frames from the video at its own size, the JPEGs base64-encoded. Read-only; see frames.py."""
        entry = self.entry(fid)
        if entry["key"] not in self.frame_cache:
            self.require_finished(fid)
            path = self.original_path(fid)
            picked = frames.pick(path, probe.probe(path)["duration"])
            self.frame_cache[entry["key"]] = [{"time": f["time"], "at": probe.format_duration(f["time"]),
                                               "jpeg": base64.b64encode(f["jpeg"]).decode()} for f in picked]
        return self.frame_cache[entry["key"]]

    IMAGE_SIZES = {"still": "w300", "backdrop": "w780", "logo": "w300", "poster": "w342"}   # for the page; the full size is one click away
    IMAGE_LABELS = {"still": "Episode still", "backdrop": "Backdrop", "logo": "Logo", "poster": "Poster"}

    def images(self, fid):
        """{'title', 'decided', 'images': [{'label', 'thumb', 'full', 'width', 'height'}]}: TMDB's artwork for what the file was matched
        to, or, while it is still under review, for the top suggestion. Artwork of a candidate is not evidence about the file:
        it is shown beside the frames so a person can compare them."""
        entry = self.entry(fid)
        record = self.known.get(entry["key"])
        if record:
            ident, decided = record["tmdb"], True
        else:
            top = next(iter(entry["initial"] or []), None)
            if top is None:
                return {"title": None, "decided": False, "images": []}
            numbers = top.get("episode") or {}
            ident = {"kind": top["kind"], "id": top["id"], "title": top["title"], "year": top["year"],
                     "season": numbers.get("season"), "episode": numbers.get("episode")}
            decided = False
        found = self._recorded(entry, tmdb.images, ident["kind"], ident["id"], ident.get("season"), ident.get("episode")) or []
        shown = [{"label": self.IMAGE_LABELS[i["kind"]], "thumb": poster_url(i["path"], self.IMAGE_SIZES[i["kind"]]), "full": poster_url(i["path"], "original"),
                  "width": i["width"], "height": i["height"]} for i in found]
        return {"title": tmdb.render(ident), "decided": decided, "images": [i for i in shown if i["thumb"]]}  # a path of an odd shape is dropped

    def _facts(self, ident):
        """Genres, director, cast, age rating and season counts of the matched title, or {} if TMDB can't be asked."""
        try:
            found = tmdb.details(ident["kind"], ident["id"])
        except RuntimeError:
            return {}
        return {k: found.get(k) for k in ("kind",) + self.EXTRA_FACTS} if found else {}

    def _poster(self, ident):
        """The matched title's poster URL, or None. Decisions store no display data, so it is looked up (TMDB answers are
        cached); a failed lookup just means no poster."""
        try:
            found = tmdb.details(ident["kind"], ident["id"])
        except RuntimeError:
            return None
        return poster_url(found.get("poster"), "w342") if found else None

    def debug_text(self, fid):
        """Everything known about one file as text to paste into a chat; see debug.py."""
        entry = self.entry(fid)
        detail = self.detail(fid)
        record = self.known.get(entry["key"])
        if record and record.get("evidence"):
            raw = record["evidence"].get("raw")
        else:
            result = self.evaluation(entry)
            raw = triage.evidence_record(result["ev"], result["opinions"], 0, 0, extra_calls=entry["extra_calls"])["raw"]
        hide = [(self.base_dir, "<inbox>"), (self.output_dir, "<output>"), (Path.home(), "~")]
        return {"text": debug.build(detail, raw, hide)}

    @staticmethod
    def _runtime_check(duration, identity):
        try:
            return triage.runtime_check(duration, identity)
        except RuntimeError:  # TMDB unreachable: show the page without the comparison
            return None

    def _live_duration(self, entry):
        """The file's length read from disk, for decisions saved without it; None if the file is gone or unreadable."""
        path = self.base_dir / entry["path"] if self.base_dir else None
        if not path or not path.is_file():
            return None
        try:
            return probe.probe(path)["duration"]
        except RuntimeError:
            return None

    def detail(self, fid):
        entry = self.entry(fid)
        status = self.status(entry)
        out = {"id": fid, "path": entry["path"], "status": status, "decision": None, "grid_html": "", "flow": "", "tips": {},
               "hints": None, "candidates": [], "clues": [], "identity": None, "consensus": None, "duration": None, "runtime": None,
               "moved_to": folders.Roots.shown(entry["moved"]["to"]) if entry["moved"] else None}
        record = self.known.get(entry["key"])
        if record:
            evidence = record.get("evidence")
            out["decision"] = {"name": decisions.render(record), "source": record.get("source"), "url": report.identity_url(record["tmdb"])}
            out["identity"] = record["tmdb"]
            out["decision"]["poster"] = self._poster(record["tmdb"])
            out["decision"]["facts"] = self._facts(record["tmdb"])
            if evidence:
                out["clues"] = report.clues(evidence["sources"], record["tmdb"])
                out["consensus"] = evidence["consensus"]
                duration = ((evidence.get("raw") or {}).get("probe") or {}).get("duration") or self._live_duration(entry)
                out["runtime"] = self._runtime_check(duration, record["tmdb"])
                out["grid_html"] = report.matrix(evidence["sources"], record["tmdb"], evidence.get("raw"))
                out["tips"] = report.node_tips(evidence.get("raw"))
                out["flow"] = flow.diagram(entry["path"], evidence["sources"], record["tmdb"], evidence.get("raw"), record.get("source"))
            return out
        result = self.evaluation(entry)
        ev, guess_parse = result["ev"], result["ev"]["guessit_parse"]
        kind = "tv" if guess_parse.get("type") == "episode" else "movie"
        if entry["initial"] is None:  # the first search is made once and kept, so reopening a file costs nothing
            entry["initial"] = self._recorded(entry, self.candidates, entry, guess_parse.get("title", ""), kind, year=guess_parse.get("year"),
                                              country=guess_parse.get("country_code")) if guess_parse.get("title") else []
        evidence = triage.evidence_record(ev, result["opinions"], 0, 0, extra_calls=entry["extra_calls"])
        out["grid_html"] = report.matrix(result["opinions"], None, evidence["raw"])
        out["tips"] = report.node_tips(evidence["raw"])
        out["flow"] = flow.diagram(entry["path"], result["opinions"], None, evidence["raw"])
        out["hints"] = {"query": guess_parse.get("title", ""), "kind": kind}
        out["clues"] = report.clues(result["opinions"], None)
        out["candidates"] = entry["initial"]
        out["runtime"] = result.get("runtime")  # set only when the sources found a match to compare against
        out["duration"] = ev.get("duration")  # seconds, so the page can set it beside TMDB's runtime
        return out
