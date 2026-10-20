"""Build small .docx and .pdf documents for tests (no fixtures on disk)."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Sequence

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml"
 ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""
RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1"
 Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
 Target="word/document.xml"/>
</Relationships>"""


def docx(body_xml: str, styles_xml: str | None = None) -> bytes:
    """A minimal .docx whose <w:body> is `body_xml` (use the `w:` prefix)."""
    doc = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W_NS}"><w:body>{body_xml}</w:body></w:document>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", RELS)
        z.writestr("word/document.xml", doc)
        if styles_xml is not None:
            z.writestr("word/styles.xml", f'<w:styles xmlns:w="{W_NS}">{styles_xml}</w:styles>')
    return buf.getvalue()


def p(text: str = "", style: str | None = None, ppr: str = "", runs: str | None = None) -> str:
    """One paragraph. `runs` overrides `text` for paragraphs with breaks inside."""
    props = (f'<w:pStyle w:val="{style}"/>' if style else "") + ppr
    body = runs if runs is not None else f'<w:r><w:t xml:space="preserve">{text}</w:t></w:r>'
    return f"<w:p>{f'<w:pPr>{props}</w:pPr>' if props else ''}{body}</w:p>"


def r(text: str) -> str:
    return f'<w:r><w:t xml:space="preserve">{text}</w:t></w:r>'


PAGE_BREAK = '<w:r><w:br w:type="page"/></w:r>'


def pdf(
    pages: Sequence[Sequence[tuple[int, str] | None]],
    labels: Sequence[tuple[int, str, int]] = (),
    header: str | None = None,
    numbers: bool = True,
) -> bytes:
    """A PDF with one text line per (indent_points, text); None is a blank line.

    `labels`: (page_index, style "/r" | "/D", start) page-label ranges.
    """
    from pypdf import PdfReader, PdfWriter
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter, invariant=1)
    for i, lines in enumerate(pages):
        if header:
            c.setFont("Times-Roman", 9)
            c.drawString(250, 750, header)
        c.setFont("Times-Roman", 12)
        y = 700
        for line in lines:
            if line is not None:
                c.drawString(72 + line[0], y, line[1])
            y -= 16
        if numbers:
            c.setFont("Times-Roman", 9)
            c.drawString(300, 40, str(i + 1))
        c.showPage()
    c.save()
    if not labels:
        return buf.getvalue()
    w = PdfWriter(clone_from=PdfReader(io.BytesIO(buf.getvalue())))
    # Each range runs until the next one starts (or the last page).
    starts = [index for index, _, _ in labels] + [len(pages)]
    for (index, style, start), nxt in zip(labels, starts[1:], strict=True):
        w.set_page_label(index, nxt - 1, style=style, start=start)  # type: ignore[arg-type]
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()
