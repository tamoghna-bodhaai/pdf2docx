"""The second look at where a page's figures are.

The transcription's own boxes are its weakest output — measured against boxes
read off a real page by hand they average about 0.13 IoU, which crops the
paragraph beside the diagram rather than the diagram. Asking again, on a page
with a coordinate grid ruled over it and with nothing else to do but look,
measured about 0.62.

None of that can be tested without a model, so what is tested here is
everything around it: that the grid gets drawn without touching the source PDF,
that a reply is read in whatever units it arrives in, that boxes are matched to
figures by reading order, and — most of all — that every way this can fail
leaves the page exactly as it would have been without it.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest

from app import locate
from app.config import settings

FIGURE = "> _Figure: a diagram_ <!--box: 100,100,300,300-->"
SECOND = "> _Figure: another_ <!--box: 400,400,600,600-->"
PAGE = f"Some text.\n\n{FIGURE}\n\nMore text.\n"


class StubClient:
    """Stands in for the OpenAI client, recording what it was asked."""

    def __init__(self, reply: str = "", error: Exception | None = None, cost: float = 0.0):
        self.reply, self.error, self.cost = reply, error, cost
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.reply))],
            usage=SimpleNamespace(model_dump=lambda: {"cost": self.cost}),
        )


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    """Pin the settings this pass reads; `Settings` is frozen, so replace it."""

    def configure(**overrides):
        pinned = {"locate_figures": True, "figure_model": "", **overrides}
        monkeypatch.setattr(locate, "settings", replace(settings, **pinned))

    configure()
    return configure


def boxes_in(markdown: str) -> list[str]:
    return [match.group("box") for match in map(locate.FIGURE_RE.match, markdown.splitlines()) if match]


# -- ruling the grid --------------------------------------------------------- #


def test_the_grid_is_drawn_without_touching_the_source(scan, tmp_path):
    """The overlay is for the model to read; the PDF it came from is evidence."""
    pdf = scan()
    before = pdf.read_bytes()
    image, render = locate.gridded_page(pdf, 0)

    assert pdf.read_bytes() == before
    assert image[:8] == b"\x89PNG\r\n\x1a\n"
    pixmap = fitz.Pixmap(image)
    assert (pixmap.width, pixmap.height) == render
    assert pixmap.n >= 3, "the rules are coloured, so the page cannot be greyscale"


def test_the_grid_actually_marks_the_page(scan, tmp_path):
    """A ruled page must differ from a plain one, or the model is reading nothing."""
    pdf = scan()
    ruled = fitz.Pixmap(locate.gridded_page(pdf, 0)[0])
    with fitz.open(pdf) as doc:
        plain = doc.load_page(0).get_pixmap(matrix=fitz.Matrix(ruled.width / 595.0, ruled.width / 595.0))
    assert ruled.samples != plain.samples


# -- reading the reply ------------------------------------------------------- #


def test_boxes_are_read_off_a_well_formed_reply():
    reply = "100,200,300,400 | a ray diagram\n500,600,700,800 | a circuit"
    assert locate.parse_boxes(reply, (1000, 1000)) == [
        (100.0, 200.0, 300.0, 400.0),
        (500.0, 600.0, 700.0, 800.0),
    ]


def test_a_reply_in_pixels_is_converted_like_any_other_box():
    """Same ambiguity as the transcription's boxes, resolved the same way."""
    boxes = locate.parse_boxes("744,1052,1116,1579 | a diagram", (1488, 2105))
    assert boxes[0][0] == pytest.approx(500.0, abs=1)
    assert boxes[0][1] == pytest.approx(500.0, abs=1)


@pytest.mark.parametrize(
    "reply",
    ["", "I could not find any figures.", "not,numbers,at,all", "1,2,3"],
)
def test_a_reply_with_no_usable_boxes_yields_none(reply):
    assert locate.parse_boxes(reply, (1000, 1000)) == []


def test_a_bulleted_reply_is_still_read():
    """Models add list markers however firmly they are told not to."""
    assert len(locate.parse_boxes("- 100,200,300,400 | a diagram", (1000, 1000))) == 1


# -- matching boxes to figures ----------------------------------------------- #


def test_equal_counts_are_paired_off_in_order():
    """Both passes list figures in reading order; that agreement beats coordinates."""
    hints = [(0.0, 0.0, 10.0, 10.0), (900.0, 900.0, 950.0, 950.0)]
    located = [(1.0, 1.0, 11.0, 11.0), (800.0, 800.0, 900.0, 900.0)]
    assert locate.align(hints, located) == located


def test_an_extra_located_figure_does_not_shift_the_others():
    """The locator found one the transcription missed; order still has to hold."""
    hints = [(0.0, 0.0, 10.0, 10.0), (900.0, 900.0, 950.0, 950.0)]
    located = [(1.0, 1.0, 11.0, 11.0), (500.0, 500.0, 520.0, 520.0), (890.0, 890.0, 940.0, 940.0)]
    assert locate.align(hints, located) == [located[0], located[2]]


def test_a_figure_with_no_box_left_is_handed_back_unassigned(scan):
    """None, not the hint — the caller has to know which boxes were located."""
    hints = [(0.0, 0.0, 10.0, 10.0), (900.0, 900.0, 950.0, 950.0)]
    located = [(1.0, 1.0, 11.0, 11.0)]
    assert locate.align(hints, located) == [located[0], None]


