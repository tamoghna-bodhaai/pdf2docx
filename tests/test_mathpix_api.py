"""The routes a `mathpix` job is served through: exports, assets, credentials.

Mathpix returns a dozen formats from one conversion, so nothing here is written
out by hand — the download table is generated from the client's own format table,
and these tests are largely about that generation staying honest as the table
grows. The rest is about a job being able to say which exports it actually has,
because a format Mathpix did not produce for a document is a fact about the
document rather than a failure.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from app import main, pipeline
from app.mathpix_client import BY_EXT, FORMATS, RAW_DIR, RAW_IMAGE_DIR
from app.mathpix_client import RAW_DIR as MATHPIX_RAW_DIR


@pytest.fixture
def job(tmp_path, monkeypatch, user):
    """A finished mathpix job holding only some of the exports."""
    directory = tmp_path / "job"
    (directory / RAW_DIR / RAW_IMAGE_DIR).mkdir(parents=True)

    (directory / "document.docx").write_bytes(b"the docx")
    (directory / "document.md").write_text("# Heading")
    (directory / "rebuilt.docx").write_bytes(b"our render")
    raw = directory / RAW_DIR
    (raw / "document.docx").write_bytes(b"the docx")
    (raw / "document.mmd").write_text("# Heading")
    (raw / "document.tex.zip").write_bytes(b"PK\x03\x04")
    (raw / "document.lines.json").write_text('{"pages": []}')
    (raw / "metadata.json").write_text(json.dumps({"document_docx": "mathpix, unedited"}))
    (raw / RAW_IMAGE_DIR / "a.png").write_bytes(b"\x89PNG\r\n\x1a\nnot-really")
    (directory / "secret.txt").write_text("private")

    record = main.Job(
        id="mathpixjob", user_id=user.id, filename="paper.pdf", pages=2,
        directory=directory, status="done", layout="mathpix",
    )
    monkeypatch.setitem(main.JOBS, record.id, record)
    return record


# -- the generated download table ------------------------------------------------ #


def test_every_format_in_the_table_can_be_downloaded():
    """A row added to the client's table must not need a second edit here."""
    for entry in FORMATS:
        assert f"mathpix-{entry.ext}" in main.DOWNLOADS


def test_each_export_keeps_its_own_name_and_media_type():
    names = {}
    for entry in FORMATS:
        path, media_type, suffix = main.DOWNLOADS[f"mathpix-{entry.ext}"]
        assert path == f"{RAW_DIR}/document.{entry.ext}"
        assert media_type == entry.media_type
        names[suffix] = entry.ext
    # No two exports may be saved under the same filename.
    assert len(names) == len(FORMATS)


def test_the_config_lists_the_formats_the_browser_labels_buttons_from(client):
    body = client.get("/api/config").json()
    assert [entry["ext"] for entry in body["mathpix_formats"]] == [e.ext for e in FORMATS]
    assert "docx" in body["mathpix_requested"]
    assert "mathpix_key_configured" in body
    # The key itself is never reported, only whether there is one.
    assert "mathpix_app_key" not in json.dumps(body)
    assert body["provider"] == "Mathpix"
    assert {"model", "layout", "columns", "api_key_configured"}.isdisjoint(body)
    by_ext = {entry["ext"]: entry for entry in body["mathpix_formats"]}
    assert by_ext["docx"]["requestable"] is True
    assert by_ext["mmd"]["requestable"] is False
    assert by_ext["lines.json"]["always"] is True


def test_the_openrouter_model_endpoint_is_not_exposed(client):
    assert client.get("/api/models").status_code == 404


# -- what a job actually has ------------------------------------------------------ #


def test_a_job_reports_only_the_exports_it_has(job):
    assert job.as_dict()["mathpix_formats"] == ["docx", "mmd", "tex.zip", "lines.json"]
    assert job.as_dict()["has_rebuilt"] is True


