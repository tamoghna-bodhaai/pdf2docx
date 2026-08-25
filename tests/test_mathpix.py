"""The `mathpix` mode, and the promise it makes.

The promise is stronger than marker's. Mathpix returns a finished Word file, so
`document.docx` is not built here at all — it is Mathpix's own bytes, copied. The
tests are therefore mostly about restraint and accounting: that the options reach
Mathpix unaltered including ones this codebase has never heard of, that every
format Mathpix returned is on disk untouched, that the deliverable is byte for
byte what arrived, and that a format Mathpix did not produce reads as a fact
about the document rather than as a failure.

No network: the client is replaced, so what is under test is this application's
half of the arrangement.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import fitz
import pytest

from app import mathpix_client, pipeline
from app.mathpix_client import Applied, MathpixError, MathpixUnsupported, parse_status_response

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00"
    b"\x00\x00IEND\xaeB`\x82"
)

# Mathpix's Markdown, with the dollar delimiters the request asks for and the
# page break that `include_page_breaks` produces.
MMD = (
    "# Heat equation\n\n"
    "The diffusion of heat is governed by\n\n"
    "$$\\frac{\\partial u}{\\partial t} = \\alpha \\frac{\\partial^2 u}{\\partial x^2}$$\n\n"
    "![](https://cdn.mathpix.com/cropped/abc123.png?height=200&width=400)\n\n"
    "| a | b |\n|---|---|\n| 1 | 2 |\n\n"
    "\\pagebreak\n\n"
    "## Second page\n\nMore text with $x^2$ inline.\n"
)

DOCX_BYTES = b"PK\x03\x04-not-really-a-docx-but-these-exact-bytes-must-survive"

LINES_JSON = json.dumps({
    "pages": [
        {
            "page": 1,
            "image_width": 1190,
            "image_height": 1684,
            "lines": [
                {"type": "title", "text": "Heat equation",
                 "cnt": [[100, 90], [700, 90], [700, 140], [100, 140]]},
                {"type": "math", "text": "\\frac{\\partial u}{\\partial t}",
                 "region": {"top_left_x": 120, "top_left_y": 300, "width": 500, "height": 80}},
            ],
        },
        {
            "page": 2,
            "lines": [
                {"type": "section_header", "text": "Second page",
                 "region": {"top_left_x": 100, "top_left_y": 90, "width": 400, "height": 40}},
            ],
        },
    ]
})


class FakeClient:
    """Stands in for Mathpix, and records exactly what it was asked."""

    submissions: list[tuple] = []
    deleted: list[str] = []
    # Formats this fake will hand back. Anything else is reported missing, the
    # way Mathpix reports a format it did not produce for this document.
    available = {
        "docx": DOCX_BYTES,
        "mmd": MMD.encode("utf-8"),
        "md": MMD.encode("utf-8"),
        "lines.json": LINES_JSON.encode("utf-8"),
        "pdf": b"%PDF-1.7 fake",
        "tex.zip": b"PK\x03\x04fake-zip",
    }

    def __init__(self, *args, **kwargs):
        FakeClient.submissions = []
        FakeClient.deleted = []

    def submit(self, pdf_path, options=None):
        FakeClient.submissions.append((pdf_path, dict(options or {})))
        return "file-abc"

    def poll(self, file_id, on_status=None, deadline=None):
        state = parse_status_response(
            {"status": "completed", "percent_done": 100.0,
             "num_pages": 2, "num_pages_completed": 2, "formats": {}},
            file_id,
        )
        if on_status is not None:
            on_status(state)
        return state

    def fetch(self, file_id, ext):
        if ext in self.available:
            return self.available[ext]
        raise MathpixUnsupported(f".{ext} was not produced")

    def fetch_all(self, file_id, wanted, on_ready, deadline=None):
        missing = {}
        for ext in dict.fromkeys(wanted):
            try:
                on_ready(ext, self.fetch(file_id, ext))
            except MathpixError as exc:
                missing[ext] = str(exc)
        for required in mathpix_client.REQUIRED_RESULTS:
            if required in missing:
                raise MathpixError(f"mathpix produced no .{required}")
        return missing

    def download_images(self, markdown, work_dir):
        # The real client fetches from the CDN; here the rewrite is what matters.
        directory = work_dir / mathpix_client.RAW_DIR / mathpix_client.RAW_IMAGE_DIR
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "abc123.png").write_bytes(PNG_BYTES)
        target = "https://cdn.mathpix.com/cropped/abc123.png?height=200&width=400"
        local = f"{mathpix_client.RAW_DIR}/{mathpix_client.RAW_IMAGE_DIR}/abc123.png"
        return markdown.replace(target, local), Applied(images_downloaded=1)

    def delete(self, file_id):
        FakeClient.deleted.append(file_id)


def settings(**overrides):
    values = {
        "max_pages": 0,
        "mathpix_options": {},
        "mathpix_formats": (),
        "mathpix_improve": False,
        "mathpix_delete": True,
        "mathpix_poll_timeout": 30.0,
        "mathpix_page_rate": 0.0015,
        "fit_docx": True,
        "fit_max_image_fraction": 1.0,
        "fit_wrap_indent": 360,
        "fit_font_points": 10.0,
        "fit_side_margin_inches": 0.5,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def source_pdf(tmp_path, pages: int = 2):
    path = tmp_path / "source.pdf"
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page(width=595, height=842)
    doc.save(path)
    doc.close()
    return path


def prepare(monkeypatch, **overrides):
    monkeypatch.setattr(pipeline, "settings", settings(**overrides))
    monkeypatch.setattr(pipeline.mathpix, "MathpixClient", FakeClient)


def convert(tmp_path, monkeypatch, **overrides):
    prepare(monkeypatch, **overrides)
    pdf = source_pdf(tmp_path)
    work = tmp_path / "job"
    result = pipeline.convert_pdf(pdf_path=pdf, work_dir=work)
    return result, work


# -- the promise --------------------------------------------------------------- #


def test_the_docx_is_mathpix_own_file_byte_for_byte(tmp_path, monkeypatch):
    """The document is not built here. Mathpix's own file is kept as it arrived."""
    result, work = convert(tmp_path, monkeypatch)
    assert (work / mathpix_client.RAW_DIR / "document.docx").read_bytes() == DOCX_BYTES
    # These bytes are not a readable .docx, so there is nothing to fit and the
    # downloadable copy is Mathpix's file unchanged.
    assert result.docx_path.read_bytes() == DOCX_BYTES


