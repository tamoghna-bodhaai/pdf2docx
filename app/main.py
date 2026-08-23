"""FastAPI app: upload a PDF, start the conversion, watch it run, download the .docx."""

from __future__ import annotations

import logging
import secrets
import shutil
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import fitz
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth, db, detection, storage
from .auth import User, current_user
from .config import settings
from .mathpix_client import FORMATS as MATHPIX_FORMATS
from .mathpix_client import MathpixError
from .mathpix_client import RAW_DIR as MATHPIX_RAW_DIR
from .mathpix_client import RAW_IMAGE_DIR as MATHPIX_RAW_IMAGE_DIR
from .mathpix_client import requestable_formats
from .mathpix_client import requested_formats
from .pdf_render import page_count, page_zoom
from .pipeline import ConversionUsage, convert_pdf

STATIC_DIR = Path(__file__).parent / "static"

# Stages during which the pipeline owns the job; a job in one of these at startup
# was interrupted by a restart.
RUNNING = ("queued", "rendering", "transcribing", "building")

app = FastAPI(title="PDF → DOCX", version="2.0.0")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mathpix_layout(value: str) -> str:
    """Pin a web conversion to Mathpix while accepting the legacy form field.

    Old clients can keep sending ``layout=mathpix`` and clients that omit the
    field get the same result. An explicit legacy value is refused instead of
    being silently reinterpreted, which makes the API contract unambiguous.
    ``PDF2DOCX_LAYOUT`` remains available to direct pipeline callers only.
    """
    choice = (value or "").strip().lower()
    if choice and choice != "mathpix":
        raise HTTPException(
            status_code=400,
            detail="Only the mathpix layout is available for new conversions.",
        )
    return "mathpix"


def _require_mathpix_credential() -> None:
    """Refuse a web job when Mathpix cannot authenticate it."""
    if not settings.mathpix_app_key:
        raise HTTPException(
            status_code=503,
            detail="MATHPIX_APP_KEY is required to convert PDFs.",
        )


def _start_formats(value: str | None) -> tuple[str, ...]:
    """Resolve the optional start-form selection through the format catalog.

    Omission is the legacy configured default. Presence is exact, including an
    empty string, because Mathpix's MMD preview does not need an optional
    conversion format.
    """
    if value is None:
        return requested_formats(settings.mathpix_formats)

    names = [name.strip().lower() for name in value.split(",") if name.strip()]
    supported = tuple(entry.ext for entry in MATHPIX_FORMATS if entry.requested)
    invalid = list(dict.fromkeys(name for name in names if name not in supported))
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported or non-requestable format(s): {', '.join(invalid)}. "
                f"Supported values: {', '.join(supported)}."
            ),
        )
    return requestable_formats(names)