def test_requested_formats_survive_the_job_record_round_trip(job):
    job.requested_formats = ("html", "pptx")

    restored = main.Job.from_record(job.to_record())

    assert restored.requested_formats == ("html", "pptx")
    assert restored.as_dict()["requested_formats"] == ["html", "pptx"]


def test_historical_records_infer_requestable_outputs_from_existing_files(job):
    record = job.to_record()
    record.pop("requested_formats")

    restored = main.Job.from_record(record)

    assert restored.requested_formats == ("docx", "tex.zip")


def test_an_export_this_document_has_downloads(client, job):
    reply = client.get(f"/api/jobs/{job.id}/download?format=mathpix-tex.zip")
    assert reply.status_code == 200
    assert reply.content == b"PK\x03\x04"
    assert reply.headers["content-type"] == BY_EXT["tex.zip"].media_type
    assert 'filename="paper.mathpix.tex.zip"' in reply.headers["content-disposition"]


def test_the_deliverable_is_mathpix_own_bytes(client, job):
    """`document.docx` is the copy, and the two must not have drifted."""
    served = client.get(f"/api/jobs/{job.id}/download?format=docx").content
    verbatim = client.get(f"/api/jobs/{job.id}/download?format=mathpix-docx").content
    assert served == verbatim == b"the docx"
    reply = client.get(f"/api/jobs/{job.id}/download?format=docx")
    assert reply.headers["content-type"] == BY_EXT["docx"].media_type
    assert 'filename="paper.docx"' in reply.headers["content-disposition"]


def test_the_rebuilt_render_is_offered_separately(client, job):
    """Historical comparison files remain reachable even though new jobs do not make them."""
    reply = client.get(f"/api/jobs/{job.id}/download?format=rebuilt-docx")
    assert reply.status_code == 200
    assert reply.content == b"our render"


def test_an_export_this_document_has_not_is_not_an_error(client, job):
    """A document with no tables has no .xlsx. The route says so plainly."""
    reply = client.get(f"/api/jobs/{job.id}/download?format=mathpix-xlsx")
    assert reply.status_code == 409
    assert "not ready" in reply.json()["detail"]


def test_a_format_nobody_offers_is_still_refused(client, job):
    assert client.get(f"/api/jobs/{job.id}/download?format=mathpix-exe").status_code == 400


# -- assets ----------------------------------------------------------------------- #


def test_mathpix_images_can_be_fetched_for_the_preview(client, job):
    reply = client.get(f"/api/jobs/{job.id}/asset/{RAW_DIR}/{RAW_IMAGE_DIR}/a.png")
    assert reply.status_code == 200


@pytest.mark.parametrize("asset", [
    "secret.txt",
    f"{RAW_DIR}/metadata.json",
    f"{RAW_DIR}/{RAW_IMAGE_DIR}/../../../secret.txt",
])
def test_nothing_else_in_the_job_is_reachable(client, job, asset):
    assert client.get(f"/api/jobs/{job.id}/asset/{asset}").status_code == 404


# -- credentials ------------------------------------------------------------------ #


def _settings(monkeypatch, **overrides):
    """`Settings` is frozen, so a test swaps the whole object rather than a field."""
    monkeypatch.setattr(main, "settings", dataclasses.replace(main.settings, **overrides))


def test_a_mathpix_job_without_a_mathpix_key_is_refused(client, monkeypatch, tmp_path):
    _settings(monkeypatch, mathpix_app_key="", data_dir=tmp_path)
    reply = client.post(
        "/api/convert",
        files={"file": ("a.pdf", b"%PDF-1.7\n", "application/pdf")},
    )
    assert reply.status_code == 503
    assert "MATHPIX_APP_KEY" in reply.json()["detail"]


def test_the_legacy_form_fields_are_accepted_and_ignored(client, monkeypatch, tmp_path):
    """A client written against the older API still gets a job, not a 400."""
    _settings(monkeypatch, mathpix_app_key="a-key", data_dir=tmp_path)
    reply = client.post(
        "/api/convert",
        files={"file": ("a.pdf", _one_page_pdf(), "application/pdf")},
        data={"model": "anthropic/claude-unused", "columns": "multi"},
    )
    assert reply.status_code == 200
    body = reply.json()
    assert body["layout"] == "mathpix"
    # Neither field survives onto the record, so neither can be acted on later.
    assert {"model", "columns", "source_columns"}.isdisjoint(body)


