"""Batch upload, and starting / pausing / cancelling a whole batch or one file.

The conversion itself is a paid remote call, so `main.convert_pdf` is replaced
throughout — what is under test is this application's queue: that files upload as
one group, that at most `batch_workers` convert at once, that a paused file is
held out of a start, and that a cancel unwinds a running conversion cleanly.

Starlette's TestClient runs a request's background tasks synchronously once the
response is returned, so a `/start` call has finished dispatching *and running*
every file by the time control comes back here. Tests therefore read the outcome
from a follow-up GET rather than from the start response's own body.
"""

from __future__ import annotations

import concurrent.futures
import dataclasses
import threading
import time

import fitz
import pytest

from app import main, pipeline
from app.mathpix_client import ConversionCancelled


def _one_page_pdf() -> bytes:
    document = fitz.open()
    document.new_page(width=595, height=842)
    data = document.tobytes()
    document.close()
    return data


def _settings(monkeypatch, **overrides):
    monkeypatch.setattr(main, "settings", dataclasses.replace(main.settings, **overrides))


def _raise_stop(**_kwargs):
    raise RuntimeError("stop after observing the public interface")


def _upload(client, monkeypatch, tmp_path, count, **setting_overrides):
    _settings(monkeypatch, mathpix_app_key="a-key", data_dir=tmp_path, **setting_overrides)
    files = [
        ("files", (f"doc{i}.pdf", _one_page_pdf(), "application/pdf")) for i in range(count)
    ]
    reply = client.post("/api/convert/batch", files=files)
    assert reply.status_code == 200, reply.text
    return reply.json()


def _statuses(client, batch_id):
    view = client.get(f"/api/batches/{batch_id}").json()
    return {job["id"]: job["status"] for job in view["jobs"]}


# -- upload -------------------------------------------------------------------- #


def test_a_batch_uploads_as_one_group(client, monkeypatch, tmp_path):
    body = _upload(client, monkeypatch, tmp_path, 3)

    assert len(body["jobs"]) == 3
    assert body["rejected"] == []
    assert {job["batch_id"] for job in body["jobs"]} == {body["batch_id"]}
    assert all(job["status"] == "ready" for job in body["jobs"])
    assert body["package_ready"] is False
    assert body["package_count"] == 0
    assert all(job["has_package"] is False for job in body["jobs"])


def test_a_batch_larger_than_the_cap_is_refused(client, monkeypatch, tmp_path):
    _settings(monkeypatch, mathpix_app_key="a-key", data_dir=tmp_path, batch_max_files=2)
    files = [("files", (f"d{i}.pdf", _one_page_pdf(), "application/pdf")) for i in range(3)]

    reply = client.post("/api/convert/batch", files=files)

    assert reply.status_code == 400
    assert "at most 2" in reply.json()["detail"]


def test_one_bad_member_is_reported_without_failing_the_rest(client, monkeypatch, tmp_path):
    _settings(monkeypatch, mathpix_app_key="a-key", data_dir=tmp_path)
    files = [
        ("files", ("good.pdf", _one_page_pdf(), "application/pdf")),
        ("files", ("notes.txt", b"not a pdf", "text/plain")),
    ]

    reply = client.post("/api/convert/batch", files=files)

    assert reply.status_code == 200
    body = reply.json()
    assert [job["filename"] for job in body["jobs"]] == ["good.pdf"]
    assert body["rejected"][0]["filename"] == "notes.txt"
    assert ".pdf" in body["rejected"][0]["detail"]


def test_a_batch_of_only_bad_files_is_a_400(client, monkeypatch, tmp_path):
    _settings(monkeypatch, mathpix_app_key="a-key", data_dir=tmp_path)
    files = [("files", ("notes.txt", b"nope", "text/plain"))]

    reply = client.post("/api/convert/batch", files=files)

    assert reply.status_code == 400


# -- starting ---------------------------------------------------------------- #


def test_batch_start_runs_every_ready_file(client, monkeypatch, tmp_path):
    body = _upload(client, monkeypatch, tmp_path, 3)
    seen = []
    monkeypatch.setattr(main, "convert_pdf", lambda **kw: seen.append(kw["work_dir"]) or _raise_stop())

    reply = client.post(f"/api/batches/{body['batch_id']}/start", data={"formats": "docx"})

    assert reply.status_code == 200
    assert len(seen) == 3
    assert set(_statuses(client, body["batch_id"]).values()) == {"error"}


