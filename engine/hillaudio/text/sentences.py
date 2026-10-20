"""Sentence splitting for narrated prose (pure).

A boundary is terminal punctuation (. ! ? or an ellipsis), optionally followed by closing
quotes or brackets, then whitespace, then something that can start a sentence
(an uppercase letter, digit, or opening quote/bracket). That rule alone keeps
`"Stop!" she cried.` and `wait... then` as one sentence. On top of it, a period
after a known abbreviation or a single initial is not a boundary.
"""

from __future__ import annotations

import re

# Lowercased, without the trailing period. Titles and other abbreviations that are
# normally followed by a capitalized word, so the general rule can't catch them.
ABBREVIATIONS = frozenset(
    [
        "mr",
        "mrs",
        "ms",
        "mx",
        "dr",
        "prof",
        "rev",
        "fr",
        "sr",
        "sra",
        "srta",
        "jr",
        "st",
        "mt",
        "ft",
        "gen",
        "col",
        "capt",
        "lt",
        "sgt",
        "cpl",
        "adm",
        "gov",
        "pres",
        "sen",
        "rep",
        "hon",
        "messrs",
        "mme",
        "mlle",
        "no",
        "nos",
        "vol",
        "vols",
        "ch",
        "chap",
        "fig",
        "p",
        "pp",
        "ed",
        "eds",
        "approx",
        "dept",
        "est",
        "inc",
        "ltd",
        "co",
        "corp",
        "bros",
        "vs",
        "etc",
        "al",
        "e.g",
        "i.e",
        "cf",
        "viz",
    ]
)

_BOUNDARY = re.compile(
    r"""(?P<end>[.!?\u2026]+["'\u201d\u2019)\]]*)   # terminal punctuation + closers
        \s+
        (?=["'\u201c\u2018(\[]*[A-Z0-9\u00c0-\u00de])  # next sentence can start here
    """,
    re.VERBOSE,
)
_LAST_WORD = re.compile(r"([A-Za-z][A-Za-z.]*)\.$")


def _is_abbreviation(text_before: str) -> bool:
    m = _LAST_WORD.search(text_before)
    if not m:
        return False
    word = m.group(1)
    if len(word) == 1 and word.isupper():
        return True  # an initial: "J. R. R. Tolkien", "Mr. T. Smith"
    return word.lower().rstrip(".") in ABBREVIATIONS


def split_sentences(text: str) -> list[tuple[int, int]]:
    """Return (start, end) spans of the sentences in `text`, trimmed of spaces."""
    spans: list[tuple[int, int]] = []
    start = 0
    for m in _BOUNDARY.finditer(text):
        end = m.end("end")
        punct = m.group("end")
        if (
            punct.startswith(".")
            and not punct.startswith("..")
            and _is_abbreviation(text[start : m.start("end") + 1])
        ):
            continue
        spans.append((start, end))
        start = m.end()
    if text[start:].strip():
        spans.append((start, len(text.rstrip())))
    # Trim leading whitespace inside each span (offsets stay exact).
    return [(s + (len(text[s:e]) - len(text[s:e].lstrip())), e) for s, e in spans if s < e]