@pytest.mark.parametrize("layout", ["structured", "replica", "flow", "marker", "sideways"])
def test_explicit_non_mathpix_layouts_are_rejected(client, monkeypatch, tmp_path, layout):
    _settings(monkeypatch, mathpix_app_key="a-key", data_dir=tmp_path)
    reply = client.post(
        "/api/convert",
        files={"file": ("a.pdf", _one_page_pdf(), "application/pdf")},
        data={"layout": layout},
    )
    assert reply.status_code == 400
    assert "mathpix" in reply.json()["detail"].lower()


def test_omitted_start_formats_keep_the_configured_default(
    client, user, monkeypatch, tmp_path
):
    _settings(
        monkeypatch,
        mathpix_app_key="a-key",
        mathpix_formats=("html",),
        data_dir=tmp_path,
    )
    directory = tmp_path / "default-formats"
    directory.mkdir()
    (directory / "source.pdf").write_bytes(_one_page_pdf())
    job = main.Job(
        id="defaultformats",
        user_id=user.id,
        filename="paper.pdf",
        pages=1,
        directory=directory,
    )
    monkeypatch.setitem(main.JOBS, job.id, job)
    called = {}

    def fake_convert_pdf(**kwargs):
        called.update(kwargs)
        raise RuntimeError("stop after observing the public interface")

    monkeypatch.setattr(main, "convert_pdf", fake_convert_pdf)

    reply = client.post(f"/api/jobs/{job.id}/start")

    assert reply.status_code == 200
    assert reply.json()["requested_formats"] == ["docx", "html"]
    assert called["mathpix_formats"] == ("docx", "html")


def test_start_accepts_multiple_exact_formats(client, user, monkeypatch, tmp_path):
    _settings(monkeypatch, mathpix_app_key="a-key", data_dir=tmp_path)
    directory = tmp_path / "multiple-formats"
    directory.mkdir()
    (directory / "source.pdf").write_bytes(_one_page_pdf())
    job = main.Job(
        id="multipleformats",
        user_id=user.id,
        filename="paper.pdf",
        pages=1,
        directory=directory,
    )
    monkeypatch.setitem(main.JOBS, job.id, job)
    called = {}

    def fake_convert_pdf(**kwargs):
        called.update(kwargs)
        raise RuntimeError("stop after observing the public interface")

    monkeypatch.setattr(main, "convert_pdf", fake_convert_pdf)

    reply = client.post(
        f"/api/jobs/{job.id}/start",
        data={"formats": "docx, html,pptx,html"},
    )

    assert reply.status_code == 200
    assert reply.json()["requested_formats"] == ["docx", "html", "pptx"]
    assert called["mathpix_formats"] == ("docx", "html", "pptx")


def test_present_empty_start_formats_request_only_always_produced_outputs(
    client, user, monkeypatch, tmp_path
):
    _settings(monkeypatch, mathpix_app_key="a-key", data_dir=tmp_path)
    directory = tmp_path / "no-optional-formats"
    directory.mkdir()
    (directory / "source.pdf").write_bytes(_one_page_pdf())
    job = main.Job(
        id="nooptionalformats",
        user_id=user.id,
        filename="paper.pdf",
        pages=1,
        directory=directory,
    )
    monkeypatch.setitem(main.JOBS, job.id, job)
    called = {}

    def fake_convert_pdf(**kwargs):
        called.update(kwargs)
        raise RuntimeError("stop after observing the public interface")

    monkeypatch.setattr(main, "convert_pdf", fake_convert_pdf)

    reply = client.post(f"/api/jobs/{job.id}/start", data={"formats": ""})

    assert reply.status_code == 200
    assert reply.json()["requested_formats"] == []
    assert called["mathpix_formats"] == ()


