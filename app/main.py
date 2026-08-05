"""FastAPI app: upload a PDF, start the conversion, watch it run, download the .docx."""

from __future__ import annotations

import shutil
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from . import history
from .config import settings
from .pdf_render import page_count
from .pipeline import ConversionUsage, convert_pdf
from .vision import list_vision_models

STATIC_DIR = Path(__file__).parent / "static"

# Stages during which the pipeline owns the job; a job in one of these at startup
# was interrupted by a restart.
RUNNING = ("queued", "rendering", "transcribing", "building")

app = FastAPI(title="PDF → DOCX", version="1.1.0")


LAYOUTS = ("structured", "replica", "flow")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _layout_choice(value: str) -> str:
    choice = (value or "").strip().lower()
    return choice if choice in LAYOUTS else settings.layout


@dataclass
class Job:
    id: str
    filename: str
    pages: int
    model: str = settings.model
    layout: str = settings.layout
    # ready | queued | rendering | transcribing | building | done | error
    status: str = "ready"
    done: int = 0
    total: int = 0
    error: str | None = None
    size_bytes: int = 0
    cost: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0
    priced_calls: int = 0
    created_at: str = field(default_factory=_now)
    started_at: str | None = None
    finished_at: str | None = None
    directory: Path | None = None

    def _file(self, name: str) -> Path:
        return (self.directory or Path()) / name

    @property
    def cost_known(self) -> bool:
        """True when every billed call reported a price (or none were needed).

        A job that has not run yet has no cost to report, known or otherwise.
        """
        if self.status == "ready":
            return False
        return self.calls == 0 or self.priced_calls >= self.calls

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "filename": self.filename,
            "pages": self.pages,
            "model": self.model,
            "layout": self.layout,
            "status": self.status,
            "done": self.done,
            "total": self.total,
            "error": self.error,
            "size_bytes": self.size_bytes,
            "cost": round(self.cost, 6),
            "cost_known": self.cost_known,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "has_docx": self._file("document.docx").exists(),
            "has_md": self._file("document.md").exists(),
            "has_source": self._file("source.pdf").exists(),
        }

    def to_record(self) -> dict:
        record = self.as_dict()
        record["calls"] = self.calls
        record["priced_calls"] = self.priced_calls
        record["directory"] = str(self.directory) if self.directory else None
        return record

    @classmethod
    def from_record(cls, record: dict) -> Job:
        directory = record.get("directory")
        status = record.get("status") or "ready"
        error = record.get("error")
        if status in RUNNING:
            # The process that owned this job is gone.
            status, error = "error", "Interrupted — the server restarted mid-conversion."
        return cls(
            id=str(record["id"]),
            filename=record.get("filename") or "document.pdf",
            pages=int(record.get("pages") or 0),
            model=record.get("model") or settings.model,
            layout=record.get("layout") or settings.layout,
            status=status,
            done=int(record.get("done") or 0),
            total=int(record.get("total") or 0),
            error=error,
            size_bytes=int(record.get("size_bytes") or 0),
            cost=float(record.get("cost") or 0.0),
            prompt_tokens=int(record.get("prompt_tokens") or 0),
            completion_tokens=int(record.get("completion_tokens") or 0),
            calls=int(record.get("calls") or 0),
            priced_calls=int(record.get("priced_calls") or 0),
            created_at=record.get("created_at") or _now(),
            started_at=record.get("started_at"),
            finished_at=record.get("finished_at"),
            directory=Path(directory) if directory else None,
        )


JOBS: dict[str, Job] = {}
JOBS_LOCK = threading.Lock()


def _newest_first() -> list[Job]:
    with JOBS_LOCK:
        jobs = list(JOBS.values())
    return sorted(jobs, key=lambda job: job.created_at, reverse=True)


def _persist() -> None:
    """Mirror the registry to disk, trimming (and deleting) the oldest overflow."""
    jobs = _newest_first()
    records = [job.to_record() for job in jobs]

    for stale in history.overflow(records):
        directory = stale.get("directory")
        if directory:
            shutil.rmtree(directory, ignore_errors=True)
        with JOBS_LOCK:
            JOBS.pop(stale["id"], None)

    limit = settings.history_limit
    history.save(records[:limit] if limit > 0 else records)


def _restore() -> None:
    """Rebuild the registry from disk, dropping records whose files are gone."""
    for record in history.load():
        job = Job.from_record(record)
        if job.directory is None or not job.directory.exists():
            continue
        JOBS[job.id] = job


_restore()


def _get_job(job_id: str) -> Job:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job id")
    return job


def _run_job(job_id: str, pdf_path: Path) -> None:
    job = JOBS[job_id]

    def on_progress(stage: str, done: int, total: int) -> None:
        with JOBS_LOCK:
            job.status = stage
            job.done = done
            job.total = total or job.total

    def on_usage(usage: ConversionUsage) -> None:
        with JOBS_LOCK:
            job.cost = usage.cost
            job.prompt_tokens = usage.prompt_tokens
            job.completion_tokens = usage.completion_tokens
            job.calls = usage.calls
            job.priced_calls = usage.priced_calls

    try:
        convert_pdf(
            pdf_path=pdf_path,
            work_dir=pdf_path.parent,
            title=Path(job.filename).stem,
            model=job.model,
            on_progress=on_progress,
            on_usage=on_usage,
            layout=job.layout,
        )
        with JOBS_LOCK:
            job.status = "done"
            job.finished_at = _now()
    except Exception as exc:  # surfaced to the browser rather than swallowed
        with JOBS_LOCK:
            job.status = "error"
            job.error = f"{type(exc).__name__}: {exc}"
            job.finished_at = _now()
    finally:
        _persist()


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