def test_a_document_that_cannot_be_read_is_still_delivered(tmp_path, monkeypatch):
    """A .docx this codebase cannot parse is passed on, not withheld."""
    _, work = convert(tmp_path, monkeypatch)
    record = json.loads((work / mathpix_client.RAW_DIR / "metadata.json").read_text())
    assert record["fit"]["applied"] is False
    assert record["fit"]["reason"]
    assert record["document_docx"] == "mathpix, unedited"


def test_fitting_can_be_turned_off_entirely(tmp_path, monkeypatch):
    _, work = convert(tmp_path, monkeypatch, fit_docx=False)
    record = json.loads((work / mathpix_client.RAW_DIR / "metadata.json").read_text())
    assert record["fit"]["reason"] == "fitting disabled"


def test_every_format_is_written_untouched(tmp_path, monkeypatch):
    _, work = convert(tmp_path, monkeypatch)
    raw = work / mathpix_client.RAW_DIR
    assert (raw / "document.mmd").read_bytes() == MMD.encode("utf-8")
    assert (raw / "document.pdf").read_bytes() == b"%PDF-1.7 fake"
    assert (raw / "document.tex.zip").read_bytes() == b"PK\x03\x04fake-zip"
    assert (raw / "document.lines.json").read_bytes() == LINES_JSON.encode("utf-8")


