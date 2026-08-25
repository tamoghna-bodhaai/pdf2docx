"""Fitting Mathpix's .docx to the measure it is laid out in.

The cases here are written against the three things a real Mathpix export was
measured doing — sizing images at their crop resolution over 96 DPI, pinning
tables to an absolute grid with no declared widths, and leaving long equations
nowhere to wrap — rather than against the module's own idea of them.
"""

from __future__ import annotations

import re
import struct
import zipfile
from io import BytesIO

import pytest

from app.docx_fit import (
    EMU_PER_INCH,
    Fit,
    fit_docx,
    image_pixels,
    measure_twips,
    render_dpi,
    source_layout,
)

# Mathpix's own section: US Letter, 1.25in side margins, so a 6.00in measure.
SECTION = (
    '<w:sectPr><w:pgSz w:w="12240" w:h="15840" w:orient="portrait"/>'
    '<w:pgMar w:top="1440" w:right="1800" w:bottom="1440" w:left="1800"'
    ' w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>'
)

RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/'
    '2006/relationships/image" Target="media/image1.png"/>'
    '</Relationships>'
)


def png(width: int, height: int) -> bytes:
    """The smallest thing that reports a size the way a real PNG does."""
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II", width, height)
        + b"\x08\x06\x00\x00\x00"
    )


def drawing(cx: int, cy: int, embed: str = "rId1") -> str:
    return (
        "<w:drawing><wp:inline>"
        f'<wp:extent cx="{cx}" cy="{cy}"/>'
        "<a:graphic><a:graphicData><pic:pic><pic:blipFill>"
        f'<a:blip r:embed="{embed}"/></pic:blipFill>'
        f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        "</pic:spPr></pic:pic></a:graphicData></a:graphic>"
        "</wp:inline></w:drawing>"
    )


# The prefixes a real `document.xml` declares, so the fixtures parse as XML and
# a malformed edit shows up as one.
NAMESPACES = " ".join(
    f'xmlns:{prefix}="{uri}"'
    for prefix, uri in (
        ("w", "http://schemas.openxmlformats.org/wordprocessingml/2006/main"),
        ("m", "http://schemas.openxmlformats.org/officeDocument/2006/math"),
        ("a", "http://schemas.openxmlformats.org/drawingml/2006/main"),
        ("r", "http://schemas.openxmlformats.org/officeDocument/2006/relationships"),
        ("wp", "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"),
        ("pic", "http://schemas.openxmlformats.org/drawingml/2006/picture"),
    )
)


# The same page already in two columns, so the narrower measure can be tested
# without going through the layout derivation to get there.
COLUMN_SECTION = SECTION.replace(
    "</w:sectPr>", '<w:cols w:num="2" w:space="360"/></w:sectPr>'
)


def document(body: str, section: str = SECTION) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f"<w:document {NAMESPACES}><w:body>" + body + section + "</w:body></w:document>"
    )


def package(
    body: str,
    media: dict[str, bytes] | None = None,
    settings: str = "",
    section: str = SECTION,
    styles: str = "",
) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", document(body, section))
        archive.writestr("word/_rels/document.xml.rels", RELS)
        if settings:
            archive.writestr("word/settings.xml", settings)
        if styles:
            archive.writestr("word/styles.xml", styles)
        for name, data in (media or {}).items():
            archive.writestr(f"word/media/{name}", data)
    return buffer.getvalue()


def read(data: bytes, name: str = "word/document.xml") -> str:
    with zipfile.ZipFile(BytesIO(data)) as archive:
        return archive.read(name).decode("utf-8")


def extents(xml: str) -> list[tuple[int, int]]:
    return [
        (int(cx), int(cy))
        for cx, cy in re.findall(r'<wp:extent cx="(\d+)" cy="(\d+)"/>', xml)
    ]


LETTER = [(612.0, 792.0)]
LINES_150 = {"pages": [{"page": 1, "image_width": 1275, "image_height": 1650}]}


# --- the resolution the images were actually cropped at ----------------------


def test_render_dpi_is_the_page_render_against_the_page_in_points():
    # 1275 pixels across 8.5 inches of paper is a 150 DPI render, which is the
    # resolution every crop off that page was taken at.
    assert render_dpi(LINES_150, LETTER) == pytest.approx(150.0)


def test_the_key_mathpix_actually_uses_is_read():
    # A real export names this `page_width`; the fixtures elsewhere in this
    # repository call the same number `image_width`. Both have to work.
    real = {"pages": [{"page": 1, "page_width": 2250, "page_height": 2875}]}
    assert render_dpi(real, [(648.0, 828.0)]) == pytest.approx(250.0)


def test_render_dpi_takes_the_median_so_one_odd_page_cannot_resize_a_document():
    lines = {
        "pages": [
            {"page": 1, "image_width": 1275},
            {"page": 2, "image_width": 1275},
            {"page": 3, "image_width": 2550},
        ]
    }
    assert render_dpi(lines, LETTER * 3) == pytest.approx(150.0)


@pytest.mark.parametrize(
    "lines",
    [
        None,
        {},
        {"pages": "not a list"},
        {"pages": [{"page": 1}]},
        {"pages": [{"page": 1, "image_width": 0}]},
        # A ratio outside any plausible render is a misread, not a document.
        {"pages": [{"page": 1, "image_width": 40}]},
        {"pages": [{"page": 1, "image_width": 400000}]},
    ],
)
def test_unusable_geometry_reports_no_resolution_rather_than_a_guess(lines):
    assert render_dpi(lines, LETTER) == 0.0


def test_a_page_out_of_range_is_ignored_rather_than_matched_by_position():
    lines = {"pages": [{"page": 9, "image_width": 1275}]}
    assert render_dpi(lines, LETTER) == 0.0


# --- the measure everything is fitted to -------------------------------------


def test_measure_is_read_from_the_document_rather_than_assumed():
    assert measure_twips(document("").encode()) == 8640


def test_measure_follows_the_columns_a_section_is_already_in():
    section = SECTION.replace(
        "</w:sectPr>", '<w:cols w:num="2" w:space="360"/></w:sectPr>'
    )
    xml = document("").replace(SECTION, section).encode()
    assert measure_twips(xml) == (8640 - 360) // 2


def test_a_document_without_a_section_falls_back_rather_than_failing():
    assert measure_twips(b"<w:document><w:body/></w:document>") == 8640


# --- images -------------------------------------------------------------------


def test_an_image_is_restored_to_the_size_it_occupied_on_the_page():
    # Mathpix sizes a 600px crop at 600/96 = 6.25in. Cropped at 150 DPI it was
    # really 4.00in wide, and the aspect ratio is Mathpix's to keep.
    body = drawing(int(6.25 * EMU_PER_INCH), int(3.125 * EMU_PER_INCH))
    data, fit = fit_docx(
        package(body, {"image1.png": png(600, 300)}), page_sizes=LETTER, lines=LINES_150
    )
    (cx, cy), = extents(read(data))
    assert cx == pytest.approx(4.00 * EMU_PER_INCH, rel=1e-3)
    assert cy == pytest.approx(2.00 * EMU_PER_INCH, rel=1e-3)
    assert fit.images_resized == 1
    assert fit.render_dpi == pytest.approx(150.0)


