"""Page numbering (pure): map any character of a Document to its page (SPEC §6.2).

Modes:
- markers:  explicit printed numbers found in the text, e.g. "[p. 12]".
- breaks:   physical page breaks (form feeds, .docx page/section breaks), counted.
- pdf:      the PDF's own pages, using its printed page labels when it has them.
- estimate: a words-per-page estimate; every label says "approx." (SPEC §6.2).
"auto" picks from what the document carries, in that order of preference.

For counted pages, `first_numbered_page` says which physical page is printed page 1
(front matter makes printed page 1 = PDF page 9, so first_numbered_page = 9).
Pages before it are front matter, which `skip_front_matter` drops from the units.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from hillaudio.text.model import Document, is_heading

Mode = Literal["auto", "markers", "breaks", "pdf", "estimate"]
ResolvedMode = Literal["markers", "breaks", "pdf", "estimate"]

DEFAULT_MARKER = r"\[\s*p(?:age|g)?\.?\s*(\d+)\s*\]"  # [p. 12], [p12], [page 12], [pg. 12]
DEFAULT_WORDS_PER_PAGE = 250  # D-022: a typical trade-paperback page
FRONT_MATTER = "front matter"
_WORD = re.compile(r"\S+")


class PageSettingsError(ValueError):
    pass


@dataclass(frozen=True)
class PageSettings:
    mode: Mode = "auto"
    marker_pattern: str = DEFAULT_MARKER
    form_feed_breaks: bool = True  # .txt: treat \f as a page break
    words_per_page: int = DEFAULT_WORDS_PER_PAGE
    first_numbered_page: int = 1
    use_pdf_labels: bool = True
    skip_front_matter: bool = False

    def validate(self) -> PageSettings:
        try:
            pattern = re.compile(self.marker_pattern)
        except re.error as e:
            raise PageSettingsError(f"page marker pattern is not a valid regex: {e}") from None
        if pattern.groups != 1:
            raise PageSettingsError(
                "page marker pattern needs exactly one (group) capturing the page number"
            )
        if self.words_per_page < 50 or self.words_per_page > 2000:
            raise PageSettingsError("words per page must be between 50 and 2000")
        if self.first_numbered_page < 1:
            raise PageSettingsError("the first numbered page must be 1 or more")
        return self

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> PageSettings:
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known).validate()


@dataclass(frozen=True)
class PageRef:
    number: int | None  # printed page number; None when not numeric (front matter, "xii")
    label: str  # for display: "12", "approx. 12", "iii", "front matter"
    estimated: bool = False
    physical: int | None = None  # page index in the source file, when meaningful
    front_matter: bool = False


@dataclass
class PageMap:
    mode: ResolvedMode
    starts: list[int]  # char offsets where each page begins (sorted)
    refs: list[PageRef]
    warnings: list[str] = field(default_factory=list)

    def at(self, char: int) -> PageRef:
        i = bisect.bisect_right(self.starts, char) - 1
        return self.refs[max(i, 0)]

    @property
    def page_count(self) -> int:
        return len({r.label for r in self.refs if not r.front_matter})


def resolve_mode(doc: Document, settings: PageSettings) -> ResolvedMode:
    if settings.mode != "auto":
        wanted: ResolvedMode = settings.mode
        if wanted == "markers" and doc.source != "markers":
            raise PageSettingsError("no page markers matching the pattern were found")
        if wanted == "breaks" and doc.source not in ("breaks", "pdf"):
            raise PageSettingsError("this document has no page breaks")
        if wanted == "pdf" and doc.source != "pdf":
            raise PageSettingsError("PDF page mode needs a PDF")
        return wanted
    if doc.source == "none":
        return "estimate"
    return doc.source


def _counted(physical: int, settings: PageSettings) -> PageRef:
    printed = physical - (settings.first_numbered_page - 1)
    if printed < 1:
        return PageRef(None, FRONT_MATTER, physical=physical, front_matter=True)
    return PageRef(printed, str(printed), physical=physical)


def build_page_map(doc: Document, settings: PageSettings) -> PageMap:
    mode = resolve_mode(doc, settings)
    if mode == "estimate":
        return _estimate(doc, settings)

    starts = [m.char for m in doc.marks]
    refs: list[PageRef] = []
    warnings: list[str] = []

    if mode == "markers":
        numbers = [int(m.label) if m.label is not None else None for m in doc.marks]
        first = next((n for n in numbers if n is not None), 1)
        last: int | None = None
        for m, n in zip(doc.marks, numbers, strict=True):
            if n is None:
                # Text before the first marker: a marker starts its page, so this text
                # is on the page before; before "[p. 1]" it's front matter.
                refs.append(
                    PageRef(first - 1, str(first - 1))
                    if first > 1
                    else PageRef(None, FRONT_MATTER, front_matter=True)
                )
                continue
            if last is not None and n <= last:
                warnings.append(f"page markers go backwards or repeat: p. {last} then p. {n}")
            last = n
            refs.append(PageRef(n, str(n), physical=m.physical))

    elif mode == "pdf" and settings.use_pdf_labels and any(m.label for m in doc.marks):
        seen_numeric = False
        for m in doc.marks:
            label = m.label or str(m.physical)
            numeric = label.isdigit()
            seen_numeric = seen_numeric or numeric
            refs.append(
                PageRef(
                    int(label) if numeric else None,
                    label,
                    physical=m.physical,
                    front_matter=not seen_numeric,
                )
            )

    else:  # breaks, or pdf without labels
        refs = [_counted(m.physical, settings) for m in doc.marks]

    return PageMap(mode, starts, refs, warnings)


def _estimate(doc: Document, settings: PageSettings) -> PageMap:
    wpp = settings.words_per_page
    begin = 0
    if settings.skip_front_matter:
        # With no real pages, "front matter" is whatever precedes the first heading.
        heading = next(
            (p for p in doc.paragraphs if is_heading(doc.text[p.start : p.end], p.kind)), None
        )
        begin = heading.start if heading else 0
    starts: list[int] = []
    refs: list[PageRef] = []
    if begin > 0:
        starts.append(0)
        refs.append(PageRef(None, FRONT_MATTER, estimated=True, front_matter=True))
    for i, w in enumerate(_WORD.finditer(doc.text, begin)):
        if i % wpp == 0:
            n = i // wpp + 1
            starts.append(w.start() if starts or begin > 0 else 0)
            refs.append(PageRef(n, f"approx. {n}", estimated=True))
    if not refs:
        starts, refs = [0], [PageRef(1, "approx. 1", estimated=True)]
    return PageMap("estimate", starts, refs)
