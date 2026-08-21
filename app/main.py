"""FastAPI app: upload a PDF, start the conversion, watch it run, download the .docx."""

from __future__ import annotations

import shutil
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import fitz
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import detection, history
from .columns import source_columns
from .config import settings
from .marker_client import RAW_DIR, RAW_IMAGE_DIR
from .mathpix_client import FORMATS as MATHPIX_FORMATS
from .mathpix_client import RAW_DIR as MATHPIX_RAW_DIR
from .mathpix_client import RAW_IMAGE_DIR as MATHPIX_RAW_IMAGE_DIR
from .mathpix_client import requested_formats
from .model_policy import ModelPolicyError, require_model_allowed
from .pdf_render import page_count, page_zoom
from .pipeline import ConversionUsage, convert_pdf
from .vision import list_vision_models

STATIC_DIR = Path(__file__).parent / "static"

# Stages during which the pipeline owns the job; a job in one of these at startup
# was interrupted by a restart.
RUNNING = ("queued", "rendering", "transcribing", "building")

app = FastAPI(title="PDF → DOCX", version="1.1.0")


LAYOUTS = ("structured", "replica", "flow", "marker", "mathpix")

# The two answers the browser can give about columns. `natural` is one flowing
# column — what a transcription is, being one linear stream of text — and `multi`
# sets each page the way the source page was set. Only the flowing modes can act
# on either; the replica modes put every block back where it came from, columns
# and all. Anything else, including the empty string the form sends when the
# control was never shown, leaves `PDF2DOCX_COLUMNS` in charge.
COLUMN_CHOICES = ("natural", "multi")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _layout_choice(value: str) -> str:
    choice = (value or "").strip().lower()
    return choice if choice in LAYOUTS else settings.layout


def _columns_choice(value: str) -> str:
    choice = (value or "").strip().lower()
    return choice if choice in COLUMN_CHOICES else ""


def _required_credential(layout: str) -> str | None:
    """Which credential the chosen path needs, or None if it needs none.

    Three answers rather than two, because the modes now disagree about more
    than whether they are remote. `flow` and the replica modes may reach for the
    vision model — `flow` for every page, the replica modes for a scanned page
    or an equation crop — so all of them want an OpenRouter key. `mathpix` is
    remote but never touches OpenRouter: it wants Mathpix's own credentials and
    nothing else.

    Note what this means for `app.model_policy`, which is about OpenRouter model
    ids. A mode that returns anything other than "openrouter" here never reaches
    a model this application chose, so the policy has nothing to say about it —
    that is already true of `marker` and is now also true of `mathpix`.
    """
    if layout == "marker":
        return None
    if layout == "mathpix":
        return "mathpix"
    return "openrouter"


def _require_credential(layout: str) -> None:
    """Refuse a job whose backend has no way to authenticate."""
    needed = _required_credential(layout)
    if needed == "mathpix" and not settings.mathpix_app_key:
        raise HTTPException(
            status_code=503,
            detail="MATHPIX_APP_KEY is required by the mathpix output mode.",
        )
    if needed == "openrouter" and not settings.api_key:
        raise HTTPException(
            status_code=503,
            detail="OPENROUTER_API_KEY is required by the selected extraction path.",
        )


def _model_choice(value: str, fallback: str, *, remote: bool) -> str:
    """Validate model selections before a job can reach a remote call."""
    try:
        selected = require_model_allowed(value.strip() or fallback)
        if remote and settings.locate_figures and settings.figure_model:
            require_model_allowed(settings.figure_model)
        return selected
    except ModelPolicyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@dataclass
class Job:
    id: str
    filename: str
    pages: int
    model: str = settings.model
    layout: str = settings.layout
    # "natural" | "multi" | "" for whatever PDF2DOCX_COLUMNS says.
    columns: str = ""
    # The most columns any page of the uploaded PDF is set in, read from the PDF
    # at upload. One means the browser has no choice to offer: a source with a
    # single column has no second column for the output to have.
    source_columns: int = 1
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
    diagnostics: list[dict] = field(default_factory=list)
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
            "columns": self.columns,
            "source_columns": self.source_columns,
            "diagnostics": self.diagnostics,
            "status": self.status,
            "done": self.done,
            "total": self.total,
            "error": self.error,
            "size_bytes": self.size_bytes,
            "cost": round(self.cost, 6),
            "cost_known": self.cost_known,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "calls": self.calls,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "has_docx": self._file("document.docx").exists(),
            "has_md": self._file("document.md").exists(),
            "has_source": self._file("source.pdf").exists(),
            "has_marker": self._file(f"{RAW_DIR}/document.md").exists(),
            "has_rebuilt": self._file("rebuilt.docx").exists(),
            # Which of Mathpix's exports this job actually has, read from disk
            # rather than remembered. A format Mathpix does not produce for a
            # given document — no .xlsx without tables — is simply absent, and
            # the browser draws a button per entry here rather than guessing.
            "mathpix_formats": self.mathpix_formats(),
            "has_detection": self._file("detection.json").exists(),
        }

    def mathpix_formats(self) -> list[str]:
        return [
            entry.ext
            for entry in MATHPIX_FORMATS
            if self._file(f"{MATHPIX_RAW_DIR}/document.{entry.ext}").exists()
        ]

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
            columns=_columns_choice(record.get("columns") or ""),
            source_columns=int(record.get("source_columns") or 1),
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
            diagnostics=record.get("diagnostics") if isinstance(record.get("diagnostics"), list) else [],
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
        result = convert_pdf(
            pdf_path=pdf_path,
            work_dir=pdf_path.parent,
            title=Path(job.filename).stem,
            model=job.model,
            on_progress=on_progress,
            on_usage=on_usage,
            layout=job.layout,
            columns=job.columns or None,
        )
        with JOBS_LOCK:
            job.diagnostics = [
                {"page": item.page, "kind": item.kind, "extractor": item.extractor,
                 "fallback_reason": item.fallback_reason}
                for item in result.diagnostics
            ]
            job.status = "done"
            job.finished_at = _now()
    except Exception as exc:  # surfaced to the browser rather than swallowed
        with JOBS_LOCK:
            job.status = "error"
            job.error = f"{type(exc).__name__}: {exc}"
            job.finished_at = _now()
    finally:
        _persist()