@pytest.mark.parametrize("formats", ["docx,exe", "mmd", "lines.json"])
def test_start_rejects_unknown_and_non_requestable_formats(
    client, user, monkeypatch, tmp_path, formats
):
    _settings(monkeypatch, mathpix_app_key="a-key", data_dir=tmp_path)
    directory = tmp_path / f"invalid-formats-{formats.replace('.', '-').replace(',', '-')}"
    directory.mkdir()
    (directory / "source.pdf").write_bytes(_one_page_pdf())
    job = main.Job(
        id=f"invalid{len(formats)}",
        user_id=user.id,
        filename="paper.pdf",
        pages=1,
        directory=directory,
    )
    monkeypatch.setitem(main.JOBS, job.id, job)

    reply = client.post(f"/api/jobs/{job.id}/start", data={"formats": formats})

    assert reply.status_code == 400
    detail = reply.json()["detail"]
    assert "Supported values" in detail
    assert "docx" in detail and "html" in detail
    assert job.status == "ready"


def test_non_pdf_uploads_are_rejected_before_staging(client, monkeypatch, tmp_path):
    _settings(monkeypatch, mathpix_app_key="a-key", data_dir=tmp_path)
    reply = client.post(
        "/api/convert",
        files={"file": ("notes.txt", b"not a pdf", "text/plain")},
    )
    assert reply.status_code == 400
    assert ".pdf" in reply.json()["detail"]


def test_rerunning_a_historical_job_is_pinned_to_mathpix(client, user, monkeypatch, tmp_path):
    _settings(monkeypatch, mathpix_app_key="a-key", data_dir=tmp_path)
    directory = tmp_path / "legacy"
    directory.mkdir()
    (directory / "source.pdf").write_bytes(_one_page_pdf())
    (directory / RAW_DIR).mkdir()
    (directory / RAW_DIR / "document.xlsx").write_bytes(b"stale table export")
    (directory / "document.docx").write_bytes(b"stale primary")
    (directory / "document.md").write_text("stale preview")
    (directory / "detection.json").write_text('{"mode":"structured","pages":[]}')
    (directory / "rebuilt.docx").write_bytes(b"historical comparison")
    legacy = main.Job(
        id="legacyjob", user_id=user.id, filename="old.pdf", pages=1, directory=directory,
        status="done", layout="structured",
    )
    monkeypatch.setitem(main.JOBS, legacy.id, legacy)
    called = {}

    def fake_convert_pdf(**kwargs):
        called.update(kwargs)
        staged = kwargs["work_dir"]
        (staged / RAW_DIR).mkdir()
        (staged / RAW_DIR / "document.xlsx").write_bytes(b"partial replacement")
        (staged / "document.docx").write_bytes(b"partial primary")
        raise RuntimeError("replacement failed after partial writes")

    monkeypatch.setattr(main, "convert_pdf", fake_convert_pdf)
    # The current browser sends no legacy fields at all. An omitted layout must
    # still override the historical job's stored layout with Mathpix.
    reply = client.post(f"/api/jobs/{legacy.id}/start")

    assert reply.status_code == 200
    assert legacy.layout == "mathpix"
    # convert_pdf no longer takes a layout: there is one backend, so a rerun
    # cannot be pointed at the mode the historical record names.
    assert {"layout", "model", "columns"}.isdisjoint(called)
    assert called["work_dir"] != directory
    assert legacy.status == "error"
    # The old successful outputs survive until the replacement conversion has
    # itself succeeded; a transient failure must not destroy valid downloads.
    assert legacy.mathpix_formats() == ["xlsx"]
    assert (directory / "document.docx").read_bytes() == b"stale primary"
    assert (directory / "document.md").read_text() == "stale preview"
    assert (directory / "detection.json").exists()
    assert (directory / "rebuilt.docx").read_bytes() == b"historical comparison"


