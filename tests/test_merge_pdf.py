import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import fitz
import pytest

from app import main, document_tools, db


def pdf(label, pages=1):
    with fitz.open() as doc:
        for index in range(pages):
            page = doc.new_page(width=410 + index, height=620)
            page.insert_text((40, 40), f"{label} {index}")
            page.set_rotation(90)
            page.add_text_annot((80, 80), "Note")
            page.insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(40, 40, 100, 60), "uri": "https://example.com"})
        doc.set_toc([[1, label, 1]])
        return doc.tobytes()


def upload(client, labels=("A", "B")):
    response = client.post("/api/tools/merge-pdf", files=[("files", ("same.pdf", pdf(label), "application/pdf")) for label in labels])
    assert response.status_code == 200, response.text
    return response.json()


def test_merge_edit_restart_and_delete(client):
    job = upload(client)
    jid = job["id"]
    a, b = [item["id"] for item in job["merge_sources"]]
    assert a != b
    assert client.get(f"/api/jobs/{jid}/merge-sources/{a}/preview.png").headers["content-type"] == "image/png"
    merged = client.post(f"/api/jobs/{jid}/merge").json()
    assert merged["has_pdf"] and not merged["merge_dirty"]
    response = client.put(f"/api/jobs/{jid}/merge-sources", json={"source_ids": [b, a]})
    assert response.json()["merge_dirty"]
    assert response.json()["output_filename"] == "same-merged.pdf"
    main.JOBS[jid] = main.Job.from_record(next(record for record in db.load_jobs() if record["id"] == jid))
    assert main.JOBS[jid].merge_sources[0]["id"] == b
    assert client.post(f"/api/jobs/{jid}/merge").status_code == 200
    with fitz.open(stream=client.get(f"/api/jobs/{jid}/download?format=pdf").content, filetype="pdf") as doc:
        assert [page.get_text().strip() for page in doc] == ["B 0", "A 0"]
        assert [item[1:3] for item in doc.get_toc()] == [["B", 1], ["A", 2]]
        assert doc[0].rotation == 90
        assert doc[0].mediabox.width == 410
        assert list(doc[0].annots())
        assert doc[0].get_links()[0]["uri"] == "https://example.com"
    appended = client.post(f"/api/jobs/{jid}/merge-sources", files=[("files", ("same.pdf", pdf("C", 2), "application/pdf"))]).json()
    assert appended["pages"] == 4
    c = appended["merge_sources"][-1]["id"]
    assert client.put(f"/api/jobs/{jid}/merge-sources", json={"source_ids": [c, a]}).status_code == 200
    assert not main.JOBS[jid]._file(f"sources/{b}.pdf").exists()
    assert client.post(f"/api/jobs/{jid}/merge").json()["output_pages"] == 3
    directory = main.JOBS[jid].directory
    assert client.delete(f"/api/jobs/{jid}").status_code == 200
    assert not directory.exists()


@pytest.mark.parametrize("name,data", [("empty.pdf", b""), ("bad.pdf", b"broken"), ("text.txt", b"hi")])
def test_invalid_upload_is_transactional(client, name, data):
    job = upload(client)
    jid = job["id"]
    before = list(main.JOBS[jid]._file("sources").iterdir())
    reply = client.post(f"/api/jobs/{jid}/merge-sources", files=[("files", ("good.pdf", pdf("C"))), ("files", (name, data))])
    assert reply.status_code == 400
    assert client.get(f"/api/jobs/{jid}").json()["merge_sources"] == job["merge_sources"]
    assert sorted(main.JOBS[jid]._file("sources").iterdir()) == sorted(before)


