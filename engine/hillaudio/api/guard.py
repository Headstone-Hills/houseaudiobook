"""Refuse requests that don't come from our own UI.

The engine listens on 127.0.0.1, but any web page the user visits can still try
to call it: a cross-site request (CSRF), or a DNS-rebinding page whose hostname
resolves to 127.0.0.1. Either could import or read local files. Two checks stop
both:
- the Host header must be a loopback name (rebinding pages carry their own host);
- a request that has an Origin header must come from an allowed origin. Requests
  with no Origin (curl, tests, the native shell's own process) pass.
"""

from __future__ import annotations

import os

from starlette.types import ASGIApp, Receive, Scope, Send

DEFAULT_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]"})
DEFAULT_ORIGINS = frozenset(
    {
        "http://127.0.0.1:5173",  # Vite dev server
        "http://localhost:5173",
        "http://127.0.0.1:8765",  # engine serving the built UI
        "http://localhost:8765",
        "tauri://localhost",  # native shell (M9)
        "http://tauri.localhost",
    }
)


def allowed_origins() -> frozenset[str]:
    extra = os.environ.get("HILLAUDIO_ALLOWED_ORIGINS", "")
    return DEFAULT_ORIGINS | {o.strip() for o in extra.split(",") if o.strip()}


def _hostname(host: str) -> str:
    """Strip the port: '127.0.0.1:8765' -> '127.0.0.1', '[::1]:8765' -> '[::1]'."""
    if host.startswith("["):
        return host.split("]")[0] + "]"
    return host.rsplit(":", 1)[0]


class LocalOnlyGuard:
    def __init__(self, app: ASGIApp, hosts: frozenset[str], origins: frozenset[str]) -> None:
        self.app, self.hosts, self.origins = app, hosts, origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
            host = headers.get("host", "")
            hostname = _hostname(host)
            origin = headers.get("origin")
            if hostname not in self.hosts or (origin is not None and origin not in self.origins):
                await self._deny(scope, send)
                return
        await self.app(scope, receive, send)

    @staticmethod
    async def _deny(scope: Scope, send: Send) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        body = b'{"detail":"request refused: not from the local HillAudiobook UI"}'
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": body})
