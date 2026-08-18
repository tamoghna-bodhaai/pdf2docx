from __future__ import annotations

import pytest

from app.extract_kit import (
    ExtractKitError,
    blocks_to_lines,
    blocks_to_markdown,
    ocr_confidence,
    order_blocks,
    parse_formula_response,
    parse_page_response,
    valid_formula_latex,
)


def page(blocks):
    return parse_page_response({"width": 1000, "height": 1200, "blocks": blocks})


def block(kind, bbox, text="", latex="", confidence=0.9, level=0):
    return {
        "type": kind,
        "bbox": bbox,
        "text": text,
        "latex": latex,
        "confidence": confidence,
        "level": level,
    }


def test_response_validation_and_normalisation():
    result = page([block("HEADING", [10, 20, 900, 70], text="  Title  ", level=9)])
    assert (result.width, result.height) == (1000, 1200)
    assert result.blocks[0].type == "heading"
    assert result.blocks[0].text == "Title"
    assert result.blocks[0].level == 6


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {"width": 0, "height": 10, "blocks": []},
        {"width": 10, "height": 10, "blocks": {}},
        {"width": 10, "height": 10, "blocks": [{"type": "unknown", "bbox": [0, 0, 1, 1]}]},
        {"width": 10, "height": 10, "blocks": [{"type": "text", "bbox": [0, 0, 11, 1]}]},
        {"width": 10, "height": 10, "blocks": [{"type": "text", "bbox": [0, 0, 1, 1], "confidence": 2}]},
    ],
)
def test_malformed_page_responses_are_rejected(payload):
    with pytest.raises(ExtractKitError):
        parse_page_response(payload)


def test_two_columns_are_read_down_left_then_down_right_with_separator():
    result = page([
        block("title", [50, 10, 950, 70], text="Header"),
        block("paragraph", [60, 100, 450, 180], text="L1"),
        block("paragraph", [60, 200, 450, 280], text="L2"),
        block("paragraph", [550, 110, 940, 190], text="R1"),
        block("paragraph", [550, 210, 940, 290], text="R2"),
    ])
    ordered = order_blocks(result)
    assert not ordered.ambiguous
    assert [item.text for item in ordered.blocks] == ["Header", "L1", "L2", "R1", "R2"]


def test_overlapping_irregular_blocks_are_ambiguous():
    result = page([
        block("paragraph", [50, 100, 500, 250], text="one"),
        block("figure", [300, 150, 800, 350], text="two"),
    ])
    ordered = order_blocks(result)
    assert ordered.ambiguous
    assert ordered.reason == "overlapping_blocks"


def test_markdown_and_figure_coordinates_use_existing_contract():
    result = page([
        block("heading", [50, 10, 950, 70], text="Heading", level=2),
        block("formula", [200, 200, 800, 300], latex=r"x=\frac{1}{2}"),
        block("figure", [100, 600, 900, 1000], text="A chart"),
    ])
    markdown = blocks_to_markdown(result, result.blocks)
    assert "## Heading" in markdown
    assert r"$$x=\frac{1}{2}$$" in markdown
    assert "<!--box: 100,500,900,833-->" in markdown


def test_coordinate_scaling_produces_pdf_point_boxes():
    result = page([block("paragraph", [100, 240, 500, 600], text="searchable")])
    lines = blocks_to_lines(result, result.blocks, pdf_width=500, pdf_height=600)
    assert lines[0].bbox == pytest.approx((50, 120, 250, 300))
    assert lines[0].spans[0].bbox == lines[0].bbox


def test_confidence_is_character_weighted():
    result = page([
        block("paragraph", [0, 0, 100, 20], text="a", confidence=0.0),
        block("paragraph", [0, 30, 100, 50], text="bbbbbbbbb", confidence=1.0),
        block("figure", [0, 60, 100, 100], confidence=0.0),
    ])
    assert ocr_confidence(result.blocks) == pytest.approx(0.9)


def test_formula_validation_rejects_blank_incomplete_and_prose():
    assert valid_formula_latex(r"x=\frac{1}{2}")
    assert not valid_formula_latex("")
    assert not valid_formula_latex(r"x=\frac{1}{2")
    assert not valid_formula_latex(r"\text{The velocity of the particle}")


def test_formula_batch_response_has_exact_cardinality():
    assert parse_formula_response({"latex": ["x=1", ""]}, 2) == ["x=1", None]
    with pytest.raises(ExtractKitError):
        parse_formula_response({"latex": ["x=1"]}, 2)
    with pytest.raises(ExtractKitError):
        parse_formula_response({"latex": [3]}, 1)
