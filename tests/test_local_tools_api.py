"""HTTP interface for local image composition and PDF range extraction."""

from __future__ import annotations

import io
import json
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import fitz
import pytest
from PIL import Image

from app import main


def _image_bytes(color: str, size: tuple[int, int] = (40, 80), format: str = "PNG") -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", size, color).save(stream, format=format)
    return stream.getvalue()


def _pdf_bytes(pages: int = 5) -> bytes:
    document = fitz.open()
    for number in range(1, pages + 1):
        page = document.new_page(width=500 + number, height=700 + number)
        page.insert_text((72, 72), f"Page {number}")
    document.set_metadata({"title": "Uploaded source"})
    content = document.tobytes()
    document.close()
    return content


def test_config_advertises_local_tool_limits_without_mathpix(client) -> None:
    body = client.get("/api/config").json()

    assert body["local_tools_available"] is True
    assert body["accepted_image_types"] == ["image/jpeg", "image/png", "image/webp"]
    assert body["image_max_files"] == 30
    assert body["image_max_pixels"] > 0
    assert body["image_max_upload_mb"] == 50
    assert body["mathpix_key_configured"] is False


def test_ordered_images_create_a_downloadable_history_job(client) -> None:
    reply = client.post(
        "/api/tools/images-to-pdf",
        files=[
            ("files", ("red.png", _image_bytes("red"), "image/png")),
            ("files", ("blue.webp", _image_bytes("blue", (90, 40), "WEBP"), "image/webp")),
        ],
    )

    assert reply.status_code == 200, reply.text
    created = reply.json()
    job = client.get(f"/api/jobs/{created['id']}").json()
    assert job["kind"] == "images_to_pdf"
    assert job["source_filenames"] == ["red.png", "blue.webp"]
    assert job["output_filename"] == "red.pdf"
    assert job["output_pages"] == 2
    assert job["has_pdf"] is True
    assert job["has_source"] is False
    assert job["status"] == "done"

    download = client.get(f"/api/jobs/{job['id']}/download?format=pdf")
    assert download.status_code == 200
    assert "red.pdf" in download.headers["content-disposition"]
    with fitz.open(stream=download.content, filetype="pdf") as document:
        assert document.page_count == 2

    [history_job] = client.get("/api/history").json()["jobs"]
    assert history_job["id"] == job["id"]
    assert history_job["kind"] == "images_to_pdf"


def test_image_orientation_is_validated_persisted_and_applied(client) -> None:
    created = client.post(
        "/api/tools/images-to-pdf",
        data={"orientation": "portrait"},
        files=[("files", ("wide.png", _image_bytes("blue", (90, 40)), "image/png"))],
    )

    assert created.status_code == 200, created.text
    job = client.get(f"/api/jobs/{created.json()['id']}").json()
    assert job["page_orientation"] == "portrait"
    with fitz.open(
        stream=client.get(f"/api/jobs/{job['id']}/download?format=pdf").content,
        filetype="pdf",
    ) as document:
        assert document[0].rect.height > document[0].rect.width

    invalid = client.post(
        "/api/tools/images-to-pdf",
        data={"orientation": "sideways"},
        files=[("files", ("wide.png", _image_bytes("blue"), "image/png"))],
    )
    assert invalid.status_code == 400


def test_image_upload_limits_and_corruption_become_concise_errors(client) -> None:
    too_many = client.post(
        "/api/tools/images-to-pdf",
        files=[("files", (f"{number}.png", _image_bytes("red"), "image/png")) for number in range(31)],
    )
    assert too_many.status_code == 400
    assert "at most 30" in too_many.json()["detail"]

    broken = client.post(
        "/api/tools/images-to-pdf",
        files=[("files", ("broken.png", b"not an image", "image/png"))],
    )
    assert broken.status_code == 200
    job = client.get(f"/api/jobs/{broken.json()['id']}").json()
    assert job["status"] == "error"
    assert "Could not read" in job["error"]
    assert job["has_pdf"] is False


def test_image_upload_rejects_a_combined_payload_over_the_configured_limit(
    client, monkeypatch
) -> None:
    monkeypatch.setattr(main, "settings", replace(main.settings, local_image_upload_mb=1))

    reply = client.post(
        "/api/tools/images-to-pdf",
        files=[("files", ("large.png", b"x" * (1_048_576 + 1), "image/png"))],
    )

    assert reply.status_code == 413
    assert "combined 1 MB limit" in reply.json()["detail"]


