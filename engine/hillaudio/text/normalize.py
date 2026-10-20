"""Normalize text into comparable word tokens (pure). SPEC §6.3 step 3.

Both the manuscript and the transcript go through the same function, so a
difference that is only spelling, not speech, never counts as a flub:
- case and punctuation are ignored; curly and straight apostrophes are equal;
- numbers become words: "12" and "twelve" match, as do "1999" and
  "nineteen ninety-nine", "3rd" and "third", "$5" and "five dollars";
- hyphens and dashes separate words: "well-known" is "well known";
- contractions expand: "don't" is "do not", and the ambiguous "'d" and "'s"
  become tokens that match either reading ("he'd" matches "he had" and
  "he would"; "it's" matches "it is" and "it has");
- a few spoken-out abbreviations: "Mr." is "mister", "&" is "and".

Each token remembers which source word it came from (its character span), so an
alignment of tokens maps straight back to the manuscript text or the
transcript's word timings.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# "_d" and "_s" stand for an ambiguous contraction; see `tokens_equal`.
EITHER = {"_d": frozenset({"would", "had"}), "_s": frozenset({"is", "has"})}

_CONTRACTIONS = {
    "can't": ["can", "not"],
    "won't": ["will", "not"],
    "shan't": ["shall", "not"],
    "ain't": ["am", "not"],
    "let's": ["let", "us"],
    "i'm": ["i", "am"],
    "o'clock": ["o'clock"],
    "y'all": ["you", "all"],
}
# Suffix rules, applied when no whole-word entry matches.
_SUFFIXES = [
    ("n't", ["not"]),
    ("'re", ["are"]),
    ("'ve", ["have"]),
    ("'ll", ["will"]),
    ("'d", ["_d"]),
    ("'m", ["am"]),
]
# 's is "is"/"has" only after these; elsewhere it's a possessive and stays.
_S_IS_HAS = frozenset(
    [
        "it",
        "that",
        "what",
        "there",
        "here",
        "he",
        "she",
        "who",
        "where",
        "how",
        "when",
        "why",
        "this",
        "nothing",
        "everyone",
        "somebody",
        "someone",
        "everything",
        "something",
    ]
)
_ABBREVIATIONS = {
    "mr": ["mister"],
    "mrs": ["missus"],
    "ms": ["miz"],
    "dr": ["doctor"],
    "&": ["and"],
    "%": ["percent"],
    "vs": ["versus"],
}

_ONES = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
]
_TENS = ["_", "_", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_SCALES = [(10**9, "billion"), (10**6, "million"), (1000, "thousand")]
_ORDINAL_IRREGULAR = {
    "one": "first",
    "two": "second",
    "three": "third",
    "five": "fifth",
    "eight": "eighth",
    "nine": "ninth",
    "twelve": "twelfth",
}

_APOSTROPHES = str.maketrans({"\u2019": "'", "\u2018": "'", "\u02bc": "'", "`": "'"})
_DASHES = re.compile(r"[-\u2010-\u2015\u2212/]")
# A word: letters/digits with inner apostrophes, decimals or digit groups; or $/&/%.
_WORD = re.compile(r"\$?\d[\d,]*(?:\.\d+)?(?:st|nd|rd|th)?%?|[a-z0-9]+(?:'[a-z]+)*|[&%]")


@dataclass(frozen=True)
class Token:
    norm: str  # normalized form used for comparison
    start: int  # character span of the source word in the original text
    end: int


def tokens_equal(a: str, b: str) -> bool:
    if a == b:
        return True
    return b in EITHER.get(a, ()) or a in EITHER.get(b, ())


def _below_thousand(n: int) -> list[str]:
    out: list[str] = []
    if n >= 100:
        out += [_ONES[n // 100], "hundred"]
        n %= 100
    if n >= 20:
        out.append(_TENS[n // 10])
        n %= 10
        if n:
            out.append(_ONES[n])
    elif n or not out:
        out.append(_ONES[n])
    return out


def number_words(n: int) -> list[str]:
    """Cardinal words: 2024 -> ["two", "thousand", "twenty", "four"]."""
    if n < 1000:
        return _below_thousand(n)
    out: list[str] = []
    for value, name in _SCALES:
        if n >= value:
            out += [*number_words(n // value), name]
            n %= value
    if n:
        out += _below_thousand(n)
    return out


def year_words(n: int) -> list[str]:
    """How years are read: 1999 -> nineteen ninety nine; 1905 -> nineteen oh five;
    1900 -> nineteen hundred; 2007 -> two thousand seven; 2024 -> twenty twenty four."""
    if 2000 <= n <= 2009:
        return number_words(n)
    hi, lo = divmod(n, 100)
    if lo == 0:
        return [*_below_thousand(hi), "hundred"]
    return [*_below_thousand(hi), *(["oh", _ONES[lo]] if lo < 10 else _below_thousand(lo))]


def ordinal(words: list[str]) -> list[str]:
    last = words[-1]
    if last in _ORDINAL_IRREGULAR:
        return [*words[:-1], _ORDINAL_IRREGULAR[last]]
    if last.endswith("y"):
        return [*words[:-1], last[:-1] + "ieth"]
    return [*words[:-1], last + "th"]


def _numeric(word: str) -> list[str]:
    currency = word.startswith("$")
    percent = word.endswith("%")
    w = word.strip("$%")
    suffix = w[-2:] if w[-2:] in ("st", "nd", "rd", "th") else ""
    w = w[: len(w) - len(suffix)].replace(",", "")
    if "." in w:
        whole, frac = w.split(".", 1)
        out = [*number_words(int(whole or "0")), "point", *(_ONES[int(d)] for d in frac)]
    else:
        n = int(w)
        # Four-digit numbers without separators read as years: 1984, 1066.
        is_year = len(w) == 4 and "," not in word and 1100 <= n <= 2099 and not suffix
        out = year_words(n) if is_year and not currency else number_words(n)
        if suffix:
            out = ordinal(out)
    if currency:
        out.append("dollar" if out == ["one"] else "dollars")
    if percent:
        out.append("percent")
    return out


def _expand(word: str) -> list[str]:
    if word in _CONTRACTIONS:
        return _CONTRACTIONS[word]
    if word[0].isdigit() or word[0] == "$":
        return _numeric(word)
    if word in _ABBREVIATIONS:
        return _ABBREVIATIONS[word]
    if word.endswith("'s"):
        stem = word[:-2]
        return [stem, "_s"] if stem in _S_IS_HAS else [word]
    for suffix, replacement in _SUFFIXES:
        if word.endswith(suffix) and len(word) > len(suffix):
            stem = word[: -len(suffix)]
            return [stem, *replacement]
    return [word]


def tokenize(text: str) -> list[Token]:
    """Normalized tokens with the character spans of the words they came from."""
    # casefold and the replacements below keep string length, so spans stay exact.
    folded = unicodedata.normalize("NFC", text).casefold()
    if len(folded) != len(text):
        folded = text.lower()  # rare characters that expand under casefold
    folded = folded.translate(_APOSTROPHES)
    folded = _DASHES.sub(" ", folded)
    out: list[Token] = []
    for m in _WORD.finditer(folded):
        word = m.group(0).strip("'")
        if not word:
            continue
        for norm in _expand(word):
            out.append(Token(norm, m.start(), m.end()))
    return out


def normalize(text: str) -> list[str]:
    return [t.norm for t in tokenize(text)]
