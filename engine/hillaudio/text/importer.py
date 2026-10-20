"""Import a manuscript into a project, preview its page mapping, and re-map pages.

The user's manuscript file is only ever read. Import copies it into `text/`
(hash-verified, read-only), and every later re-mapping reads that copy, never
the original. Units are replaced on re-map; re-importing a different manuscript
keeps the previous copy on disk (D-023).
"""

from __future__ import annotations

import json
import shutil
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hillaudio.ingest.hashing import sha256_file
from hillaudio.ingest.importer import safe_filename
from hillaudio.store.project import Project, now_iso
from hillaudio.text.extract_docx import extract_docx
from hillaudio.text.extract_pdf import extract_pdf
from hillaudio.text.extract_txt import extract_text
from hillaudio.text.model import Document
from hillaudio.text.pages import PageMap, PageSettings, build_page_map
from hillaudio.text.units import Unit, build_units

FORMATS = {
    ".txt": "txt",
    ".text": "txt",
    ".md": "md",
    ".markdown": "md",
    ".docx": "docx",
    ".pdf": "pdf",
}
UNSUPPORTED_HINTS = {
    ".epub": "EPUB isn't supported yet; export the book as .docx, .pdf or .txt",
    ".doc": "old Word .doc files aren't supported; save it as .docx",
    ".rtf": "RTF isn't supported; save it as .docx or .txt",
    ".odt": "OpenDocument isn't supported; save it as .docx",
}
PREVIEW_UNITS = 40
PREVIEW_PAGES = 2000


class ManuscriptError(ValueError):
    """A user-facing reason a manuscript can't be read or imported."""


