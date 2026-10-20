"""Text extraction, page mapping and units (M2), with page edge cases."""

from __future__ import annotations

import pytest
from textdocs import PAGE_BREAK, docx, p, pdf, r

from hillaudio.text.extract_docx import DocxError, extract_docx
from hillaudio.text.extract_pdf import PdfError, extract_pdf, extract_pdf_pages
from hillaudio.text.extract_txt import extract_text
from hillaudio.text.model import DocBuilder, Document
from hillaudio.text.pages import PageSettings, PageSettingsError, build_page_map
from hillaudio.text.sentences import split_sentences
from hillaudio.text.units import Unit, build_units


def units_of(doc: Document, **settings: object) -> list[Unit]:
    s = PageSettings(**settings).validate()  # type: ignore[arg-type]
    return build_units(doc, build_page_map(doc, s), s.skip_front_matter)


def txt_units(text: str, **settings: object) -> list[Unit]:
    s = PageSettings(**settings).validate()  # type: ignore[arg-type]
    doc = extract_text(text.encode(), s)
    return build_units(doc, build_page_map(doc, s), s.skip_front_matter)


def sentences(units: list[Unit]) -> list[tuple[str, str]]:
    """(text, page label) of every sentence."""
    return [(u.text, u.page.label) for u in units if u.kind == "sentence"]


# ---- sentence splitting ----


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("One. Two! Three? Four.", ["One.", "Two!", "Three?", "Four."]),
        (
            "Mr. Smith met Dr. Jones at St. Paul's. They left.",
            ["Mr. Smith met Dr. Jones at St. Paul's.", "They left."],
        ),
        (
            "J. R. R. Tolkien wrote it. Then he slept.",
            ["J. R. R. Tolkien wrote it.", "Then he slept."],
        ),
        ('"Stop!" she cried. "Now!" He ran.', ['"Stop!" she cried.', '"Now!"', "He ran."]),
        ("“Wait,” he said. “Why?”", ["“Wait,” he said.", "“Why?”"]),
        ("And then... nothing. Silence.", ["And then... nothing.", "Silence."]),
        ("It cost 3.50 dollars. Cheap.", ["It cost 3.50 dollars.", "Cheap."]),
        ("He said so (quietly.) Then left.", ["He said so (quietly.)", "Then left."]),
        ("No final punctuation", ["No final punctuation"]),
    ],
)
def test_split_sentences(text: str, expected: list[str]) -> None:
    assert [text[s:e] for s, e in split_sentences(text)] == expected


# ---- builder ----


def test_builder_marks_land_on_next_text_and_blank_pages_count() -> None:
    b = DocBuilder("breaks")
    b.start()
    b.write("Page one.")
    b.end()
    b.page_break()
    b.page_break()  # a blank page in between
    b.start()
    b.write("Page three.")
    b.end()
    doc = b.build()
    assert doc.text == "Page one.\n\nPage three."
    assert [(m.char, m.physical) for m in doc.marks] == [(0, 1), (11, 3)]


def test_builder_trailing_break_adds_no_page() -> None:
    b = DocBuilder("breaks")
    b.write("Only page.")
    b.page_break()
    assert len(b.build().marks) == 1


# ---- .txt: markers ----


def test_markers_between_paragraphs_and_text_before_first_marker() -> None:
    text = "Dedication.\n\n[p. 12]\nFirst page text.\n\n[p. 13]\nSecond page text."
    assert sentences(txt_units(text)) == [
        ("Dedication.", "11"),  # a marker starts its page, so earlier text is p. 11
        ("First page text.", "12"),
        ("Second page text.", "13"),
    ]


def test_text_before_marker_p1_is_front_matter_and_can_be_skipped() -> None:
    text = "Copyright 2026.\n\n[p. 1]\nChapter One\n\nIt began."
    assert sentences(txt_units(text))[0] == ("Copyright 2026.", "front matter")
    kept = sentences(txt_units(text, skip_front_matter=True))
    assert kept == [("Chapter One", "1"), ("It began.", "1")]


def test_marker_mid_sentence_and_mid_paragraph() -> None:
    text = "[p. 4]\nThe fox jumped [p. 5] high. Then he fell."
    units = txt_units(text)
    s = [u for u in units if u.kind == "sentence"]
    assert s[0].text == "The fox jumped high."  # marker removed, spacing repaired
    assert (s[0].page.label, s[0].page_end.label) == ("4", "5")  # spans the page turn
    assert s[1].page.label == "5"
    para = next(u for u in units if u.kind == "paragraph")
    assert (para.page.label, para.page_end.label) == ("4", "5")