def test_the_picture_transform_is_kept_in_step_with_the_layout_size():
    # `a:ext` disagreeing with `wp:extent` draws the bitmap at one size inside a
    # frame of another, which is a worse defect than the one being fixed.
    body = drawing(int(6.25 * EMU_PER_INCH), int(3.125 * EMU_PER_INCH))
    data, _ = fit_docx(
        package(body, {"image1.png": png(600, 300)}), page_sizes=LETTER, lines=LINES_150
    )
    xml = read(data)
    assert re.findall(r'<a:ext cx="(\d+)" cy="(\d+)"/>', xml) == [
        (str(int(round(4.00 * EMU_PER_INCH))), str(int(round(2.00 * EMU_PER_INCH))))
    ]


def test_only_the_outer_frame_of_a_grouped_drawing_is_resized():
    # A group carries an `a:ext` per shape. The first is the frame being resized;
    # the rest are positions inside it and mean something else entirely.
    inner = '<a:ext cx="111" cy="222"/>'
    body = drawing(int(6.25 * EMU_PER_INCH), int(3.125 * EMU_PER_INCH)).replace(
        "</pic:spPr>", inner + "</pic:spPr>"
    )
    data, _ = fit_docx(
        package(body, {"image1.png": png(600, 300)}), page_sizes=LETTER, lines=LINES_150
    )
    assert '<a:ext cx="111" cy="222"/>' in read(data)


def test_an_image_is_never_left_wider_than_the_measure():
    # A genuinely huge crop: 1800px at 150 DPI is 12in, and the measure is 6.
    body = drawing(int(18.75 * EMU_PER_INCH), int(9.375 * EMU_PER_INCH))
    data, fit = fit_docx(
        package(body, {"image1.png": png(1800, 900)}), page_sizes=LETTER, lines=LINES_150,
        side_margin_inches=0,
    )
    (cx, _), = extents(read(data))
    assert cx == 6 * EMU_PER_INCH
    assert fit.images_capped == 1


def test_without_geometry_an_oversized_image_is_capped_rather_than_restored():
    body = drawing(int(9.0 * EMU_PER_INCH), int(4.5 * EMU_PER_INCH))
    data, fit = fit_docx(
        package(body, {"image1.png": png(864, 432)}), lines=None, side_margin_inches=0
    )
    (cx, cy), = extents(read(data))
    assert cx == 6 * EMU_PER_INCH
    assert cy == 3 * EMU_PER_INCH  # the aspect ratio survives the cap
    assert fit.render_dpi == 0.0
    assert fit.images_capped == 1


def test_without_geometry_an_image_that_already_fits_is_left_exactly_alone():
    body = drawing(int(3.0 * EMU_PER_INCH), int(1.5 * EMU_PER_INCH))
    original = package(body, {"image1.png": png(288, 144)})
    data, fit = fit_docx(original, lines=None, side_margin_inches=0)
    assert extents(read(data)) == [(int(3.0 * EMU_PER_INCH), int(1.5 * EMU_PER_INCH))]
    assert fit.images_resized == 0
    assert data == original  # nothing to do means the file is not rewritten


def test_an_image_inside_a_table_is_fitted_to_its_cell_and_not_the_page():
    cell = (
        "<w:tc><w:tcPr/><w:p><w:r>"
        + drawing(int(6.0 * EMU_PER_INCH), int(3.0 * EMU_PER_INCH))
        + "</w:r></w:p></w:tc>"
    )
    body = (
        "<w:tbl><w:tblPr><w:tblStyle w:val=\"TableGrid\"/></w:tblPr>"
        '<w:tblGrid><w:gridCol w:w="2160"/><w:gridCol w:w="6480"/></w:tblGrid>'
        "<w:tr>" + cell + "<w:tc><w:tcPr/><w:p/></w:tc></w:tr></w:tbl>"
    )
    data, _ = fit_docx(
        package(body, {"image1.png": png(2000, 1000)}), lines=None, side_margin_inches=0
    )
    (cx, _), = extents(read(data))
    # The first column is a quarter of a six inch measure.
    assert cx == pytest.approx(1.5 * EMU_PER_INCH, rel=1e-3)


def test_a_crop_whose_header_cannot_be_read_keeps_the_size_mathpix_gave_it():
    body = drawing(int(4.0 * EMU_PER_INCH), int(2.0 * EMU_PER_INCH))
    data, fit = fit_docx(
        package(body, {"image1.png": b"not an image"}),
        page_sizes=LETTER,
        lines=LINES_150,
    )
    assert extents(read(data)) == [(int(4.0 * EMU_PER_INCH), int(2.0 * EMU_PER_INCH))]
    assert fit.images_resized == 0


def test_a_tiny_symbol_is_not_shrunk_out_of_legibility():
    body = drawing(int(0.1 * EMU_PER_INCH), int(0.1 * EMU_PER_INCH))
    data, _ = fit_docx(
        package(body, {"image1.png": png(4, 4)}), page_sizes=LETTER, lines=LINES_150
    )
    (cx, _), = extents(read(data))
    assert cx == pytest.approx(0.18 * EMU_PER_INCH, rel=1e-2)


@pytest.mark.parametrize(
    "data,expected",
    [
        (png(640, 480), (640, 480)),
        (b"GIF89a" + struct.pack("<HH", 12, 34), (12, 34)),
        (b"not an image at all", None),
        (b"", None),
    ],
)
def test_image_headers_are_read_or_refused(data, expected):
    assert image_pixels(data) == expected


def test_a_jpeg_header_is_read_past_its_application_segments():
    body = (
        b"\xff\xd8\xff"
        + b"\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
        + b"\xff\xc0" + struct.pack(">H", 17) + b"\x08"
        + struct.pack(">HH", 400, 800) + b"\x00" * 8
    )
    assert image_pixels(body) == (800, 400)


# --- tables -------------------------------------------------------------------


TABLE = (
    '<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/><w:jc w:val="left"/>'
    '<w:tblBorders/><w:tblCellMar><w:top w:type="dxa" w:w="80"/></w:tblCellMar>'
    "</w:tblPr>"
    '<w:tblGrid><w:gridCol w:w="2160"/><w:gridCol w:w="2160"/>'
    '<w:gridCol w:w="2160"/><w:gridCol w:w="2160"/></w:tblGrid>'
    "<w:tr>"
    '<w:tc><w:tcPr><w:gridSpan w:val="2"/></w:tcPr><w:p/></w:tc>'
    "<w:tc><w:tcPr/><w:p/></w:tc>"
    "<w:tc><w:tcPr/><w:p/></w:tc>"
    "</w:tr></w:tbl>"
)


def test_a_table_is_given_the_width_and_layout_mathpix_leaves_out():
    data, fit = fit_docx(package(TABLE), lines=None)
    xml = read(data)
    assert '<w:tblW w:w="5000" w:type="pct"/>' in xml
    assert '<w:tblLayout w:type="fixed"/>' in xml
    assert fit.tables_fitted == 1