def test_a_badly_placed_hint_does_not_steal_the_next_figure_s_box():
    """The real page this was built for, and the reason matching is not greedy.

    Three figures were transcribed and four located. The second hint is far from
    where its figure actually is — which is the whole reason for locating again —
    and it sits nearer the third located box than the second. Taking the nearest
    box for each figure in turn hands it that one, and the last figure is left
    with nothing; aligning the sequences as a whole keeps them in step.
    """
    hints = [(631, 316, 787, 440), (554, 586, 664, 668), (757, 688, 852, 836)]
    located = [(141, 280, 354, 350), (636, 350, 781, 425), (636, 550, 863, 645), (672, 640, 778, 725)]

    assert locate.align(hints, located) == [located[1], located[2], located[3]]


def test_matching_never_runs_backwards():
    """Assignments only ever move forward, so reading order cannot be inverted."""
    hints = [(900.0, 900.0, 950.0, 950.0), (0.0, 0.0, 10.0, 10.0)]
    located = [(0.0, 0.0, 10.0, 10.0), (900.0, 900.0, 950.0, 950.0), (950.0, 950.0, 980.0, 980.0)]
    result = locate.align(hints, located)
    assert located.index(result[0]) < located.index(result[1])


# -- the whole pass ---------------------------------------------------------- #


def test_a_located_box_replaces_the_transcription_s(scan):
    client = StubClient("200,300,400,500 | a diagram")
    markdown, _ = locate.relocate_figures(scan(), 0, PAGE, client)

    assert boxes_in(markdown) == ["200,300,400,500"]
    assert "_Figure: a diagram_" in markdown, "the caption is the transcription's"
    assert markdown.startswith("Some text."), "the rest of the page is untouched"


def test_the_blockquote_marker_survives_the_rewrite(scan):
    markdown, _ = locate.relocate_figures(scan(), 0, PAGE, StubClient("200,300,400,500 | x"))
    figure = [line for line in markdown.splitlines() if "box:" in line][0]
    assert figure.startswith("> _Figure:")


def test_a_page_with_no_figures_is_never_sent(scan):
    """No figures, no second look, no charge."""
    client = StubClient("100,100,200,200 | a diagram")
    markdown, cost = locate.relocate_figures(scan(), 0, "Just text.\n", client)

    assert markdown == "Just text.\n"
    assert client.calls == [] and cost == 0.0


def test_the_pass_can_be_switched_off(scan, enabled):
    enabled(locate_figures=False)
    client = StubClient("200,300,400,500 | a diagram")
    markdown, cost = locate.relocate_figures(scan(), 0, PAGE, client)

    assert markdown == PAGE
    assert client.calls == [] and cost == 0.0


def test_a_failed_request_leaves_the_page_as_it_was(scan):
    """The transcription's boxes are a working fallback; the page still converts."""
    client = StubClient(error=RuntimeError("the provider is down"))
    markdown, cost = locate.relocate_figures(scan(), 0, PAGE, client)

    assert markdown == PAGE
    assert cost == 0.0


def test_an_unreadable_reply_leaves_the_boxes_alone(scan):
    markdown, _ = locate.relocate_figures(scan(), 0, PAGE, StubClient("I see no figures."))
    assert boxes_in(markdown) == ["100,100,300,300"]


def test_a_kept_box_is_brought_onto_the_grid_with_the_rest(scan):
    """A leftover box in pixels would drag the located ones out of the grid.

    The units of a page are decided from all of its boxes together, so the one
    figure the locator did not reach cannot be left in a different system from
    the one it did.
    """
    # Two figures, boxes in the render's pixels; the locator finds only the first.
    page = "> _Figure: one_ <!--box: 744,1052,1116,1579-->\n\n> _Figure: two_ <!--box: 1200,1600,1400,1800-->"
    markdown, _ = locate.relocate_figures(scan(), 0, page, StubClient("100,100,200,200 | one"))

    kept = boxes_in(markdown)[1]
    assert all(float(value) <= 1000 for value in kept.split(",")), f"still in pixels: {kept}"


def test_the_cost_of_the_extra_request_is_reported(scan):
    _, cost = locate.relocate_figures(scan(), 0, PAGE, StubClient("1,2,3,4 | x", cost=0.0123))
    assert cost == pytest.approx(0.0123)


def test_the_request_is_budgeted_for_a_model_that_reasons(scan):
    """A short budget looks sufficient and is not.

    The answer is a handful of short lines, so 1000 tokens was allowed for it.
    On a reasoning model that budget goes on the reasoning: the reply came back
    truncated mid-box, or as a fragment of the thinking itself, and the page
    scored below doing nothing at all. The budget is for the thinking.
    """
    client = StubClient("200,300,400,500 | a diagram")
    locate.relocate_figures(scan(), 0, PAGE, client)
    assert client.calls[0]["max_tokens"] >= 4000


def test_a_truncated_box_is_not_half_read(scan):
    """A reply cut off mid-number must lose that box, not invent one."""
    client = StubClient("100,200,300,400 | a diagram\n605,345,7")
    assert locate.parse_boxes(client.reply, (1000, 1000)) == [(100.0, 200.0, 300.0, 400.0)]


def test_the_configured_figure_model_wins(scan, enabled):
    enabled(figure_model="some/other-model")
    client = StubClient("200,300,400,500 | a diagram")
    locate.relocate_figures(scan(), 0, PAGE, client, model="the/job-model")
    assert client.calls[0]["model"] == "some/other-model"


def test_otherwise_the_job_s_own_model_is_used(scan):
    client = StubClient("200,300,400,500 | a diagram")
    locate.relocate_figures(scan(), 0, PAGE, client, model="the/job-model")
    assert client.calls[0]["model"] == "the/job-model"


def test_two_figures_are_both_replaced(scan):
    page = f"{FIGURE}\n\n{SECOND}"
    client = StubClient("10,20,30,40 | one\n50,60,70,80 | two")
    markdown, _ = locate.relocate_figures(scan(), 0, page, client)
    assert boxes_in(markdown) == ["10,20,30,40", "50,60,70,80"]