def test_consecutive_and_backwards_markers() -> None:
    s = PageSettings().validate()
    doc = extract_text(b"[p. 2]\nA.\n\n[p. 3]\n[p. 4]\nB.\n\n[p. 3]\nC.", s)
    pm = build_page_map(doc, s)
    assert sentences(build_units(doc, pm)) == [("A.", "2"), ("B.", "4"), ("C.", "3")]
    assert any("backwards" in w for w in pm.warnings)


def test_custom_marker_pattern_and_validation() -> None:
    units = txt_units("<<7>>\nHello there.", marker_pattern=r"<<(\d+)>>")
    assert sentences(units) == [("Hello there.", "7")]
    with pytest.raises(PageSettingsError, match="exactly one"):
        PageSettings(marker_pattern=r"\[p\. \d+\]").validate()
    with pytest.raises(PageSettingsError, match="valid regex"):
        PageSettings(marker_pattern=r"(").validate()


def test_markers_mode_without_markers_is_an_error() -> None:
    s = PageSettings(mode="markers").validate()
    doc = extract_text(b"No markers here.", s)
    with pytest.raises(PageSettingsError, match="no page markers"):
        build_page_map(doc, s)


# ---- .txt: form feeds, offset, encoding ----


def test_form_feeds_with_offset_and_front_matter() -> None:
    text = "Title page.\fCopyright.\fChapter 1\n\nStory starts.\f\fAfter a blank page.\f"
    units = txt_units(text, first_numbered_page=3)
    assert sentences(units) == [
        ("Title page.", "front matter"),
        ("Copyright.", "front matter"),
        ("Chapter 1", "1"),
        ("Story starts.", "1"),
        ("After a blank page.", "3"),  # physical 5; the trailing \f adds no page
    ]
    skipped = sentences(txt_units(text, first_numbered_page=3, skip_front_matter=True))
    assert skipped[0] == ("Chapter 1", "1")


def test_hard_wraps_crlf_bom_and_cp1252() -> None:
    s = PageSettings().validate()
    doc = extract_text("﻿A line that\r\nwraps here.\r\n\r\nNext.".encode(), s)
    assert doc.text == "A line that wraps here.\n\nNext."
    latin = extract_text("Café au lait.".encode("cp1252"), s)
    assert latin.text == "Café au lait." and "Windows-1252" in latin.warnings[0]


def test_markdown_headings_and_emphasis() -> None:
    s = PageSettings().validate()
    doc = extract_text(b"# The Fox\nA *very* [hungry](x) fox.\n\n## Part Two", s, markdown=True)
    assert [doc.text[p.start : p.end] for p in doc.paragraphs] == [
        "The Fox",
        "A very hungry fox.",
        "Part Two",
    ]
    assert [p.kind for p in doc.paragraphs] == ["heading", "paragraph", "heading"]


# ---- estimate mode ----


def test_estimate_pages_are_labelled_approx() -> None:
    # 12 ten-word sentences: "W0 W1 ... W9." etc.
    words = " ".join(f"W{i}." if i % 10 == 9 else f"W{i}" for i in range(120))
    units = txt_units(words, words_per_page=50)
    s = [u for u in units if u.kind == "sentence"]
    assert s[0].page.label == "approx. 1" and s[0].page.estimated
    # Sentence of words 40-49 starts on page 1; words 50-59 start page 2.
    assert [x.page.number for x in s] == [1, 1, 1, 1, 1, 2, 2, 2, 2, 2, 3, 3]


def test_estimate_skip_front_matter_starts_at_first_heading() -> None:
    text = "Praise for the book. Lots of it.\n\nChapter One\n\nIt began."
    units = txt_units(text, mode="estimate", skip_front_matter=True)
    assert sentences(units) == [("Chapter One", "approx. 1"), ("It began.", "approx. 1")]


def test_settings_bounds() -> None:
    with pytest.raises(PageSettingsError):
        PageSettings(words_per_page=10).validate()
    with pytest.raises(PageSettingsError):
        PageSettings(first_numbered_page=0).validate()