def detect_format(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in UNSUPPORTED_HINTS:
        raise ManuscriptError(UNSUPPORTED_HINTS[suffix])
    if suffix not in FORMATS:
        raise ManuscriptError(
            f"unsupported manuscript type {suffix or '(none)'!r}; use .txt, .md, .docx or .pdf"
        )
    return FORMATS[suffix]


def extract(data: bytes, fmt: str, settings: PageSettings) -> Document:
    try:
        if fmt == "docx":
            return extract_docx(data)
        if fmt == "pdf":
            return extract_pdf(data)
        return extract_text(data, settings, markdown=fmt == "md")
    except ValueError as e:  # DocxError, PdfError
        raise ManuscriptError(str(e)) from None


@dataclass
class Analysis:
    doc: Document
    pages: PageMap
    units: list[Unit]
    warnings: list[str] = field(default_factory=list)


def analyze(data: bytes, fmt: str, settings: PageSettings) -> Analysis:
    try:
        settings = settings.validate()
        doc = extract(data, fmt, settings)
        if not doc.text.strip():
            raise ManuscriptError("the manuscript contains no text")
        pages = build_page_map(doc, settings)
    except ValueError as e:  # PageSettingsError
        raise ManuscriptError(str(e)) from None
    units = build_units(doc, pages, settings.skip_front_matter)
    if not any(u.kind == "sentence" for u in units):
        raise ManuscriptError("no text left after skipping front matter")
    return Analysis(doc, pages, units, [*doc.warnings, *pages.warnings])


def unit_json(u: Unit) -> dict[str, Any]:
    return {
        "kind": u.kind,
        "ordinal": u.ordinal,
        "text": u.text,
        "char_start": u.char_start,
        "char_end": u.char_end,
        "page": u.page.number,
        "page_label": u.page.label,
        "page_end": u.page_end.number,
        "page_end_label": u.page_end.label,
        "page_estimated": u.page.estimated,
        "front_matter": u.page.front_matter,
    }


def summary(a: Analysis, fmt: str, settings: PageSettings) -> dict[str, Any]:
    counts = {
        k: sum(1 for u in a.units if u.kind == k) for k in ("chapter", "paragraph", "sentence")
    }
    return {
        "format": fmt,
        "page_source": a.doc.source,  # what the document itself carried
        "page_mode": a.pages.mode,  # what was used
        "page_count": a.pages.page_count,
        "estimated": a.pages.mode == "estimate",
        "counts": counts,
        "warnings": a.warnings,
        "settings": settings.to_json(),
    }


def preview(data: bytes, fmt: str, settings: PageSettings) -> dict[str, Any]:
    """What an import with these settings would produce; nothing is written."""
    a = analyze(data, fmt, settings)
    ends = [*a.pages.starts[1:], len(a.doc.text)]
    pages = [
        {
            "label": ref.label,
            "physical": ref.physical,
            "front_matter": ref.front_matter,
            # Stop at the next page so a short page doesn't show the next one's text.
            "first_text": a.doc.text[start : min(start + 80, end)].strip(),
        }
        for start, end, ref in list(zip(a.pages.starts, ends, a.pages.refs, strict=True))[
            :PREVIEW_PAGES
        ]
    ]
    first_sentences = [unit_json(u) for u in a.units if u.kind == "sentence"][:PREVIEW_UNITS]
    return summary(a, fmt, settings) | {"pages": pages, "sentences": first_sentences}


def _store(project: Project, path: Path, digest: str, name: str) -> str:
    """Copy the manuscript into text/ (read-only), verified. Returns project-relative path."""
    text_dir = project.root / "text"
    final = text_dir / f"{digest[:12]}_{safe_filename(name)}"
    if final.exists():
        if sha256_file(final) == digest:
            return str(final.relative_to(project.root))  # same file imported before
        # Never overwrite (= delete) a damaged copy: set it aside, as for audio.
        final.rename(final.with_name(f"{final.name}.damaged-{now_iso().replace(':', '')}"))
    partial = final.with_name(final.name + ".partial")
    try:
        shutil.copyfile(path, partial)  # reads the user's file; writes only our copy
        if sha256_file(partial) != digest:
            raise ManuscriptError("the copy did not match the original; nothing imported")
        partial.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        partial.replace(final)
    finally:
        if partial.exists():
            partial.chmod(stat.S_IRUSR | stat.S_IWUSR)
            partial.unlink()  # only our own half-written copy
    return str(final.relative_to(project.root))


def _write_units(project: Project, a: Analysis, settings: PageSettings, conn: Any) -> None:
    conn.execute("DELETE FROM manuscript_unit")
    ids: list[int] = []
    for u in a.units:
        cur = conn.execute(
            """INSERT INTO manuscript_unit (kind, ordinal, parent_id, text, char_start,
                char_end, page, page_label, page_end, page_end_label, page_estimated,
                front_matter) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                u.kind,
                u.ordinal,
                ids[u.parent] if u.parent is not None else None,
                u.text,
                u.char_start,
                u.char_end,
                u.page.number,
                u.page.label,
                u.page_end.number,
                u.page_end.label,
                int(u.page.estimated),
                int(u.page.front_matter),
            ),
        )
        ids.append(int(cur.lastrowid))
    # Derived, regenerable: the extracted text beside the copy, for transparency.
    (project.root / "text" / "extracted.txt").write_text(a.doc.text, encoding="utf-8")


def import_manuscript(
    project: Project, path: Path, settings: PageSettings, original_name: str | None = None
) -> dict[str, Any]:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise ManuscriptError(f"file not found: {path}")
    name = original_name or path.name
    fmt = detect_format(Path(name))
    data = path.read_bytes()
    a = analyze(data, fmt, settings)  # fail before copying anything
    digest = sha256_file(path)
    stored = _store(project, path, digest, name)
    now = now_iso()
    with project.db() as conn:
        previous = conn.execute("SELECT sha256 FROM manuscript WHERE id = 1").fetchone()
        conn.execute("DELETE FROM manuscript")
        conn.execute(
            """INSERT INTO manuscript (id, original_path, original_name, stored_path, sha256,
                size_bytes, format, settings_json, page_mode, page_count, warnings_json,
                full_text, imported_at, mapped_at)
               VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(path),
                name,
                stored,
                digest,
                len(data),
                fmt,
                json.dumps(settings.to_json()),
                a.pages.mode,
                a.pages.page_count,
                json.dumps(a.warnings),
                a.doc.text,
                now,
                now,
            ),
        )
        _write_units(project, a, settings, conn)
        project.log(
            conn,
            "import_manuscript",
            {
                "sha256": digest,
                "name": name,
                "format": fmt,
                "replaced": previous["sha256"] if previous else None,
                "settings": settings.to_json(),
            },
            "user",
        )
    return summary(a, fmt, settings)


