"""Finalize, export and the narrator guide through the API (M8)."""

from __future__ import annotations

import csv
import io
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import test_analysis_api as base
from conftest import FIXTURES
from fastapi.testclient import TestClient
from pypdf import PdfReader

from hillaudio.ingest.decode import iter_blocks
from hillaudio.render import dsp

client, factory, setup_book, wait = base.client, base.factory, base.setup_book, base.wait


def analyzed(c: TestClient) -> None:
    setup_book(c)
    job = wait(c, c.post("/api/project/analysis", json={}))
    assert job["status"] == "done", job


def job(c: TestClient, path: str, body: dict[str, Any], ok: bool = True) -> dict[str, Any]:
    r = c.post(path, json=body)
    assert r.status_code == 200, r.text
    j = c.app.state.engine.jobs.wait(r.json()["job_id"], 120).to_json()  # type: ignore[attr-defined]
    if ok:
        assert j["status"] == "done", j
    out: dict[str, Any] = j
    return out


def root(c: TestClient) -> Path:
    p: Path = c.app.state.engine.require_project().root  # type: ignore[attr-defined]
    return p


def failing(report: dict[str, Any]) -> list[Any]:
    return [(f["title"], r) for f in report["files"] for r in f["checks"] if not r["pass"]]


def test_analyze_meets_acx_after_finalize(client: TestClient) -> None:
    analyzed(client)
    rep = job(client, "/api/project/finalize/analyze", {"split": "single"})["result"]
    assert failing(rep) == [] and rep["pass"]
    (f,) = rep["files"]
    lv = f["levels"]
    assert -23 <= lv["rms_db"] <= -18 and lv["peak_db"] <= -3 and lv["noise_floor_db"] <= -60
    # Remembered for the export dialog.
    assert client.get("/api/project/export/preflight").json()["last_analysis"]["pass"]


def test_mp3_export_is_acx_shaped_and_measured_from_the_file(client: TestClient) -> None:
    analyzed(client)
    rep = job(client, "/api/project/export", {"format": "mp3"})["result"]
    (f,) = rep["files"]
    path = root(client) / f["file"]
    assert path.suffix == ".mp3" and path.is_file()
    info = f["format"]
    assert (info["codec"], info["sample_rate"], info["channels"]) == ("mp3", 44100, 1)
    assert round(info["bit_rate"] / 1000) == 192
    assert failing(rep) == [], failing(rep)
    # The report is the decoded file's levels, independently re-measured here.
    audio = np.concatenate(list(iter_blocks(path, 1)))
    lv = dsp.measure(audio, 44100)
    assert lv.peak_db == pytest.approx(f["levels"]["peak_db"], abs=1e-3)
    # Room tone at the head: the first ~1 s is quiet, then speech.
    head = dsp.measure(audio[: int(0.9 * 44100)], 44100)
    assert head.rms_db < -50
    assert (root(client) / Path(f["file"]).parent / "report.json").is_file()
    listed = client.get("/api/project/exports").json()
    assert listed[0]["files"][0]["file"] == f["file"]
    r = client.get(f"/api/project/exports/{Path(f['file']).parent.name}/{path.name}")
    assert r.status_code == 200 and r.content == path.read_bytes()


def test_wav_export_split_by_chapter(client: TestClient) -> None:
    analyzed(client)
    pf = client.get(
        "/api/project/export/preflight", params={"format": "wav24", "split": "chapters"}
    ).json()
    assert [o["title"] for o in pf["outputs"]] == [
        "Chapter One. The Fox and the Grapes.",
        "Chapter Two. The Tortoise and the Hare.",
    ]
    rep = job(client, "/api/project/export", {"format": "wav24", "split": "chapters"})["result"]
    assert [Path(f["file"]).name for f in rep["files"]] == [
        "01_Chapter_One_The_Fox_and_the_Grapes.wav",
        "02_Chapter_Two_The_Tortoise_and_the_Hare.wav",
    ]
    assert all(f["format"]["sample_rate"] == 16000 for f in rep["files"])
    assert failing(rep) == []
    # 24-bit PCM.
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_name",
            "-of",
            "csv=p=0",
            str(root(client) / rep["files"][0]["file"]),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == "pcm_s24le"


def test_mp3_from_mp3_sources_is_refused(client: TestClient, tmp_path: Path) -> None:
    mp3 = tmp_path / "pickup.mp3"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(FIXTURES / "ch01_take2.wav"), "-b:a", "128k", str(mp3)],
        check=True,
    )
    wait(client, client.post("/api/project/sources/import", json={"paths": [str(mp3)]}))
    pf = client.get("/api/project/export/preflight", params={"format": "mp3"}).json()
    assert pf["lossy_sources"] == ["pickup"] and "lossy to lossy" in pf["problems"][0]
    r = client.post("/api/project/export", json={"format": "mp3"})
    assert r.status_code == 409 and "never done" in r.json()["detail"]
    # WAV is fine.
    assert (
        client.get("/api/project/export/preflight", params={"format": "wav16"}).json()["problems"]
        == []
    )


