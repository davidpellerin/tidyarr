"""Serve the review web UI on localhost and open it in your browser.

This is the `tidyarr` executable (`make build`); from source it needs the project venv (FastAPI) and runs with
`make serve`. The address includes a random access token in the URL fragment; without it the API refuses every
request. The server only listens on 127.0.0.1.

By default the server starts in the background, logging to server.log in the state folder (~/.local/state/tidyarr
on Linux), and stops itself a couple of minutes after the last page showing it is closed. Running it again while it is
up just reopens the page. --foreground keeps it in this terminal, logging here, until Ctrl+C (`make serve` does that).

Usage: tidyarr [--input-root DIR] [--output-root DIR] [--port 8765] [--no-open] [--foreground]
       tidyarr --stop ...
"""
import argparse
import os
import secrets
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    import uvicorn
except ImportError:
    sys.exit("error: FastAPI/uvicorn are not installed in this Python. Use `make serve` (it runs the project venv).")
from tidyarr import config, decisions, folders, instance, tmdb  # noqa: E402
from tidyarr.server import create_app  # noqa: E402
from tidyarr.session import Session  # noqa: E402

IDLE_LIMIT = 120  # seconds: a background server stops once no page has pinged it (every 30s, slower in a hidden tab) for this long
START_LIMIT = 30  # seconds to wait for a background server to answer (a `make build` executable unpacks itself first)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=config.PATH, type=Path, help="the settings file (folders, naming formats, TMDB API key)")
    parser.add_argument("--input-root", help="folders to scan are picked from under this one (overrides the config)")
    parser.add_argument("--output-root", help="the output folder is picked from under this one (overrides the config)")
    parser.add_argument("--decisions", default=decisions.DEFAULT_PATH, type=Path)
    parser.add_argument("--port", default=8765, type=int)
    parser.add_argument("--no-open", action="store_true", help="print the address but don't open a browser")
    parser.add_argument("--foreground", action="store_true", help="stay in this terminal and log here, until Ctrl+C")
    parser.add_argument("--dev", action="store_true", help="for development (`make dev`): keep the token from $TIDYARR_DEV_TOKEN across restarts, and let the page reload itself")
    parser.add_argument("--stop", action="store_true", help="stop the server that is running")
    parser.add_argument("--detached", action="store_true", help=argparse.SUPPRESS)  # the background server itself
    args = parser.parse_args()

    running = instance.find()
    if args.stop:
        if running:
            instance.ask(running, "quit", method="POST")
        print(f"Stopped tidyarr (pid {running['pid']})." if running else "tidyarr is not running.")
        return
    if running:
        print(f"tidyarr is already running (pid {running['pid']}); `tidyarr --stop` to restart it with other options.\n  {instance.url(running)}")
        if not args.no_open:
            webbrowser.open(instance.url(running))
        return
    config.PATH = args.config
    if not config.PATH.exists():
        config.save(config.DEFAULTS)  # so there is a file to edit, with every setting in it
    settings = config.load()
    if sys.stdin.isatty():
        setup_missing_folders(args, settings)
    try:
        roots = folders.Roots(args.input_root or settings["input_root"], args.output_root or settings["output_root"])
    except ValueError as e:
        sys.exit(f"error: {e}")
    if not (args.foreground or args.detached):
        return launch(args)

    token = os.environ.get("TIDYARR_DEV_TOKEN") if args.dev else None
    token = token or secrets.token_urlsafe(24)
    hosts = {f"127.0.0.1:{args.port}", f"localhost:{args.port}"}
    app = create_app(Session(None, args.decisions), token, hosts, roots,
                     {key: settings[key] for key in ("tv_format", "movie_format")}, quit=lambda: setattr(server, "should_exit", True), dev=args.dev)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning"))
    url = f"http://127.0.0.1:{args.port}/#token={token}"
    print(f"Review UI (pid {os.getpid()}): input under {roots.input_root}, output under {roots.output_root}", flush=True)
    if args.foreground:
        print(f"  {url}\n(Ctrl+C to stop)", flush=True)
        if not tmdb.key_status()["source"]:
            print("No TMDB API key yet: the page will ask for one.", flush=True)
        if not args.no_open:
            threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    else:
        threading.Thread(target=stop_when_idle, args=(app, server), daemon=True).start()
    instance.write(args.port, token)
    try:
        server.run()
    except KeyboardInterrupt:  # uvicorn has already shut down cleanly; it re-raises Ctrl+C once it's done
        print("\nStopped.", flush=True)
    finally:
        instance.clear()


def setup_missing_folders(args, settings):
    """First run: the default folders may not exist on this machine, so ask for them (Enter creates the suggestion) and remember."""
    chosen = {}
    for key, flag, what in (("input_root", args.input_root, "scan for videos"), ("output_root", args.output_root, "move the sorted videos to")):
        if flag or Path(settings[key]).expanduser().is_dir():
            continue
        if not chosen:
            print(f"First run: some folders in {config.PATH} don't exist yet.")
        while True:
            try:
                answer = input(f"Folder to {what} [{settings[key]}]: ").strip() or settings[key]
            except EOFError:
                return
            path = Path(answer).expanduser()
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                print(f"  can't use that: {e}")
                continue
            chosen[key] = settings[key] = answer
            break
    if chosen:
        config.save(chosen)
        print(f"Saved to {config.PATH}; edit it there to change them later.\n")


def stop_when_idle(app, server):
    while not server.should_exit:
        time.sleep(5)
        if time.monotonic() - app.state.last_seen > IDLE_LIMIT:  # monotonic: time asleep (suspend) doesn't count
            print(f"No page open for {IDLE_LIMIT}s: stopping.", flush=True)
            server.should_exit = True


def launch(args):
    """Start this program again as a background server, wait for it to answer, then open the page."""
    command = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, str(Path(__file__).resolve())]
    env = {**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"}  # the copy a build unpacks is deleted when we exit, so it unpacks its own
    detach = ({"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
              else {"start_new_session": True})  # so closing the terminal doesn't stop it
    instance.LOG.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with open(instance.LOG, "w") as log:
        child = subprocess.Popen([*command, *sys.argv[1:], "--detached"], stdin=subprocess.DEVNULL, stdout=log,
                                 stderr=subprocess.STDOUT, env=env, **detach)
    deadline = time.monotonic() + START_LIMIT
    while (running := instance.find()) is None:
        if child.poll() is not None or time.monotonic() > deadline:
            tail = "\n".join(instance.LOG.read_text(errors="replace").splitlines()[-15:])
            sys.exit(f"error: the server didn't start (log: {instance.LOG})\n{tail}")
        time.sleep(0.2)
    url = instance.url(running)
    print(f"tidyarr is running in the background (pid {running['pid']}, log: {instance.LOG}).\n  {url}\n"
          "It stops a couple of minutes after you close the page, or with `tidyarr --stop`.")
    if not tmdb.key_status()["source"]:
        print("No TMDB API key yet: the page will ask for one.")
    if not args.no_open:
        webbrowser.open(url)


if __name__ == "__main__":
    main()