# ---- units ----


def test_chapters_paragraphs_sentences_and_offsets() -> None:
    text = "Chapter One\n\nA fox. A vine.\n\nPart of the plan.\n\nCHAPTER II\n\nThe end."
    units = txt_units(text, mode="estimate")
    kinds = [(u.kind, u.text) for u in units if u.kind != "sentence"]
    assert kinds == [
        ("chapter", "Chapter One"),
        ("paragraph", "Chapter One"),
        ("paragraph", "A fox. A vine."),
        ("paragraph", "Part of the plan."),  # "Part of" is not "Part Two"
        ("chapter", "CHAPTER II"),
        ("paragraph", "CHAPTER II"),
        ("paragraph", "The end."),
    ]
    doc_text = extract_text(text.encode(), PageSettings()).text
    for u in units:
        if u.kind != "chapter":
            assert doc_text[u.char_start : u.char_end] == u.text
    ch1 = units[0]
    assert doc_text[ch1.char_start : ch1.char_end].endswith("Part of the plan.")
    sentences_ = [u for u in units if u.kind == "sentence"]
    assert [s.ordinal for s in sentences_] == list(range(len(sentences_)))
    assert units[sentences_[1].parent].text == "A fox. A vine."  # type: ignore[index]


# ---- .docx ----


def test_docx_explicit_breaks_inside_and_between_paragraphs() -> None:
    body = (
        p("Title Page")
        + p(runs=r("Ends here.") + PAGE_BREAK)
        + p("Chapter 1", style="Heading1")
        + p(runs=r("The fox jumped ") + PAGE_BREAK + r("very high."))
        + p("Before.", ppr="<w:pageBreakBefore/>")
        + p("Not a break.", ppr='<w:pageBreakBefore w:val="0"/>')
    )
    doc = extract_docx(docx(body))
    assert doc.source == "breaks"
    units = units_of(doc, first_numbered_page=2)
    s = [(u.text, u.page.label, u.page_end.label) for u in units if u.kind == "sentence"]
    assert s == [
        ("Title Page", "front matter", "front matter"),
        ("Ends here.", "front matter", "front matter"),
        ("Chapter 1", "1", "1"),
        ("The fox jumped very high.", "1", "2"),
        ("Before.", "3", "3"),
        ("Not a break.", "3", "3"),
    ]
    assert [u.text for u in units if u.kind == "chapter"] == ["Chapter 1"]


def test_docx_section_breaks() -> None:
    sect_next = '<w:sectPr><w:type w:val="nextPage"/></w:sectPr>'
    sect_cont = '<w:sectPr><w:type w:val="continuous"/></w:sectPr>'
    body = (
        p("One.", ppr=sect_next)
        + p("Two.", ppr=sect_cont)
        + p("Still two.")
        + p("End.", ppr="<w:sectPr/>")
    )  # no type = nextPage, but nothing follows
    units = units_of(extract_docx(docx(body)))
    assert sentences(units) == [("One.", "1"), ("Two.", "2"), ("Still two.", "2"), ("End.", "2")]


def test_docx_localized_heading_style_tracked_changes_and_fields() -> None:
    styles = (
        '<w:style w:type="paragraph" w:styleId="berschrift1"><w:name w:val="heading 1"/></w:style>'
    )
    body = (
        p("Kapitel Eins", style="berschrift1")
        + p(
            runs=r("Kept ")
            + "<w:ins><w:r><w:t>inserted </w:t></w:r></w:ins>"
            + "<w:del><w:r><w:delText>deleted </w:delText></w:r></w:del>"
            + "<w:r><w:instrText> PAGE </w:instrText></w:r>"
            + r("text.")
        )
        + "<w:tbl><w:tr><w:tc>"
        + p("In a table.")
        + "</w:tc></w:tr></w:tbl>"
    )
    doc = extract_docx(docx(body, styles))
    assert doc.source == "none"  # no explicit breaks: estimate mode
    assert [doc.text[x.start : x.end] for x in doc.paragraphs] == [
        "Kapitel Eins",
        "Kept inserted text.",
        "In a table.",
    ]
    assert doc.paragraphs[0].kind == "heading"
    assert units_of(doc)[2].page.label == "approx. 1"


def test_docx_rejects_non_docx() -> None:
    with pytest.raises(DocxError):
        extract_docx(b"not a zip")