def test_the_only_edits_are_images_and_the_page_split(tmp_path, monkeypatch):
    """document.md differs from Mathpix's own copy in exactly the recorded ways."""
    _, work = convert(tmp_path, monkeypatch)
    saved = (work / "document.md").read_text(encoding="utf-8")
    original = (work / mathpix_client.RAW_DIR / "document.mmd").read_text(encoding="utf-8")

    # The maths is Mathpix's, character for character — it is not translated here.
    assert "$$\\frac{\\partial u}{\\partial t}" in saved
    assert "$x^2$" in saved
    # The only difference is where the image points.
    assert saved.replace(
        f"{mathpix_client.RAW_DIR}/{mathpix_client.RAW_IMAGE_DIR}/abc123.png",
        "https://cdn.mathpix.com/cropped/abc123.png?height=200&width=400",
    ) == original


def test_the_metadata_accounts_for_what_was_done(tmp_path, monkeypatch):
    _, work = convert(tmp_path, monkeypatch)
    meta = json.loads((work / mathpix_client.RAW_DIR / "metadata.json").read_text())
    assert meta["document_docx"] == "mathpix, unedited"
    assert meta["file_id"] == "file-abc"
    assert meta["applied"]["images_downloaded"] == 1
    assert meta["applied"]["pages"] == 2
    assert meta["applied"]["paginated"] is True
    assert "rebuilt_docx" not in meta


# -- the options ---------------------------------------------------------------- #


def test_unknown_options_reach_mathpix_intact(tmp_path, monkeypatch):
    """An option this codebase has never heard of is Mathpix's business, not ours."""
    convert(tmp_path, monkeypatch, mathpix_options={
        "some_option_added_next_year": {"deeply": ["nested", 1, True]},
        "rm_spaces": False,
    })
    _, options = FakeClient.submissions[0]
    assert options["some_option_added_next_year"] == {"deeply": ["nested", 1, True]}
    assert options["rm_spaces"] is False


def test_the_users_options_win_over_the_defaults(tmp_path, monkeypatch):
    convert(tmp_path, monkeypatch, mathpix_options={"math_inline_delimiters": ["\\(", "\\)"]})
    _, options = FakeClient.submissions[0]
    assert options["math_inline_delimiters"] == ["\\(", "\\)"]


def test_dollar_delimiters_and_page_breaks_are_asked_for(tmp_path, monkeypatch):
    """The two defaults without which this codebase cannot read the result."""
    convert(tmp_path, monkeypatch)
    _, options = FakeClient.submissions[0]
    assert options["math_inline_delimiters"] == ["$", "$"]
    assert options["math_display_delimiters"] == ["$$", "$$"]
    assert options["include_page_breaks"] is True


def test_retention_is_off_unless_asked_for(tmp_path, monkeypatch):
    convert(tmp_path, monkeypatch)
    assert FakeClient.submissions[0][1]["metadata"]["improve_mathpix"] is False
    convert(tmp_path, monkeypatch, mathpix_improve=True)
    assert FakeClient.submissions[0][1]["metadata"]["improve_mathpix"] is True


def test_legacy_config_defaults_still_include_docx(tmp_path, monkeypatch):
    """Direct callers without a per-job selection retain their old default."""
    convert(tmp_path, monkeypatch, mathpix_formats=("html", "pptx"))
    formats = FakeClient.submissions[0][1]["conversion_formats"]
    assert formats["docx"] is True
    assert formats["html"] is True

    convert(tmp_path, monkeypatch, mathpix_options={"conversion_formats": {"docx": False}})
    assert FakeClient.submissions[0][1]["conversion_formats"]["docx"] is True


def test_an_empty_format_setting_means_all_of_them(tmp_path, monkeypatch):
    convert(tmp_path, monkeypatch, mathpix_formats=())
    formats = FakeClient.submissions[0][1]["conversion_formats"]
    assert set(formats) == {entry.key for entry in mathpix_client.FORMATS if entry.requested}


