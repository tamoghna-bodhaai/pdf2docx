"""What the browser makes of Mathpix Markdown.

Mathpix returns MMD: Markdown with LaTeX still standing in it. Headings arrive
as `\\section*{...}`, question numbering as `\\begin{itemize}\\item[(a)]`,
pictures and tables inside float environments. None of that is Markdown, so
until `app/static/mmd.js` existed all of it reached the page as literal
backslashes in the middle of the text — which is what a reader saw instead of
the document.

These run the real files the page loads, under node, because a reimplementation
of the converter in Python would test the reimplementation. The rules being held
to are the two the converter is built on: mathematics is passed through
untouched, and nothing is deleted for being unrecognised.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

RENDERER = Path(__file__).parent / "render_mmd.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def render(source: str, markdown: bool = False) -> str:
    """The HTML the browser would show for `source`, or the Markdown behind it."""
    argv = ["node", str(RENDERER)] + (["--markdown"] if markdown else [])
    done = subprocess.run(argv, input=source, capture_output=True, text=True, check=True)
    return done.stdout


# -- headings -------------------------------------------------------------------- #


def test_a_section_becomes_a_heading():
    assert "<h2>Electric Charges</h2>" in render(r"\section*{Electric Charges}")


def test_the_heading_levels_descend():
    html = render("\\title{One}\n\n\\section*{Two}\n\n\\subsection*{Three}")
    assert "<h1>One</h1>" in html
    assert "<h2>Two</h2>" in html
    assert "<h3>Three</h3>" in html


def test_a_break_inside_a_heading_stays_a_break():
    """Mathpix runs a multi-line title together with `\\\\`, not with newlines."""
    html = render(r"\section*{PHYSICS PAPER-1 \\ Class 12 \\ (Solved)}")
    assert "PHYSICS PAPER-1 <br> Class 12 <br> (Solved)" in html


# -- lists ----------------------------------------------------------------------- #


def test_an_itemize_becomes_a_list():
    html = render("\\begin{itemize}\n\\item[(a)] zero\n\\item[(b)] one\n\\end{itemize}")
    assert html.count("<li>") == 2
    assert "\\item" not in html
    assert "\\begin" not in html


def test_an_item_keeps_the_label_mathpix_gave_it():
    """`(a)`, `(ii)`, `3.` are the question numbers. A disc is not a substitute."""
    html = render("\\begin{itemize}\n\\item[(iii)] answer\n\\end{itemize}")
    assert '<span class="mmd-item-label">(iii)</span>' in html


def test_a_nested_itemize_nests():
    html = render(
        "\\begin{itemize}\n"
        "\\item[(a)] outer\n"
        "\\begin{itemize}\n\\item[(i)] inner\n\\end{itemize}\n"
        "\\end{itemize}"
    )
    assert "<ul>" in html
    # The inner list opens before the outer item closes, which is what nesting is.
    assert html.index("<ul>", html.index("outer")) < html.index("</li>", html.index("outer"))
    assert "inner" in html


def test_a_list_left_open_by_a_page_break_still_renders_its_items():
    """Pages are split on `\\pagebreak`, so one page routinely inherits an
    environment that another page closes."""
    html = render("\\begin{itemize}\n\\item[(a)] first\n\\item[(b)] second")
    assert html.count("<li>") == 2
    assert "second" in html


def test_an_orphaned_end_does_not_reach_the_page():
    assert "itemize" not in render("\\end{itemize}\n\nplain text")


def test_a_list_that_declares_no_items_yields_no_list():
    """Mathpix emits these around a page break; an empty bullet is not content."""
    html = render("\\begin{itemize}\n\\begin{itemize}\n\\end{itemize}\n\\end{itemize}")
    assert "<li>" not in html


# -- mathematics ----------------------------------------------------------------- #


def test_mathematics_reaches_katex_exactly_as_mathpix_wrote_it():
    assert "$\\frac{r_{0}}{2}$" in render(r"the answer is $\frac{r_{0}}{2}$ exactly")


def test_markdown_cannot_reinterpret_subscripts_as_emphasis():
    """`$q_{1} ... q_{2}$` is one equation, not a pair of underscores."""
    html = render(r"charges $q_{1}=+3 \mu C$ and $q_{2}=-3 \mu C$ are placed")
    assert "<em>" not in html
    assert "$q_{1}=+3 \\mu C$" in html


def test_display_mathematics_survives_a_document_command_beside_it():
    html = render("\\section*{Result}\n\n$$E = m c^{2}$$")
    assert "$$E = m c^{2}$$" in html


def test_an_environment_inside_mathematics_is_left_to_katex():
    html = render(r"$$\begin{aligned} a &= b \\ c &= d \end{aligned}$$")
    assert r"\begin{aligned}" in html
    assert "<li>" not in html


def test_an_unpaired_dollar_does_not_swallow_the_document():
    html = render("costs $5 for the first\n\nand the second paragraph survives")
    assert "second paragraph survives" in html


# -- figures and tables ---------------------------------------------------------- #


def test_a_figure_becomes_an_image_and_a_caption():
    html = render(
        "\\begin{figure}\n"
        "\\includegraphics[alt={},max width=\\textwidth]{mathpix/images/a.jpg}\n"
        "\\captionsetup{labelformat=empty}\n"
        "\\caption{Figure 1}\n"
        "\\end{figure}"
    )
    assert 'src="mathpix/images/a.jpg"' in html
    assert '<p class="mmd-caption">Figure 1</p>' in html
    assert "captionsetup" not in html


def test_an_image_reference_is_left_as_markdown_for_the_caller_to_repoint():
    """The page rewrites relative image paths to the job's asset route, and only
    recognises them written as Markdown."""
    markdown = render(r"\includegraphics{images/a.jpg}", markdown=True)
    assert "![](images/a.jpg)" in markdown


def test_a_tabular_becomes_a_table():
    html = render(
        "\\begin{tabular}[t]{|l|l|}\n"
        "\\hline Speed of light & $3 \\times 10^{8}$ \\\\\n"
        "\\hline Charge & $1.6 \\times 10^{-19}$ \\\\\n"
        "\\hline\n"
        "\\end{tabular}"
    )
    assert html.count("<tr>") == 2
    assert "<td>Speed of light</td>" in html
    assert "$3 \\times 10^{8}$" in html
    assert "hline" not in html


def test_a_wide_table_scrolls_inside_its_own_box():
    """Otherwise the page itself slides sideways on a phone."""
    assert '<div class="mmd-table">' in render(
        "\\begin{tabular}{ll}\na & b \\\\\n\\end{tabular}"
    )


def test_a_multicolumn_cell_spans_its_columns():
    html = render("\\begin{tabular}{ll}\n\\multicolumn{2}{c}{Total} \\\\\n\\end{tabular}")
    assert '<td colspan="2">Total</td>' in html


# -- text ------------------------------------------------------------------------ #


def test_every_line_mathpix_writes_is_a_line():
    """Mathpix does not wrap a paragraph, so its newlines are all deliberate —
    four answer options are four lines, not one run-on sentence."""
    html = render("(a) $10 \\Omega$\n(b) $20 \\Omega$\n(c) $25 \\Omega$")
    assert html.count("<br>") == 2


def test_a_page_break_separates_the_pages_it_sits_between():
    html = render("end of one\n\\pagebreak\nstart of two")
    assert "end of one" in html and "start of two" in html
    assert "pagebreak" not in html


def test_emphasis_commands_become_emphasis():
    html = render(r"\textbf{Assertion} and \textit{Reason}")
    assert "<strong>Assertion</strong>" in html
    assert "<em>Reason</em>" in html


def test_an_escaped_character_loses_its_backslash():
    # `&` arrives HTML-escaped, which is the same character once the browser
    # has it — and what KaTeX reads back out of the text node.
    assert "100% of 5 &amp; 6" in render(r"100\% of 5 \& 6")


def test_an_unrecognised_command_is_left_on_the_page_rather_than_deleted():
    """Visible and reportable beats a sentence quietly missing a word."""
    assert "invented" in render(r"before \invented{something} after")


def test_html_in_the_document_cannot_become_html_in_a_table_cell():
    html = render("\\begin{tabular}{l}\n<img src=x onerror=alert(1)> \\\\\n\\end{tabular}")
    assert "onerror" not in html or "&lt;img" in html


# -- a whole document ------------------------------------------------------------ #


# Every construct Mathpix was observed to emit for a question paper, in the shapes
# it emits them: the float wrappers, the nesting, and the malformed nesting it
# produces around a page break.
DOCUMENT = r"""\section*{PHYSICS PAPER-1 \\ Class 12}

