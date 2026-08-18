"""A two-column source should come back as a two-column document.

Every transcriber here returns one linear stream of Markdown — the right reading
order, but not the page's layout — so a textbook set in two columns was arriving
as one long single-column document. The layout is read from the PDF instead, and
the Word section is set to match.

The pages are built rather than fixtured, so what each test asserts is visible in
the test itself: a two-column page is two blocks of lines with a gutter between
them, and a one-column page is one. The cases that are not layouts at all — a
numbered list whose numbers hang in the margin, a chapter tab down the outer
edge — are the ones a gutter alone cannot tell apart, and they are here too.
"""

from __future__ import annotations

from dataclasses import replace

import fitz
import pytest

from app import main, pipeline
from app.columns import page_columns, source_columns
from app.docx_builder import DocxWriter, set_columns

WIDTH, HEIGHT = 612.0, 792.0
LINE = "the quick brown fox jumps over the lazy dog and keeps on running along "


Line = tuple[float, float, str]


def pdf_bytes(*pages: list[list[Line]]) -> bytes:
    """A PDF of these pages, each carrying its groups of lines."""
    document = fitz.open()
    for groups in pages:
        page = document.new_page(width=WIDTH, height=HEIGHT)
        for group in groups:
            for x, y, text in group:
                page.insert_text((x, y), text, fontsize=9)
    return document.tobytes()


def written(*groups: list[Line]) -> fitz.Page:
    """A page carrying these lines, read back the way a real source PDF is read.

    Saved and reopened rather than measured in place: text inserted into a page
    is not in its text blocks until the document has been written out, so a page
    measured directly comes back blank whatever was put on it.
    """
    return fitz.open("pdf", pdf_bytes(list(groups)))[0]


def saved(tmp_path, *pages: list[list[Line]]):
    """The same, as a file on disk, for what reads a PDF by its path."""
    path = tmp_path / "source.pdf"
    path.write_bytes(pdf_bytes(*pages))
    return path


def one_column() -> list[list[Line]]:
    return [column(72, WIDTH - 72)]


def two_columns() -> list[list[Line]]:
    return [column(72, 290), column(322, WIDTH - 72)]


def column(left: float, right: float, top: float = 90.0, bottom: float = 700.0):
    """A band of the page filled with lines of text, as a column of type is.

    Broken into paragraphs, because a column set as one unbroken run of lines is
    read back as a single text block, and one block is not enough for anything
    here to have an opinion about.
    """
    characters = max(4, int((right - left) / 5.5))
    lines: list[Line] = []
    y = top
    while y < bottom:
        for _ in range(6):
            if y >= bottom:
                break
            lines.append((left, y, (LINE * 3)[:characters]))
            y += 11.0
        y += 16.0  # the space between one paragraph and the next
    return lines


def test_one_column_of_text_is_one_column():
    assert page_columns(written(column(72, WIDTH - 72))) == 1


def test_two_columns_of_text_are_two_columns():
    assert page_columns(written(column(72, 290), column(322, WIDTH - 72))) == 2


def test_a_running_head_across_the_top_does_not_close_the_gutter():
    """The head spans the page and hides the gutter under it — the common case."""
    head = [(72.0, 60.0, "Chapter 1  Complex Numbers and Quadratic Equations  47")]
    assert page_columns(written(head, column(72, 290), column(322, WIDTH - 72))) == 2


def test_numbers_hanging_in_the_margin_are_not_a_column():
    """A numbered list has a gutter, a fifth of the way across, and one column."""
    numbers = [(72.0, float(y), f"{index + 1}.") for index, y in enumerate(range(90, 600, 60))]
    body = [
        line
        for index, y in enumerate(range(90, 600, 60))
        for line in column(130, WIDTH - 72, top=y, bottom=y + 40)
    ]
    assert page_columns(written(numbers, body)) == 1


def test_a_gutter_off_centre_is_not_a_two_column_layout():
    assert page_columns(written(column(72, 180), column(220, WIDTH - 72))) == 1


def test_a_blank_page_is_one_column():
    assert page_columns(written()) == 1


# -- what the source is allowed to be asked for ------------------------------- #
#
# The choice offered in the browser is between one flowing column ("natural" —
# what a linear transcription is) and the source's own columns ("multi"). Only
# one of those is ever a choice: a single-column source has no second column for
# the output to put anything in, so it can only come back the way it went in.


def test_natural_keeps_a_two_column_source_in_one_column(tmp_path):
    assert pipeline._column_layout(saved(tmp_path, two_columns()), 1, "natural") == [1]


def test_multi_gives_a_two_column_source_its_columns_back(tmp_path):
    assert pipeline._column_layout(saved(tmp_path, two_columns()), 1, "multi") == [2]