def test_rerun_rejects_an_explicit_legacy_layout(client, user, monkeypatch, tmp_path):
    _settings(monkeypatch, mathpix_app_key="a-key", data_dir=tmp_path)
    directory = tmp_path / "legacy-rejected"
    directory.mkdir()
    (directory / "source.pdf").write_bytes(_one_page_pdf())
    legacy = main.Job(
        id="legacyrejected", user_id=user.id, filename="old.pdf", pages=1, directory=directory,
        status="done", layout="replica",
    )
    monkeypatch.setitem(main.JOBS, legacy.id, legacy)
    reply = client.post(f"/api/jobs/{legacy.id}/start", data={"layout": "flow"})
    assert reply.status_code == 400
    assert legacy.layout == "replica"


def _one_page_pdf() -> bytes:
    import fitz

    document = fitz.open()
    document.new_page(width=595, height=842)
    data = document.tobytes()
    document.close()
    return data


def test_interrupted_job_directory_promotion_is_recovered(tmp_path):
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    backup = jobs / ".abc123-previous-token"
    backup.mkdir()
    (backup / "source.pdf").write_bytes(b"old valid job")
    abandoned = jobs / ".abc123-run-token"
    abandoned.mkdir()
    (abandoned / "document.docx").write_bytes(b"partial replacement")

    main._recover_interrupted_promotions(jobs)

    assert (jobs / "abc123" / "source.pdf").read_bytes() == b"old valid job"
    assert not backup.exists()
    assert not abandoned.exists()


def test_a_job_is_not_reported_done_before_its_outputs_are_in_place(
    tmp_path, monkeypatch, user
):
    """The pipeline's own "done" must not reach the browser.

    Every mode signals "done" when its work in the staging directory is over,
    which for mathpix is still one remote deletion and two directory renames
    away from the results existing where a download route would look. A poll
    that saw "done" there would find no outputs — and the browser stops polling
    at "done", so it would keep showing that empty result for good.
    """
    jobs = tmp_path / "jobs"
    directory = jobs / "settling"
    directory.mkdir(parents=True)
    pdf_path = directory / "source.pdf"
    pdf_path.write_bytes(_one_page_pdf())

    job = main.Job(
        id="settling", user_id=user.id, filename="paper.pdf", pages=1,
        directory=directory, status="queued", requested_formats=("docx",),
    )
    monkeypatch.setitem(main.JOBS, job.id, job)

    seen: dict = {}

    def fake_convert(*, pdf_path, work_dir, on_progress, **kwargs):
        markdown = work_dir / "document.md"
        markdown.write_text("# Converted", encoding="utf-8")
        (work_dir / "detection.json").write_text('{"mode": "mathpix", "pages": []}')
        (work_dir / MATHPIX_RAW_DIR).mkdir(parents=True, exist_ok=True)
        (work_dir / MATHPIX_RAW_DIR / "document.docx").write_bytes(b"the docx")
        # Where the mathpix mode then deletes the remote upload, and where the
        # caller has yet to promote any of the above into `directory`.
        on_progress("done", 1, 1)
        seen["mid_run"] = main.JOBS[job.id].as_dict()
        return pipeline.ConversionResult(markdown_path=markdown, docx_path=None)

    monkeypatch.setattr(main, "convert_pdf", fake_convert)
    main._run_job(job.id, pdf_path)

    assert seen["mid_run"]["status"] != "done"
    assert seen["mid_run"]["has_md"] is False
    # And the finished job is only ever seen with everything it produced.
    finished = job.as_dict()
    assert finished["status"] == "done"
    assert finished["has_md"] is True
    assert finished["has_detection"] is True
    assert "docx" in finished["mathpix_formats"]


# -- laying the document out in the source's columns ------------------------------


