"""The routes the page viewer reads: detection, page renders, and job assets."""

from __future__ import annotations

import json

import fitz
import pytest

from app import detection, main


@pytest.fixture
def job(tmp_path, monkeypatch, user):
    """A finished job on disk, registered as if the server had just run it."""
    directory = tmp_path / "job"
    directory.mkdir()

    document = fitz.open()
    for _ in range(2):
        document.new_page(width=595, height=842)
    document.save(directory / "source.pdf")
    document.close()

    detection.write(
        [
            detection.DetectedPage(
                number=1,
                width=595.0,
                height=842.0,
                markdown="# Heading\n\n![a figure](figures/page-0001-figure-1.png)",
                blocks=[detection.DetectedBlock(index=0, kind="heading", bbox=(72, 80, 523, 104))],
            )
        ],
        directory / "detection.json",
        "structured",
    )

    (directory / "figures").mkdir()
    (directory / "figures" / "page-0001-figure-1.png").write_bytes(b"\x89PNG\r\n\x1a\nnot-really")
    (directory / "secret.txt").write_text("private")

    record = main.Job(
        id="testjob", user_id=user.id, filename="paper.pdf", pages=2,
        directory=directory, status="done",
    )
    monkeypatch.setitem(main.JOBS, record.id, record)
    return record


def test_a_job_reports_whether_it_has_something_to_show(job):
    assert job.as_dict()["has_detection"] is True


def test_the_detection_comes_back_as_it_was_written(client, job):
    body = client.get(f"/api/jobs/{job.id}/detection").json()

    assert body["mode"] == "structured"
    assert body["pages"][0]["blocks"][0]["kind"] == "heading"
    assert body["pages"][0]["blocks"][0]["bbox"] == [72, 80, 523, 104]


def test_a_job_with_no_detection_says_so_rather_than_failing(client, job):
    (job.directory / "detection.json").unlink()

    response = client.get(f"/api/jobs/{job.id}/detection")
    assert response.status_code == 409


def test_a_page_is_rendered_on_demand_and_kept(client, job):
    response = client.get(f"/api/jobs/{job.id}/page/2.png")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG")
    # Rendered once: the second reader is served the file the first one made.
    assert (job.directory / "preview" / "page-0002.png").exists()


def test_a_page_the_document_does_not_have_is_a_404(client, job):
    assert client.get(f"/api/jobs/{job.id}/page/9.png").status_code == 404


def test_a_documents_own_figures_are_served_to_the_preview(client, job):
    response = client.get(f"/api/jobs/{job.id}/asset/figures/page-0001-figure-1.png")

    assert response.status_code == 200
    assert response.content.startswith(b"\x89PNG")


@pytest.mark.parametrize(
    "asset",
    [
        "secret.txt",                      # in the job, but not a document's image
        "source.pdf",                      # likewise — it has its own route
        "../../../../etc/passwd",          # out of the job entirely
        "figures/../../../etc/passwd",     # ...by way of a directory that is allowed
    ],
)
def test_nothing_but_a_documents_images_can_be_fetched(client, job, asset):
    assert client.get(f"/api/jobs/{job.id}/asset/{asset}").status_code == 404


def test_the_pages_own_assets_are_served(client):
    """The viewer's script, stylesheet and renderer all come from this server."""
    for path in ("/static/app.js", "/static/app.css", "/static/vendor/marked.min.js",
                 "/static/vendor/katex/katex.min.js", "/static/vendor/katex/katex.min.css",
                 "/static/vendor/inter/InterVariable.woff2",
                 "/static/vendor/inter/InterVariable-Italic.woff2"):
        assert client.get(path).status_code == 200, path