def test_table_properties_are_inserted_in_the_order_the_schema_requires():
    # Word rejects the part outright when these are out of sequence, so the
    # position matters as much as the presence.
    xml = read(fit_docx(package(TABLE), lines=None)[0])
    properties = re.search(r"<w:tblPr>(.*?)</w:tblPr>", xml, re.S).group(1)
    order = [name for name in re.findall(r"<(w:\w+)", properties)]
    assert order.index("w:tblW") < order.index("w:jc")
    assert order.index("w:tblBorders") < order.index("w:tblLayout") < order.index("w:tblCellMar")


def test_a_cell_gets_its_share_of_the_grid_including_the_columns_it_spans():
    xml = read(fit_docx(package(TABLE), lines=None)[0])
    widths = re.findall(r'<w:tcW w:w="(\d+)" w:type="pct"/>', xml)
    # Two of four columns, then one, then one: half, a quarter, a quarter.
    assert widths == ["2500", "1250", "1250"]


def test_every_row_sums_to_the_whole_table():
    xml = read(fit_docx(package(TABLE), lines=None)[0])
    row = re.search(r"<w:tr>(.*?)</w:tr>", xml, re.S).group(1)
    assert sum(int(w) for w in re.findall(r'<w:tcW w:w="(\d+)"', row)) == 5000


def test_a_cell_width_lands_inside_the_properties_before_the_span():
    xml = read(fit_docx(package(TABLE), lines=None)[0])
    properties = re.search(r"<w:tcPr>(.*?)</w:tcPr>", xml, re.S).group(1)
    assert properties.index("w:tcW") < properties.index("w:gridSpan")


def test_an_empty_properties_element_is_replaced_rather_than_duplicated():
    # `<w:tcPr/>` holds nothing and cannot be inserted into. Adding a second one
    # beside it is a document Word refuses to open.
    body = (
        '<w:tbl><w:tblPr/><w:tblGrid><w:gridCol w:w="4320"/><w:gridCol w:w="4320"/>'
        "</w:tblGrid><w:tr><w:tc><w:tcPr/><w:p/></w:tc>"
        "<w:tc><w:tcPr/><w:p/></w:tc></w:tr></w:tbl>"
    )
    xml = read(fit_docx(package(body), lines=None)[0])
    assert xml.count("<w:tcPr>") == 2
    assert "<w:tcPr/>" not in xml
    assert xml.count('<w:tcW w:w="2500" w:type="pct"/>') == 2


def test_an_empty_table_properties_element_is_replaced_too():
    body = (
        '<w:tbl><w:tblPr/><w:tblGrid><w:gridCol w:w="8640"/></w:tblGrid>'
        "<w:tr><w:tc><w:tcPr/><w:p/></w:tc></w:tr></w:tbl>"
    )
    data, fit = fit_docx(package(body), lines=None)
    xml = read(data)
    assert "<w:tblPr/>" not in xml
    assert '<w:tblW w:w="5000" w:type="pct"/>' in xml
    assert '<w:tblLayout w:type="fixed"/>' in xml
    assert fit.tables_fitted == 1


def test_a_table_with_no_properties_at_all_is_given_them():
    body = (
        '<w:tbl><w:tblGrid><w:gridCol w:w="8640"/></w:tblGrid>'
        "<w:tr><w:tc><w:p/></w:tc></w:tr></w:tbl>"
    )
    xml = read(fit_docx(package(body), lines=None)[0])
    # `w:tblPr` has to be the first child of `w:tbl`.
    assert xml.index("<w:tblPr>") < xml.index("<w:tblGrid>")


def test_a_cell_with_no_properties_at_all_is_given_some():
    body = (
        '<w:tbl><w:tblPr/><w:tblGrid><w:gridCol w:w="4320"/><w:gridCol w:w="4320"/>'
        "</w:tblGrid><w:tr><w:tc><w:p/></w:tc><w:tc><w:p/></w:tc></w:tr></w:tbl>"
    )
    xml = read(fit_docx(package(body), lines=None)[0])
    assert xml.count('<w:tcPr><w:tcW w:w="2500" w:type="pct"/></w:tcPr>') == 2


def test_widths_mathpix_did_supply_are_left_as_they_are():
    body = TABLE.replace(
        "<w:tcPr/>", '<w:tcPr><w:tcW w:w="1000" w:type="pct"/></w:tcPr>', 1
    ).replace('<w:tblPr>', '<w:tblPr><w:tblW w:w="3000" w:type="pct"/>', 1)
    xml = read(fit_docx(package(body), lines=None)[0])
    assert '<w:tblW w:w="3000" w:type="pct"/>' in xml
    assert '<w:tblW w:w="5000" w:type="pct"/>' not in xml
    assert '<w:tcW w:w="1000" w:type="pct"/>' in xml


def test_a_nested_table_is_fitted_to_the_cell_it_sits_in():
    inner = (
        '<w:tbl><w:tblPr/><w:tblGrid><w:gridCol w:w="1080"/><w:gridCol w:w="1080"/>'
        "</w:tblGrid><w:tr><w:tc><w:tcPr/><w:p/></w:tc>"
        "<w:tc><w:tcPr/><w:p/></w:tc></w:tr></w:tbl>"
    )
    body = (
        '<w:tbl><w:tblPr/><w:tblGrid><w:gridCol w:w="4320"/><w:gridCol w:w="4320"/>'
        "</w:tblGrid><w:tr><w:tc><w:tcPr/>" + inner + "</w:tc>"
        "<w:tc><w:tcPr/><w:p/></w:tc></w:tr></w:tbl>"
    )
    data, fit = fit_docx(package(body), lines=None)
    assert fit.tables_fitted == 2
    # Both tables are stated as a share of their own container, so both read 100%.
    assert read(data).count('<w:tblW w:w="5000" w:type="pct"/>') == 2


# --- equations ----------------------------------------------------------------


def run(text: str) -> str:
    return f"<m:r><m:rPr><m:sty/></m:rPr><m:t>{text}</m:t></m:r>"


def equation(*parts: str) -> str:
    return "<m:oMathPara><m:oMathParaPr/><m:oMath>" + "".join(parts) + "</m:oMath></m:oMathPara>"


LONG = equation(
    run("x"), run("="), run("a" * 60), run("="), run("b" * 60), run("⇒"), run("c" * 40)
)

MATH_SETTINGS = (
    f"<w:settings {NAMESPACES}><m:mathPr>"
    '<m:wrapIndent m:val="1440"/></m:mathPr></w:settings>'
)


def test_a_long_equation_is_given_somewhere_to_wrap():
    data, fit = fit_docx(package(LONG, settings=MATH_SETTINGS), lines=None)
    assert read(data).count("<m:brk/>") == 2
    assert fit.equations_broken == 1


def test_the_leading_relation_is_not_a_place_to_wrap():
    # Breaking before the first `=` would put the whole equation on a line of
    # its own under a lone `x`.
    xml = read(fit_docx(package(LONG, settings=MATH_SETTINGS), lines=None)[0])
    first = xml.index("<m:t>=</m:t>")
    assert "<m:brk/>" not in xml[:first]