def test_the_column_toggle_reaches_the_pipeline(client, user, monkeypatch, tmp_path):
    _settings(monkeypatch, mathpix_app_key="a-key", data_dir=tmp_path)
    directory = tmp_path / "columns"
    directory.mkdir()
    (directory / "source.pdf").write_bytes(_one_page_pdf())
    job = main.Job(
        id="columnsjob", user_id=user.id, filename="paper.pdf", pages=1,
        directory=directory,
    )
    monkeypatch.setitem(main.JOBS, job.id, job)
    called = {}

    def fake_convert_pdf(**kwargs):
        called.update(kwargs)
        raise RuntimeError("stop after observing the public interface")

    monkeypatch.setattr(main, "convert_pdf", fake_convert_pdf)

    reply = client.post(f"/api/jobs/{job.id}/start", data={"multi_column": "true"})

    assert reply.status_code == 200
    assert reply.json()["multi_column"] is True
    assert called["multi_column"] is True


def test_a_conversion_is_single_column_unless_it_is_asked_not_to_be(
    client, user, monkeypatch, tmp_path
):
    _settings(monkeypatch, mathpix_app_key="a-key", data_dir=tmp_path)
    directory = tmp_path / "one-column"
    directory.mkdir()
    (directory / "source.pdf").write_bytes(_one_page_pdf())
    job = main.Job(
        id="onecolumnjob", user_id=user.id, filename="paper.pdf", pages=1,
        directory=directory,
    )
    monkeypatch.setitem(main.JOBS, job.id, job)
    called = {}

    def fake_convert_pdf(**kwargs):
        called.update(kwargs)
        raise RuntimeError("stop after observing the public interface")

    monkeypatch.setattr(main, "convert_pdf", fake_convert_pdf)

    reply = client.post(f"/api/jobs/{job.id}/start")

    assert reply.json()["multi_column"] is False
    assert called["multi_column"] is False


def test_the_column_choice_survives_the_job_record_round_trip(job):
    job.multi_column = True

    restored = main.Job.from_record(job.to_record())

    assert restored.multi_column is True
    assert restored.as_dict()["multi_column"] is True


def test_a_record_written_before_the_toggle_existed_reads_as_single_column(job):
    record = job.to_record()
    record.pop("multi_column")

    assert main.Job.from_record(record).multi_column is False


# -- re-fitting, which costs nothing ---------------------------------------------


def test_refitting_rebuilds_the_document_without_converting_it_again(
    client, user, monkeypatch, tmp_path
):
    """The whole point of the route: no upload, no poll, no charge."""
    _settings(monkeypatch, data_dir=tmp_path)
    directory = tmp_path / "refit"
    (directory / MATHPIX_RAW_DIR).mkdir(parents=True)
    (directory / "source.pdf").write_bytes(_one_page_pdf())
    (directory / MATHPIX_RAW_DIR / "document.docx").write_bytes(b"mathpix bytes")
    (directory / "document.docx").write_bytes(b"stale")
    record = main.Job(
        id="refitjob", user_id=user.id, filename="paper.pdf", pages=1,
        directory=directory, status="done",
    )
    monkeypatch.setitem(main.JOBS, record.id, record)

    def fail(*args, **kwargs):  # pragma: no cover - the failure is the assertion
        raise AssertionError("re-fitting must not reach Mathpix")

    monkeypatch.setattr(main, "convert_pdf", fail)

    reply = client.post(f"/api/jobs/{record.id}/refit")

    assert reply.status_code == 200
    assert reply.json()["fit"]["applied"] is False
    # Not a .docx at all, so it is passed through rather than withheld.
    assert (directory / "document.docx").read_bytes() == b"mathpix bytes"
    # And Mathpix's own export is what it always was.
    assert (directory / MATHPIX_RAW_DIR / "document.docx").read_bytes() == b"mathpix bytes"


def test_refitting_refuses_a_job_whose_export_is_gone(client, user, monkeypatch, tmp_path):
    _settings(monkeypatch, data_dir=tmp_path)
    directory = tmp_path / "no-export"
    (directory / MATHPIX_RAW_DIR).mkdir(parents=True)
    record = main.Job(
        id="noexportjob", user_id=user.id, filename="paper.pdf", pages=1,
        directory=directory, status="done",
    )
    monkeypatch.setitem(main.JOBS, record.id, record)

    reply = client.post(f"/api/jobs/{record.id}/refit")

    assert reply.status_code == 409
    assert "Mathpix" in reply.json()["detail"]


