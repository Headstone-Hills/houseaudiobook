"""Golden test: every manuscript format yields the ground truth's sentences and pages.

The fixture generator writes the same text as .txt (page markers), .docx (a title
page then explicit page breaks) and .pdf (a roman-numbered title page, running
heads, page numbers, hard-wrapped indented paragraphs). The ground truth lists
the 13 sentences the narration fixtures read, with their pages.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from conftest import FIXTURES

from hillaudio.text.extract_docx import extract_docx
from hillaudio.text.extract_pdf import extract_pdf
from hillaudio.text.extract_txt import extract_text
from hillaudio.text.model import Document
from hillaudio.text.pages import PageSettings, build_page_map
from hillaudio.text.units import build_units


@pytest.fixture(scope="module")
def truth() -> list[tuple[str, str]]:
    data: dict[str, Any] = json.loads((FIXTURES / "ground_truth.json").read_text())
    return [(s["text"], str(s["page"])) for s in data["sentences"]]


def read(name: str, settings: PageSettings) -> Document:
    data = (FIXTURES / name).read_bytes()
    if name.endswith(".docx"):
        return extract_docx(data)
    if name.endswith(".pdf"):
        return extract_pdf(data)
    return extract_text(data, settings)


@pytest.mark.parametrize(
    ("name", "settings", "mode"),
    [
        ("manuscript.txt", PageSettings(), "markers"),
        # Title page is physical page 1, so printed page 1 is physical page 2.
        ("manuscript.docx", PageSettings(first_numbered_page=2, skip_front_matter=True), "breaks"),
        # The PDF's own labels say the title page is "i" and the story starts at "1".
        ("manuscript.pdf", PageSettings(skip_front_matter=True), "pdf"),
    ],
)
def test_format_matches_ground_truth(
    name: str, settings: PageSettings, mode: str, truth: list[tuple[str, str]]
) -> None:
    doc = read(name, settings)
    pages = build_page_map(doc, settings.validate())
    assert pages.mode == mode
    units = build_units(doc, pages, settings.skip_front_matter)
    got = [(u.text, u.page.label) for u in units if u.kind == "sentence"]
    assert got == truth
    chapters = [u.text for u in units if u.kind == "chapter"]
    assert chapters == [
        "Chapter One. The Fox and the Grapes.",
        "Chapter Two. The Tortoise and the Hare.",
    ]


def test_front_matter_is_kept_unless_skipped() -> None:
    doc = read("manuscript.pdf", PageSettings())
    units = build_units(doc, build_page_map(doc, PageSettings()))
    first = next(u for u in units if u.kind == "sentence")
    assert (first.text, first.page.label, first.page.front_matter) == (
        "Two Fables from Aesop",
        "i",
        True,
    )
