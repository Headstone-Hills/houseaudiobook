"""`hillaudio serve`: the engine and the built UI on one localhost port.

The packaged app's shell starts this (M9, D-054): with `--port 0` the engine
picks a free port itself (no race between choosing and binding), prints

    HILLAUDIO_READY port=53712 url=http://127.0.0.1:53712/

once it is listening, and with `--exit-with-stdin` stops when its stdin closes,
so it never outlives the app, even if the app is killed.
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
from pathlib import Path

READY = "HILLAUDIO_READY"


def default_web_dir() -> Path | None:
    """The UI bundled into a frozen engine, else the repo's web/dist if built."""
    base = getattr(sys, "_MEIPASS", None)
    candidates = [Path(base) / "web"] if base else []
    candidates.append(Path(__file__).resolve().parents[2] / "web" / "dist")
    return next((c for c in candidates if (c / "index.html").is_file()), None)


def serve(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hillaudio serve")
    ap.add_argument("--host", default="127.0.0.1", help="loopback only (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=8765, help="0 picks a free port")
    ap.add_argument("--web-dir", type=Path, default=None, help="built UI to serve at /")
    ap.add_argument("--no-web", action="store_true", help="API only")
    ap.add_argument("--exit-with-stdin", action="store_true", help="stop when stdin closes")
    args = ap.parse_args(argv)
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        ap.error("the engine only listens on loopback (local only, CLAUDE.md)")

    import uvicorn

    from hillaudio.api.app import create_app
    from hillaudio.api.guard import allowed_origins

    sock = socket.socket(socket.AF_INET6 if args.host == "::1" else socket.AF_INET)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((args.host, args.port))
    port = sock.getsockname()[1]
    origins = allowed_origins() | {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
    web = None if args.no_web else (args.web_dir or default_web_dir())
    app = create_app(origins=origins, web_dir=web)

    class Server(uvicorn.Server):
        async def startup(self, sockets: list[socket.socket] | None = None) -> None:
            await super().startup(sockets)
            host = "127.0.0.1" if args.host != "::1" else "[::1]"
            print(f"{READY} port={port} url=http://{host}:{port}/", flush=True)

    server = Server(uvicorn.Config(app, log_level="warning", lifespan="on"))
    if args.exit_with_stdin:

        def watch() -> None:
            sys.stdin.read()  # returns at EOF: the parent has gone
            server.should_exit = True

        threading.Thread(target=watch, daemon=True).start()
    server.run(sockets=[sock])
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: hillaudio serve [--port N] [--web-dir DIR] [--exit-with-stdin]")
        return 0
    if argv[0] == "serve":
        return serve(argv[1:])
    print(f"unknown command {argv[0]!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