def test_split_upload_reports_pages_then_regenerates_one_owned_job(client) -> None:
    staged = client.post(
        "/api/tools/split-pdf",
        files={"file": ("report.pdf", _pdf_bytes(), "application/pdf")},
    )
    assert staged.status_code == 200, staged.text
    job = staged.json()
    assert job["kind"] == "split_pdf"
    assert job["status"] == "ready"
    assert job["pages"] == 5
    assert job["output_pages"] == 0
    assert job["page_range"] is None
    assert job["has_source"] is True

    first = client.post(
        f"/api/jobs/{job['id']}/split", data={"start_page": 2, "end_page": 4}
    )
    assert first.status_code == 200, first.text
    assert first.json()["page_range"] == {"start": 2, "end": 4}
    assert first.json()["output_pages"] == 3
    assert first.json()["output_filename"] == "report-pages-2-4.pdf"

    second = client.post(
        f"/api/jobs/{job['id']}/split", data={"start_page": 5, "end_page": 5}
    )
    assert second.status_code == 200, second.text
    assert second.json()["page_range"] == {"start": 5, "end": 5}
    assert second.json()["output_pages"] == 1
    assert second.json()["id"] == job["id"]
    download = client.get(f"/api/jobs/{job['id']}/download?format=pdf")
    with fitz.open(stream=download.content, filetype="pdf") as document:
        assert document.page_count == 1
        assert "Page 5" in document[0].get_text()


def test_multiple_split_ranges_create_ordered_artifacts_and_a_zip(client) -> None:
    staged = client.post(
        "/api/tools/split-pdf",
        files={"file": ("report.pdf", _pdf_bytes(), "application/pdf")},
    ).json()

    response = client.post(
        f"/api/jobs/{staged['id']}/split",
        data={
            "ranges": json.dumps([{"start": 4, "end": 5}, {"start": 2, "end": 2}]),
            "merge": "false",
        },
    )

    assert response.status_code == 200, response.text
    job = response.json()
    assert job["page_ranges"] == [{"start": 4, "end": 5}, {"start": 2, "end": 2}]
    assert [item["filename"] for item in job["artifacts"]] == [
        "report-pages-4-5.pdf", "report-pages-2-2.pdf", "report-ranges.zip"
    ]
    first = client.get(f"/api/jobs/{job['id']}/artifacts/range-1")
    with fitz.open(stream=first.content, filetype="pdf") as document:
        assert [page.get_text().strip() for page in document] == ["Page 4", "Page 5"]
    package = client.get(f"/api/jobs/{job['id']}/artifacts/package")
    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        assert archive.namelist() == ["report-pages-4-5.pdf", "report-pages-2-2.pdf"]


def test_merged_and_repeated_ranges_preserve_configured_order_and_names(client) -> None:
    staged = client.post(
        "/api/tools/split-pdf",
        files={"file": ("report.pdf", _pdf_bytes(4), "application/pdf")},
    ).json()
    path = f"/api/jobs/{staged['id']}/split"

    separate = client.post(path, data={"ranges": json.dumps([
        {"start": 2, "end": 2}, {"start": 2, "end": 2}
    ])}).json()
    assert [item["filename"] for item in separate["artifacts"][:2]] == [
        "report-pages-2-2.pdf", "report-pages-2-2-2.pdf"
    ]

    merged = client.post(path, data={
        "ranges": json.dumps([{"start": 3, "end": 4}, {"start": 1, "end": 1}]),
        "merge": "true",
    })
    assert merged.status_code == 200, merged.text
    job = merged.json()
    assert job["merge_ranges"] is True
    assert job["artifacts"] == [{
        "key": "merged", "filename": "report-selected-pages.pdf",
        "media_type": "application/pdf", "pages": 3,
    }]
    content = client.get(f"/api/jobs/{job['id']}/artifacts/merged").content
    with fitz.open(stream=content, filetype="pdf") as document:
        assert [page.get_text().strip() for page in document] == ["Page 3", "Page 4", "Page 1"]