def test_limits_ids_and_single_source(client, monkeypatch):
    job = upload(client, ("A",))
    jid = job["id"]
    assert client.post(f"/api/jobs/{jid}/merge").status_code == 400
    key = job["merge_sources"][0]["id"]
    for ids in [[key, key], ["../../wrong"]]:
        assert client.put(f"/api/jobs/{jid}/merge-sources", json={"source_ids": ids}).status_code == 400
    assert client.get(f"/api/jobs/{jid}/merge-sources/wrong/preview.png").status_code == 404
    monkeypatch.setattr(main, "settings", replace(main.settings, merge_max_files=1))
    assert client.post(f"/api/jobs/{jid}/merge-sources", files=[("files", ("b.pdf", pdf("B")))]).status_code == 400
    monkeypatch.setattr(main, "settings", replace(main.settings, merge_max_files=30, merge_upload_mb=0.001))
    assert client.post(f"/api/jobs/{jid}/merge-sources", files=[("files", ("b.pdf", pdf("B")))]).status_code == 413


def test_generation_failure_preserves_download(client, monkeypatch):
    job = upload(client)
    jid = job["id"]
    client.post(f"/api/jobs/{jid}/merge")
    before = client.get(f"/api/jobs/{jid}/download?format=pdf").content
    original = fitz.Document.save
    def fail(self, *args, **kwargs):
        raise RuntimeError("disk failure")
    monkeypatch.setattr(fitz.Document, "save", fail)
    assert client.post(f"/api/jobs/{jid}/merge").status_code == 400
    assert client.get(f"/api/jobs/{jid}/download?format=pdf").content == before
    assert main.JOBS[jid].status == "done"
    monkeypatch.setattr(fitz.Document, "save", original)


def test_edit_waits_for_generation(client, monkeypatch):
    job = upload(client)
    jid = job["id"]
    started, release = threading.Event(), threading.Event()
    original = document_tools.merge_pdf
    def delayed(*args):
        started.set()
        assert release.wait(5)
        return original(*args)
    monkeypatch.setattr(document_tools, "merge_pdf", delayed)
    with ThreadPoolExecutor(max_workers=2) as pool:
        merge = pool.submit(client.post, f"/api/jobs/{jid}/merge")
        assert started.wait(5)
        reorder = pool.submit(client.put, f"/api/jobs/{jid}/merge-sources", json={"source_ids": [s["id"] for s in reversed(job["merge_sources"])]})
        assert not reorder.done()
        release.set()
        assert merge.result().status_code == reorder.result().status_code == 200
    assert main.JOBS[jid].merge_dirty


def test_ownership(client, other_client, anonymous):
    job = upload(client)
    jid = job["id"]
    source = job["merge_sources"][0]["id"]
    for outsider, status in [(other_client, 404), (anonymous, 401)]:
        assert outsider.post(f"/api/jobs/{jid}/merge").status_code == status
        assert outsider.put(f"/api/jobs/{jid}/merge-sources", json={"source_ids": []}).status_code == status
        assert outsider.post(f"/api/jobs/{jid}/merge-sources", files=[("files", ("b.pdf", pdf("B")))]).status_code == status
        assert outsider.get(f"/api/jobs/{jid}/merge-sources/{source}/preview.png").status_code == status


def test_encrypted_and_disguised_uploads(client):
    with fitz.open(stream=pdf("secret"), filetype="pdf") as doc:
        encrypted = doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="user")
    for content in [encrypted, b"<html>not PDF</html>"]:
        reply = client.post("/api/tools/merge-pdf", files=[("files", ("source.pdf", content))])
        assert reply.status_code == 400
    assert client.get("/api/history").json()["jobs"] == []


def test_metadata_failure_restores_previous_output_and_sources(client, monkeypatch):
    job = upload(client)
    jid = job["id"]
    client.post(f"/api/jobs/{jid}/merge")
    before = client.get(f"/api/jobs/{jid}/download?format=pdf").content
    client.put(f"/api/jobs/{jid}/merge-sources", json={"source_ids": [s["id"] for s in reversed(job["merge_sources"])]})
    original = db.save_job
    def fail_done(user_id, job_id, created_at, record):
        if record["status"] == "done":
            raise OSError("Database write failed")
        return original(user_id, job_id, created_at, record)
    monkeypatch.setattr(db, "save_job", fail_done)
    assert client.post(f"/api/jobs/{jid}/merge").status_code == 400
    assert client.get(f"/api/jobs/{jid}/download?format=pdf").content == before
    assert main.JOBS[jid].merge_dirty
    assert main.JOBS[jid].status == "ready"