def test_multi_on_a_single_column_source_is_still_one_column(tmp_path):
    """The choice can be asked for; it cannot invent a column that was never set."""
    assert pipeline._column_layout(saved(tmp_path, one_column()), 1, "multi") == [1]


def test_a_page_set_each_way_is_set_each_way(tmp_path):
    pdf = saved(tmp_path, two_columns(), one_column())
    assert pipeline._column_layout(pdf, 2, "multi") == [2, 1]


def test_no_choice_leaves_the_configured_default_in_charge(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "settings", replace(pipeline.settings, columns="off"))
    assert pipeline._column_layout(saved(tmp_path, two_columns()), 1, None) == [1]


def test_the_choice_wins_over_the_configured_default(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "settings", replace(pipeline.settings, columns="off"))
    assert pipeline._column_layout(saved(tmp_path, two_columns()), 1, "multi") == [2]


def test_a_source_that_cannot_be_read_costs_nobody_their_conversion(tmp_path):
    not_a_pdf = tmp_path / "source.pdf"
    not_a_pdf.write_bytes(b"this is not a PDF")
    assert pipeline._column_layout(not_a_pdf, 3, "multi") == [1, 1, 1]


# -- what the upload tells the browser ---------------------------------------- #


def test_the_upload_probe_reports_the_most_columns_any_page_has(tmp_path):
    assert source_columns(saved(tmp_path, one_column(), two_columns())) == 2


def test_a_single_column_source_probes_as_one_column(tmp_path):
    assert source_columns(saved(tmp_path, one_column())) == 1


def test_a_pdf_that_cannot_be_read_probes_as_one_column(tmp_path):
    """The same answer the conversion would fall back to, so nothing is promised twice."""
    path = tmp_path / "source.pdf"
    path.write_bytes(b"this is not a PDF")
    assert source_columns(path) == 1


@pytest.mark.parametrize(
    ("sent", "kept"),
    [
        ("natural", "natural"),
        ("multi", "multi"),
        ("MULTI ", "multi"),
        # Not answers to this question. An empty field is what the form sends
        # when the control was never shown, and both of these leave
        # `PDF2DOCX_COLUMNS` in charge rather than overriding it with a guess.
        ("", ""),
        ("2", ""),
        ("sideways", ""),
    ],
)
def test_only_the_answers_the_browser_can_give_are_taken_from_it(sent, kept):
    assert main._columns_choice(sent) == kept


def test_a_stored_job_remembers_the_choice_and_the_layout_its_source_had():
    job = main.Job(id="a", filename="x.pdf", pages=2, columns="multi", source_columns=2)
    restored = main.Job.from_record(job.to_record())
    assert (restored.columns, restored.source_columns) == ("multi", 2)


def test_a_job_stored_before_any_of_this_existed_still_loads():
    restored = main.Job.from_record({"id": "a", "filename": "x.pdf", "pages": 1})
    assert (restored.columns, restored.source_columns) == ("", 1)


# -- what the writer does with the count -------------------------------------- #


def test_the_section_is_divided_into_the_columns_asked_for():
    writer = DocxWriter()
    writer.start_page(2)
    assert writer.document.sections[-1]._sectPr.xpath("./w:cols/@w:num") == ["2"]


def test_pages_set_the_same_way_stay_in_one_section():
    writer = DocxWriter()
    writer.start_page(2)
    writer.start_page(2)
    assert len(writer.document.sections) == 1


def test_a_page_set_differently_starts_a_section_of_its_own():
    writer = DocxWriter()
    writer.start_page(2)
    writer.start_page(1)
    sections = writer.document.sections
    assert len(sections) == 2
    assert sections[0]._sectPr.xpath("./w:cols/@w:num") == ["2"]
    assert sections[1]._sectPr.xpath("./w:cols/@w:num") == ["1"]


def test_a_figure_is_scaled_to_the_column_and_not_to_the_page():
    writer = DocxWriter()
    writer.start_page(1)
    whole = writer.column
    writer.start_page(2)
    assert writer.column < whole / 2 + 1


def test_the_columns_element_is_edited_in_place_not_appended():
    """`w:sectPr` is an ordered sequence; Word discards it if written out of order."""
    writer = DocxWriter()
    section = writer.document.sections[-1]
    set_columns(section, 2)
    set_columns(section, 3)
    assert len(section._sectPr.xpath("./w:cols")) == 1


@pytest.mark.parametrize("count", [0, -1])
def test_a_nonsense_count_is_taken_as_one_column(count):
    writer = DocxWriter()
    writer.start_page(count)
    assert writer.document.sections[-1]._sectPr.xpath("./w:cols/@w:num") == ["1"]