def test_refitting_refuses_columns_it_could_not_derive(client, user, monkeypatch, tmp_path):
    """Answering "two columns" with a single-column document would be a lie."""
    _settings(monkeypatch, data_dir=tmp_path)
    directory = tmp_path / "no-geometry"
    (directory / MATHPIX_RAW_DIR).mkdir(parents=True)
    (directory / "source.pdf").write_bytes(_one_page_pdf())
    (directory / MATHPIX_RAW_DIR / "document.docx").write_bytes(b"mathpix bytes")
    (directory / "document.docx").write_bytes(b"already delivered")
    record = main.Job(
        id="nogeometryjob", user_id=user.id, filename="paper.pdf", pages=1,
        directory=directory, status="done",
    )
    monkeypatch.setitem(main.JOBS, record.id, record)

    reply = client.post(f"/api/jobs/{record.id}/refit", data={"multi_column": "true"})

    assert reply.status_code == 409
    assert "column" in reply.json()["detail"].lower()
    # And the document that was already delivered is still the one on disk.
    assert (directory / "document.docx").read_bytes() == b"already delivered"


def test_another_account_cannot_refit_this_ones_job(client, job, monkeypatch):
    monkeypatch.setattr(main, "_get_job", _refuse)

    assert client.post(f"/api/jobs/{job.id}/refit").status_code == 404


def _refuse(job_id, user):
    from fastapi import HTTPException

    raise HTTPException(status_code=404, detail="Unknown job id")


def _tiny_docx(section: str) -> bytes:
    """The smallest archive `docx_fit` will treat as a document."""
    import io
    import zipfile

    namespaces = (
        'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(
            "word/document.xml",
            f"<w:document {namespaces}><w:body>"
            '<w:tbl><w:tblPr/><w:tblGrid><w:gridCol w:w="8640"/></w:tblGrid>'
            "<w:tr><w:tc><w:tcPr/><w:p/></w:tc></w:tr></w:tbl>"
            f"{section}</w:body></w:document>",
        )
    return buffer.getvalue()


def test_a_re_fit_rewrites_the_delivered_document_and_says_what_it_did(
    client, user, monkeypatch, tmp_path
):
    _settings(monkeypatch, data_dir=tmp_path)
    directory = tmp_path / "real-refit"
    (directory / MATHPIX_RAW_DIR).mkdir(parents=True)
    (directory / "source.pdf").write_bytes(_one_page_pdf())
    section = (
        '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
        '<w:pgMar w:top="1440" w:right="1800" w:bottom="1440" w:left="1800"/>'
        '<w:cols w:num="2" w:space="360"/></w:sectPr>'
    )
    (directory / MATHPIX_RAW_DIR / "document.docx").write_bytes(_tiny_docx(section))
    (directory / MATHPIX_RAW_DIR / "metadata.json").write_text(
        json.dumps({"file_id": "abc", "fit": {"applied": False}})
    )
    record = main.Job(
        id="realrefitjob", user_id=user.id, filename="paper.pdf", pages=1,
        directory=directory, status="done",
    )
    monkeypatch.setitem(main.JOBS, record.id, record)

    reply = client.post(f"/api/jobs/{record.id}/refit")

    assert reply.status_code == 200
    assert reply.json()["fit"]["applied"] is True
    assert reply.json()["fit"]["tables_fitted"] == 1

    import zipfile

    with zipfile.ZipFile(directory / "document.docx") as archive:
        xml = archive.read("word/document.xml").decode()
    # The two-column section's measure, not the six inches the grid arrived at.
    assert '<w:gridCol w:w="4140"/>' in xml
    assert '<w:tblLayout w:type="fixed"/>' in xml

    stored = json.loads((directory / MATHPIX_RAW_DIR / "metadata.json").read_text())
    assert stored["fit"]["applied"] is True
    assert stored["document_docx"] == "mathpix, fitted to measure"
    # Everything the record already said is still there.
    assert stored["file_id"] == "abc"