def test_split_page_thumbnails_use_bounded_width_specific_caches(client) -> None:
    staged = client.post(
        "/api/tools/split-pdf",
        files={"file": ("report.pdf", _pdf_bytes(2), "application/pdf")},
    ).json()

    small = client.get(f"/api/jobs/{staged['id']}/page/1.png?width=96")
    large = client.get(f"/api/jobs/{staged['id']}/page/1.png?width=180")

    assert small.status_code == large.status_code == 200
    assert fitz.Pixmap(small.content).width == 96
    assert fitz.Pixmap(large.content).width == 180
    assert client.get(f"/api/jobs/{staged['id']}/page/1.png?width=20").status_code == 422


def test_history_clear_can_be_scoped_to_one_tool(client) -> None:
    image = client.post(
        "/api/tools/images-to-pdf",
        files=[("files", ("page.png", _image_bytes("red"), "image/png"))],
    ).json()
    split = client.post(
        "/api/tools/split-pdf",
        files={"file": ("report.pdf", _pdf_bytes(2), "application/pdf")},
    ).json()

    assert client.delete("/api/history?kind=images_to_pdf").json() == {"deleted": 1}
    assert client.get(f"/api/jobs/{image['id']}").status_code == 404
    assert client.get(f"/api/jobs/{split['id']}").status_code == 200
    assert client.delete("/api/history?kind=unknown").status_code == 422


def test_split_upload_rejects_corrupt_and_encrypted_sources_without_a_job(client) -> None:
    corrupt = client.post(
        "/api/tools/split-pdf",
        files={"file": ("broken.pdf", b"not a pdf", "application/pdf")},
    )
    plain = fitz.open(stream=_pdf_bytes(1), filetype="pdf")
    encrypted_stream = io.BytesIO()
    plain.save(
        encrypted_stream,
        encryption=fitz.PDF_ENCRYPT_AES_256,
        owner_pw="owner",
        user_pw="reader",
    )
    plain.close()
    encrypted = client.post(
        "/api/tools/split-pdf",
        files={"file": ("locked.pdf", encrypted_stream.getvalue(), "application/pdf")},
    )

    assert corrupt.status_code == 400
    assert encrypted.status_code == 400
    assert "Encrypted PDFs" in encrypted.json()["detail"]
    assert client.get("/api/history").json()["jobs"] == []


def test_split_upload_rejects_empty_password_encryption(client) -> None:
    plain = fitz.open(stream=_pdf_bytes(1), filetype="pdf")
    encrypted_stream = io.BytesIO()
    plain.save(
        encrypted_stream,
        encryption=fitz.PDF_ENCRYPT_AES_256,
        owner_pw="owner",
        user_pw="",
    )
    plain.close()

    response = client.post(
        "/api/tools/split-pdf",
        files={"file": ("locked.pdf", encrypted_stream.getvalue(), "application/pdf")},
    )

    assert response.status_code == 400
    assert "Encrypted PDFs" in response.json()["detail"]


def test_split_validation_keeps_the_source_and_previous_result(client) -> None:
    staged = client.post(
        "/api/tools/split-pdf",
        files={"file": ("report.pdf", _pdf_bytes(3), "application/pdf")},
    ).json()
    path = f"/api/jobs/{staged['id']}/split"
    assert client.post(path, data={"start_page": 1, "end_page": 2}).status_code == 200
    previous = client.get(f"/api/jobs/{staged['id']}/download?format=pdf").content

    invalid = client.post(path, data={"start_page": 3, "end_page": 2})

    assert invalid.status_code == 400
    assert "page range" in invalid.json()["detail"]
    job = client.get(f"/api/jobs/{staged['id']}").json()
    assert job["has_source"] is True
    assert job["page_range"] == {"start": 1, "end": 2}
    assert client.get(f"/api/jobs/{staged['id']}/download?format=pdf").content == previous