def test_settings_and_presets(client: TestClient) -> None:
    v = client.get("/api/project/finalize").json()
    assert v["settings"]["preset"] == "acx" and v["presets"]["acx"]["rms_min_db"] == -23.0
    assert "Verify on acx.com" in v["presets"]["acx"]["note"]
    r = client.put("/api/project/finalize", json={"nr_strength": 0.8, "gate": True})
    assert r.status_code == 200 and r.json()["settings"]["nr_strength"] == 0.8
    assert client.put("/api/project/finalize", json={"nr_strength": 3}).status_code == 400
    assert client.put("/api/project/finalize", json={"preset": "nope"}).status_code == 400
    assert (
        client.put("/api/project/finalize/presets/acx", json={"rms_max_db": -10}).status_code == 400
    )
    r = client.put(
        "/api/project/finalize/presets/podcast",
        json={"name": "Podcast", "rms_min_db": -19, "rms_max_db": -14, "max_minutes": None},
    )
    assert r.status_code == 200 and r.json()["presets"]["podcast"]["rms_max_db"] == -14
    assert client.put("/api/project/finalize", json={"preset": "podcast"}).status_code == 200


def test_preview_with_the_chain(client: TestClient) -> None:
    analyzed(client)
    r = client.get("/api/project/finalize/preview", params={"start": 2.0, "end": 6.0})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    import soundfile as sf

    data, sr = sf.read(io.BytesIO(r.content))
    assert sr == 16000 and len(data) == 4 * 16000
    assert (
        client.get("/api/project/finalize/preview", params={"start": 3, "end": 2}).status_code
        == 400
    )


def test_corrections_guide_in_three_formats(client: TestClient) -> None:
    analyzed(client)
    r = client.get("/api/project/guide", params={"format": "csv", "narrator": "Sam Reader"})
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    lines = [row for row in csv.reader(io.StringIO(r.text))]
    assert lines[0][0].startswith("# Corrections guide: Book") and lines[1] == [
        "# Narrator: Sam Reader"
    ]
    head = lines[3]
    body = [dict(zip(head, row, strict=True)) for row in lines[4:]]
    assert [(b["Type"], b["Page"], b["Recording"]) for b in body] == [("Flub", "2", "ch01_take1")]
    assert "now" in body[0]["Expected"] and "now" not in body[0]["Heard"].split()
    assert body[0]["Time"] == "0:29.7"

    # The edited master: the same issue, at its timeline time (cleanup and the
    # take choice moved it earlier).
    rows = client.get(
        "/api/project/guide/rows", params={"reference": "master", "include_silences": True}
    ).json()
    assert {r["type"] for r in rows["rows"]} == {"Flub", "Long pause"}
    flub = next(r for r in rows["rows"] if r["type"] == "Flub")
    assert flub["recording"] == "edited master" and flub["t"] < 29.7
    assert "edited master" in rows["header"]["reference"]

    md = client.get("/api/project/guide", params={"format": "md"}).text
    assert (
        md.startswith("# Corrections guide: Book")
        and "| 1 | 2 | 0:29.7 | ch01_take1 | Flub |" in md
    )
    pdf = client.get("/api/project/guide", params={"format": "pdf", "narrator": "Sam Reader"})
    assert pdf.headers["content-type"] == "application/pdf"
    text = "".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf.content)).pages)
    assert "Corrections guide: Book" in text and "Sam Reader" in text and "Flub" in text
    assert client.get("/api/project/guide", params={"format": "docx"}).status_code == 400


def test_downloads_stay_inside_exports(client: TestClient) -> None:
    assert client.get("/api/project/exports/..%2F..%2F/project.db").status_code == 404
    assert client.get("/api/project/exports/x/y.mp3").status_code == 404


def test_without_noise_reduction_the_report_fails_the_noise_floor(client: TestClient) -> None:
    """The fixtures read -25.5 dB RMS (too quiet for ACX) over a -65 dB noise floor:
    normalizing to -20 dB lifts the noise above -60 dB unless it is reduced first."""
    analyzed(client)
    client.put("/api/project/finalize", json={"noise_reduction": False, "gate": False})
    rep = job(client, "/api/project/finalize/analyze", {"split": "single"})["result"]
    assert [r["name"] for _, r in failing(rep)] == ["Noise floor"]
    assert not rep["pass"]