def test_a_paused_file_is_skipped_by_a_batch_start_until_resumed(client, monkeypatch, tmp_path):
    body = _upload(client, monkeypatch, tmp_path, 2)
    held, other = (job["id"] for job in body["jobs"])
    assert client.post(f"/api/jobs/{held}/pause").status_code == 200

    calls = []
    monkeypatch.setattr(main, "convert_pdf", lambda **kw: calls.append(kw) or _raise_stop())
    client.post(f"/api/batches/{body['batch_id']}/start", data={"formats": "docx"})

    after = _statuses(client, body["batch_id"])
    assert after[held] == "paused"
    assert after[other] == "error"
    assert len(calls) == 1

    assert client.post(f"/api/jobs/{held}/resume").status_code == 200
    assert _statuses(client, body["batch_id"])[held] == "error"
    assert len(calls) == 2


def test_pause_is_refused_once_a_file_has_finished(client, monkeypatch, tmp_path):
    body = _upload(client, monkeypatch, tmp_path, 1)
    job_id = body["jobs"][0]["id"]
    monkeypatch.setattr(main, "convert_pdf", _raise_stop)
    client.post(f"/api/batches/{body['batch_id']}/start", data={"formats": "docx"})

    reply = client.post(f"/api/jobs/{job_id}/pause")

    assert reply.status_code == 409


# -- cancelling ------------------------------------------------------------- #


def test_cancelling_a_pre_run_file_keeps_it_from_ever_converting(client, monkeypatch, tmp_path):
    body = _upload(client, monkeypatch, tmp_path, 2)
    target, other = (job["id"] for job in body["jobs"])

    assert client.post(f"/api/jobs/{target}/cancel").status_code == 200
    assert _statuses(client, body["batch_id"])[target] == "cancelled"

    calls = []
    monkeypatch.setattr(main, "convert_pdf", lambda **kw: calls.append(kw) or _raise_stop())
    client.post(f"/api/batches/{body['batch_id']}/start", data={"formats": "docx"})

    after = _statuses(client, body["batch_id"])
    assert after[target] == "cancelled"
    assert after[other] == "error"
    assert len(calls) == 1


def test_cancelling_a_running_file_unwinds_it_and_leaves_it_retryable(
    client, monkeypatch, tmp_path
):
    body = _upload(client, monkeypatch, tmp_path, 1)
    job_id = body["jobs"][0]["id"]

    def cancelled(**_kw):
        raise pipeline.ConversionCancelled("user cancelled mid-conversion")

    monkeypatch.setattr(main, "convert_pdf", cancelled)
    client.post(f"/api/batches/{body['batch_id']}/start", data={"formats": "docx"})

    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "cancelled"
    assert job["error"] is None
    assert job["has_source"] is True
    # The staging directory the abandoned run used is gone.
    assert not list(main.settings.jobs_dir.glob(".*-run-*"))

    def succeeds(**kw):
        markdown = kw["work_dir"] / "document.md"
        markdown.write_text("# Recovered", encoding="utf-8")
        (kw["work_dir"] / "detection.json").write_text('{"mode": "mathpix", "pages": []}')
        return pipeline.ConversionResult(markdown_path=markdown, docx_path=None)

    monkeypatch.setattr(main, "convert_pdf", succeeds)
    reply = client.post(f"/api/jobs/{job_id}/start", data={"formats": "docx"})

    assert reply.status_code == 200
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "done"


def test_the_running_conversion_sees_the_cancel_flag(client, monkeypatch, tmp_path):
    """`should_cancel` is what the pipeline actually polls; prove it is wired."""
    body = _upload(client, monkeypatch, tmp_path, 1)
    job_id = body["jobs"][0]["id"]

    def observe(**kw):
        check = kw["should_cancel"]
        assert check() is False
        main._cancel_event(job_id).set()
        assert check() is True
        raise pipeline.ConversionCancelled("cancelled")

    monkeypatch.setattr(main, "convert_pdf", observe)
    client.post(f"/api/batches/{body['batch_id']}/start", data={"formats": "docx"})

    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "cancelled"


# -- whole-batch controls -------------------------------------------------- #