@app.get("/api/config")
def config() -> dict:
    return {
        "provider": "OpenRouter",
        "model": settings.model,
        "reasoning_effort": settings.reasoning_effort or None,
        "dpi": settings.dpi,
        "concurrency": settings.concurrency,
        "max_pages": settings.max_pages,
        "api_key_configured": bool(settings.api_key),
        "layout": settings.layout,
        "math_mode": settings.math_mode,
        "data_dir": str(settings.data_dir),
        "history_limit": settings.history_limit,
    }


@app.get("/api/models")
def models() -> dict:
    """Vision-capable models currently available on OpenRouter."""
    try:
        return {"models": list_vision_models(), "selected": settings.model}
    except Exception as exc:
        # The picker is a convenience — a failure here must not block conversion.
        return {"models": [], "selected": settings.model, "error": str(exc)}


@app.get("/api/history")
def get_history() -> dict:
    jobs = [job.as_dict() for job in _newest_first()]
    spent = sum(job["cost"] for job in jobs if job["cost_known"])
    return {"jobs": jobs, "total_cost": round(spent, 6), "count": len(jobs)}


@app.post("/api/convert")
async def convert(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    model: str = Form(default=""),
    layout: str = Form(default=""),
    start: bool = Form(default=False),
) -> dict:
    """Stage an uploaded PDF. Conversion waits for /start unless `start` is set."""
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Please upload a .pdf file.")
    if not settings.api_key:
        raise HTTPException(
            status_code=503,
            detail="OPENROUTER_API_KEY is not set. Add it to .env and restart the server.",
        )

    job_id = uuid.uuid4().hex[:12]
    directory = settings.jobs_dir / job_id
    directory.mkdir(parents=True, exist_ok=True)
    pdf_path = directory / "source.pdf"

    with pdf_path.open("wb") as handle:
        shutil.copyfileobj(file.file, handle)

    size = pdf_path.stat().st_size
    if size == 0:
        shutil.rmtree(directory, ignore_errors=True)
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")

    try:
        pages = page_count(pdf_path)
    except Exception as exc:
        shutil.rmtree(directory, ignore_errors=True)
        raise HTTPException(status_code=400, detail=f"Could not read the PDF: {exc}") from exc

    if settings.max_pages and pages > settings.max_pages:
        pages = settings.max_pages

    job = Job(
        id=job_id,
        filename=file.filename,
        pages=pages,
        model=model.strip() or settings.model,
        layout=_layout_choice(layout),
        total=pages,
        size_bytes=size,
        directory=directory,
    )
    with JOBS_LOCK:
        JOBS[job_id] = job
    _persist()

    if start:
        return start_job(job_id, background, model=model, layout=layout)
    return job.as_dict()


@app.post("/api/jobs/{job_id}/start")
def start_job(
    job_id: str,
    background: BackgroundTasks,
    model: str = Form(default=""),
    layout: str = Form(default=""),
) -> dict:
    """Begin (or re-run) the conversion for an already-uploaded PDF."""
    job = _get_job(job_id)
    if job.status in RUNNING:
        raise HTTPException(status_code=409, detail="This conversion is already running.")

    pdf_path = (job.directory or Path()) / "source.pdf"
    if not pdf_path.exists():
        raise HTTPException(
            status_code=409, detail="The uploaded PDF is no longer available — upload it again."
        )

    with JOBS_LOCK:
        job.model = model.strip() or job.model
        job.layout = _layout_choice(layout) if layout.strip() else job.layout
        job.status = "queued"
        job.done = 0
        job.total = job.pages
        job.error = None
        job.cost = 0.0
        job.prompt_tokens = 0
        job.completion_tokens = 0
        job.calls = 0
        job.priced_calls = 0
        job.started_at = _now()
        job.finished_at = None
    _persist()

    background.add_task(_run_job, job_id, pdf_path)
    return job.as_dict()


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    return _get_job(job_id).as_dict()


@app.get("/api/jobs/{job_id}/markdown")
def job_markdown(job_id: str) -> dict:
    job = _get_job(job_id)
    path = (job.directory or Path()) / "document.md"
    if not path.exists():
        raise HTTPException(status_code=409, detail="Markdown is not ready yet.")
    return {"markdown": path.read_text(encoding="utf-8")}


@app.get("/api/jobs/{job_id}/download")
def job_download(job_id: str, format: str = "docx") -> FileResponse:
    job = _get_job(job_id)
    if format not in ("docx", "md"):
        raise HTTPException(status_code=400, detail="format must be 'docx' or 'md'")

    path = (job.directory or Path()) / ("document.docx" if format == "docx" else "document.md")
    if not path.exists():
        raise HTTPException(status_code=409, detail="The file is not ready yet.")

    media_type = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        if format == "docx"
        else "text/markdown"
    )
    stem = Path(job.filename).stem or "document"
    return FileResponse(path, media_type=media_type, filename=f"{stem}.{format}")


@app.delete("/api/jobs/{job_id}")
def job_delete(job_id: str) -> dict:
    job = _get_job(job_id)
    if job.status in RUNNING:
        raise HTTPException(
            status_code=409, detail="This conversion is still running — wait for it to finish."
        )
    if job.directory:
        shutil.rmtree(job.directory, ignore_errors=True)
    with JOBS_LOCK:
        JOBS.pop(job_id, None)
    _persist()
    return {"deleted": job_id}


@app.delete("/api/history")
def history_clear() -> dict:
    """Delete every job that is not currently running, and its files."""
    deleted = 0
    for job in _newest_first():
        if job.status in RUNNING:
            continue
        if job.directory:
            shutil.rmtree(job.directory, ignore_errors=True)
        with JOBS_LOCK:
            JOBS.pop(job.id, None)
        deleted += 1
    _persist()
    return {"deleted": deleted}