def test_a_relation_inside_a_fraction_is_never_broken():
    body = equation(
        run("x"),
        "<m:f><m:num>" + run("=") + run("y" * 100) + "</m:num>"
        "<m:den>" + run("z") + "</m:den></m:f>",
    )
    data, fit = fit_docx(package(body, settings=MATH_SETTINGS), lines=None)
    assert "<m:brk/>" not in read(data)
    assert fit.equations_broken == 0


def test_a_short_equation_is_left_entirely_alone():
    body = equation(run("x"), run("="), run("y"))
    data, fit = fit_docx(package(body, settings=MATH_SETTINGS), lines=None)
    assert "<m:brk/>" not in read(data)
    assert fit.equations_broken == 0


def test_an_equation_that_already_wraps_is_not_broken_again():
    body = LONG.replace("<m:rPr><m:sty/></m:rPr>", "<m:rPr><m:brk/></m:rPr>", 1)
    _, fit = fit_docx(package(body, settings=MATH_SETTINGS), lines=None)
    assert fit.equations_broken == 0


def test_the_wrap_indent_is_relaxed_only_once_an_equation_can_actually_wrap():
    data, _ = fit_docx(package(LONG, settings=MATH_SETTINGS), lines=None)
    assert '<m:wrapIndent m:val="360"/>' in read(data, "word/settings.xml")


def test_an_untouched_document_leaves_the_wrap_indent_alone():
    body = equation(run("x"), run("="), run("y"))
    data, _ = fit_docx(
        package(body, settings=MATH_SETTINGS), lines=None, font_points=0,
        side_margin_inches=0,
    )
    assert data == package(body, settings=MATH_SETTINGS)


# --- the archive itself --------------------------------------------------------


def test_every_other_part_of_the_package_is_carried_through_untouched():
    original = package(TABLE, {"image1.png": png(600, 300)})
    data, _ = fit_docx(original, lines=None)
    with zipfile.ZipFile(BytesIO(original)) as before, zipfile.ZipFile(BytesIO(data)) as after:
        assert before.namelist() == after.namelist()
        for name in before.namelist():
            if name != "word/document.xml":
                assert before.read(name) == after.read(name), name


def test_the_result_is_still_well_formed_xml():
    import xml.etree.ElementTree as ElementTree

    data, _ = fit_docx(
        package(TABLE + LONG, {"image1.png": png(600, 300)}, settings=MATH_SETTINGS),
        page_sizes=LETTER,
        lines=LINES_150,
    )
    with zipfile.ZipFile(BytesIO(data)) as archive:
        for name in archive.namelist():
            if name.endswith((".xml", ".rels")):
                ElementTree.fromstring(archive.read(name))


@pytest.mark.parametrize(
    "data", [b"", b"not a zip at all", b"PK\x03\x04truncated"]
)
def test_something_that_is_not_a_docx_comes_back_exactly_as_it_arrived(data):
    result, fit = fit_docx(data, lines=None)
    assert result == data
    assert not fit.applied
    assert fit.reason


def test_a_zip_without_a_document_is_returned_rather_than_repaired():
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("hello.txt", "not a word document")
    original = buffer.getvalue()
    result, fit = fit_docx(original, lines=None)
    assert result == original
    assert fit.reason == "no word/document.xml"


def test_each_selector_can_be_turned_off_on_its_own():
    original = package(
        TABLE + LONG, {"image1.png": png(600, 300)}, settings=MATH_SETTINGS
    )
    _, fit = fit_docx(
        original,
        page_sizes=LETTER,
        lines=LINES_150,
        fit_images=False,
        fit_tables=False,
        fit_equations=False,
        font_points=0,
        side_margin_inches=0,
    )
    assert not fit.applied
    assert fit.reason == "nothing to fit"


def test_what_was_changed_is_reported_for_the_job_record():
    data = Fit(images_resized=3, applied=True, render_dpi=150.0, measure_inches=6.0)
    assert data.as_dict()["images_resized"] == 3
    assert data.as_dict()["render_dpi"] == 150.0
    assert data.as_dict()["reason"] is None


# --- the page the source was actually laid out on -----------------------------


# A 9.00 x 11.50in page rendered at 250 DPI, set in two columns of 3.60in with a
# 0.12in gutter and 0.84in side margins — the geometry of a real textbook, and
# every number below is that page in the units `lines.json` reports it in.
COLUMN_PAGE = (648.0, 828.0)  # 9.00 x 11.50in, in points
COLUMN_EDGES = {1: (210, 1105), 2: (1135, 2040)}


def line(column: int, left: int, right: int, top: int) -> dict:
    return {
        "column": column,
        "region": {
            "top_left_x": left,
            "top_left_y": top,
            "width": right - left,
            "height": 40,
        },
    }


def column_page(number: int, columns: int = 2, rows: int = 8) -> dict:
    lines = []
    for index in range(rows):
        top = 250 + index * 290
        for column in range(1, columns + 1):
            left, right = COLUMN_EDGES[column]
            lines.append(line(column, left, right, top))
    return {
        "page": number,
        "page_width": 2250,
        "page_height": 2875,
        "lines": lines,
    }


def column_lines(pages: int = 4, **kwargs) -> dict:
    return {"pages": [column_page(number, **kwargs) for number in range(1, pages + 1)]}


COLUMN_SIZES = [COLUMN_PAGE] * 4


def test_the_source_page_is_read_out_of_the_line_boxes():
    layout = source_layout(column_lines(), COLUMN_SIZES)
    assert layout is not None
    assert (round(layout.page_width, 2), round(layout.page_height, 2)) == (9.0, 11.5)
    assert layout.columns == 2
    assert round(layout.margin_left, 2) == 0.84
    assert round(layout.margin_right, 2) == 0.84
    assert round(layout.gutter, 2) == 0.12
    assert round(layout.column_width, 2) == 3.6


def test_a_line_reaching_across_the_gutter_is_not_taken_for_the_margin():
    """A spanning heading widens the text block; one stray line does not."""
    data = column_lines()
    data["pages"][0]["lines"].append(line(1, 210, 2040, 100))
    layout = source_layout(data, COLUMN_SIZES)
    assert layout is not None
    assert layout.columns == 2
    assert round(layout.column_width, 2) == 3.6


def test_a_full_width_heading_widens_the_text_block_without_dividing_it():
    data = column_lines()
    for page in data["pages"]:
        page["lines"].append(line(0, 180, 2070, 120))
    layout = source_layout(data, COLUMN_SIZES)
    assert layout is not None
    assert layout.columns == 2
    assert round(layout.margin_left, 2) == 0.72


def test_a_single_column_source_is_read_as_one_column():
    data = column_lines(rows=12)
    for page in data["pages"]:
        for entry in page["lines"]:
            entry["column"] = 0
        page["lines"] = [
            entry for entry in page["lines"] if entry["region"]["top_left_x"] == 210
        ]
        for entry in page["lines"]:
            entry["region"]["width"] = 2040 - 210
    layout = source_layout(data, COLUMN_SIZES)
    assert layout is not None
    assert layout.columns == 1
    assert layout.gutter == 0.0


