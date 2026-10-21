"""`hillaudio serve` (M9): one port for UI + API, READY line, exits with stdin."""

from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from hillaudio import settings, tools


def start(tmp_path: Path, *extra: str) -> tuple[subprocess.Popen[str], int]:
    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text("<!doctype html><title>HillAudiobook</title>")
    (web / "assets" / "app.js").write_text("console.log('hi')")
    env = os.environ | {"HILLAUDIO_HOME": str(tmp_path / "home")}
    proc = subprocess.Popen(
        [sys.executable, "-m", "hillaudio", "serve", "--port", "0", "--web-dir", str(web), *extra],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert proc.stdout is not None
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if line.startswith("HILLAUDIO_READY"):
            fields = dict(kv.split("=", 1) for kv in line.split()[1:])
            assert fields["url"] == f"http://127.0.0.1:{fields['port']}/"
            return proc, int(fields["port"])
        if proc.poll() is not None:
            break
    proc.kill()
    raise AssertionError(f"no READY line: {proc.stderr.read() if proc.stderr else ''}")


def get(port: int, path: str, headers: dict[str, str] | None = None) -> tuple[int, bytes, str]:
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("GET", path, headers=headers or {})
    r = c.getresponse()
    return r.status, r.read(), r.getheader("content-type") or ""


def test_serves_ui_and_api_on_one_port_and_exits_with_stdin(tmp_path: Path) -> None:
    proc, port = start(tmp_path, "--exit-with-stdin")
    try:
        status, body, ctype = get(port, "/")
        assert (
            status == 200
            and b"<title>HillAudiobook</title>" in body
            and ctype.startswith("text/html")
        )
        assert get(port, "/assets/app.js")[0] == 200
        status, body, _ = get(port, "/api/health")
        assert status == 200 and json.loads(body)["status"] == "ok"
        # Its own origin is allowed (the window loads from it); others are not.
        assert get(port, "/api/health", {"Origin": f"http://127.0.0.1:{port}"})[0] == 200
        assert get(port, "/api/health", {"Origin": "https://evil.example"})[0] == 403
        assert get(port, "/api/health", {"Host": "evil.example"})[0] == 403
    finally:
        assert proc.stdin is not None
        proc.stdin.close()  # the shell went away
    assert proc.wait(timeout=10) == 0


def test_refuses_non_loopback_and_unknown_commands(tmp_path: Path) -> None:
    out = subprocess.run(
        [sys.executable, "-m", "hillaudio", "serve", "--host", "0.0.0.0"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert out.returncode == 2 and "loopback" in out.stderr
    out = subprocess.run(
        [sys.executable, "-m", "hillaudio", "dance"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert out.returncode == 2


def test_missing_ui_is_a_clear_error(tmp_path: Path) -> None:
    from hillaudio.api.app import create_app

    with pytest.raises(RuntimeError, match="npm run build"):
        create_app(web_dir=tmp_path)


def test_tool_resolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tools._find.cache_clear()
    for n in ("ffmpeg", "ffprobe"):
        (tmp_path / n).write_text("#!/bin/sh\n")
    monkeypatch.setenv("HILLAUDIO_FFMPEG_DIR", str(tmp_path))
    assert tools.ffmpeg() == str(tmp_path / "ffmpeg") and tools.ffprobe() == str(
        tmp_path / "ffprobe"
    )
    tools._find.cache_clear()
    monkeypatch.delenv("HILLAUDIO_FFMPEG_DIR")
    bundle = tmp_path / "frozen"
    (bundle / "bin").mkdir(parents=True)
    (bundle / "bin" / "ffmpeg").write_text("")
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    assert tools.ffmpeg() == str(bundle / "bin" / "ffmpeg")
    tools._find.cache_clear()


def test_app_home_per_platform(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HILLAUDIO_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "darwin")
    assert settings.app_home() == tmp_path / "Library" / "Application Support" / "HillAudiobook"
    (tmp_path / ".hillaudiobook").mkdir()  # an existing home keeps working
    assert settings.app_home() == tmp_path / ".hillaudiobook"
    monkeypatch.setattr(sys, "platform", "linux")
    assert settings.app_home() == tmp_path / ".hillaudiobook"
    monkeypatch.setenv("HILLAUDIO_HOME", str(tmp_path / "x"))
    assert settings.app_home() == tmp_path / "x"