def test_the_page_cap_is_one_based(tmp_path, monkeypatch):
    """Mathpix counts pages from one; marker's `page_range` counts from zero."""
    convert(tmp_path, monkeypatch, max_pages=3)
    assert FakeClient.submissions[0][1]["page_ranges"] == "1-3"


# -- what came back, and what did not -------------------------------------------- #


def test_a_format_mathpix_did_not_produce_is_recorded_not_raised(tmp_path, monkeypatch):
    """A document with no tables has no .xlsx. That is not a failed conversion."""
    result, work = convert(tmp_path, monkeypatch)
    meta = json.loads((work / mathpix_client.RAW_DIR / "metadata.json").read_text())
    assert "xlsx" in meta["formats_missing"]
    assert "xlsx" not in meta["formats"]
    # ...and the job still produced everything that matters.
    assert result.docx_path.exists()
    assert "docx" in meta["formats"]


def test_a_successful_rerun_removes_formats_left_by_the_previous_run(tmp_path, monkeypatch):
    prepare(monkeypatch)
    pdf = source_pdf(tmp_path)
    work = tmp_path / "job"
    raw = work / mathpix_client.RAW_DIR
    raw.mkdir(parents=True)
    (raw / "document.xlsx").write_bytes(b"old table export")

    pipeline.convert_pdf(pdf_path=pdf, work_dir=work)

    assert not (raw / "document.xlsx").exists()
    assert (raw / "document.docx").read_bytes() == DOCX_BYTES


def test_a_non_docx_selection_completes_with_only_preview_and_selected_outputs(
    tmp_path, monkeypatch
):
    available = {
        "mmd": MMD.encode("utf-8"),
        "html": b"<h1>Heat equation</h1>",
        "lines.json": LINES_JSON.encode("utf-8"),
    }
    monkeypatch.setattr(FakeClient, "available", available)
    prepare(monkeypatch)
    pdf = source_pdf(tmp_path)
    work = tmp_path / "job"

    result = pipeline.convert_pdf(
        pdf_path=pdf,
        work_dir=work,
        mathpix_formats=("html",),
    )

    assert result.docx_path is None
    assert result.markdown_path.exists()
    assert not (work / "document.docx").exists()
    assert (work / mathpix_client.RAW_DIR / "document.html").read_bytes() == available["html"]
    assert not (work / mathpix_client.RAW_DIR / "document.docx").exists()
    assert FakeClient.submissions[0][1]["conversion_formats"] == {"html": True}
    metadata = json.loads(
        (work / mathpix_client.RAW_DIR / "metadata.json").read_text()
    )
    assert metadata["requested_formats"] == ["html"]
    assert "docx" not in metadata["formats_missing"]
    assert "pptx" not in metadata["formats_missing"]


def test_an_empty_selection_completes_with_only_always_produced_preview_data(
    tmp_path, monkeypatch
):
    available = {
        "mmd": MMD.encode("utf-8"),
        "lines.json": LINES_JSON.encode("utf-8"),
        "lines.mmd.json": b'{"pages": []}',
    }
    monkeypatch.setattr(FakeClient, "available", available)
    prepare(monkeypatch)
    pdf = source_pdf(tmp_path)
    work = tmp_path / "job"

    result = pipeline.convert_pdf(
        pdf_path=pdf,
        work_dir=work,
        mathpix_formats=(),
    )

    assert result.docx_path is None
    assert result.markdown_path.exists()
    assert FakeClient.submissions[0][1]["conversion_formats"] == {}
    assert sorted(path.name for path in (work / mathpix_client.RAW_DIR).glob("document.*")) == [
        "document.lines.json",
        "document.lines.mmd.json",
        "document.mmd",
    ]
    metadata = json.loads(
        (work / mathpix_client.RAW_DIR / "metadata.json").read_text()
    )
    assert metadata["requested_formats"] == []
    assert metadata["formats_missing"] == {}