def test_pages_that_disagree_about_their_columns_get_no_answer():
    """Guessing a layout is worse than leaving the document as Mathpix wrote it."""
    data = column_lines(pages=6)
    for page in data["pages"][1:]:
        for entry in page["lines"]:
            entry["column"] = 0
    assert source_layout(data, COLUMN_SIZES * 2) is None


def test_a_document_too_short_to_measure_gets_no_answer():
    assert source_layout(column_lines(pages=1, rows=2), COLUMN_SIZES) is None


def test_without_the_source_pdf_there_is_no_resolution_and_so_no_layout():
    assert source_layout(column_lines(), []) is None


@pytest.mark.parametrize("lines", [None, {}, {"pages": []}, {"pages": "no"}])
def test_unreadable_geometry_gets_no_answer(lines):
    assert source_layout(lines, COLUMN_SIZES) is None


def test_columns_that_overlap_are_a_misread_rather_than_a_layout():
    data = column_lines()
    for page in data["pages"]:
        for entry in page["lines"]:
            if entry["column"] == 1:
                entry["region"]["width"] = 1600 - 210
    assert source_layout(data, COLUMN_SIZES) is None


# --- laying the document out on it --------------------------------------------


def section_of(xml: str) -> str:
    return re.search(r"<w:sectPr>.*?</w:sectPr>", xml, re.S).group(0)


def test_the_section_is_restated_as_the_source_page():
    data, fit = fit_docx(
        package("<w:p/>"),
        page_sizes=COLUMN_SIZES,
        lines=column_lines(),
        multi_column=True,
        side_margin_inches=0,
    )
    section = section_of(read(data))
    assert '<w:cols w:num="2"' in section
    # 9.00 x 11.50in, and margins of 0.84in, in twips.
    assert 'w:w="12960"' in section and 'w:h="16560"' in section
    assert 'w:left="1210"' in section and 'w:right="1210"' in section
    assert fit.columns == 2
    assert fit.page_inches == (9.0, 11.5)


def test_everything_else_is_fitted_to_the_column_the_section_now_has():
    """The measure is read back out of the section, not passed around."""
    data, fit = fit_docx(
        package("<w:p/>"),
        page_sizes=COLUMN_SIZES,
        lines=column_lines(),
        multi_column=True,
        side_margin_inches=0,
    )
    assert measure_twips(read(data).encode()) == pytest.approx(5183, abs=2)
    assert fit.measure_inches == pytest.approx(3.6, abs=0.01)


def test_the_section_is_left_alone_unless_columns_were_asked_for():
    data, fit = fit_docx(
        package(TABLE), page_sizes=COLUMN_SIZES, lines=column_lines(),
        side_margin_inches=0,
    )
    assert "<w:cols" not in read(data)
    assert fit.columns == 0
    assert fit.page_inches is None
    assert fit.measure_inches == 6.0


def test_a_source_whose_layout_cannot_be_read_stays_single_column():
    """Falling back is the point: a guessed page is worse than Mathpix's own."""
    data, fit = fit_docx(package(TABLE), page_sizes=LETTER, lines=LINES_150,
                         multi_column=True)
    assert "<w:cols" not in read(data)
    assert fit.columns == 0


def test_an_image_is_fitted_to_the_column_rather_than_the_page():
    # 1000px at 250 DPI is 4.00in, which fits a 6.00in page and not a 3.60in
    # column.
    body = drawing(int(4.0 * EMU_PER_INCH), int(3.0 * EMU_PER_INCH))
    data, fit = fit_docx(
        package(body, {"image1.png": png(1000, 750)}),
        page_sizes=COLUMN_SIZES,
        lines=column_lines(),
        multi_column=True,
        side_margin_inches=0,
    )
    width = extents(read(data))[0][0]
    assert width / EMU_PER_INCH == pytest.approx(3.6, abs=0.01)
    assert fit.images_capped == 1


def test_display_equations_are_left_aligned_once_they_have_a_column():
    body = '<w:p><w:pPr><w:jc w:val="center"/></w:pPr></w:p>'
    settings = (
        f"<w:settings {NAMESPACES}><m:mathPr>"
        '<m:defJc m:val="centerGroup"/><m:wrapIndent m:val="1440"/>'
        "</m:mathPr></w:settings>"
    )
    data, _ = fit_docx(
        package(body, settings=settings),
        page_sizes=COLUMN_SIZES,
        lines=column_lines(),
        multi_column=True,
    )
    assert '<w:jc w:val="left"/>' in read(data)
    assert '<w:jc w:val="center"/>' not in read(data)
    assert '<m:defJc m:val="left"/>' in read(data, "word/settings.xml")


def test_centring_is_left_as_it_is_in_a_single_column_document():
    body = '<w:p><w:pPr><w:jc w:val="center"/></w:pPr></w:p>' + TABLE
    data, _ = fit_docx(package(body), lines=None)
    assert '<w:jc w:val="center"/>' in read(data)


# --- the grid, which a fixed layout reads in preference to any percentage ------


def test_the_grid_is_brought_to_the_measure_instead_of_staying_absolute():
    """The regression: `tblLayout="fixed"` makes an absolute grid binding."""
    data, fit = fit_docx(
        package(TABLE, section=COLUMN_SECTION), lines=None, side_margin_inches=0
    )
    xml = read(data)
    total = sum(int(width) for width in re.findall(r'<w:gridCol w:w="(\d+)"', xml))
    # A two-column section of Mathpix's own page: (8640 - 360) / 2.
    assert measure_twips(xml.encode()) == 4140
    assert total == 4140
    assert total != 8640
    assert fit.tables_fitted == 1


def test_the_columns_keep_the_proportions_mathpix_gave_them():
    body = (
        '<w:tbl><w:tblPr/><w:tblGrid><w:gridCol w:w="2160"/><w:gridCol w:w="6480"/>'
        "</w:tblGrid><w:tr><w:tc><w:tcPr/><w:p/></w:tc>"
        "<w:tc><w:tcPr/><w:p/></w:tc></w:tr></w:tbl>"
    )
    xml = read(
        fit_docx(package(body, section=COLUMN_SECTION), lines=None, side_margin_inches=0)[0]
    )
    widths = [int(width) for width in re.findall(r'<w:gridCol w:w="(\d+)"', xml)]
    assert sum(widths) == 4140
    assert widths[1] == 3 * widths[0]


def test_a_grid_already_at_the_measure_is_not_rewritten():
    xml = read(fit_docx(package(TABLE), lines=None, side_margin_inches=0)[0])
    assert xml.count('<w:gridCol w:w="2160"/>') == 4


def test_other_attributes_on_a_grid_column_survive_being_rescaled():
    body = TABLE.replace(
        '<w:gridCol w:w="2160"/>', '<w:gridCol w:w="2160" w:hRule="auto"/>', 1
    )
    xml = read(fit_docx(package(body, section=COLUMN_SECTION), lines=None)[0])
    assert 'w:hRule="auto"' in xml


