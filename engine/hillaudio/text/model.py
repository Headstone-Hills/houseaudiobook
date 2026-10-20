"""The extracted-document model shared by every format (pure; no I/O).

Extractors stream paragraphs and page breaks into a `DocBuilder`. The result is
plain text (paragraphs joined by a blank line) plus *page marks*: character
offsets where a new page begins. Page numbering (pages.py) and unit splitting
(units.py) work only on this model, so they behave identically for .txt, .docx
and .pdf.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

ParaKind = Literal["heading", "paragraph"]
# What kind of page information the source document carried.
PageSource = Literal["markers", "breaks", "pdf", "none"]

PARA_SEP = "\n\n"
_INVISIBLE = dict.fromkeys(map(ord, "\u00ad\u200b\u200c\u200d\u2060\ufeff"))  # soft hyphen, ZW*
_SPACES = re.compile(r"[ \t\r\n\u00a0\u2000-\u200a\u202f\u205f\u3000]+")


def clean(text: str) -> str:
    """NFC, drop invisible characters, collapse whitespace. Keeps curly quotes etc."""
    text = unicodedata.normalize("NFC", text).translate(_INVISIBLE)
    return _SPACES.sub(" ", text)


_NUM_WORD = (
    r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen"
    r"|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy"
    r"|eighty|ninety|hundred)"
)
# "Chapter 3", "CHAPTER XII", "Part Two", "Prologue". Bare "Part"/"Book" need a number
# so "Part of the problem..." isn't a chapter.
CHAPTER_RE = re.compile(
    rf"^(?:(?:chapter|part|book)\s+(?:\d+|[ivxlcdm]+|{_NUM_WORD}(?:[- ]{_NUM_WORD})*)\b"
    r"|(?:prologue|epilogue|interlude|preface|foreword|afterword|introduction)\b)",
    re.IGNORECASE,
)
MAX_HEADING_CHARS = 120


def is_heading(text: str, kind: str) -> bool:
    """A chapter-level heading: styled as one, or reading like one ("Chapter 3")."""
    if kind == "heading":
        return True
    return len(text) <= MAX_HEADING_CHARS and bool(CHAPTER_RE.match(text))


@dataclass(frozen=True)
class Paragraph:
    kind: ParaKind
    start: int  # char offset in Document.text
    end: int


@dataclass(frozen=True)
class PageMark:
    char: int  # the page begins at this offset in Document.text
    physical: int  # 1-based index of the page in the source document
    label: str | None = None  # an explicit printed label (marker number, PDF label)


@dataclass
class Document:
    text: str
    paragraphs: list[Paragraph]
    marks: list[PageMark]
    source: PageSource
    warnings: list[str] = field(default_factory=list)


class DocBuilder:
    """Accumulates paragraphs; page breaks may fall between or inside paragraphs.

    A break records a mark at the offset of the *next* text written, so a break
    at the end of a paragraph, or several breaks in a row (blank pages), land on
    the right paragraph without empty pages producing stray offsets.
    """

    def __init__(self, source: PageSource) -> None:
        self.source: PageSource = source
        self._parts: list[str] = []
        self._len = 0
        self._paras: list[Paragraph] = []
        self._marks: list[PageMark] = []
        self._pending: list[str | None] = []  # labels of breaks awaiting text
        self._physical = 1
        self._buf: list[str] = []  # current paragraph's cleaned pieces
        self._buf_len = 0
        self._kind: ParaKind = "paragraph"
        self.warnings: list[str] = []

    # -- paragraphs --------------------------------------------------------------

    def start(self, kind: ParaKind = "paragraph") -> None:
        if self._buf:
            self.end()
        self._kind = kind

    def write(self, text: str) -> None:
        piece = clean(text)
        if not piece or (not self._buf and piece == " "):
            return
        if not self._buf:
            piece = piece.lstrip()
        elif self._buf[-1].endswith(" ") and piece.startswith(" "):
            piece = piece[1:]
        if not piece:
            return
        if self._pending:
            self._flush_marks(self._para_offset() + self._buf_len)
        self._buf.append(piece)
        self._buf_len += len(piece)

    def unwrite(self, n: int) -> None:
        """Remove the last `n` characters of the current paragraph (e.g. a line-end hyphen)."""
        text = "".join(self._buf)[:-n] if n else "".join(self._buf)
        self._buf, self._buf_len = ([text] if text else []), len(text)

    def end(self) -> None:
        text = "".join(self._buf).rstrip()
        self._buf, self._buf_len = [], 0
        if not text:
            return
        start = self._para_offset()
        if self._paras:
            self._parts.append(PARA_SEP)
            self._len += len(PARA_SEP)
        self._parts.append(text)
        self._len += len(text)
        self._paras.append(Paragraph(self._kind, start, start + len(text)))
        self._kind = "paragraph"

    def _para_offset(self) -> int:
        return self._len + (len(PARA_SEP) if self._paras else 0)

    # -- pages ---------------------------------------------------------------------

    def page_break(self, label: str | None = None) -> None:
        """A new page begins here. `label` is its printed number, if the source says."""
        self._pending.append(label)

    def _flush_marks(self, at: int) -> None:
        # Consecutive breaks with no text between them are blank pages: they count
        # toward the physical index but only the last one gets a mark.
        self._physical += len(self._pending)
        self._marks.append(PageMark(at, self._physical, self._pending[-1]))
        self._pending = []

    def first_page_label(self, label: str) -> None:
        """Label for the page the document starts on (e.g. a PDF's page labels)."""
        self._first_label = label

    def build(self) -> Document:
        self.end()
        text = "".join(self._parts)
        marks = [PageMark(0, 1, getattr(self, "_first_label", None))]
        marks += [m for m in self._marks if m.char < len(text)]
        # A break before any text (e.g. "[p. 1]" on the first line) labels the first
        # page rather than ending an empty one, so it replaces the implicit page 1
        # and physical numbering shifts down to start at 1.
        if len(marks) > 1 and marks[1].char == 0:
            shift = marks[1].physical - 1
            marks = [PageMark(m.char, m.physical - shift, m.label) for m in marks[1:]]
        return Document(text, self._paras, marks, self.source, self.warnings)
