"""Word (.docx) manuscripts (pure: bytes in, Document out).

A .docx is a zip of XML; reading `word/document.xml` with the standard library
avoids a dependency. Page information comes only from *explicit* breaks: page
breaks (`<w:br w:type="page"/>`), "page break before" paragraphs, and section
breaks that start a new page. Word's own layout isn't stored reliably, so a
document without explicit breaks falls back to estimate mode (SPEC §6.2).

Tracked deletions are skipped and tracked insertions kept, so the text is the
document with all changes accepted. Field codes are skipped. Tables are read row
by row, one paragraph per cell paragraph. Footnotes, comments and headers are
not part of the narration and are ignored.
"""

from __future__ import annotations

import io
import re
import zipfile
from xml.etree import ElementTree as ET

from hillaudio.text.model import DocBuilder, Document

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_HEADING_NAME = re.compile(r"^(heading\s*\d|title)$", re.IGNORECASE)
_SKIP = {
    f"{W}del",
    f"{W}delText",
    f"{W}instrText",
    f"{W}pPr",
    f"{W}rPr",
    f"{W}footnoteReference",
    f"{W}endnoteReference",
    f"{W}commentReference",
}


class DocxError(ValueError):
    pass


def _styles(z: zipfile.ZipFile) -> set[str]:
    """Style ids that are headings (by name, or by having an outline level)."""
    try:
        root = ET.fromstring(z.read("word/styles.xml"))
    except KeyError:
        return set()
    heading_ids: set[str] = set()
    for st in root.iter(f"{W}style"):
        sid = st.get(f"{W}styleId", "")
        name_el = st.find(f"{W}name")
        name = name_el.get(f"{W}val", "") if name_el is not None else ""
        if _HEADING_NAME.match(name) or st.find(f"{W}pPr/{W}outlineLvl") is not None:
            heading_ids.add(sid)
    return heading_ids


def _is_heading(p: ET.Element, heading_styles: set[str]) -> bool:
    ppr = p.find(f"{W}pPr")
    if ppr is None:
        return False
    if ppr.find(f"{W}outlineLvl") is not None:
        return True
    style = ppr.find(f"{W}pStyle")
    sid = style.get(f"{W}val", "") if style is not None else ""
    return sid in heading_styles or bool(_HEADING_NAME.match(sid))


def _on(el: ET.Element | None) -> bool:
    """A Word boolean property: present and not switched off with val="0"/"false"."""
    return el is not None and el.get(f"{W}val", "true") not in ("0", "false", "off")


def _section_starts_new_page(p: ET.Element) -> bool:
    sect = p.find(f"{W}pPr/{W}sectPr")
    if sect is None:
        return False
    t = sect.find(f"{W}type")
    # No type means nextPage; "continuous" (and "nextColumn") don't start a page.
    return t is None or t.get(f"{W}val", "nextPage") in ("nextPage", "oddPage", "evenPage")


def _walk_runs(el: ET.Element, b: DocBuilder) -> None:
    for child in el:
        tag = child.tag
        if tag in _SKIP:
            continue
        if tag == f"{W}t":
            b.write(child.text or "")
        elif tag in (f"{W}tab", f"{W}cr"):
            b.write(" ")
        elif tag == f"{W}br":
            if child.get(f"{W}type") == "page":
                b.page_break()
            else:
                b.write(" ")  # a line break within the paragraph
        elif tag == f"{W}noBreakHyphen":
            b.write("-")
        elif tag == f"{W}softHyphen":
            pass
        else:
            _walk_runs(child, b)  # runs, hyperlinks, insertions, smart tags, content controls


def _paragraphs(body: ET.Element) -> list[ET.Element]:
    out: list[ET.Element] = []
    for child in body:
        if child.tag == f"{W}p":
            out.append(child)
        elif child.tag == f"{W}tbl":
            out.extend(child.iter(f"{W}p"))
        elif child.tag == f"{W}sdt":
            content = child.find(f"{W}sdtContent")
            if content is not None:
                out.extend(_paragraphs(content))
    return out


def extract_docx(data: bytes) -> Document:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        root = ET.fromstring(z.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as e:
        raise DocxError(f"not a readable .docx file ({e})") from None
    body = root.find(f"{W}body")
    if body is None:
        raise DocxError("the .docx has no document body")

    heading_styles = _styles(z)
    paras = _paragraphs(body)
    has_breaks = (
        any(el.get(f"{W}type") == "page" for el in body.iter(f"{W}br"))
        or any(_on(el) for el in body.iter(f"{W}pageBreakBefore"))
        or any(_section_starts_new_page(p) for p in paras)
    )
    b = DocBuilder("breaks" if has_breaks else "none")
    for p in paras:
        if _on(p.find(f"{W}pPr/{W}pageBreakBefore")):
            b.page_break()
        b.start("heading" if _is_heading(p, heading_styles) else "paragraph")
        _walk_runs(p, b)
        b.end()
        if _section_starts_new_page(p):
            b.page_break()
    return b.build()
