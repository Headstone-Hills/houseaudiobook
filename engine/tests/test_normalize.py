from __future__ import annotations

import pytest

from hillaudio.text.normalize import normalize, number_words, tokenize, tokens_equal, year_words


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("The Fox, and the GRAPES!", "the fox and the grapes"),
        ("\u201cWell-known,\u201d she said\u2014quietly.", "well known she said quietly"),
        ("I don't know; can't you? Won't they?", "i do not know can not you will not they"),
        (
            "We're here, you've seen, they'll go, I'm off.",
            "we are here you have seen they will go i am off",
        ),
        ("It\u2019s John's book; let's go.", "it _s john's book let us go"),
        ("He'd left.", "he _d left"),
        ("Mr. and Mrs. Smith & Dr. Jones", "mister and missus smith and doctor jones"),
        ("12 apples, 3rd place, 21st time", "twelve apples third place twenty first time"),
        (
            "In 1999 and 1905 and 2007 and 2024.",
            "in nineteen ninety nine and nineteen oh five and two thousand seven and twenty "
            "twenty four",
        ),
        (
            "It cost $5 or $1,250.50, about 40%.",
            "it cost five dollars or one thousand two hundred fifty point five zero dollars "
            "about forty percent",
        ),
        ("1,000,000 stars", "one million stars"),
        ("rock 'n' roll", "rock n roll"),
    ],
)
def test_normalize(text: str, expected: str) -> None:
    assert " ".join(normalize(text)) == expected


def test_spoken_and_written_forms_match() -> None:
    def same(a: str, b: str) -> bool:
        na, nb = normalize(a), normalize(b)
        return len(na) == len(nb) and all(tokens_equal(x, y) for x, y in zip(na, nb, strict=True))

    assert same("twelve", "12")
    assert same("nineteen ninety-nine", "1999")
    assert same("He'd gone", "he had gone") and same("He'd go", "he would go")
    assert same("It's late", "it is late") and same("it's been", "it has been")
    assert not same("John's", "John is")  # a possessive is not "is"
    assert same("Don't", "do not")


def test_number_words() -> None:
    assert number_words(0) == ["zero"]
    assert number_words(105) == ["one", "hundred", "five"]
    assert number_words(2024) == ["two", "thousand", "twenty", "four"]
    assert year_words(1900) == ["nineteen", "hundred"]


def test_token_spans_point_at_source_words() -> None:
    text = "Mr. O\u2019Brien didn't pay $5."
    toks = tokenize(text)
    assert [(t.norm, text[t.start : t.end]) for t in toks] == [
        ("mister", "Mr"),
        ("o'brien", "O\u2019Brien"),
        ("did", "didn't"),
        ("not", "didn't"),
        ("pay", "pay"),
        ("five", "$5"),
        ("dollars", "$5"),
    ]