def test_batch_pause_resume_and_cancel_over_a_mixed_batch(client, monkeypatch, tmp_path):
    body = _upload(client, monkeypatch, tmp_path, 3)
    batch_id = body["batch_id"]

    paused = client.post(f"/api/batches/{batch_id}/pause").json()
    assert paused["counts"].get("paused") == 3

    monkeypatch.setattr(main, "convert_pdf", _raise_stop)
    client.post(f"/api/batches/{batch_id}/resume", data={"formats": "docx"})
    assert set(_statuses(client, batch_id).values()) == {"error"}

    # Everything is terminal now; cancel-all is a no-op rather than an error.
    view = client.post(f"/api/batches/{batch_id}/cancel").json()
    assert set(job["status"] for job in view["jobs"]) == {"error"}


def test_batch_status_reports_counts_and_activity(client, monkeypatch, tmp_path):
    body = _upload(client, monkeypatch, tmp_path, 2)

    view = client.get(f"/api/batches/{body['batch_id']}").json()
    assert view["counts"] == {"ready": 2}
    assert view["active"] is False

    assert client.get("/api/batches/does-not-exist").status_code == 404


def test_another_account_cannot_see_or_touch_this_batch(
    client, other_client, monkeypatch, tmp_path
):
    body = _upload(client, monkeypatch, tmp_path, 1)

    assert other_client.get(f"/api/batches/{body['batch_id']}").status_code == 404
    assert other_client.post(f"/api/batches/{body['batch_id']}/cancel").status_code == 404
    assert other_client.post(f"/api/batches/{body['batch_id']}/start").status_code == 404


# -- concurrency --------------------------------------------------------- #


def test_a_batch_never_runs_more_files_than_the_worker_limit(
    client, monkeypatch, tmp_path
):
    body = _upload(client, monkeypatch, tmp_path, 5)
    monkeypatch.setattr(main, "BATCH_SLOTS", threading.BoundedSemaphore(2))

    lock = threading.Lock()
    release = threading.Event()
    state = {"live": 0, "peak": 0}

    def blocking(**_kw):
        with lock:
            state["live"] += 1
            state["peak"] = max(state["peak"], state["live"])
        release.wait(3)
        with lock:
            state["live"] -= 1
        raise RuntimeError("stop")

    monkeypatch.setattr(main, "convert_pdf", blocking)

    workers = []
    for job in body["jobs"]:
        record = main.JOBS[job["id"]]
        record.status = "queued"  # what `_dispatch` sets before handing off
        pdf_path = record.directory / "source.pdf"
        thread = threading.Thread(target=main._run_job, args=(job["id"], pdf_path))
        thread.start()
        workers.append(thread)

    time.sleep(0.4)
    peak = state["peak"]
    release.set()
    for thread in workers:
        thread.join(5)

    assert peak == 2


def test_dispatch_hands_each_file_to_the_worker_pool(client, monkeypatch, tmp_path):
    """The parallelism comes from `_POOL`. If a batch start ever went straight to
    `background.add_task` instead, Starlette would run the files one after another
    however many workers were configured — so pin the wiring."""
    body = _upload(client, monkeypatch, tmp_path, 3)
    submitted: list[tuple] = []

    class RecordingPool:
        def submit(self, fn, *args):
            submitted.append((fn, args))
            future: concurrent.futures.Future = concurrent.futures.Future()
            future.set_result(None)
            return future

    monkeypatch.setattr(main, "_POOL", RecordingPool())
    monkeypatch.setattr(main, "convert_pdf", _raise_stop)

    reply = client.post(f"/api/batches/{body['batch_id']}/start", data={"formats": "docx"})

    assert reply.status_code == 200
    assert len(submitted) == 3
    assert all(fn is main._run_job for fn, _ in submitted)
    assert {args[0] for _, args in submitted} == {job["id"] for job in body["jobs"]}


# -- persistence ------------------------------------------------------- #


def test_a_job_record_round_trips_its_batch_and_restart_state(tmp_path):
    job = main.Job(
        id="b1", user_id="u", batch_id="batch-9", filename="p.pdf", pages=2,
        status="queued", directory=tmp_path,
    )
    restored = main.Job.from_record(job.to_record())
    assert restored.batch_id == "batch-9"
    # Only ever waiting: it comes back re-startable, not failed.
    assert restored.status == "ready"

    job.status = "paused"
    assert main.Job.from_record(job.to_record()).status == "ready"

    job.status = "transcribing"
    assert main.Job.from_record(job.to_record()).status == "error"
