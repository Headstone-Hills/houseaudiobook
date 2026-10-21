"""A stand-in for Hugging Face's file server: pinned /resolve/ paths, Range
requests, and optional faults (a dropped connection). Used by the model-download
tests and by the e2e engine, so neither downloads real weights."""

from __future__ import annotations

import hashlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from hillaudio.asr import models


def git_sha1(b: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(b) + b, usedforsecurity=False).hexdigest()


def fake_model(
    name: str, weights: bytes, repo: str | None = None, revision: str = "abc123"
) -> tuple[models.Model, dict[str, bytes]]:
    """A catalog entry and its files, with real checksums of fake content."""
    config = b'{"fake": "%s"}' % name.encode()
    m = models.Model(
        name,
        repo or f"Fake/{name}",
        revision,
        "MIT",
        (
            models.ModelFile("config.json", len(config), git_sha1=git_sha1(config)),
            models.ModelFile("model.bin", len(weights), hashlib.sha256(weights).hexdigest()),
        ),
    )
    return m, {"config.json": config, "model.bin": weights}


class FakeHub:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}  # "/<repo>/resolve/<rev>/<file>" -> bytes
        self.requests: list[tuple[str, str | None]] = []
        self.fail_after: int | None = None  # drop model.bin after this many bytes
        self.delay_s = 0.0  # per chunk, so a UI can see progress
        hub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a: Any) -> None:
                pass

            def do_GET(self) -> None:
                hub.requests.append((self.path, self.headers.get("Range")))
                body = hub.files.get(self.path)
                if body is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                rng = self.headers.get("Range")
                start = int(rng.split("=")[1].split("-")[0]) if rng else 0
                self.send_response(206 if rng else 200)
                part = body[start:]
                self.send_header("Content-Length", str(len(part)))
                self.end_headers()
                if hub.fail_after is not None and self.path.endswith("model.bin"):
                    self.wfile.write(part[: hub.fail_after])
                    self.wfile.flush()
                    self.connection.shutdown(2)
                    return
                import time

                for i in range(0, len(part), 1 << 18):
                    self.wfile.write(part[i : i + (1 << 18)])
                    if hub.delay_s:
                        time.sleep(hub.delay_s)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def add(self, m: models.Model, files: dict[str, bytes]) -> None:
        for name, body in files.items():
            self.files[f"/{m.repo}/resolve/{m.revision}/{name}"] = body
