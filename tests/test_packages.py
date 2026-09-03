"""On-demand document and batch archives contain only public conversion outputs."""

from __future__ import annotations

import io
import json
import zipfile

from app import main
from app.mathpix_client import RAW_DIR


def _job(tmp_path, user, monkeypatch, job_id="job-1", filename="paper.pdf", **values):
    directory = tmp_path / job_id
    (directory / RAW_DIR).mkdir(parents=True)
    defaults = dict(
        id=job_id,
        user_id=user.id,
        filename=filename,
        pages=3,
        status="done",
        requested_formats=("docx", "html", "tex.zip"),
        directory=directory,
        batch_id="batch-1",
        multi_column=True,
        cost=0.12,
        calls=1,
        priced_calls=1,
    )
    defaults.update(values)
    job = main.Job(**defaults)
    monkeypatch.setitem(main.JOBS, job.id, job)
    return job


def _archive(response):
    assert response.status_code == 200, response.text
    return zipfile.ZipFile(io.BytesIO(response.content))


def test_document_package_uses_fitted_docx_selected_exports_and_manifest(
    client, user, monkeypatch, tmp_path
):
    job = _job(tmp_path, user, monkeypatch)
    (job.directory / "document.docx").write_bytes(b"fitted")
    (job.directory / "source.pdf").write_bytes(b"source")
    (job.directory / "preview").mkdir()
    (job.directory / "preview" / "page-0001.png").write_bytes(b"preview")
    raw = job.directory / RAW_DIR
    (raw / "document.docx").write_bytes(b"raw duplicate")
    (raw / "document.html").write_text("<p>export</p>")
    (raw / "document.tex.zip").write_bytes(b"compound extension")
    (raw / "document.xlsx").write_bytes(b"not selected")
    (raw / "document.lines.json").write_text("{}")
    (raw / "metadata.json").write_text("{}")

    with _archive(client.get(f"/api/jobs/{job.id}/package.zip")) as archive:
        assert archive.namelist() == [
            "document.docx", "document.html", "document.tex.zip", "manifest.json"
        ]
        assert archive.read("document.docx") == b"fitted"
        manifest = json.loads(archive.read("manifest.json"))

    assert manifest["job_id"] == job.id
    assert manifest["settings"] == {"layout": "mathpix", "multi_column": True}
    assert manifest["requested_formats"] == ["docx", "html", "tex.zip"]
    assert manifest["included_files"] == [
        "document.docx", "document.html", "document.tex.zip"
    ]
    assert manifest["missing_requested_outputs"] == []
    assert manifest["pages"] == 3
    assert manifest["cost"] == 0.12


def test_document_package_reports_missing_selected_outputs_and_sanitizes_filename(
    client, user, monkeypatch, tmp_path
):
    job = _job(
        tmp_path, user, monkeypatch, filename="../bad\\name?.pdf",
        requested_formats=("docx", "pptx", "pptx"),
    )
    (job.directory / "document.docx").write_bytes(b"good")

    response = client.get(f"/api/jobs/{job.id}/package.zip")
    with _archive(response) as archive:
        manifest = json.loads(archive.read("manifest.json"))

    assert "name_.zip" in response.headers["content-disposition"]
    assert manifest["missing_requested_outputs"] == ["pptx"]


def test_document_package_is_409_without_a_user_facing_output(
    client, user, monkeypatch, tmp_path
):
    job = _job(tmp_path, user, monkeypatch, requested_formats=("docx",))
    (job.directory / RAW_DIR / "document.docx").write_bytes(b"raw only")
    (job.directory / RAW_DIR / "document.mmd").write_text("preview")

    assert job.as_dict()["has_package"] is False
    assert client.get(f"/api/jobs/{job.id}/package.zip").status_code == 409


def test_document_package_temp_file_is_removed_after_the_response(
    client, user, monkeypatch, tmp_path
):
    job = _job(tmp_path, user, monkeypatch, requested_formats=("docx",))
    (job.directory / "document.docx").write_bytes(b"good")
    temporary = tmp_path / "transient-package.zip"
    monkeypatch.setattr(main, "_temporary_zip", lambda: temporary)

    assert client.get(f"/api/jobs/{job.id}/package.zip").status_code == 200
    assert not temporary.exists()


def test_batch_package_requires_terminal_members_and_reports_readiness(
    client, user, monkeypatch, tmp_path
):
    done = _job(tmp_path, user, monkeypatch, job_id="done")
    (done.directory / "document.docx").write_bytes(b"done")
    active = _job(tmp_path, user, monkeypatch, job_id="active", status="paused")

    view = client.get("/api/batches/batch-1").json()
    assert view["package_count"] == 1
    assert view["package_ready"] is False
    assert client.get("/api/batches/batch-1/package.zip").status_code == 409

    active.status = "error"
    view = client.get("/api/batches/batch-1").json()
    assert view["package_ready"] is True


def test_batch_package_uses_collision_safe_folders_and_keeps_failed_jobs_in_manifest(
    client, user, monkeypatch, tmp_path
):
    first = _job(tmp_path, user, monkeypatch, job_id="one", filename="report.pdf")
    second = _job(tmp_path, user, monkeypatch, job_id="two", filename="REPORT.pdf")
    failed = _job(
        tmp_path, user, monkeypatch, job_id="failed", filename="failed.pdf",
        status="error", requested_formats=("docx",),
    )
    (first.directory / "document.docx").write_bytes(b"one")
    (second.directory / "document.docx").write_bytes(b"two")

    with _archive(client.get("/api/batches/batch-1/package.zip")) as archive:
        assert "report/document.docx" in archive.namelist()
        assert "REPORT-2/document.docx" in archive.namelist()
        manifest = json.loads(archive.read("manifest.json"))

    by_id = {entry["job_id"]: entry for entry in manifest["jobs"]}
    assert manifest["package_count"] == 2
    assert by_id[failed.id]["status"] == "error"
    assert by_id[failed.id]["folder"] is None
    assert by_id[failed.id]["missing_requested_outputs"] == ["docx"]


def test_package_routes_keep_job_and_batch_ownership_private(
    client, other_client, user, monkeypatch, tmp_path
):
    job = _job(tmp_path, user, monkeypatch)
    (job.directory / "document.docx").write_bytes(b"private")

    assert other_client.get(f"/api/jobs/{job.id}/package.zip").status_code == 404
    assert other_client.get("/api/batches/batch-1/package.zip").status_code == 404


def test_terminal_batch_without_any_outputs_is_409(
    client, user, monkeypatch, tmp_path
):
    _job(tmp_path, user, monkeypatch, requested_formats=("docx",), status="error")

    view = client.get("/api/batches/batch-1").json()
    assert view["package_count"] == 0
    assert view["package_ready"] is False
    assert client.get("/api/batches/batch-1/package.zip").status_code == 409
