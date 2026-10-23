"""The review web UI: a small FastAPI app over a Session.

It can launch a video player and write decisions and move files, so it is built to be local-only:
  - the caller binds it to 127.0.0.1;
  - every /api request needs the random per-run token (the page gets it from the URL fragment, which is never sent
    to the server or written to logs);
  - the Host header must be our own address, which defeats DNS-rebinding attacks from other web pages;
  - writes must not come from another origin;
  - the input and output folders are the server-configured roots (folders.py); the browser never names a folder;
  - the TMDB API key can be saved from the page and is sent back only to fill the (masked) Settings field;
  - the page runs only our own script (Content-Security-Policy), and all text is inserted as text.
"""
import hmac
import os
import time
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from tidyarr import config, naming, report, tmdb
from tidyarr.folders import Roots

WEB_DIR = Path(__file__).resolve().parent / "web"
VENDOR_DIR = WEB_DIR / "vendor"  # movi-player and Mermaid, fetched from jsDelivr by `make serve` / `make build`; only these files are ever served
VENDOR_FILES = {"movi-element.js": "text/javascript", "movi.wasm": "application/wasm", "mermaid.js": "text/javascript"}
CSP = ("default-src 'none'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; "
       "img-src https://image.tmdb.org blob: data:; media-src blob:; worker-src blob:; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


class SearchBody(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    kind: Literal["tv", "movie"]


class DestinationBody(BaseModel):
    tv: str = Field(max_length=naming.MAX_FORMAT + 1)
    movie: str = Field(max_length=naming.MAX_FORMAT + 1)


class SettingsBody(BaseModel):
    tv_format: str = Field(max_length=naming.MAX_FORMAT + 1)
    movie_format: str = Field(max_length=naming.MAX_FORMAT + 1)
    tmdb_api_key: str = ""  # "" keeps the key there is. No length limit here: a validation error would repeat what was sent
    remove_empty_folders: bool | None = None  # None keeps what is saved


SAMPLE_TV = {"kind": "tv", "title": "Example", "year": 2024, "season": 1, "episode": 1}
SAMPLE_MOVIE = {"kind": "movie", "title": "Example", "year": 2024}


class CleanBody(BaseModel):
    junk: list[str] = Field(max_length=10000)  # the list the page was shown, which must still be what is there


class DecideBody(BaseModel):
    kind: Literal["tv", "movie"]
    id: int = Field(gt=0, lt=10**9)


def run(fn, *args):
    """Call a Session method, turning its errors into HTTP errors with messages that are safe to show."""
    try:
        return fn(*args)
    except KeyError:
        raise HTTPException(404, "no such file") from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    except RuntimeError as e:  # tmdb.py raises RuntimeError with printable messages
        raise HTTPException(502, str(e)) from None


def create_app(session, token, allowed_hosts, roots=None, formats=None, quit=None, dev=False):
    """`quit`, if given, is called by POST /api/quit to stop the server. app.state.last_seen is when the page last
    made an authenticated request (it pings while open), so the caller can stop the server once no page is left.
    `dev` adds /api/dev-version, which the page polls to reload itself when the server restarts or a web file changes."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)  # no self-describing API surface
    app.state.last_seen = time.monotonic()
    own_origins = {f"http://{host}" for host in allowed_hosts}
    formats = dict(formats or {"tv_format": naming.DEFAULT_TV, "movie_format": naming.DEFAULT_MOVIE})

    @app.middleware("http")
    async def guard(request: Request, call_next):
        rejection = None
        if request.headers.get("host", "") not in allowed_hosts:
            rejection = JSONResponse({"detail": "unexpected host"}, status_code=403)
        elif request.url.path.startswith("/api/"):
            if not hmac.compare_digest(request.headers.get("x-token", "").encode(), token.encode()):
                rejection = JSONResponse({"detail": "missing or wrong token"}, status_code=401)
            elif request.method != "GET" and request.headers.get("origin", next(iter(own_origins))) not in own_origins:
                rejection = JSONResponse({"detail": "cross-origin request refused"}, status_code=403)
            else:
                app.state.last_seen = time.monotonic()
        response = rejection or await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    async def index():
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/app.js")
    async def script():
        return FileResponse(WEB_DIR / "app.js", media_type="text/javascript")

    @app.get("/app.css")
    async def styles():
        return FileResponse(WEB_DIR / "app.css", media_type="text/css")

    @app.get("/report.css")  # the evidence grid is rendered by report.py, so it shares that stylesheet
    async def report_styles():
        return Response(report.CSS, media_type="text/css")

    @app.get("/vendor/{name}")
    async def vendor(name: str):
        path = VENDOR_DIR / name
        if name not in VENDOR_FILES or not path.is_file():
            raise HTTPException(404, "run `make serve` to fetch the in-browser player")
        return FileResponse(path, media_type=VENDOR_FILES[name])

    @app.get("/api/ping")
    def ping():
        """The page calls this while it is open; a second launch calls it to check this server is the one it recorded."""
        return {"pid": os.getpid()}

    if dev:
        @app.get("/api/dev-version")
        def dev_version():
            return {"pid": os.getpid(), "web": max(path.stat().st_mtime_ns for path in WEB_DIR.glob("*") if path.is_file())}

    @app.post("/api/quit")
    def stop():
        if quit is None:
            raise HTTPException(404, "this server can't be stopped from here")
        quit()
        return {}

    @app.get("/api/files")
    def files():
        return run(session.list)

    @app.get("/api/files/writing")
    def writing():
        """Which files look like downloads still in progress; the page asks every few seconds."""
        return run(session.writing)

    @app.get("/api/poster/{kind}/{tmdb_id}")
    def poster(kind: Literal["movie", "tv"], tmdb_id: int):
        """The poster for one title, for the file list's thumbnails; null when TMDB has none."""
        return {"poster": run(lambda: session.poster_for({"kind": kind, "id": tmdb_id}))}

    @app.get("/api/stats")
    def stats():
        return run(session.stats)

    @app.get("/api/folders")
    def folders():
        choices = roots.choices() if roots else {"input": None, "output": None}
        defaults = {"tv_format": naming.DEFAULT_TV, "movie_format": naming.DEFAULT_MOVIE}
        return {**choices, "formats": formats, "default_formats": defaults, "tmdb_key": tmdb.key_status(),
                "remove_empty_folders": config.load()["remove_empty_folders"]}

    @app.post("/api/settings")
    def settings(body: SettingsBody):
        """Save the naming formats, removing empty folders and, if one was typed, the TMDB API key (once TMDB has accepted it)
        to the settings file."""
        def save():
            naming.check(body.tv_format, "tv")
            naming.check(body.movie_format, "movie")
            updates = {"tv_format": body.tv_format, "movie_format": body.movie_format}
            key = body.tmdb_api_key.strip()
            if key:
                tmdb.check_key(key)
                updates[config.KEY] = key
            if body.remove_empty_folders is not None:
                updates["remove_empty_folders"] = body.remove_empty_folders
            config.save(updates)
            formats.update({"tv_format": body.tv_format, "movie_format": body.movie_format})
            return {"formats": formats, "tmdb_key": tmdb.key_status(), "remove_empty_folders": config.load()["remove_empty_folders"]}
        return run(save)

    @app.post("/api/preview")
    def preview(body: DestinationBody):
        """What each format gives for a made-up show and movie, so Settings can show it; an unusable format gives an error text."""
        def one(template, kind, ident, episode_name=None):
            try:
                return {"path": str(naming.render(template, kind, naming.values(ident, "mkv", episode_name)))}
            except ValueError as e:
                return {"error": str(e)}
        return {"tv": one(body.tv, "tv", SAMPLE_TV, "Pilot"), "movie": one(body.movie, "movie", SAMPLE_MOVIE)}

    @app.post("/api/scan")
    def scan():
        """Scan the input root (or, without roots, the session's fixed inbox)."""
        if roots is not None:
            run(lambda: session.set_folders(roots.input_root, roots.output_root))
        return run(session.scan)

    @app.get("/api/files/{fid}")
    def detail(fid: str):
        return run(session.detail, fid)

    @app.get("/api/files/{fid}/frames")
    def frames(fid: str):
        """Still frames picked from the video (a title card, the credits) to help tell what it is. Read-only."""
        return run(session.frames, fid)

    @app.get("/api/files/{fid}/images")
    def images(fid: str):
        """TMDB artwork (backdrops, logos, posters, the episode's stills) for the matched title, or the top suggestion. Read-only."""
        return run(session.images, fid)

    @app.get("/api/files/{fid}/debug")
    def debug(fid: str):
        return run(session.debug_text, fid)

    @app.post("/api/files/{fid}/search")
    def search(fid: str, body: SearchBody):
        return run(session.search, fid, body.query, body.kind)

    @app.post("/api/files/{fid}/destination")
    def destination(fid: str, body: DestinationBody):
        """Where this file would be put, by the naming formats in the page. Read-only: nothing is renamed or moved."""
        relative = run(session.destination, fid, {"tv": body.tv, "movie": body.movie})
        subs = run(session.subtitle_destinations, fid, relative)
        return {"path": f"{Roots.shown(session.output_dir)}/{relative}", "relative": relative, "subtitles": [Path(s).name for s in subs],
                "taken": any((session.output_dir / end).exists() for end in (relative, *subs))}

    @app.get("/api/leftovers")
    def leftovers():
        """Release folders that files were moved out of, with no video left: the junk that could go to the trash."""
        return run(session.leftovers, config.load()["junk_extensions"])

    @app.post("/api/leftovers/{lid}/clean")
    def clean(lid: str, body: CleanBody):
        """Put one folder's junk in the trash. The page sends this only when the person asks, with the list it showed."""
        return run(session.clean_leftovers, lid, config.load()["junk_extensions"], body.junk)

    @app.post("/api/files/{fid}/decide")
    def decide(fid: str, body: DecideBody):
        return run(session.decide, fid, body.kind, body.id)

    @app.post("/api/files/{fid}/undo")
    def undo(fid: str):
        return run(session.undo, fid)

    @app.post("/api/files/{fid}/approve")
    def approve(fid: str, body: DestinationBody):
        """Move the file to its new name under the output folder. The page sends this only when the person approves.
        If Settings say so, the release folder it came from is removed when the move leaves it empty."""
        return run(session.approve, fid, {"tv": body.tv, "movie": body.movie}, config.load()["remove_empty_folders"])

    @app.post("/api/files/{fid}/delete")
    def delete(fid: str):
        """Put the file (and its subtitles) in the system trash. The page sends this only once the person has confirmed."""
        return run(session.delete, fid, config.load()["remove_empty_folders"])

    @app.post("/api/files/{fid}/restore")
    def restore(fid: str):
        """Put a moved file back where it was."""
        return run(session.restore, fid)

    @app.api_route("/api/files/{fid}/original", methods=["GET", "HEAD"])  # the player asks for the size with HEAD first
    def original(fid: str):
        """The video file itself, for the in-browser player, once it has finished arriving. FileResponse answers Range requests, so it can seek."""
        return FileResponse(run(session.playable_path, fid))

    return app
