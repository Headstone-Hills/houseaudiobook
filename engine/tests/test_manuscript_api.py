"""Manuscript import through the API (M2)."""

from __future__ import annotations

import json
import os
import shutil
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from conftest import FIXTURES
from fastapi.testclient import TestClient

from hillaudio.api.app import create_app
from hillaudio.api.guard import DEFAULT_HOSTS


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("HILLAUDIO_HOME", str(tmp_path / "home"))
    with TestClient(create_app(hosts=DEFAULT_HOSTS | {"testserver"})) as c:
        loc = tmp_path / "projects"
        loc.mkdir()
        r = c.post("/api/projects", json={"location": str(loc), "name": "Book"})
        assert r.status_code == 200
        yield c


def wait(client: TestClient, r: Any) -> dict[str, Any]:
    assert r.status_code == 200, r.text
    job = client.app.state.engine.jobs.wait(r.json()["job_id"])  # type: ignore[attr-defined]
    data: dict[str, Any] = job.to_json()
    return data


def project_root(client: TestClient) -> Path:
    return Path(client.get("/api/project").json()["path"])


TRUTH = [
    (s["text"], str(s["page"]))
    for s in json.loads((FIXTURES / "ground_truth.json").read_text())["sentences"]
]


def all_sentences(client: TestClient) -> list[tuple[str, str]]:
    units = client.get("/api/project/manuscript/units", params={"kind": "sentence"}).json()
    return [(u["text"], u["page_label"]) for u in units["units"]]


def test_preview_then_import_txt(client: TestClient) -> None:
    src = {"path": str(FIXTURES / "manuscript.txt")}
    prev = client.post("/api/project/manuscript/preview", json={"source": src}).json()
    assert (prev["format"], prev["page_mode"], prev["page_count"]) == ("txt", "markers", 3)
    assert prev["counts"] == {"chapter": 2, "paragraph": 6, "sentence": 13}
    assert [p["label"] for p in prev["pages"]] == ["1", "2", "3"]
    assert prev["pages"][1]["first_text"].startswith("But it was all in vain")
    pdf_prev = client.post(
        "/api/project/manuscript/preview",
        json={"source": {"path": str(FIXTURES / "manuscript.pdf")}},
    ).json()
    assert pdf_prev["pages"][0]["first_text"] == "Two Fables from Aesop"  # stops at page end
    assert client.get("/api/project/manuscript").json() is None  # preview writes nothing

    job = wait(client, client.post("/api/project/manuscript", json={"source": src}))
    assert job["status"] == "done", job
    info = client.get("/api/project/manuscript").json()
    assert (info["format"], info["page_mode"], info["status"]) == ("txt", "markers", "ok")
    assert all_sentences(client) == TRUTH
    assert client.get("/api/project").json()["manuscript"]["counts"]["sentence"] == 13

    root = project_root(client)
    copies = list((root / "text").glob("*_manuscript.txt"))
    assert len(copies) == 1 and not copies[0].stat().st_mode & stat.S_IWUSR
    assert (root / "text" / "extracted.txt").read_text().startswith("Chapter One.")


def test_remap_pdf_offset_and_front_matter(client: TestClient) -> None:
    src = {"path": str(FIXTURES / "manuscript.pdf")}
    wait(client, client.post("/api/project/manuscript", json={"source": src}))
    first = all_sentences(client)[0]
    assert first == ("Two Fables from Aesop", "i")

    # Ignore the PDF's labels and count pages instead, skipping the title page.
    settings = {"use_pdf_labels": False, "first_numbered_page": 2, "skip_front_matter": True}
    prev = client.post(
        "/api/project/manuscript/preview", json={"source": {"current": True}, "settings": settings}
    ).json()
    assert prev["sentences"][0]["page_label"] == "1" and prev["name"] == "manuscript.pdf"
    job = wait(client, client.put("/api/project/manuscript/settings", json={"settings": settings}))
    assert job["status"] == "done", job
    assert all_sentences(client) == TRUTH
    ops = [o["op_type"] for o in client.app.state.engine.project.operations()]  # type: ignore[attr-defined]
    assert ops[-2:] == ["import_manuscript", "remap_manuscript_pages"]