def remap(project: Project, settings: PageSettings) -> dict[str, Any]:
    """Re-run page mapping with new settings, from the project's copy."""
    with project.db() as conn:
        row = conn.execute("SELECT * FROM manuscript WHERE id = 1").fetchone()
    if row is None:
        raise ManuscriptError("no manuscript has been imported")
    copy = project.root / row["stored_path"]
    if not copy.is_file() or sha256_file(copy) != row["sha256"]:
        raise ManuscriptError(
            "the project's copy of the manuscript is missing or changed; import it again"
        )
    a = analyze(copy.read_bytes(), row["format"], settings)
    with project.db() as conn:
        conn.execute(
            """UPDATE manuscript SET settings_json = ?, page_mode = ?, page_count = ?,
                warnings_json = ?, full_text = ?, mapped_at = ? WHERE id = 1""",
            (
                json.dumps(settings.to_json()),
                a.pages.mode,
                a.pages.page_count,
                json.dumps(a.warnings),
                a.doc.text,
                now_iso(),
            ),
        )
        _write_units(project, a, settings, conn)
        project.log(conn, "remap_manuscript_pages", {"settings": settings.to_json()}, "user")
    return summary(a, row["format"], settings)


def stored_copy(project: Project) -> tuple[Path, str] | None:
    """The project's copy of its manuscript, and the original file name."""
    with project.db() as conn:
        row = conn.execute(
            "SELECT stored_path, original_name FROM manuscript WHERE id = 1"
        ).fetchone()
    return (project.root / row["stored_path"], row["original_name"]) if row else None


def verify_copy(project: Project) -> str | None:
    """Check the project's manuscript copy against its hash: ok, missing or modified."""
    with project.db() as conn:
        row = conn.execute("SELECT stored_path, sha256 FROM manuscript WHERE id = 1").fetchone()
    if row is None:
        return None
    copy = project.root / row["stored_path"]
    if not copy.is_file():
        return "missing"
    return "ok" if sha256_file(copy) == row["sha256"] else "modified"


def manuscript_info(project: Project) -> dict[str, Any] | None:
    with project.db() as conn:
        row = conn.execute(
            "SELECT original_name, original_path, sha256, size_bytes, format, settings_json,"
            " page_mode, page_count, warnings_json, imported_at, mapped_at"
            " FROM manuscript WHERE id = 1"
        ).fetchone()
        if row is None:
            return None
        counts = dict(
            conn.execute("SELECT kind, COUNT(*) FROM manuscript_unit GROUP BY kind").fetchall()
        )
    info = dict(row)
    info["settings"] = json.loads(info.pop("settings_json"))
    info["warnings"] = json.loads(info.pop("warnings_json"))
    info["estimated"] = info["page_mode"] == "estimate"
    info["counts"] = {k: counts.get(k, 0) for k in ("chapter", "paragraph", "sentence")}
    return info


def list_units(project: Project, kind: str, offset: int, limit: int) -> dict[str, Any]:
    with project.db() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM manuscript_unit WHERE kind = ?", (kind,)
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT id, kind, ordinal, parent_id, text, char_start, char_end, page, page_label,"
            " page_end, page_end_label, page_estimated, front_matter FROM manuscript_unit"
            " WHERE kind = ? ORDER BY ordinal LIMIT ? OFFSET ?",
            (kind, limit, offset),
        ).fetchall()
    units = [
        dict(r)
        | {"page_estimated": bool(r["page_estimated"]), "front_matter": bool(r["front_matter"])}
        for r in rows
    ]
    return {"total": total, "offset": offset, "units": units}
