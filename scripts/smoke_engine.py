"""Drive a built engine through a real first run (M9 acceptance).

    python scripts/smoke_engine.py build/engine/hillaudio-engine/hillaudio-engine [--model tiny.en]

Starts the engine like the app does, then over HTTP: downloads the speech model
(first run), creates a project, imports the fixture recordings and manuscript,
transcribes and compares with the real model, exports an MP3, and checks every
job finished. Uses a throwaway app home, so it never touches the user's.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "engine" / "fixtures"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("engine", type=Path)
    ap.add_argument("--model", default="tiny.en")
    ap.add_argument(
        "--bundled-ffmpeg", action="store_true", help="require the engine's own ffmpeg copy"
    )
    args = ap.parse_args()
    home = Path(tempfile.mkdtemp(prefix="hillaudio-smoke-"))
    env = os.environ | {"HILLAUDIO_HOME": str(home)}
    env.pop("HILLAUDIO_MODELS", None)
    proc = subprocess.Popen(
        [str(args.engine), "serve", "--port", "0", "--exit-with-stdin"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env=env,
    )
    assert proc.stdout is not None and proc.stdin is not None
    t0 = time.monotonic()
    line = proc.stdout.readline()
    if not line.startswith("HILLAUDIO_READY"):
        print(f"FAIL: no READY line ({line!r})")
        return 1
    port = int(dict(kv.split("=", 1) for kv in line.split()[1:])["port"])
    print(f"engine ready in {time.monotonic() - t0:.1f} s on port {port}")
    base = f"http://127.0.0.1:{port}"

    def call(method: str, path: str, body: Any = None) -> Any:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(
            base + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"} if data else {},
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"null")

    def job(r: dict[str, Any], timeout: float = 900) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            j: dict[str, Any] = next(j for j in call("GET", "/api/jobs") if j["id"] == r["job_id"])
            if j["status"] in ("done", "failed", "cancelled"):
                if j["status"] != "done":
                    raise SystemExit(f"FAIL: {j['label']}: {j['status']} {j['error']}")
                return j
            time.sleep(0.5)
        raise SystemExit("FAIL: job timed out")

    try:
        assert call("GET", "/api/health")["status"] == "ok"
        ff = call("GET", "/api/health")["ffmpeg"]
        print(f"ffmpeg: {ff}")
        if args.bundled_ffmpeg:
            assert Path(ff).is_relative_to(args.engine.resolve().parent), "not the bundled ffmpeg"
        with urllib.request.urlopen(base + "/", timeout=10) as r:
            assert b'id="root"' in r.read(), "the UI isn't served"
        t = time.monotonic()
        job(call("POST", f"/api/models/{args.model}/download"))
        print(f"downloaded {args.model} in {time.monotonic() - t:.0f} s")
        loc = home / "projects"
        loc.mkdir()
        call("POST", "/api/projects", {"location": str(loc), "name": "Smoke"})
        names = ("ch01_take1.wav", "ch01_take2.wav", "ch02_take1.wav")
        paths = [str(FIXTURES / n) for n in names]
        job(call("POST", "/api/project/sources/import", {"paths": paths}))
        script = {"source": {"path": str(FIXTURES / "manuscript.txt")}}
        job(call("POST", "/api/project/manuscript", script))
        t = time.monotonic()
        a = job(call("POST", "/api/project/analysis", {"asr": {"model": args.model}}))
        print(f"analysis in {time.monotonic() - t:.0f} s: {json.dumps(a['result'])[:200]}")
        issues = call("GET", "/api/project/issues")["issues"]
        print(f"issues: {sorted(i['kind'] for i in issues)}")
        e = job(call("POST", "/api/project/export", {"format": "mp3", "split": "chapters"}))
        print(f"export pass={e['result']['pass']}: {[f['file'] for f in e['result']['files']]}")
        pdf = urllib.request.urlopen(base + "/api/project/guide?format=pdf", timeout=30).read()
        assert pdf.startswith(b"%PDF"), "the guide PDF"
        print("SMOKE OK")
        return 0
    finally:
        proc.stdin.close()
        proc.wait(timeout=20)


if __name__ == "__main__":
    sys.exit(main())
