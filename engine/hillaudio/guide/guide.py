"""The narrator corrections guide (SPEC §6.9): what to re-record, where.

One row per marked correction (flubs, unread lines and, optionally, long pauses):
timestamp, page, expected text, heard text, type and notes. Sorted by page
(default) or time. Timestamps refer to a stated reference (D-053):
- "original": the recording it was found in, e.g. 2:13.4 in `Ch03_Take1.wav`;
- "master": the edited master, i.e. the timeline (an issue whose audio was cut
  says so).
CSV and Markdown are plain text; the PDF is built with reportlab (BSD).
"""

from __future__ import annotations

import csv
import io
from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from hillaudio import issue_store
from hillaudio.edl import timeline_store
from hillaudio.store.project import Project

Reference = Literal["original", "master"]
Sort = Literal["page", "time"]
KIND_LABELS = {"flub": "Flub", "missing_line": "Unread line", "silence": "Long pause"}
COLUMNS = ("#", "Page", "Time", "Recording", "Type", "Expected", "Heard", "Note", "Status")


@dataclass(frozen=True)
class GuideOptions:
    reference: Reference = "original"
    sort: Sort = "page"
    narrator: str = ""
    include_resolved: bool = False
    include_silences: bool = False


def clock(t: float) -> str:
    h, rem = divmod(max(0.0, t), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:04.1f}" if h else f"{int(m)}:{s:04.1f}"


def rows(project: Project, o: GuideOptions) -> list[dict[str, Any]]:
    st = timeline_store.load(project)
    loc = timeline_store.Locator(st)
    out = []
    for i in issue_store.list_issues(project):
        if i["kind"] == "silence" and not o.include_silences:
            continue
        if i["status"] == "dismissed" or (i["status"] == "resolved" and not o.include_resolved):
            continue
        t: float | None = None
        where = ""
        if i["source_file_id"] is not None and i["start"] is not None:
            if o.reference == "original":
                t, where = i["start"], i["source_label"] or ""
            else:
                t = loc.at(i["source_file_id"], i["start"])
                where = "edited master" if t is not None else "cut from the master"
        else:
            where = "not recorded"
        out.append(
            {
                "unit": i["manuscript_unit_id"] if i["manuscript_unit_id"] is not None else 10**9,
                "page": i["page_label"] or "",
                "t": t,
                "time": clock(t) if t is not None else "",
                "recording": where,
                "type": KIND_LABELS.get(i["kind"], i["kind"]),
                "kind": i["kind"],
                "expected": i["expected"] if i["kind"] != "silence" else "",
                "heard": i["heard"] if i["kind"] == "flub" else _silence(i),
                "note": i["note"],
                "status": i["status"],
            }
        )
    if o.sort == "page":
        out.sort(key=lambda r: (r["unit"], r["t"] if r["t"] is not None else 1e12))
    else:
        out.sort(key=lambda r: (r["recording"], r["t"] if r["t"] is not None else 1e12, r["unit"]))
    for n, r in enumerate(out, 1):
        r["n"] = n
    return out


def _silence(i: dict[str, Any]) -> str:
    d = i["detail"]
    return f"{d.get('seconds', 0):.1f} s pause" if i["kind"] == "silence" else ""


def header(project: Project, o: GuideOptions, rs: list[dict[str, Any]]) -> dict[str, Any]:
    ref = (
        "Times are positions in the original recordings (file named in each row)."
        if o.reference == "original"
        else "Times are positions in the edited master (the timeline as exported in one file)."
    )
    counts = Counter(r["type"] for r in rs)
    return {
        "title": f"Corrections guide: {project.name}",
        "narrator": o.narrator,
        "date": date.today().isoformat(),
        "reference": ref,
        "sorted": "by page" if o.sort == "page" else "by time",
        "counts": dict(sorted(counts.items())),
        "total": len(rs),
    }


def _cells(r: dict[str, Any]) -> list[str]:
    return [
        str(r["n"]),
        r["page"],
        r["time"],
        r["recording"],
        r["type"],
        r["expected"],
        r["heard"],
        r["note"],
        r["status"],
    ]


def to_csv(project: Project, o: GuideOptions) -> str:
    rs = rows(project, o)
    h = header(project, o, rs)
    buf = io.StringIO()
    w = csv.writer(buf)
    # A spreadsheet-friendly preamble: commented lines first, then the table.
    w.writerow([f"# {h['title']}"])
    if h["narrator"]:
        w.writerow([f"# Narrator: {h['narrator']}"])
    w.writerow([f"# {h['date']} · {h['reference']}"])
    w.writerow(COLUMNS)
    for r in rs:
        w.writerow(_cells(r))
    return buf.getvalue()


def _md(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def to_markdown(project: Project, o: GuideOptions) -> str:
    rs = rows(project, o)
    h = header(project, o, rs)
    lines = [f"# {h['title']}", ""]
    if h["narrator"]:
        lines.append(f"Narrator: {h['narrator']}  ")
    lines += [f"Date: {h['date']}  ", f"{h['reference']} Sorted {h['sorted']}.", ""]
    summary = ", ".join(f"{k}: {v}" for k, v in h["counts"].items()) or "none"
    lines += [f"**{h['total']} correction{'s' if h['total'] != 1 else ''}** ({summary})", ""]
    lines.append("| " + " | ".join(COLUMNS) + " |")
    lines.append("|" + "---|" * len(COLUMNS))
    for r in rs:
        lines.append("| " + " | ".join(_md(c) for c in _cells(r)) + " |")
    return "\n".join(lines) + "\n"


def to_pdf(project: Project, o: GuideOptions) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    rs = rows(project, o)
    h = header(project, o, rs)
    styles = getSampleStyleSheet()
    small = styles["BodyText"].clone("small", fontSize=8, leading=10)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title=h["title"],
    )
    story: list[Any] = [Paragraph(_esc(h["title"]), styles["Title"])]
    meta = [f"Date: {h['date']}"]
    if h["narrator"]:
        meta.insert(0, f"Narrator: {_esc(h['narrator'])}")
    story.append(Paragraph(" · ".join(meta), styles["BodyText"]))
    story.append(Paragraph(_esc(f"{h['reference']} Sorted {h['sorted']}."), styles["BodyText"]))
    summary = ", ".join(f"{k}: {v}" for k, v in h["counts"].items()) or "none"
    story.append(Paragraph(_esc(f"{h['total']} corrections ({summary})"), styles["Heading3"]))
    story.append(Spacer(1, 4 * mm))
    data = [[Paragraph(f"<b>{c}</b>", small) for c in COLUMNS]]
    data += [[Paragraph(_esc(c), small) for c in _cells(r)] for r in rs]
    widths = [8, 14, 18, 30, 20, 70, 70, 35, 16]
    t = Table(data, colWidths=[w * mm for w in widths], repeatRows=1)
    t.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(t)
    doc.build(story)
    return buf.getvalue()


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