def test_split_regeneration_claims_the_job_until_metadata_and_output_agree(
    client, monkeypatch
) -> None:
    staged = client.post(
        "/api/tools/split-pdf",
        files={"file": ("report.pdf", _pdf_bytes(3), "application/pdf")},
    ).json()
    entered = threading.Event()
    release = threading.Event()
    original = main.document_tools.split_pdf

    def blocked_split(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=5)
        return original(*args, **kwargs)

    monkeypatch.setattr(main.document_tools, "split_pdf", blocked_split)
    path = f"/api/jobs/{staged['id']}/split"
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            client.post, path, data={"start_page": 1, "end_page": 2}
        )
        assert entered.wait(timeout=5)
        competing = client.post(path, data={"start_page": 3, "end_page": 3})
        assert competing.status_code == 409
        assert client.delete(f"/api/jobs/{staged['id']}").status_code == 409
        release.set()
        assert first.result(timeout=5).status_code == 200

    job = client.get(f"/api/jobs/{staged['id']}").json()
    assert job["page_range"] == {"start": 1, "end": 2}
    assert job["output_filename"] == "report-pages-1-2.pdf"
    with fitz.open(
        stream=client.get(f"/api/jobs/{staged['id']}/download?format=pdf").content,
        filetype="pdf",
    ) as document:
        assert document.page_count == 2


def test_local_tools_require_authentication_and_keep_job_ownership_private(
    anonymous, client, other_client
) -> None:
    assert anonymous.post("/api/tools/images-to-pdf").status_code == 401
    assert anonymous.post("/api/tools/split-pdf").status_code == 401

    job = client.post(
        "/api/tools/split-pdf",
        files={"file": ("private.pdf", _pdf_bytes(2), "application/pdf")},
    ).json()
    assert client.post(
        f"/api/jobs/{job['id']}/split", data={"start_page": 1, "end_page": 1}
    ).status_code == 200
    assert other_client.post(
        f"/api/jobs/{job['id']}/split", data={"start_page": 1, "end_page": 1}
    ).status_code == 404
    assert other_client.get(f"/api/jobs/{job['id']}/download?format=pdf").status_code == 404
    assert other_client.get(f"/api/jobs/{job['id']}/artifacts/range-1").status_code == 404


def test_local_jobs_cannot_enter_paid_mathpix_control_routes(client) -> None:
    job = client.post(
        "/api/tools/split-pdf",
        files={"file": ("local.pdf", _pdf_bytes(2), "application/pdf")},
    ).json()

    for action in ("start", "pause", "resume", "cancel", "refit"):
        response = client.post(f"/api/jobs/{job['id']}/{action}")
        assert response.status_code == 409, (action, response.text)
        assert "PDF-to-DOCX" in response.json()["detail"]

    restored = client.get(f"/api/jobs/{job['id']}").json()
    assert restored["kind"] == "split_pdf"
    assert restored["status"] == "ready"


def test_old_records_default_to_pdf_to_docx(tmp_path: Path) -> None:
    restored = main.Job.from_record(
        {
            "id": "old-job",
            "filename": "legacy.pdf",
            "pages": 7,
            "directory": str(tmp_path),
            "status": "done",
        }
    )

    assert restored.as_dict()["kind"] == "pdf_to_docx"
    assert restored.as_dict()["source_filenames"] == ["legacy.pdf"]
    assert restored.as_dict()["has_pdf"] is False


def test_legacy_local_records_hydrate_new_workbench_defaults(tmp_path: Path) -> None:
    image = main.Job.from_record({
        "id": "old-image", "filename": "page.png", "kind": "images_to_pdf",
        "pages": 1, "directory": str(tmp_path), "status": "done",
    })
    split = main.Job.from_record({
        "id": "old-split", "filename": "source.pdf", "kind": "split_pdf",
        "pages": 7, "directory": str(tmp_path), "status": "done",
        "page_range": {"start": 2, "end": 5},
    })

    assert image.as_dict()["page_orientation"] == "auto"
    assert split.as_dict()["page_ranges"] == [{"start": 2, "end": 5}]
    assert split.as_dict()["merge_ranges"] is False
    assert split.as_dict()["artifacts"] == []


def test_an_interrupted_local_composition_is_restored_as_retryable_error(tmp_path: Path) -> None:
    restored = main.Job.from_record(
        {
            "id": "local-job",
            "filename": "first.png",
            "kind": "images_to_pdf",
            "source_filenames": ["first.png"],
            "pages": 1,
            "directory": str(tmp_path),
            "status": "processing",
        }
    )

    assert restored.status == "error"
    assert "server restarted" in (restored.error or "")
    assert restored.as_dict()["kind"] == "images_to_pdf"
