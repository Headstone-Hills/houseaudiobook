"""Against a real uvicorn server, not the TestClient.

Regression: TestClient handles WebSockets on its own, so the API tests passed
while the real server answered /api/jobs/ws with 404 (uvicorn had no WebSocket
library installed). This test serves the app for real and connects to it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import threading
import time
from collections.abc import Callable, Iterator

import pytest
import uvicorn
import websockets
from fastapi import FastAPI
from websockets.typing import Origin

from hillaudio.api.app import create_app


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


class Served:
    def __init__(
        self, url: str, app: FastAPI, server: uvicorn.Server, thread: threading.Thread
    ) -> None:
        self.url, self.app, self.server, self.thread = url, app, server, thread


@pytest.fixture
def served(tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> Iterator[Served]:
    monkeypatch.setenv("HILLAUDIO_HOME", str(tmp_path))
    port = free_port()
    app = create_app()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "server did not start"
        time.sleep(0.02)
    yield Served(f"127.0.0.1:{port}", app, server, thread)
    server.should_exit = True
    thread.join(5)


def test_jobs_websocket_on_real_server(served: Served) -> None:
    server_url = served.url

    async def first_message() -> dict[str, object]:
        async with websockets.connect(f"ws://{server_url}/api/jobs/ws") as ws:
            data: dict[str, object] = json.loads(await asyncio.wait_for(ws.recv(), 5))
            return data

    assert asyncio.run(first_message()) == {"type": "jobs", "jobs": []}


def test_real_server_refuses_foreign_origin(served: Served) -> None:
    server_url = served.url

    async def connect() -> None:
        async with websockets.connect(
            f"ws://{server_url}/api/jobs/ws", origin=Origin("https://evil.example")
        ) as ws:
            await asyncio.wait_for(ws.recv(), 5)

    with pytest.raises(websockets.exceptions.WebSocketException):
        asyncio.run(connect())


def wait_until(cond: Callable[[], bool], timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def test_websocket_handler_exits_when_client_leaves(served: Served) -> None:
    """Regression: an idle handler never noticed the client was gone."""

    async def connect_then_leave() -> None:
        async with websockets.connect(f"ws://{served.url}/api/jobs/ws") as ws:
            await asyncio.wait_for(ws.recv(), 5)
            assert served.app.state.engine.ws_clients == 1

    asyncio.run(connect_then_leave())
    assert wait_until(lambda: served.app.state.engine.ws_clients == 0)


def test_server_shuts_down_with_a_client_connected(served: Served) -> None:
    """Regression: an open job WebSocket blocked uvicorn's shutdown and auto-reload."""

    async def hold_open() -> None:
        async with websockets.connect(f"ws://{served.url}/api/jobs/ws") as ws:
            await asyncio.wait_for(ws.recv(), 5)
            served.server.should_exit = True
            with contextlib.suppress(websockets.exceptions.ConnectionClosed):
                await asyncio.wait_for(ws.recv(), 5)

    asyncio.run(hold_open())
    served.thread.join(5)
    assert not served.thread.is_alive()