def test_missing_mathpix_markdown_fails_the_preview_job(tmp_path, monkeypatch):
    available = dict(FakeClient.available)
    available.pop("mmd")
    monkeypatch.setattr(FakeClient, "available", available)
    with pytest.raises(MathpixError, match="no .mmd"):
        convert(tmp_path, monkeypatch)


def test_the_pages_are_split_on_mathpix_own_break(tmp_path, monkeypatch):
    result, _ = convert(tmp_path, monkeypatch)
    assert len(result.page_markdown) == 2
    assert "Heat equation" in result.page_markdown[0]
    assert "Second page" in result.page_markdown[1]


def test_missing_page_breaks_keep_preview_navigation_at_the_source_page_count(
    tmp_path, monkeypatch
):
    available = dict(FakeClient.available)
    available["mmd"] = b"# Mathpix returned one unsplit stream"
    monkeypatch.setattr(FakeClient, "available", available)

    result, work = convert(tmp_path, monkeypatch)
    preview = json.loads((work / "detection.json").read_text())

    assert len(result.page_markdown) == len(preview["pages"]) == 2
    assert "unsplit stream" in preview["pages"][0]["markdown"]
    assert preview["pages"][1]["markdown"] == ""


def test_no_comparison_docx_is_rebuilt_for_a_new_job(tmp_path, monkeypatch):
    _, work = convert(tmp_path, monkeypatch)
    assert not (work / "rebuilt.docx").exists()


def test_the_uploaded_document_is_deleted_afterwards(tmp_path, monkeypatch):
    convert(tmp_path, monkeypatch)
    assert FakeClient.deleted == ["file-abc"]
    convert(tmp_path, monkeypatch, mathpix_delete=False)
    assert FakeClient.deleted == []


def test_the_uploaded_document_is_deleted_when_collection_fails(tmp_path, monkeypatch):
    def fail_collection(self, file_id, wanted, on_ready, deadline=None):
        raise MathpixError("network failed while downloading exports")

    monkeypatch.setattr(FakeClient, "fetch_all", fail_collection)
    with pytest.raises(MathpixError, match="network failed"):
        convert(tmp_path, monkeypatch)
    assert FakeClient.deleted == ["file-abc"]


# -- the page viewer ------------------------------------------------------------- #


def test_lines_json_stays_raw_but_never_becomes_preview_boxes(tmp_path, monkeypatch):
    _, work = convert(tmp_path, monkeypatch)
    assert (work / mathpix_client.RAW_DIR / "document.lines.json").read_bytes() == LINES_JSON.encode()
    written = json.loads((work / "detection.json").read_text())
    assert written["mode"] == "mathpix"
    first = written["pages"][0]
    assert first["width"] == 595 and first["height"] == 842
    assert all(not page["blocks"] for page in written["pages"])


def test_preview_pages_keep_rich_markdown_and_local_images_aligned(tmp_path, monkeypatch):
    result, work = convert(tmp_path, monkeypatch)
    written = json.loads((work / "detection.json").read_text())

    assert len(written["pages"]) == len(result.page_markdown) == 2
    first, second = written["pages"]
    assert first["number"] == 1 and second["number"] == 2
    assert "# Heat equation" in first["markdown"]
    assert "$$\\frac{\\partial u}{\\partial t}" in first["markdown"]
    assert "| a | b |" in first["markdown"]
    assert "mathpix/images/abc123.png" in first["markdown"]
    assert "## Second page" in second["markdown"]


