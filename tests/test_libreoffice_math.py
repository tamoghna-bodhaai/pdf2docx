"""Constructs that Word draws correctly and LibreOffice draws as damage.

Every case here was found the same way: convert the .docx with
`libreoffice --headless --convert-to pdf` and read the page. LibreOffice's OMML
import is the stricter of the two — it takes each run structurally, and anything
it cannot resolve is drawn as `¿`, the inverted question mark it uses for a
missing operand. Word resolves the same markup silently, so none of these are
visible in the XML or in Word, and all of them are visible to whoever opens the
document on Linux.

The fixes are all of one kind: say what the character *is* rather than leaving
it to be interpreted. A relation opening a line, a bracket that opens nothing, a
bar that means "such that" — each is marked as literal text, which is upright in
mathematics anyway, so nothing about the equation looks different for it.
"""

from __future__ import annotations

import re

import pytest

from app.latex_omml import latex_to_omml_body

TEXT_RE = re.compile(r"<m:t[^>]*>(.*?)</m:t>", re.S)
# A run marked as literal text: `<m:nor/>` in its properties.
LITERAL_RUN_RE = re.compile(
    r'<m:r><m:rPr>(?:(?!</m:r>).)*?<m:nor/>.*?<m:t[^>]*>(.*?)</m:t></m:r>', re.S
)


def rendered(latex: str) -> str:
    return "".join(TEXT_RE.findall(latex_to_omml_body(latex)))


def literals(latex: str) -> list[str]:
    return LITERAL_RUN_RE.findall(latex_to_omml_body(latex))


# -- text mode carries its brackets ------------------------------------------ #
#
# LibreOffice imports an `<m:nor/>` run as StarMath literal text, escapes the
# characters StarMath gives meaning to, and then prints the escape instead of
# consuming it. `\text{(a) arg}` — how a multiple-choice option is labelled —
# reached the page as `\(a\) arg`.


@pytest.mark.parametrize(
    ("latex", "expected"),
    [
        (r"\text{(a) arg}", "(a) arg"),
        (r"\text{(a) 23}", "(a) 23"),
        (r"\mathrm{f(x)}", "f(x)"),
        (r"\text{a {b} c}", "a {b} c"),
        (r'\text{say "no"}', 'say "no"'),
    ],
)
def test_text_mode_brackets_survive(latex, expected):
    assert rendered(latex) == expected


def test_brackets_leave_the_literal_run_rather_than_being_escaped_in_it():
    """The words are literal text; the brackets are ordinary runs beside them."""
    assert literals(r"\text{(a) arg}") == ["a", " arg"]


def test_a_word_with_no_brackets_is_still_one_run():
    assert literals(r"\text{radius of path}") == ["radius of path"]


# -- alignment cells --------------------------------------------------------- #
#
# `aligned` puts the alignment marker exactly where the relation is, so in a
# column of worked steps it is the cells, not the equation, that open with a
# relation whose left operand is the line above. LibreOffice read each as an
# operator with a missing operand, drew `¿` for the operand and dropped the
# relation: `x &= 1` came out as `x ¿1`.


@pytest.mark.parametrize(
    "latex",
    [
        r"\begin{aligned} x &= 1 \\ y &= 2 \end{aligned}",
        r"\begin{aligned} \arg(z) &= \frac{\pi}{6} \end{aligned}",
    ],
)
def test_a_cell_opening_with_a_relation_keeps_it(latex):
    assert "=" in literals(latex)


def test_the_relation_is_still_there_to_be_read():
    assert rendered(r"\begin{aligned} x &= 1 \end{aligned}") == "x=1"


# -- rows of unequal length -------------------------------------------------- #
#
# `aligned` lets a line carry fewer alignment markers than its neighbours; `m:m`
# does not. LibreOffice answered a short row by dropping every cell it did hold
# and drawing `¿` in each place, which cost the row its whole content.


def test_every_row_is_given_the_full_count_of_cells():
    latex = r"\begin{aligned} a &= b \\ c \\ &= d \end{aligned}"
    body = latex_to_omml_body(latex)
    count = int(re.search(r'<m:count m:val="(\d+)"/>', body).group(1))
    for row in re.findall(r"<m:mr>(.*?)</m:mr>", body, re.S):
        assert row.count("<m:e>") + row.count("<m:e/>") == count


def test_a_padded_cell_is_a_space_and_not_an_empty_element():
    """An empty `<m:e/>` is drawn as a missing operand, which is the `¿` again."""
    body = latex_to_omml_body(r"\begin{aligned} a &= b \\ c \end{aligned}")
    assert "<m:e/>" not in body
    assert " " in literals(r"\begin{aligned} a &= b \\ c \end{aligned}")


def test_a_line_that_opens_with_its_alignment_marker_keeps_its_content():
    rows = re.findall(
        r"<m:mr>(.*?)</m:mr>",
        latex_to_omml_body(r"\begin{aligned} a &= b \\ &= c \end{aligned}"),
        re.S,
    )
    assert "c" in "".join(TEXT_RE.findall(rows[1]))


# -- delimiters standing alone ----------------------------------------------- #
#
# A transcription ending `\leq |z_1| + |z_2|]` has a bracket that opens nothing,
# and a bar meaning "such that" is not the side of a modulus. LibreOffice read
# both structurally: the bracket became one whose operand never came and was
# drawn as `¿`, and the bar became the logical `or` and was drawn as `∨`.


def test_an_unmatched_closing_bracket_is_drawn_as_itself():
    assert "]" in literals(r"|z_1| + |z_2|]")


def test_an_unmatched_bar_is_drawn_as_itself():
    assert "|" in literals(r"x | y > 0")


def test_a_matched_pair_of_bars_is_still_a_modulus():
    body = latex_to_omml_body(r"|z|")
    assert '<m:begChr m:val="|"/>' in body
    assert "|" not in literals(r"|z|")