def test_a_nested_grid_is_brought_to_its_cell_rather_than_the_page():
    inner = (
        '<w:tbl><w:tblPr/><w:tblGrid><w:gridCol w:w="1080"/><w:gridCol w:w="1080"/>'
        "</w:tblGrid><w:tr><w:tc><w:tcPr/><w:p/></w:tc>"
        "<w:tc><w:tcPr/><w:p/></w:tc></w:tr></w:tbl>"
    )
    body = (
        '<w:tbl><w:tblPr/><w:tblGrid><w:gridCol w:w="4320"/><w:gridCol w:w="4320"/>'
        "</w:tblGrid><w:tr><w:tc><w:tcPr/>" + inner + "</w:tc>"
        "<w:tc><w:tcPr/><w:p/></w:tc></w:tr></w:tbl>"
    )
    xml = read(
        fit_docx(package(body, section=COLUMN_SECTION), lines=None, side_margin_inches=0)[0]
    )
    grids = re.findall(r"<w:tblGrid>(.*?)</w:tblGrid>", xml, re.S)
    totals = [
        sum(int(width) for width in re.findall(r'<w:gridCol w:w="(\d+)"', grid))
        for grid in grids
    ]
    # The outer table has the whole measure; the inner one half of it.
    assert sorted(totals) == [2070, 4140]


# --- the maths that has nothing on its left -----------------------------------


ZWSP = "​"
OPERAND = f'<m:r><m:t xml:space="preserve">{ZWSP}</m:t></m:r>'


def matrix(*rows: tuple[str, ...]) -> str:
    body = "".join(
        "<m:mr>" + "".join(f"<m:e>{cell}</m:e>" if cell else "<m:e/>" for cell in row)
        + "</m:mr>"
        for row in rows
    )
    return f"<m:oMathPara><m:oMath><m:m><m:mPr/>{body}</m:m></m:oMath></m:oMathPara>"


def test_an_empty_argument_is_given_something_to_be():
    data, fit = fit_docx(package(matrix((run("x"), ""))), lines=None, font_points=0)
    xml = read(data)
    assert "<m:e/>" not in xml
    assert f"<m:e>{OPERAND}</m:e>" in xml
    assert fit.math_gaps_filled == 1


def test_the_filler_is_marked_to_survive_being_read_back():
    """Without `xml:space`, LibreOffice trims it away and the mark comes back."""
    xml = read(fit_docx(package(matrix((run("x"), ""))), lines=None)[0])
    assert f'<m:t xml:space="preserve">{ZWSP}</m:t>' in xml


def test_a_row_continuing_a_derivation_is_given_its_missing_left_hand_side():
    body = matrix((run("y"), run("=") + run("2")), (run("z"), run("=") + run("3")))
    data, fit = fit_docx(package(body), lines=None, font_points=0)
    xml = read(data)
    assert xml.count(f"<m:e>{OPERAND}<m:r>") == 2
    assert fit.math_gaps_filled == 2


def test_a_cell_of_nothing_but_padding_counts_as_empty():
    padding = '<m:r><m:t xml:space="preserve">    </m:t></m:r>'
    data, fit = fit_docx(package(matrix((padding, run("x")))), lines=None, font_points=0)
    assert read(data).count(OPERAND) == 1
    assert fit.math_gaps_filled == 1


def test_a_row_that_starts_with_a_value_is_left_alone():
    body = matrix((run("y"), run("2") + run("=") + run("x")))
    data, fit = fit_docx(package(body), lines=None, font_points=0, side_margin_inches=0)
    assert fit.math_gaps_filled == 0
    assert read(data) == document(body)


def test_a_leading_sign_inside_a_bracket_belongs_to_what_follows_it():
    """`(−b)` is a negative number, not a subtraction missing its left side."""
    body = matrix((run("y"), "<m:d><m:e>" + run("−") + run("b") + "</m:e></m:d>"))
    _, fit = fit_docx(package(body), lines=None)
    assert fit.math_gaps_filled == 0


def test_a_square_roots_hidden_degree_is_not_a_gap():
    body = f"<m:oMathPara><m:oMath><m:rad><m:radPr><m:degHide m:val=\"1\"/></m:radPr>" \
           f"<m:deg/><m:e>{run('x')}</m:e></m:rad></m:oMath></m:oMathPara>"
    data, fit = fit_docx(package(body), lines=None)
    assert "<m:deg/>" in read(data)
    assert fit.math_gaps_filled == 0


def test_a_whole_equation_that_opens_on_a_relation_is_given_one_too():
    body = f"<m:oMathPara><m:oMath>{run('=')}{run('0')}</m:oMath></m:oMathPara>"
    data, fit = fit_docx(package(body), lines=None, font_points=0)
    assert f"<m:oMath>{OPERAND}<m:r>" in read(data)
    assert fit.math_gaps_filled == 1


def test_running_over_an_already_repaired_document_changes_nothing_more():
    once, _ = fit_docx(package(matrix((run("y"), run("=") + run("2")))), lines=None)
    twice, fit = fit_docx(once, lines=None)
    assert fit.math_gaps_filled == 0
    assert twice == once


def test_filling_the_gaps_can_be_turned_off():
    body = matrix((run("y"), run("=") + run("2")))
    _, fit = fit_docx(
        package(body), lines=None, fill_math_gaps=False, font_points=0,
        side_margin_inches=0,
    )
    assert fit.math_gaps_filled == 0
    assert fit.reason == "nothing to fit"


# --- one type size, stated rather than inherited -------------------------------

# Mathpix's own `styles.xml`, cut to what carries a size: the defaults every run
# inherits from, the `Normal` style that restates them, and the verbatim style.
STYLES = (
    f'<w:styles {NAMESPACES}><w:docDefaults><w:rPrDefault><w:rPr>'
    '<w:rFonts w:ascii="Georgia" w:hAnsi="Georgia"/><w:sz w:val="22"/>'
    '<w:szCs w:val="22"/></w:rPr></w:rPrDefault></w:docDefaults>'
    '<w:style w:type="paragraph" w:styleId="Normal"><w:rPr><w:sz w:val="22"/>'
    '</w:rPr></w:style></w:styles>'
)

# A heading, the way this document writes one: no `w:pStyle` anywhere in the
# file, so 21pt is direct formatting on the run itself.
HEADING = (
    '<w:p><w:r><w:rPr><w:b/><w:sz w:val="42"/></w:rPr>'
    "<w:t>Conic Sections</w:t></w:r></w:p>"
)


def sizes(xml: str, attribute: str = "sz") -> list[str]:
    return re.findall(rf'<w:{attribute} w:val="(\d+)"/>', xml)


def test_the_size_every_run_inherits_is_the_one_the_document_is_set_in():
    data, fit = fit_docx(package(HEADING, styles=STYLES), lines=None)
    styles = read(data, "word/styles.xml")
    assert sizes(styles) == ["20", "20"]
    assert fit.font_points == 10.0
    assert fit.sizes_restated


