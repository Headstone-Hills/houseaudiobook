"""Manuscript units (pure): chapters, paragraphs and sentences, each with a page.

Units carry character offsets into Document.text, so later stages can map any
transcript alignment back to exact text and therefore to a page (SPEC §6.2:
"every issue inherits its page through alignment").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from hillaudio.text.model import Document, is_heading
from hillaudio.text.pages import PageMap, PageRef
from hillaudio.text.sentences import split_sentences

UnitKind = Literal["chapter", "paragraph", "sentence"]


@dataclass(frozen=True)
class Unit:
    kind: UnitKind
    ordinal: int  # 0-based, per kind, in document order
    parent: int | None  # index of the parent unit in the returned list
    text: str  # the unit's text; for a chapter, just its heading
    char_start: int
    char_end: int
    page: PageRef
    page_end: PageRef


def build_units(doc: Document, pages: PageMap, skip_front_matter: bool = False) -> list[Unit]:
    units: list[Unit] = []
    counts = {"chapter": 0, "paragraph": 0, "sentence": 0}
    chapter_idx: int | None = None

    def add(kind: UnitKind, parent: int | None, start: int, end: int) -> int:
        units.append(
            Unit(
                kind,
                counts[kind],
                parent,
                doc.text[start:end],
                start,
                end,
                pages.at(start),
                pages.at(max(start, end - 1)),
            )
        )
        counts[kind] += 1
        return len(units) - 1

    for para in doc.paragraphs:
        text = doc.text[para.start : para.end]
        heading = is_heading(text, para.kind)
        # Sentence spans (absolute). A title is read as one utterance, so it isn't
        # split: "Chapter One. The Fox." stays whole.
        spans = (
            [(para.start, para.end)]
            if heading
            else [(para.start + s, para.start + e) for s, e in split_sentences(text)]
        )
        if skip_front_matter:
            # Per sentence, not per paragraph: a paragraph can run from the last
            # front-matter page onto page 1.
            spans = [(s, e) for s, e in spans if not pages.at(s).front_matter]
            if not spans:
                continue
        start, end = spans[0][0], para.end
        if heading:
            chapter_idx = add("chapter", None, start, end)
        p_idx = add("paragraph", chapter_idx, start, end)
        for s, e in spans:
            add("sentence", p_idx, s, e)

    # A chapter spans from its heading to the end of its last paragraph.
    last_end: dict[int, int] = {}
    for u in units:
        if u.kind == "paragraph" and u.parent is not None:
            last_end[u.parent] = u.char_end
    for i, end in last_end.items():
        u = units[i]
        units[i] = Unit(
            u.kind, u.ordinal, u.parent, u.text, u.char_start, end, u.page, pages.at(end - 1)
        )
    return units