# ---- .pdf (pure line handling) ----


def test_pdf_lines_paragraphs_hyphens_and_cross_page_paragraph() -> None:
    page1 = "\n".join(
        [
            "              THE FOX AND THE GRAPES",
            "",
            "Chapter One",
            "",
            "    A hungry fox saw some fine grapes hanging from a vine that",
            "was trained along a high trellis. He tried to reach them by jump-",
            "ing as high as he could.",
            "    But it was all in vain, for they were just out of reach. So he",
            "walked away, remarking that he thought the grapes were ripe",
            "",
            "                          1",
        ]
    )
    page2 = "\n".join(
        [
            "              THE FOX AND THE GRAPES",
            "but now saw that they were quite sour.",
            "    Slow and steady wins the race.",
            "                          2",
        ]
    )
    page3 = "\n".join(["              THE FOX AND THE GRAPES", "    The end.", "      3"])
    doc = extract_pdf_pages([page1, page2, page3])
    paras = [doc.text[x.start : x.end] for x in doc.paragraphs]
    assert paras == [
        "Chapter One",
        "A hungry fox saw some fine grapes hanging from a vine that was trained along a "
        "high trellis. He tried to reach them by jumping as high as he could.",
        "But it was all in vain, for they were just out of reach. So he walked away, "
        "remarking that he thought the grapes were ripe but now saw that they were quite "
        "sour.",
        "Slow and steady wins the race.",
        "The end.",
    ]
    units = units_of(doc)
    last_of_p1 = [u for u in units if u.kind == "sentence"][4]
    assert last_of_p1.text.startswith("So he walked away")
    assert (last_of_p1.page.label, last_of_p1.page_end.label) == ("1", "2")


def test_pdf_scanned_and_blank_pages() -> None:
    with pytest.raises(PdfError, match="no text layer"):
        extract_pdf_pages(["", "   \n  ", "\n"])
    doc = extract_pdf_pages(["Some text.", ""])
    assert "1 page(s) have no text" in doc.warnings[0]


# ---- .pdf (real files) ----


def test_real_pdf_with_roman_front_matter_labels() -> None:
    data = pdf(
        [
            [(0, "Contents")],
            [(0, "Chapter One"), None, (18, "It began on a Tuesday.")],
            [(18, "It ended on a Friday.")],
        ],
        labels=[(0, "/r", 1), (1, "/D", 1)],
        header="A BOOK",
    )
    units = units_of(extract_pdf(data))
    assert sentences(units) == [
        ("Contents", "i"),
        ("Chapter One", "1"),
        ("It began on a Tuesday.", "1"),
        ("It ended on a Friday.", "2"),
    ]
    assert units[0].page.front_matter and units[0].page.number is None
    skipped = units_of(extract_pdf(data), skip_front_matter=True)
    assert sentences(skipped)[0] == ("Chapter One", "1")


def test_real_pdf_without_labels_uses_offset() -> None:
    data = pdf([[(0, "Title.")], [(18, "Body text.")]])
    assert sentences(units_of(extract_pdf(data), first_numbered_page=2)) == [
        ("Title.", "front matter"),
        ("Body text.", "1"),
    ]
    # Labels can be ignored in favour of counting.
    labelled = pdf([[(18, "First.")], [(18, "Second.")]], labels=[(0, "/D", 7)])
    assert sentences(units_of(extract_pdf(labelled))) == [("First.", "7"), ("Second.", "8")]
    assert sentences(units_of(extract_pdf(labelled), use_pdf_labels=False))[0] == ("First.", "1")


def test_real_pdf_rejects_garbage() -> None:
    with pytest.raises(PdfError):
        extract_pdf(b"%PDF-1.4 garbage")


def test_pdf_title_page_survives_matching_running_head() -> None:
    """Regression: a title page whose text equals the running head was stripped."""
    head = "              THE BOOK"
    body = ["It was dark.", "The fox ran.", "A vine grew.", "Then it ended."]
    pages = ["  The Book  "] + [f"{head}\n    {b}\n      {n}" for n, b in enumerate(body, 1)]
    doc = extract_pdf_pages(pages)
    assert doc.text.startswith("The Book")  # the title page is kept...
    assert "THE BOOK" not in doc.text  # ...while the running heads go
    assert doc.text.count("The Book") == 1