@dataclass
class Job:
    id: str
    filename: str
    pages: int
    # Which account uploaded this. Every route that reaches a job checks it, and
    # it is the only thing separating one teammate's documents from another's.
    user_id: str = ""
    # Always "mathpix" for a new job; the browser reads it to pick the viewer,
    # and a pre-Mathpix record may still say something else.
    layout: str = "mathpix"
    requested_formats: tuple[str, ...] = ()
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
            "layout": self.layout,
            "requested_formats": list(self.requested_formats),
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
        # Ownership is stored, but never reported: `as_dict` is what the browser
        # sees, and it has no use for another account's identifier.
        record["user_id"] = self.user_id
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
        raw_requested = record.get("requested_formats")
        if isinstance(raw_requested, list):
            selected = tuple(
                name
                for name in raw_requested
                if isinstance(name, str)
                and name in {entry.ext for entry in MATHPIX_FORMATS if entry.requested}
            )
        else:
            # Records written before per-job selection existed infer what was
            # requested from the requestable outputs that remain on disk.
            root = Path(directory) if directory else Path()
            selected = tuple(
                entry.ext
                for entry in MATHPIX_FORMATS
                if entry.requested
                and (root / MATHPIX_RAW_DIR / f"document.{entry.ext}").exists()
            )
        return cls(
            id=str(record["id"]),
            user_id=str(record.get("user_id") or ""),
            filename=record.get("filename") or "document.pdf",
            pages=int(record.get("pages") or 0),
            layout=record.get("layout") or "mathpix",
            requested_formats=selected,
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


def _newest_first(user_id: str) -> list[Job]:
    """One account's jobs, newest first. Never another account's."""
    with JOBS_LOCK:
        jobs = [job for job in JOBS.values() if job.user_id == user_id]
    return sorted(jobs, key=lambda job: job.created_at, reverse=True)


def _persist(job: Job) -> None:
    """Mirror one job to the database, trimming (and deleting) its owner's overflow.

    The history limit is per account rather than global: one teammate's busy week
    should not push another's finished documents off the end.
    """
    db.save_job(job.user_id, job.id, job.created_at, job.to_record())

    for stale in db.overflow(job.user_id):
        directory = stale.get("directory")
        if directory:
            shutil.rmtree(directory, ignore_errors=True)
        with JOBS_LOCK:
            JOBS.pop(stale["id"], None)
        db.delete_job(stale["id"])


def _restore() -> None:
    """Rebuild the registry from the database, dropping records whose files are gone."""
    for record in db.load_jobs():
        job = Job.from_record(record)
        if job.directory is None or not job.directory.exists():
            continue
        JOBS[job.id] = job


def _recover_interrupted_promotions(jobs_dir: Path | None = None) -> None:
    """Restore or clean the narrowly named directories left by a process crash."""
    root = jobs_dir or settings.jobs_dir
    if not root.is_dir():
        return

    backups = sorted(
        (path for path in root.glob(".*-previous-*") if path.is_dir()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for backup in backups:
        job_id, separator, _token = backup.name[1:].partition("-previous-")
        if not separator or not job_id:
            continue
        canonical = root / job_id
        if canonical.exists():
            shutil.rmtree(backup, ignore_errors=True)
        else:
            backup.rename(canonical)

    # A running conversion never promotes its `-run-` directory until every
    # result is complete. After a restart, any such sibling is necessarily an
    # abandoned partial run and cannot be a source of valid downloads.
    for staged in root.glob(".*-run-*"):
        if staged.is_dir():
            shutil.rmtree(staged, ignore_errors=True)


_recover_interrupted_promotions()
_restore()
# Sessions outlive the process; the expired ones are only ever refused, never
# cleaned up in the request path, so boot is where they go. A row that can only
# ever be refused is dead weight, and anyone reaching /login can mint one.
db.purge_expired_sessions()

# Fatal, at boot, before anything can be written. An instance whose volume was
# never attached otherwise looks completely healthy right up until the deploy
# that empties it, and a warning cannot stop that deploy — it has already
# happened by the time anyone reads the log. Refusing to start does stop it: the
# healthcheck goes unanswered, Railway marks the deploy failed, and the previous
# deployment keeps serving. Nothing is lost by a failed deploy; everything is
# lost by a successful one writing to disposable storage.
#
# This raises only on Railway. Everywhere else the check is advisory, because a
# data directory on the root device is completely normal on a laptop.
storage.require_durable_storage()

_EPHEMERAL_WARNING = storage.warn_if_ephemeral()
if _EPHEMERAL_WARNING:
    logging.getLogger("uvicorn.error").warning(_EPHEMERAL_WARNING)


def _get_job(job_id: str, user: User) -> Job:
    """One of this account's jobs.

    Someone else's job is reported as unknown rather than forbidden: a 403 would
    confirm the id exists, and a job id is short enough to be worth guessing.
    """
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if job is None or job.user_id != user.id:
        raise HTTPException(status_code=404, detail="Unknown job id")
    return job


def _preserve_compatibility_files(previous: Path, staged: Path) -> None:
    """Carry historical-only downloads and the source preview into a rerun."""
    source = previous / "preview"
    target = staged / "preview"
    if source.is_dir() and not target.exists():
        shutil.copytree(source, target)
    rebuilt = previous / "rebuilt.docx"
    if rebuilt.is_file():
        shutil.copy2(rebuilt, staged / rebuilt.name)


def _promote_staged_job(directory: Path, staged: Path) -> None:
    """Replace one completed job directory, restoring the old one if promotion fails."""
    backup = directory.parent / f".{directory.name}-previous-{uuid.uuid4().hex}"
    directory.rename(backup)
    try:
        staged.rename(directory)
    except OSError:
        backup.rename(directory)
        raise
    shutil.rmtree(backup, ignore_errors=True)


def _run_job(job_id: str, pdf_path: Path) -> None:
    job = JOBS[job_id]
    directory = pdf_path.parent
    staged: Path | None = None

    def on_progress(stage: str, done: int, total: int) -> None:
        with JOBS_LOCK:
            # A pipeline reports "done" when *its* work is over, which is still
            # several steps before the results reach `job.directory`: the
            # mathpix mode then deletes the remote upload over the network, and
            # this function's caller has yet to promote the staged directory.
            # Publishing that stage verbatim would let a poll see a finished job
            # whose outputs do not exist yet — and the browser stops polling on
            # "done", so it would never see them appear. Only the promotion path
            # below may end a job; until then the last stage the job can be in
            # is the one that is still assembling it.
            job.status = "building" if stage == "done" else stage
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
        staged = Path(tempfile.mkdtemp(prefix=f".{job.id}-run-", dir=directory.parent))
        staged_pdf = staged / "source.pdf"
        shutil.copy2(pdf_path, staged_pdf)
        result = convert_pdf(
            pdf_path=staged_pdf,
            work_dir=staged,
            on_progress=on_progress,
            on_usage=on_usage,
            mathpix_formats=job.requested_formats,
        )
        _preserve_compatibility_files(directory, staged)
        _promote_staged_job(directory, staged)
        with JOBS_LOCK:
            usage = getattr(result, "usage", ConversionUsage())
            job.cost = usage.cost
            job.prompt_tokens = usage.prompt_tokens
            job.completion_tokens = usage.completion_tokens
            job.calls = usage.calls
            job.priced_calls = usage.priced_calls
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
            # A conversion failure already reads as a sentence; prefixing it with
            # the exception's class name only puts the provider's name on screen.
            job.error = str(exc) if isinstance(exc, MathpixError) else f"{type(exc).__name__}: {exc}"
            job.finished_at = _now()
    finally:
        if staged is not None and staged.exists():
            shutil.rmtree(staged, ignore_errors=True)
        _persist(job)


# The page's own stylesheet and script, and the vendored Markdown/maths
# renderer. Everything the viewer loads comes from here; only the conversion
# request and Mathpix-hosted images cross the network.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/healthz")
def healthz() -> dict:
    """Unauthenticated liveness check, for the platform rather than the browser.

    It also reports where the data lives: which volume, mounted where, and how
    much room is left on it. A missing volume can no longer get this far on
    Railway, but free space can still run out, and running out is the next way
    conversions start failing. Unauthenticated on purpose, so an instance can be
    diagnosed without signing in to it.
    """
    return {"ok": True, "storage": storage.report()}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    if auth.session_user(request) is None:
        # Relative, so a TLS-terminating proxy in front of this cannot turn the
        # redirect back into plain http.
        return RedirectResponse("/login", status_code=302)
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if auth.session_user(request) is not None:
        return RedirectResponse("/", status_code=302)
    return HTMLResponse((STATIC_DIR / "login.html").read_text(encoding="utf-8"))


class Credentials(BaseModel):
    email: str
    password: str


class Registration(Credentials):
    invite_code: str = ""


@app.get("/api/auth/config")
def auth_config(request: Request) -> dict:
    """What the sign-in page needs before anyone is signed in.

    Only whether an account can be created at all. With no invite codes
    configured, signup is closed and the page says so rather than offering a
    form that can only be refused.
    """
    return {"signup_open": auth.signup_open()}


@app.post("/api/auth/signup")
def signup(body: Registration, request: Request, response: Response) -> dict:
    """Create an account.

    The invite code is checked before anything is created. It is what decides
    whether an address may spend this instance's Mathpix credit, so it is the
    first thing consulted and the password is not even looked at until it
    passes.
    """
    auth.check_invite(body.invite_code, request)
    user = auth.register(body.email, body.password)
    auth.open_session(request, response, user.id)
    return user.as_dict()


@app.post("/api/auth/login")
def login(body: Credentials, request: Request, response: Response):
    """Sign in against the `users` table.

    Identity lives here and nowhere else. It used to be held in Supabase, which
    survived a redeploy while the local rows did not — so after every deploy the
    password was accepted and the account behind it was gone, and the user was
    asked for an invite code as though they were a stranger. One store, on the
    volume, cannot disagree with itself that way.
    """
    user = auth.authenticate(body.email, body.password)
    auth.open_session(request, response, user.id)
    return user.as_dict()


@app.post("/api/auth/logout")
def logout(request: Request, response: Response) -> dict:
    auth.close_session(request, response)
    return {"signed_out": True}


@app.get("/api/auth/me")
def whoami(user: User = Depends(current_user)) -> dict:
    return user.as_dict()


@app.get("/api/config")
def config(user: User = Depends(current_user)) -> dict:
    return {
        "provider": "Mathpix",
        "mathpix_url": settings.mathpix_url,
        "mathpix_options": settings.mathpix_options,
        "mathpix_requested": list(requested_formats(settings.mathpix_formats)),
        # The browser labels its download buttons from this, so the list of
        # formats lives in one place rather than being spelled out again in JS.
        "mathpix_formats": [
            {
                "ext": entry.ext,
                "media_type": entry.media_type,
                "note": entry.note,
                "requestable": entry.requested,
                "always": not entry.requested,
            }
            for entry in MATHPIX_FORMATS
        ],
        "mathpix_key_configured": bool(settings.mathpix_app_key),
        "dpi": settings.dpi,
        "max_pages": settings.max_pages,
        "max_upload_mb": settings.max_upload_mb,
        "mathpix_page_rate": settings.mathpix_page_rate,
        "remote_delete": settings.mathpix_delete,
        "improve_mathpix": settings.mathpix_improve,
        "history_limit": settings.history_limit,
    }


@app.get("/api/history")
def get_history(user: User = Depends(current_user)) -> dict:
    jobs = [job.as_dict() for job in _newest_first(user.id)]
    spent = sum(job["cost"] for job in jobs if job["cost_known"])
    return {"jobs": jobs, "total_cost": round(spent, 6), "count": len(jobs)}


@app.post("/api/convert")
async def convert(
    request: Request,
    background: BackgroundTasks,
    file: UploadFile = File(...),
    # Accepted and ignored, so a client written against the older API still
    # works. `layout` is the exception: `_mathpix_layout` refuses a legacy value
    # rather than silently converting something else.
    model: str = Form(default=""),
    layout: str = Form(default=""),
    columns: str = Form(default=""),
    start: bool = Form(default=False),
    user: User = Depends(current_user),
) -> dict:
    """Stage an uploaded PDF. Conversion waits for /start unless `start` is set."""
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Please upload a .pdf file.")
    selected_layout = _mathpix_layout(layout)
    _require_mathpix_credential()

    job_id = uuid.uuid4().hex[:12]
    directory = settings.jobs_dir / job_id
    directory.mkdir(parents=True, exist_ok=True)
    pdf_path = directory / "source.pdf"

    # Streamed with a running total rather than copied wholesale, so an
    # oversized upload is refused partway instead of after it has already filled
    # the volume the whole history lives on.
    limit = settings.max_upload_bytes
    size = 0
    try:
        with pdf_path.open("wb") as handle:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if limit and size > limit:
                    raise HTTPException(
                        status_code=413,
                        detail=f"That PDF is larger than the {settings.max_upload_mb} MB limit.",
                    )
                handle.write(chunk)
    except HTTPException:
        shutil.rmtree(directory, ignore_errors=True)
        raise

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
        user_id=user.id,
        filename=file.filename,
        pages=pages,
        layout=selected_layout,
        total=pages,
        size_bytes=size,
        directory=directory,
    )
    with JOBS_LOCK:
        JOBS[job_id] = job
    _persist(job)

    if start:
        return await start_job(
            request,
            job_id,
            background,
            model=model,
            layout=layout,
            columns=columns,
            user=user,
        )
    return job.as_dict()


@app.post("/api/jobs/{job_id}/start")
async def start_job(
    request: Request,
    job_id: str,
    background: BackgroundTasks,
    model: str = Form(default=""),
    layout: str = Form(default=""),
    columns: str = Form(default=""),
    formats: str | None = Form(default=None),
    user: User = Depends(current_user),
) -> dict:
    """Begin (or re-run) the conversion for an already-uploaded PDF."""
    # FastAPI treats an empty optional form string like an omitted value. Read
    # presence from the parsed form itself so ``formats=`` remains distinct
    # from a legacy client that sent no field at all.
    form = await request.form()
    if "formats" in form:
        raw_formats = str(form.get("formats") or "")
    else:
        raw_formats = formats

    job = _get_job(job_id, user)
    if job.status in RUNNING:
        raise HTTPException(status_code=409, detail="This conversion is already running.")

    selected_layout = _mathpix_layout(layout)
    selected_formats = _start_formats(raw_formats)
    _require_mathpix_credential()

    pdf_path = (job.directory or Path()) / "source.pdf"
    if not pdf_path.exists():
        raise HTTPException(
            status_code=409, detail="The uploaded PDF is no longer available — upload it again."
        )

    with JOBS_LOCK:
        job.layout = selected_layout
        job.requested_formats = selected_formats
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
    _persist(job)

    background.add_task(_run_job, job_id, pdf_path)
    return job.as_dict()


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str, user: User = Depends(current_user)) -> dict:
    return _get_job(job_id, user).as_dict()


@app.get("/api/jobs/{job_id}/markdown")
def job_markdown(job_id: str, user: User = Depends(current_user)) -> dict:
    job = _get_job(job_id, user)
    path = (job.directory or Path()) / "document.md"
    if not path.exists():
        raise HTTPException(status_code=409, detail="Markdown is not ready yet.")
    return {"markdown": path.read_text(encoding="utf-8")}


@app.get("/api/jobs/{job_id}/detection")
def job_detection(job_id: str, user: User = Depends(current_user)) -> dict:
    """What the converter saw, page by page: block boxes, kinds, reading order."""
    job = _get_job(job_id, user)
    path = (job.directory or Path()) / "detection.json"
    if not path.exists():
        raise HTTPException(status_code=409, detail="The page detection is not ready yet.")
    return detection.read(path)


@app.get("/api/jobs/{job_id}/page/{number}.png")
def job_page(job_id: str, number: int, user: User = Depends(current_user)) -> FileResponse:
    """One page of the source PDF, rasterised for the viewer.

    Rendered on demand and kept, rather than rendered for every job up front:
    only the flow mode rasterises whole pages during a conversion, and rendering
    every page of every job would fill the history directory for a view that may
    never be opened.
    """
    job = _get_job(job_id, user)
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


# Where a job's Markdown may point: the crops Mathpix returned, and the figures
# a pre-Mathpix job cut for itself. Nothing else in a job directory is meant to
# be fetched by the browser, and the finished documents have their own route.
ASSET_DIRS = (
    "figures",
    f"{MATHPIX_RAW_DIR}/{MATHPIX_RAW_IMAGE_DIR}",
)


@app.get("/api/jobs/{job_id}/asset/{asset:path}")
def job_asset(job_id: str, asset: str, user: User = Depends(current_user)) -> FileResponse:
    """An image referenced by a job's Markdown, for the rendered preview."""
    job = _get_job(job_id, user)
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


# What each `format` names, as a fixed path and media type. `rebuilt.docx` is
# only ever a pre-Mathpix job's comparison render — nothing produces one now —
# but it stays downloadable for the accounts that still have one.
DOWNLOADS = {
    "docx": (
        "document.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "docx",
    ),
    "md": ("document.md", "text/markdown", "md"),
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
def job_download(
    job_id: str, format: str = "docx", user: User = Depends(current_user)
) -> FileResponse:
    job = _get_job(job_id, user)
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
def job_delete(job_id: str, user: User = Depends(current_user)) -> dict:
    job = _get_job(job_id, user)
    if job.status in RUNNING:
        raise HTTPException(
            status_code=409, detail="This conversion is still running — wait for it to finish."
        )
    if job.directory:
        shutil.rmtree(job.directory, ignore_errors=True)
    with JOBS_LOCK:
        JOBS.pop(job_id, None)
    db.delete_job(job_id)
    return {"deleted": job_id}


@app.delete("/api/history")
def history_clear(user: User = Depends(current_user)) -> dict:
    """Delete every one of this account's jobs that is not currently running."""
    deleted = 0
    for job in _newest_first(user.id):
        if job.status in RUNNING:
            continue
        if job.directory:
            shutil.rmtree(job.directory, ignore_errors=True)
        with JOBS_LOCK:
            JOBS.pop(job.id, None)
        db.delete_job(job.id)
        deleted += 1
    return {"deleted": deleted}