Maximum Marks: 70 Time Allotted: Three Hours

Instructions to Candidates:
\begin{itemize}
\item[1.] Answer all questions.
\item[2.] The intended marks are given in brackets [].
\end{itemize}

\section*{SECTION-A (14 MARKS)}

Question 1
\begin{itemize}
\item[(i)] No current flows through the $5 \Omega$ resistor. The value of $X$ is: [1]
\end{itemize}

\begin{figure}
\includegraphics[alt={},max width=\textwidth]{mathpix/images/circuit.jpg}
\captionsetup{labelformat=empty}
\caption{Figure 1}
\end{figure}
(a) $10 \Omega$
(b) $20 \Omega$
(c) $25 \Omega$
(d) $45 \Omega$
\begin{itemize}
\item[(ii)] The relative permeability of ' $X$ ' is slightly less than one. Then: [1]
\begin{itemize}
\item[(a)] ' $X$ ' is diamagnetic.
\item[(b)] ' $X$ ' is paramagnetic.
\end{itemize}
\end{itemize}
\pagebreak

\begin{itemize}
\begin{itemize}
\item[(c)] Neither of these.
\end{itemize}
\end{itemize}

\section*{USEFUL CONSTANTS}

\begin{table}
\captionsetup{labelformat=empty}
\caption{USEFUL CONSTANTS AND RELATIONS}
\begin{tabular}[t]{|l|l|l|}
\hline 1. & Speed of light in vacuum & $3 \times 10^{8} \mathrm{~ms}^{-1}$ \\
\hline 2. & Charge of a proton & $1.6 \times 10^{-19} \mathrm{C}$ \\
\hline
\end{tabular}
\end{table}

$$\begin{aligned} F &= q E \\ E &= \frac{k Q}{r^{2}} \end{aligned}$$
"""


def test_a_whole_document_leaves_no_latex_command_on_the_page():
    """The failure all of this exists for: commands reaching the reader as text."""
    markdown = render(DOCUMENT, markdown=True)
    assert not re.findall(r"\\[a-zA-Z]+", markdown), markdown


def test_a_whole_document_keeps_every_piece_of_it():
    html = render(DOCUMENT)
    assert "<h2>SECTION-A (14 MARKS)</h2>" in html
    assert '<span class="mmd-item-label">(ii)</span>' in html
    assert 'src="mathpix/images/circuit.jpg"' in html
    assert "<td>Speed of light in vacuum</td>" in html
    assert r"$$\begin{aligned} F &amp;= q E \\ E &amp;= \frac{k Q}{r^{2}} \end{aligned}$$" in html