# The page's own stylesheet and script, and the vendored Markdown/maths
# renderer. Everything the browser loads comes from here, so the viewer works
# with no network at all — the same promise the conversion itself makes.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


@app.get("/api/config")
def config() -> dict:
    return {
        "provider": "OpenRouter",
        "marker_url": settings.marker_url,
        "marker_options": settings.marker_options,
        "marker_extra_formats": list(settings.marker_extra_formats),
        "mathpix_url": settings.mathpix_url,
        "mathpix_options": settings.mathpix_options,
        "mathpix_requested": list(requested_formats(settings.mathpix_formats)),
        # The browser labels its download buttons from this, so the list of
        # formats lives in one place rather than being spelled out again in JS.
        "mathpix_formats": [
            {"ext": entry.ext, "media_type": entry.media_type, "note": entry.note}
            for entry in MATHPIX_FORMATS
        ],
        "mathpix_key_configured": bool(settings.mathpix_app_key),
        "model": settings.model,
        "reasoning_effort": settings.reasoning_effort or None,
        "dpi": settings.dpi,
        "concurrency": settings.concurrency,
        "max_pages": settings.max_pages,
        "api_key_configured": bool(settings.api_key),
        "layout": settings.layout,
        "columns": settings.columns,
        "math_mode": settings.math_mode,
        "data_dir": str(settings.data_dir),
        "history_limit": settings.history_limit,
    }


@app.get("/api/models")
def models() -> dict:
    """Allowed vision models currently available on OpenRouter."""
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
    columns: str = Form(default=""),
    start: bool = Form(default=False),
) -> dict:
    """Stage an uploaded PDF. Conversion waits for /start unless `start` is set."""
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Please upload a .pdf file.")
    selected_layout = _layout_choice(layout)
    selected_model = _model_choice(
        model, settings.model, remote=_required_credential(selected_layout) == "openrouter"
    )
    _require_credential(selected_layout)

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
        model=selected_model,
        layout=selected_layout,
        columns=_columns_choice(columns),
        # Read here rather than in the browser, which cannot see inside a PDF,
        # and before the conversion starts, because it decides which choices the
        # page is allowed to offer for it.
        source_columns=source_columns(pdf_path),
        total=pages,
        size_bytes=size,
        directory=directory,
    )
    with JOBS_LOCK:
        JOBS[job_id] = job
    _persist()

    if start:
        return start_job(job_id, background, model=model, layout=layout, columns=columns)
    return job.as_dict()


@app.post("/api/jobs/{job_id}/start")
def start_job(
    job_id: str,
    background: BackgroundTasks,
    model: str = Form(default=""),
    layout: str = Form(default=""),
    columns: str = Form(default=""),
) -> dict:
    """Begin (or re-run) the conversion for an already-uploaded PDF."""
    job = _get_job(job_id)
    if job.status in RUNNING:
        raise HTTPException(status_code=409, detail="This conversion is already running.")

    selected_layout = _layout_choice(layout) if layout.strip() else job.layout
    selected_model = _model_choice(
        model, job.model, remote=_required_credential(selected_layout) == "openrouter"
    )
    _require_credential(selected_layout)

    pdf_path = (job.directory or Path()) / "source.pdf"
    if not pdf_path.exists():
        raise HTTPException(
            status_code=409, detail="The uploaded PDF is no longer available — upload it again."
        )

    with JOBS_LOCK:
        job.model = selected_model
        job.layout = selected_layout
        job.columns = _columns_choice(columns) or job.columns
        job.status = "queued"
        job.done = 0
        job.total = job.pages
        job.error = None
        job.cost = 0.0
        job.prompt_tokens = 0
        job.completion_tokens = 0
        job.calls = 0
        job.priced_calls = 0
        job.diagnostics = []
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


