"""Spaces inside `\\text{...}` are content, and were being thrown away.

LaTeX ignores whitespace in mathematics, so the tokeniser dropped it outright.
But `\\text{...}` is the one construct that is *not* mathematics, and everything
a physics book puts in it is worded or unitted: `20 T` arrived as `20T`,
`radius of path` as `radiusofpath`, `1.6\\times10^{-13}\\text{ N}` as `...N`.

Whitespace is now kept by the tokeniser and stepped over at the parser's cursor,
so these tests come in pairs: the text case must keep its spaces and the maths
case must still ignore them.
"""

from __future__ import annotations

import re

import pytest

from app.latex_omml import latex_to_omml_body

TEXT_RE = re.compile(r"<m:t[^>]*>(.*?)</m:t>", re.S)


def rendered(latex: str) -> str:
    """The text a reader will actually see, concatenated out of the OMML runs."""
    return "".join(TEXT_RE.findall(latex_to_omml_body(latex)))


# -- text mode keeps its spaces ---------------------------------------------- #


@pytest.mark.parametrize(
    ("latex", "expected"),
    [
        (r"\text{radius of path}", "radius of path"),
        (r"\text{20 T}", "20 T"),
        (r"d = \text{radius of path}", "d=radius of path"),
        (r"K_p = 2 \times 50 = 100\text{ keV}", "Kp=2×50=100 keV"),
        (r"\textrm{a b}", "a b"),
        (r"\mathrm{a b}", "a b"),
        (r"\mbox{a b}", "a b"),
    ],
)
def test_text_mode_keeps_its_spaces(latex, expected):
    assert rendered(latex) == expected


def test_a_unit_stays_separated_from_its_number():
    """The case from the page that exposed this: `= 1.6 x 10^-13 N`."""
    assert rendered(r"1.6\times10^{-13}\text{ N}").endswith(" N")


def test_a_run_of_whitespace_becomes_one_space():
    """As in LaTeX text mode, and so a wrapped transcription reads normally."""
    assert rendered("\\text{a  \n  b}") == "a b"


# -- mathematics still ignores them ------------------------------------------ #


@pytest.mark.parametrize(
    ("latex", "expected"),
    [
        ("x + y", "x+y"),
        ("a  +   b", "a+b"),
        ("\\frac{a}{b}", "ab"),
        ("\\sqrt{2mK}", "2mK"),
        ("r \\propto \\frac{\\sqrt{m}}{q}", "r∝mq"),
    ],
)
def test_maths_mode_still_ignores_whitespace(latex, expected):
    assert rendered(latex) == expected


def test_spacing_commands_are_unaffected():
    """`\\quad` and friends are explicit spacing and were never the problem."""
    assert rendered(r"\text{a\quad b}").startswith("a")
    assert " " in rendered(r"\text{a\quad b}")


def test_the_document_still_builds_around_the_change():
    """A whole equation of the kind the page is full of, end to end."""
    body = latex_to_omml_body(r"B = \frac{mg}{qv} = \frac{0.6 \times 10^{-3} \times 10}{25} = 20\text{ T}")
    assert body.count("<m:f>") == 2
    assert rendered(r"B = \frac{mg}{qv} = 20\text{ T}").endswith("20 T")


# -- chemistry structures emitted by page transcription -------------------- #


def test_overset_becomes_an_upper_limit_structure():
    body = latex_to_omml_body(r"\overset{\ominus}{\text{O}}")

    assert "<m:limUpp>" in body
    assert "overset" not in rendered(r"\overset{\ominus}{\text{O}}")


def test_underset_and_substack_become_native_structures():
    latex = r"\text{CH}_3-\underset{\substack{|\\\text{CH}_3}}{\text{N}}-\text{CH}_3"
    body = latex_to_omml_body(latex)

    assert "<m:limLow>" in body
    assert body.count("<m:mr>") == 2
    assert "underset" not in rendered(latex)
    assert "substack" not in rendered(latex)


def test_backslash_is_rendered_as_a_symbol_not_a_command_name():
    text = rendered(r"\text{H}_2\text{N}\backslash\text{C}=\text{NH}")

    assert "\\" in text
    assert "backslash" not in text
