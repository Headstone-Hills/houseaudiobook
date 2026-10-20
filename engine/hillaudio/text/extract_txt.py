"""Plain text and Markdown manuscripts (pure: bytes in, Document out).

Paragraphs are separated by blank lines; single newlines inside a paragraph are
hard wraps and become spaces. Page information, in order of preference:
explicit markers matching the configured pattern, then form feeds, then none
(the caller falls back to a words-per-page estimate).
"""

from __future__ import annotations

import re

from hillaudio.text.model import DocBuilder, Document, PageSource
from hillaudio.text.pages import PageSettings

_MD_HEADING = re.compile(r"^#{1,6}\s+")
_MD_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_MD_EMPHASIS = re.compile(r"(\*{1,3}|_{1,3}|`+)(?=\S)(.+?)(?<=\S)\1")


def decode(data: bytes) -> tuple[str, list[str]]:
    """UTF-8 (with or without BOM), else Windows-1252, which never fails to decode."""
    try:
        return data.decode("utf-8-sig"), []
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace"), [
            "text is not UTF-8; read it as Windows-1252 (check accented characters)"
        ]


def _strip_markdown(line: str) -> str:
    line = _MD_LINK.sub(r"\1", line)
    return _MD_EMPHASIS.sub(r"\2", line)


def extract_text(data: bytes, settings: PageSettings, markdown: bool = False) -> Document:
    text, warnings = decode(data)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    marker = re.compile(settings.marker_pattern)

    use_markers = settings.mode in ("auto", "markers") and marker.search(text) is not None
    use_ff = (
        not use_markers
        and settings.form_feed_breaks
        and "\f" in text
        and settings.mode in ("auto", "breaks")
    )
    source: PageSource = "markers" if use_markers else "breaks" if use_ff else "none"
    b = DocBuilder(source)
    b.warnings.extend(warnings)

    for block in re.split(r"\n[ \t]*\n", text):
        b.start()
        for line in block.split("\n"):
            md_heading = markdown and _MD_HEADING.match(line) is not None
            if md_heading:
                b.start("heading")
                line = _MD_HEADING.sub("", line)
            if markdown:
                line = _strip_markdown(line)
            # Split on form feeds and markers, keeping their positions in the line.
            pos = 0
            tokens: list[tuple[int, int, str | None]] = []  # (start, end, marker label)
            if use_markers:
                tokens += [(m.start(), m.end(), m.group(1)) for m in marker.finditer(line)]
            if use_ff or "\f" in line:
                tokens += [(m.start(), m.end(), None) for m in re.finditer("\f", line)]
            for start, end, label in sorted(tokens, key=lambda tok: tok[0]):
                b.write(line[pos:start])
                if label is not None or use_ff:
                    b.page_break(label)
                if label is None:
                    b.write(" ")  # a form feed separates words like any whitespace
                pos = end
            b.write(line[pos:])
            b.write(" ")  # the hard wrap
            if md_heading:
                b.end()  # a Markdown heading is exactly one line
        b.end()
    return b.build()