def test_upload_docx_then_import(client: TestClient) -> None:
    data = (FIXTURES / "manuscript.docx").read_bytes()
    up = client.put(
        "/api/project/manuscript/upload", params={"filename": "../My Book.docx"}, content=data
    ).json()
    assert up["name"] == "My Book.docx"
    body = {
        "source": {"upload_id": up["upload_id"]},
        "settings": {"first_numbered_page": 2, "skip_front_matter": True},
    }
    assert client.post("/api/project/manuscript/preview", json=body).json()["page_mode"] == "breaks"
    job = wait(client, client.post("/api/project/manuscript", json=body))
    assert job["status"] == "done", job
    assert all_sentences(client) == TRUTH
    assert list((project_root(client) / "cache" / "uploads").iterdir()) == []
    # The upload is consumed by the import.
    again = client.post("/api/project/manuscript/preview", json=body)
    assert again.status_code == 404


def test_errors_are_explained(client: TestClient, tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    epub.write_bytes(b"x")
    r = client.post("/api/project/manuscript/preview", json={"source": {"path": str(epub)}})
    assert r.status_code == 400 and "EPUB" in r.json()["detail"]

    scanned = tmp_path / "scan.pdf"
    from textdocs import pdf

    scanned.write_bytes(pdf([[]], numbers=False))
    r = client.post("/api/project/manuscript/preview", json={"source": {"path": str(scanned)}})
    assert r.status_code == 400 and "no text layer" in r.json()["detail"]

    bad = {
        "source": {"path": str(FIXTURES / "manuscript.txt")},
        "settings": {"marker_pattern": "(", "words_per_page": 250},
    }
    r = client.post("/api/project/manuscript/preview", json=bad)
    assert r.status_code == 400 and "regex" in r.json()["detail"]

    r = client.post("/api/project/manuscript/preview", json={"source": {}})
    assert r.status_code == 400
    r = client.put("/api/project/manuscript/settings", json={"settings": {}})
    job = wait(client, r)
    assert job["status"] == "failed" and "no manuscript" in job["error"]


def test_units_paging_and_kinds(client: TestClient) -> None:
    wait(
        client,
        client.post(
            "/api/project/manuscript", json={"source": {"path": str(FIXTURES / "manuscript.txt")}}
        ),
    )
    page = client.get(
        "/api/project/manuscript/units", params={"kind": "sentence", "offset": 10, "limit": 2}
    ).json()
    assert page["total"] == 13 and [u["ordinal"] for u in page["units"]] == [10, 11]
    chapters = client.get("/api/project/manuscript/units", params={"kind": "chapter"}).json()
    assert [u["text"] for u in chapters["units"]] == [
        "Chapter One. The Fox and the Grapes.",
        "Chapter Two. The Tortoise and the Hare.",
    ]
    sentence = page["units"][0]
    paragraph_ids = {
        u["id"]
        for u in client.get("/api/project/manuscript/units", params={"kind": "paragraph"}).json()[
            "units"
        ]
    }
    assert sentence["parent_id"] in paragraph_ids


def test_reopen_verifies_manuscript_copy(client: TestClient) -> None:
    wait(
        client,
        client.post(
            "/api/project/manuscript", json={"source": {"path": str(FIXTURES / "manuscript.txt")}}
        ),
    )
    root = project_root(client)
    client.post("/api/project/close")
    (copy,) = (root / "text").glob("*_manuscript.txt")
    os.chmod(copy, 0o644)
    copy.write_text("tampered")
    client.post("/api/projects/open", json={"path": str(root)})
    for job in client.get("/api/jobs").json():
        client.app.state.engine.jobs.wait(job["id"])  # type: ignore[attr-defined]
    assert client.get("/api/project/manuscript").json()["status"] == "modified"
    r = client.put("/api/project/manuscript/settings", json={"settings": {}})
    assert "missing or changed" in (wait(client, r)["error"] or "")

    # Re-importing sets the damaged copy aside rather than overwriting it.
    shutil.copy(FIXTURES / "manuscript.txt", root.parent / "manuscript.txt")
    wait(
        client,
        client.post(
            "/api/project/manuscript",
            json={"source": {"path": str(root.parent / "manuscript.txt")}},
        ),
    )
    assert len(list((root / "text").glob("*.damaged-*"))) == 1
    assert client.get("/api/project/manuscript").json()["status"] == "ok"