def test_pages_without_geometry_still_appear(tmp_path, monkeypatch):
    """No lines.json is a viewer without boxes, never a failed job."""
    available = dict(FakeClient.available)
    available.pop("lines.json")
    monkeypatch.setattr(FakeClient, "available", available)
    result, work = convert(tmp_path, monkeypatch)
    written = json.loads((work / "detection.json").read_text())
    assert len(written["pages"]) == len(result.page_markdown)
    assert all(not page["blocks"] for page in written["pages"])
    assert written["pages"][0]["markdown"]


# -- cost ------------------------------------------------------------------------ #


def test_the_cost_is_an_estimate_and_says_so(tmp_path, monkeypatch):
    """Mathpix bills per page, so the figure is derived and flagged as unpriced."""
    result, _ = convert(tmp_path, monkeypatch)
    assert result.usage.cost == pytest.approx(2 * 0.0015)
    # `calls` without `priced_calls` is how this codebase says "really charged,
    # but not a figure the provider reported".
    assert result.usage.calls == 1
    assert result.usage.priced_calls == 0


def test_a_blank_page_is_reported_per_page(tmp_path, monkeypatch):
    available = dict(FakeClient.available)
    available["mmd"] = b"# Only page one\n\n\\pagebreak\n\n   \n"
    monkeypatch.setattr(FakeClient, "available", available)
    result, _ = convert(tmp_path, monkeypatch)
    reasons = {item.page: item.fallback_reason for item in result.diagnostics}
    assert reasons == {1: None, 2: "empty_output"}
    assert all(item.extractor == "mathpix" for item in result.diagnostics)


# -- the columns the source page was set in --------------------------------------


def test_the_column_choice_reaches_the_fitting(tmp_path, monkeypatch):
    """`convert_pdf` is the seam; the option has to survive all three hops."""
    prepare(monkeypatch)
    seen = {}
    real = pipeline.docx_fit.fit_docx

    def watch(data, **kwargs):
        seen.update(kwargs)
        return real(data, **kwargs)

    monkeypatch.setattr(pipeline.docx_fit, "fit_docx", watch)
    pipeline.convert_pdf(
        pdf_path=source_pdf(tmp_path),
        work_dir=tmp_path / "job",
        multi_column=True,
    )
    assert seen["multi_column"] is True


def test_a_conversion_is_single_column_unless_asked(tmp_path, monkeypatch):
    prepare(monkeypatch)
    seen = {}
    real = pipeline.docx_fit.fit_docx

    def watch(data, **kwargs):
        seen.update(kwargs)
        return real(data, **kwargs)

    monkeypatch.setattr(pipeline.docx_fit, "fit_docx", watch)
    pipeline.convert_pdf(pdf_path=source_pdf(tmp_path), work_dir=tmp_path / "job")
    assert seen["multi_column"] is False


def test_re_fitting_reads_mathpix_own_export_and_never_writes_it(tmp_path, monkeypatch):
    """No submit, no poll, no charge — only the delivered copy is rebuilt."""
    prepare(monkeypatch)
    _, work = convert(tmp_path, monkeypatch)
    raw = work / mathpix_client.RAW_DIR / "document.docx"
    before = raw.read_bytes()

    def fail(*args, **kwargs):  # pragma: no cover - the failure is the assertion
        raise AssertionError("re-fitting must not submit anything")

    monkeypatch.setattr(pipeline.mathpix, "MathpixClient", fail)
    fit = pipeline.refit_docx(work)

    assert raw.read_bytes() == before
    record = json.loads((work / mathpix_client.RAW_DIR / "metadata.json").read_text())
    assert record["fit"] == fit.as_dict()
    # These fixture bytes are not a readable .docx, so the delivered copy is
    # Mathpix's file unchanged — and the record says so rather than claiming a fit.
    assert (work / "document.docx").read_bytes() == before
    assert record["document_docx"] == "mathpix, unedited"


def test_re_fitting_a_job_that_has_no_export_refuses(tmp_path, monkeypatch):
    prepare(monkeypatch)
    work = tmp_path / "empty"
    (work / mathpix_client.RAW_DIR).mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        pipeline.refit_docx(work)