@app.get("/api/jobs/{job_id}/detection")
def job_detection(job_id: str) -> dict:
    """What the converter saw, page by page: block boxes, kinds, reading order."""
    job = _get_job(job_id)
    path = (job.directory or Path()) / "detection.json"
    if not path.exists():
        raise HTTPException(status_code=409, detail="The page detection is not ready yet.")
    return detection.read(path)


@app.get("/api/jobs/{job_id}/page/{number}.png")
def job_page(job_id: str, number: int) -> FileResponse:
    """One page of the source PDF, rasterised for the viewer.

    Rendered on demand and kept, rather than rendered for every job up front:
    only the flow mode rasterises whole pages during a conversion, and rendering
    every page of every job would fill the history directory for a view that may
    never be opened.
    """
    job = _get_job(job_id)
    directory = job.directory or Path()
    pdf_path = directory / "source.pdf"
    if not pdf_path.exists():
        raise HTTPException(status_code=409, detail="The uploaded PDF is no longer available.")

    path = directory / "preview" / f"page-{number:04d}.png"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with fitz.open(pdf_path) as doc:
            if not 1 <= number <= doc.page_count:
                raise HTTPException(status_code=404, detail="No such page.")
            page = doc.load_page(number - 1)
            # The same zoom the rest of the application renders at, so a box
            # read off one view of the page lands where it does on the other.
            zoom = page_zoom(page.rect.width, page.rect.height)
            page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False).save(path)
    return FileResponse(path, media_type="image/png")


# Where a job's Markdown may point. Figures this application cut, and images
# marker extracted — nothing else in a job directory is meant to be fetched by
# the browser, and the finished documents have their own download route.
ASSET_DIRS = (
    "figures",
    f"{RAW_DIR}/{RAW_IMAGE_DIR}",
    f"{MATHPIX_RAW_DIR}/{MATHPIX_RAW_IMAGE_DIR}",
)


@app.get("/api/jobs/{job_id}/asset/{asset:path}")
def job_asset(job_id: str, asset: str) -> FileResponse:
    """An image referenced by a job's Markdown, for the rendered preview."""
    job = _get_job(job_id)
    directory = (job.directory or Path()).resolve()
    path = (directory / asset).resolve()
    # Two separate questions: is this still inside the job (a `..` in the path
    # would leave it), and is it one of the directories a document may refer to.
    if not path.is_relative_to(directory) or not path.is_file():
        raise HTTPException(status_code=404, detail="No such asset.")
    relative = path.relative_to(directory).as_posix()
    if not any(relative.startswith(f"{allowed}/") for allowed in ASSET_DIRS):
        raise HTTPException(status_code=404, detail="No such asset.")
    return FileResponse(path)


# What each `format` names, as a fixed path and media type. Marker's own output
# is downloadable because reading it is how the mode gets judged — and because
# it is the only copy that nothing in this application has edited.
DOWNLOADS = {
    "docx": (
        "document.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "docx",
    ),
    "md": ("document.md", "text/markdown", "md"),
    "marker-md": (f"{RAW_DIR}/document.md", "text/markdown", "marker.md"),
    "marker-html": (f"{RAW_DIR}/document.html", "text/html", "marker.html"),
    "marker-json": (f"{RAW_DIR}/document.json", "application/json", "marker.json"),
    "marker-meta": (f"{RAW_DIR}/metadata.json", "application/json", "marker.meta.json"),
    "rebuilt-docx": (
        "rebuilt.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "rebuilt.docx",
    ),
    "mathpix-meta": (
        f"{MATHPIX_RAW_DIR}/metadata.json", "application/json", "mathpix.meta.json"
    ),
}

# Every export Mathpix offers, generated from the client's own table rather than
# written out here. A format Mathpix ships later is a row in that table and
# nothing else — this loop, the config endpoint and the browser's button row all
# follow it. The route below needs no special case: it already refuses a format
# it does not know and reports one this job has not got as not ready.
DOWNLOADS.update(
    {
        f"mathpix-{entry.ext}": (
            f"{MATHPIX_RAW_DIR}/document.{entry.ext}",
            entry.media_type,
            f"mathpix.{entry.ext}",
        )
        for entry in MATHPIX_FORMATS
    }
)


@app.get("/api/jobs/{job_id}/download")
def job_download(job_id: str, format: str = "docx") -> FileResponse:
    job = _get_job(job_id)
    choice = DOWNLOADS.get(format)
    if choice is None:
        raise HTTPException(
            status_code=400, detail=f"format must be one of {', '.join(DOWNLOADS)}"
        )

    name, media_type, extension = choice
    path = (job.directory or Path()) / name
    if not path.exists():
        raise HTTPException(status_code=409, detail="The file is not ready yet.")

    stem = Path(job.filename).stem or "document"
    return FileResponse(path, media_type=media_type, filename=f"{stem}.{extension}")


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