def test_a_heading_stops_being_twenty_one_point_and_stays_a_heading():
    xml = read(fit_docx(package(HEADING, styles=STYLES), lines=None)[0])
    assert '<w:sz w:val="42"/>' not in xml
    assert '<w:b/><w:sz w:val="20"/>' in xml


def test_a_size_with_no_complex_script_twin_is_given_one():
    # `w:sz` alone leaves complex-script text on whatever it inherited, which is
    # the size this pass exists to stop being a question.
    xml = read(fit_docx(package(HEADING, styles=STYLES), lines=None)[0])
    assert '<w:sz w:val="20"/><w:szCs w:val="20"/>' in xml
    styles = read(fit_docx(package(HEADING, styles=STYLES), lines=None)[0],
                  "word/styles.xml")
    assert sizes(styles, "szCs") == ["20", "20"]


def test_a_maths_run_with_no_properties_at_all_is_given_the_size():
    body = "<m:oMathPara><m:oMath><m:r><m:t>x</m:t></m:r></m:oMath></m:oMathPara>"
    xml = read(fit_docx(package(body), lines=None)[0])
    assert '<m:r><w:rPr><w:sz w:val="20"/><w:szCs w:val="20"/></w:rPr><m:t>x</m:t>' in xml


def test_a_maths_run_keeps_its_own_properties_and_takes_the_size_after_them():
    # `CT_R` sequences `m:rPr` before `w:rPr`; the other way round Word refuses
    # to open the part at all.
    xml = read(fit_docx(package(equation(run("x"))), lines=None)[0])
    assert '<m:rPr><m:sty/></m:rPr><w:rPr><w:sz w:val="20"/>' in xml
    assert '<w:rPr><w:sz w:val="20"/><w:szCs w:val="20"/></w:rPr><m:rPr>' not in xml


def test_the_glyphs_a_fraction_is_drawn_with_are_sized_too():
    body = (
        "<m:oMathPara><m:oMath><m:f><m:fPr><m:ctrlPr><w:rPr>"
        '<w:rFonts w:ascii="Cambria Math"/></w:rPr></m:ctrlPr></m:fPr>'
        f"<m:num>{run('a')}</m:num><m:den>{run('b')}</m:den>"
        "</m:f></m:oMath></m:oMathPara>"
    )
    xml = read(fit_docx(package(body), lines=None)[0])
    assert '<w:rFonts w:ascii="Cambria Math"/><w:sz w:val="20"/>' in xml


def test_a_run_that_already_states_the_size_is_not_given_a_second_one():
    body = (
        '<m:oMathPara><m:oMath><m:r><w:rPr><w:sz w:val="28"/></w:rPr>'
        "<m:t>x</m:t></m:r></m:oMath></m:oMathPara>"
    )
    xml = read(fit_docx(package(body), lines=None)[0])
    assert xml.count('<w:sz w:val="20"/>') == 1


def test_turning_the_size_off_leaves_every_one_of_them_alone():
    data, fit = fit_docx(
        package(HEADING, styles=STYLES), lines=None, font_points=0, side_margin_inches=0
    )
    assert data == package(HEADING, styles=STYLES)
    assert fit.font_points == 0.0
    assert fit.reason == "nothing to fit"


def test_a_package_with_no_styles_part_is_fitted_without_complaint():
    data, fit = fit_docx(package(HEADING), lines=None)
    assert "word/styles.xml" not in zipfile.ZipFile(BytesIO(data)).namelist()
    assert '<w:sz w:val="20"/>' in read(data)


def test_stating_the_size_twice_states_it_once():
    once, _ = fit_docx(package(equation(run("x")) + HEADING, styles=STYLES), lines=None)
    twice, fit = fit_docx(once, lines=None)
    assert twice == once
    assert fit.reason == "nothing to fit"


# --- a step's connective, joined to the equation it introduces ------------------


def connective_paragraph(token: str) -> str:
    return (
        '<w:p><w:pPr><w:spacing w:after="220"/></w:pPr><w:r>'
        f'<w:rPr><w:rFonts w:ascii="Georgia"/></w:rPr>'
        f'<w:t xml:space="preserve">{token}</w:t></w:r></w:p>'
    )


def math_paragraph(*parts: str) -> str:
    return (
        '<w:p><w:pPr><w:spacing w:after="220"/></w:pPr>'
        "<m:oMathPara><m:oMath>" + "".join(parts) + "</m:oMath></m:oMathPara></w:p>"
    )


def broken_paragraph(token: str, lead: str = "from the figure") -> str:
    return (
        "<w:p><w:pPr><w:numPr><w:ilvl w:val=\"0\"/><w:numId w:val=\"3\"/></w:numPr>"
        f'</w:pPr><w:r><w:rPr/><w:t xml:space="preserve">{lead}</w:t></w:r>'
        '<w:r><w:rPr/><w:br w:type="textWrapping"/></w:r>'
        f'<w:r><w:rPr/><w:t xml:space="preserve">{token}</w:t></w:r></w:p>'
    )


def test_a_connective_on_its_own_line_moves_into_the_equation_below_it():
    body = connective_paragraph("⇒") + math_paragraph(run("x"), run("="), run("y"))
    data, fit = fit_docx(package(body), lines=None, font_points=0)
    xml = read(data)
    assert fit.steps_joined == 1
    # The paragraph that held it is gone, and the connective is inside the maths.
    assert '<w:t xml:space="preserve">⇒</w:t>' not in xml
    assert '<m:rPr><m:nor/></m:rPr><m:t xml:space="preserve">⇒&#32;&#32;</m:t>' in xml


def test_the_connective_is_kept_upright_and_keeps_its_gap():
    body = connective_paragraph("or") + math_paragraph(run("x"))
    xml = read(fit_docx(package(body), lines=None, font_points=0)[0])
    # Without `m:nor` this is set as the product of two variables, and without
    # `xml:space` LibreOffice trims the gap the source's narrow column stood in.
    assert '<m:nor/>' in xml
    assert 'xml:space="preserve">or&#32;&#32;</m:t>' in xml


def test_the_joined_equation_says_where_it_sits_rather_than_inheriting_it():
    body = connective_paragraph("∴") + math_paragraph(run("x"))
    xml = read(fit_docx(package(body), lines=None, font_points=0)[0])
    assert '<m:oMathPara><m:oMathParaPr><m:jc m:val="left"/></m:oMathParaPr>' in xml


def test_an_equation_that_already_says_where_it_sits_is_not_told_twice():
    body = connective_paragraph("⇒") + (
        '<w:p><m:oMathPara><m:oMathParaPr><m:jc m:val="center"/></m:oMathParaPr>'
        f"<m:oMath>{run('x')}</m:oMath></m:oMathPara></w:p>"
    )
    xml = read(fit_docx(package(body), lines=None, font_points=0)[0])
    assert xml.count("<m:oMathParaPr>") == 1
    assert '<m:jc m:val="center"/>' in xml


