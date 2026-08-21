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
from fastapi.testclient import TestClient

from app import main
from app.mathpix_client import BY_EXT, FORMATS, RAW_DIR, RAW_IMAGE_DIR


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture
def job(tmp_path, monkeypatch):
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
        id="mathpixjob", filename="paper.pdf", pages=2,
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


def test_the_openrouter_model_endpoint_is_not_exposed(client):
    assert client.get("/api/models").status_code == 404


# -- what a job actually has ------------------------------------------------------ #


def test_a_job_reports_only_the_exports_it_has(job):
    assert job.as_dict()["mathpix_formats"] == ["docx", "mmd", "tex.zip", "lines.json"]
    assert job.as_dict()["has_rebuilt"] is True


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


def test_a_mathpix_job_does_not_need_an_openrouter_key(client, monkeypatch, tmp_path):
    """The two credentials are separate, and the mode reaches only one service."""
    _settings(monkeypatch, mathpix_app_key="a-key", api_key="", data_dir=tmp_path)
    reply = client.post(
        "/api/convert",
        files={"file": ("a.pdf", _one_page_pdf(), "application/pdf")},
        data={"model": "anthropic/claude-unused", "columns": "multi"},
    )
    assert reply.status_code == 200
    assert reply.json()["layout"] == "mathpix"
    assert reply.json()["columns"] == ""


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


def test_web_jobs_ignore_the_configured_legacy_layout(client, monkeypatch, tmp_path):
    _settings(monkeypatch, mathpix_app_key="a-key", api_key="", layout="flow", data_dir=tmp_path)
    reply = client.post(
        "/api/convert",
        files={"file": ("a.pdf", _one_page_pdf(), "application/pdf")},
    )
    assert reply.status_code == 200
    job_id = reply.json()["id"]
    try:
        assert reply.json()["layout"] == "mathpix"
    finally:
        main.JOBS.pop(job_id, None)


def test_non_pdf_uploads_are_rejected_before_staging(client, monkeypatch, tmp_path):
    _settings(monkeypatch, mathpix_app_key="a-key", data_dir=tmp_path)
    reply = client.post(
        "/api/convert",
        files={"file": ("notes.txt", b"not a pdf", "text/plain")},
    )
    assert reply.status_code == 400
    assert ".pdf" in reply.json()["detail"]


def test_rerunning_a_historical_job_is_pinned_to_mathpix(client, monkeypatch, tmp_path):
    _settings(monkeypatch, mathpix_app_key="a-key", api_key="", data_dir=tmp_path)
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
        id="legacyjob", filename="old.pdf", pages=1, directory=directory,
        status="done", layout="structured", columns="multi",
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
    assert legacy.columns == ""
    assert called["layout"] == "mathpix"
    assert called["model"] is None
    assert called["columns"] is None
    assert called["work_dir"] != directory
    assert legacy.status == "error"
    # The old successful outputs survive until the replacement conversion has
    # itself succeeded; a transient failure must not destroy valid downloads.
    assert legacy.mathpix_formats() == ["xlsx"]
    assert (directory / "document.docx").read_bytes() == b"stale primary"
    assert (directory / "document.md").read_text() == "stale preview"
    assert (directory / "detection.json").exists()
    assert (directory / "rebuilt.docx").read_bytes() == b"historical comparison"


def test_rerun_rejects_an_explicit_legacy_layout(client, monkeypatch, tmp_path):
    _settings(monkeypatch, mathpix_app_key="a-key", data_dir=tmp_path)
    directory = tmp_path / "legacy-rejected"
    directory.mkdir()
    (directory / "source.pdf").write_bytes(_one_page_pdf())
    legacy = main.Job(
        id="legacyrejected", filename="old.pdf", pages=1, directory=directory,
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
