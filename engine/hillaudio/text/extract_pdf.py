"""PDF manuscripts with a text layer (SPEC \u00a76.2; scanned PDFs are out of scope).

`pdf_pages` reads each page's text with pypdf in layout mode, which keeps
indentation and blank lines. `extract_pdf_pages` (pure) turns those lines into
paragraphs:
- running heads/feet and bare page numbers are dropped (they repeat on many pages);
- a paragraph starts after a blank line, at an indented line, or after a short
  line ending a sentence (flush-left styles);
- "jump-\\ning" across a line or page break is rejoined as "jumping";
- a paragraph that runs onto the next page stays one paragraph, with the page
  boundary recorded inside it.
"""

from __future__ import annotations

import io
import re
import statistics
from collections import Counter

from hillaudio.text.model import CHAPTER_RE, MAX_HEADING_CHARS, DocBuilder, Document

_PAGE_NUMBER = re.compile(
    r"^[\s\-\u2013\u2014]*(?:page\s+)?(\d+|[ivxlcdm]+)[\s\-\u2013\u2014]*$", re.IGNORECASE
)
_TERMINAL = re.compile(r"[.!?\u2026:][\"'\u201d\u2019)\]]*$")
_HYPHENATED = re.compile(r"[a-z]-$")
INDENT_SPACES = 2  # layout mode renders a first-line indent as a few extra spaces
SHORT_LINE = 0.8  # a line under 80% of the median length can end a paragraph
MIN_LINES_FOR_MARGIN = 3
FURNITURE_SHARE = 0.3  # a head/foot repeated on >=30% of pages is page furniture


class PdfError(ValueError):
    pass


def pdf_pages(data: bytes) -> tuple[list[str], list[str] | None]:
    """Text of each page, plus printed page labels if the PDF defines any."""
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise PdfError("the PDF is password-protected")
        pages = [p.extract_text(extraction_mode="layout") or "" for p in reader.pages]
        labels = list(reader.page_labels) if "/PageLabels" in reader.root_object else None
    except PdfReadError as e:
        raise PdfError(f"not a readable PDF ({e})") from None
    return pages, labels


def _furniture_key(line: str) -> str:
    return re.sub(r"\d+", "#", " ".join(line.split()).lower())


def _strip_furniture(pages: list[list[str]]) -> list[list[str]]:
    """Drop running heads/feet and page numbers from the top and bottom of pages.

    Heads and feet are counted separately, and a repeated line is only furniture
    on a page that has other text: a page holding nothing but the book's title is
    a title page, even when the running head repeats that title.
    """

    def body(lines: list[str]) -> list[str]:
        return [ln for ln in lines if ln.strip()]

    heads = Counter(_furniture_key(ln) for ls in pages for ln in set(body(ls)[:2]))
    feet = Counter(_furniture_key(ln) for ls in pages for ln in set(body(ls)[-2:]))
    need = max(3, FURNITURE_SHARE * len(pages))

    out = []
    for lines in pages:
        lines = list(lines)
        for repeated in (heads, feet):
            from_top = repeated is heads
            for _ in range(2):
                order = range(len(lines)) if from_top else range(len(lines) - 1, -1, -1)
                idx = next((i for i in order if lines[i].strip()), None)
                if idx is None:
                    break
                ln = lines[idx]
                only_line = len(body(lines)) == 1
                if _PAGE_NUMBER.match(ln) or (
                    repeated[_furniture_key(ln)] >= need and not only_line
                ):
                    lines[idx] = ""
                else:
                    break
        out.append(lines)
    return out


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def extract_pdf_pages(pages: list[str], labels: list[str] | None = None) -> Document:
    split = [[ln.rstrip() for ln in p.split("\n")] for p in pages]
    split = _strip_furniture(split)
    if not any(ln.strip() for lines in split for ln in lines):
        raise PdfError(
            "the PDF has no text layer (is it scanned?). Scanned PDFs need OCR, which "
            "isn't supported; export the manuscript as text or Word instead"
        )

    b = DocBuilder("pdf")
    if labels:
        b.first_page_label(labels[0])
    blank_pages = 0
    prev: str | None = None  # last body line written, across pages
    prev_median = 0.0

    # The body text's left margin: per page when the page has enough lines to tell,
    # else the margin most pages share (a one-line page can't show its own indent).
    page_mins = [
        min(_indent(ln) for ln in lines if ln.strip())
        for lines in split
        if any(ln.strip() for ln in lines)
    ]
    common_base = Counter(page_mins).most_common(1)[0][0]

    for i, lines in enumerate(split):
        body = [ln for ln in lines if ln.strip()]
        if not body:
            blank_pages += 1
        if i > 0:
            b.page_break(labels[i] if labels else None)
        if not body:
            continue
        base = min(_indent(ln) for ln in body) if len(body) >= MIN_LINES_FOR_MARGIN else common_base
        median = statistics.median(len(ln.strip()) for ln in body)
        blank_before = False
        seen_on_page = False

        for ln in lines:
            text = ln.strip()
            if not text:
                # Blank lines separate paragraphs, but not the top margin of a page
                # (a paragraph may continue from the previous page).
                blank_before = blank_before or seen_on_page
                continue
            seen_on_page = True
            heading = len(text) <= MAX_HEADING_CHARS and bool(CHAPTER_RE.match(text))
            ends_para = prev is not None and (
                bool(_TERMINAL.search(prev)) and len(prev) < SHORT_LINE * max(median, prev_median)
            )
            new_para = (
                prev is None
                or blank_before
                or heading
                or _indent(ln) > base + INDENT_SPACES
                or ends_para
                or bool(prev and CHAPTER_RE.match(prev))
            )
            if new_para:
                b.start("heading" if heading else "paragraph")
                b.write(text)
            elif prev is not None and _HYPHENATED.search(prev) and text[:1].islower():
                b.unwrite(1)  # "jump-" + "ing" -> "jumping"
                b.write(text)
            else:
                b.write(" " + text)
            prev, prev_median, blank_before = text, median, False

    if blank_pages:
        b.warnings.append(f"{blank_pages} page(s) have no text (blank, or images only)")
    return b.build()


def extract_pdf(data: bytes) -> Document:
    pages, labels = pdf_pages(data)
    if not pages:
        raise PdfError("the PDF has no pages")
    return extract_pdf_pages(pages, labels)