def test_a_connective_hanging_off_a_line_of_prose_is_trimmed_not_swallowed():
    body = broken_paragraph("⇒") + math_paragraph(run("x"))
    data, fit = fit_docx(package(body), lines=None, font_points=0)
    xml = read(data)
    assert fit.steps_joined == 1
    # The host paragraph survives, numbering and lead text and all; only the
    # soft break and what hung off it are gone.
    assert '<w:numId w:val="3"/>' in xml
    assert "from the figure" in xml
    assert '<w:br w:type="textWrapping"/>' not in xml
    assert '<m:t xml:space="preserve">⇒&#32;&#32;</m:t>' in xml


def test_a_connective_paragraph_that_also_carries_maths_is_left_alone():
    body = (
        f'<w:p><w:r><w:t xml:space="preserve">⇒</w:t></w:r>'
        f"<m:oMath>{run('x')}</m:oMath></w:p>"
    ) + math_paragraph(run("y"))
    _, fit = fit_docx(package(body), lines=None, font_points=0)
    assert fit.steps_joined == 0


def test_a_connective_with_no_equation_under_it_stays_where_it_is():
    body = connective_paragraph("⇒") + "<w:p><w:r><w:t>Example 41</w:t></w:r></w:p>"
    data, fit = fit_docx(package(body), lines=None, font_points=0, side_margin_inches=0)
    assert fit.steps_joined == 0
    assert read(data) == document(body)


def test_a_line_of_prose_above_an_equation_is_not_a_connective():
    body = (
        '<w:p><w:r><w:t xml:space="preserve">Substituting for x we get</w:t></w:r></w:p>'
        + math_paragraph(run("x"))
    )
    _, fit = fit_docx(package(body), lines=None, font_points=0)
    assert fit.steps_joined == 0


def test_a_paragraph_holding_a_figure_is_never_eaten_for_its_caption():
    body = (
        f'<w:p><w:r>{drawing(1371600, 685800)}</w:r>'
        '<w:r><w:t xml:space="preserve">⇒</w:t></w:r></w:p>'
        + math_paragraph(run("x"))
    )
    _, fit = fit_docx(package(body), lines=None, font_points=0, fit_images=False)
    assert fit.steps_joined == 0


def test_joining_can_be_turned_off():
    body = connective_paragraph("⇒") + math_paragraph(run("x"))
    data, fit = fit_docx(
        package(body), lines=None, font_points=0, join_steps=False,
        side_margin_inches=0,
    )
    assert fit.steps_joined == 0
    assert read(data) == document(body)


def test_a_joined_equation_gets_the_operand_its_new_leading_relation_needs():
    """The join runs first precisely so this falls out of `_fill_math_gaps`."""
    body = connective_paragraph("⇒") + math_paragraph(run("x"), run("="), run("y"))
    data, fit = fit_docx(package(body), lines=None, font_points=0)
    assert fit.math_gaps_filled == 1
    assert f"<m:oMath>{OPERAND}<m:r><m:rPr><m:nor/>" in read(data)


# --- half an inch of margin, whatever the source had ---------------------------


def test_every_section_is_given_the_same_half_inch_of_side_margin():
    data, fit = fit_docx(package("<w:p/>"), lines=None)
    section = section_of(read(data))
    assert 'w:left="720"' in section and 'w:right="720"' in section
    # Only the sides were asked for.
    assert 'w:top="1440"' in section and 'w:bottom="1440"' in section
    assert fit.side_margin_inches == 0.5


def test_the_measure_follows_the_margin_it_was_just_given():
    """Nothing is told the measure changed; it is read back out of the section."""
    data, fit = fit_docx(package("<w:p/>"), lines=None)
    assert measure_twips(read(data).encode()) == 12240 - 720 - 720
    assert fit.measure_inches == pytest.approx(7.5, abs=0.01)


def test_a_table_is_fitted_to_the_measure_the_wider_margin_leaves():
    data, _ = fit_docx(package(TABLE), lines=None)
    xml = read(data)
    total = sum(int(width) for width in re.findall(r'<w:gridCol w:w="(\d+)"', xml))
    assert total == 10800


def test_the_margin_read_off_the_source_page_is_overridden_too():
    """The page and the columns are the source's; the side margins are not."""
    data, fit = fit_docx(
        package("<w:p/>"),
        page_sizes=COLUMN_SIZES,
        lines=column_lines(),
        multi_column=True,
    )
    section = section_of(read(data))
    assert 'w:w="12960"' in section  # still the source's own page
    assert '<w:cols w:num="2"' in section
    assert 'w:left="720"' in section and 'w:right="720"' in section
    assert "1210" not in section  # and not the 0.84in it was read at
    assert fit.columns == 2


def test_a_column_widens_by_what_the_margins_gave_back():
    data, fit = fit_docx(
        package("<w:p/>"),
        page_sizes=COLUMN_SIZES,
        lines=column_lines(),
        multi_column=True,
    )
    # (12960 - 720 - 720 - 174) / 2, against the 5183 the source's own margins
    # left. Half an inch of margin is a quarter inch of column.
    assert measure_twips(read(data).encode()) == pytest.approx(5673, abs=2)


def test_the_gutter_goes_with_it():
    """Word adds the gutter to the binding edge on top of the margin."""
    section = SECTION.replace('w:gutter="0"', 'w:gutter="720"')
    data, _ = fit_docx(package("<w:p/>", section=section), lines=None)
    assert 'w:gutter="0"' in section_of(read(data))


def test_a_section_stating_no_margins_at_all_is_given_them():
    section = '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/></w:sectPr>'
    data, fit = fit_docx(package("<w:p/>", section=section), lines=None)
    restated = section_of(read(data))
    assert 'w:left="720"' in restated and 'w:right="720"' in restated
    # `w:pgMar` sits after `w:pgSz`, where the schema puts it.
    assert restated.index("w:pgSz") < restated.index("w:pgMar")
    assert fit.side_margin_inches == 0.5


def test_every_section_gets_it_and_not_only_the_last():
    body = (
        '<w:p><w:pPr>' + SECTION + '</w:pPr></w:p>'
    )
    data, _ = fit_docx(package(body), lines=None)
    xml = read(data)
    assert xml.count('w:left="720"') == 2
    assert 'w:left="1800"' not in xml


def test_a_margin_already_at_the_measure_is_not_restated():
    section = SECTION.replace('w:right="1800"', 'w:right="720"').replace(
        'w:left="1800"', 'w:left="720"'
    )
    data, fit = fit_docx(package("<w:p/>", section=section), lines=None, font_points=0)
    assert fit.side_margin_inches == 0.0
    assert fit.reason == "nothing to fit"
    assert data == package("<w:p/>", section=section)


def test_turning_the_margin_off_leaves_the_ones_mathpix_wrote():
    data, fit = fit_docx(package("<w:p/>"), lines=None, font_points=0, side_margin_inches=0)
    assert fit.side_margin_inches == 0.0
    assert data == package("<w:p/>")


def test_the_margin_is_stated_in_the_record():
    _, fit = fit_docx(package("<w:p/>"), lines=None)
    assert fit.as_dict()["side_margin_inches"] == 0.5
