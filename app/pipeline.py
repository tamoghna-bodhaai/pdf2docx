"""End-to-end conversion: hand the PDF to Mathpix and write back what it returns."""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

import fitz

from . import detection
from . import docx_fit
from . import mathpix_client as mathpix
from .config import settings

ProgressHook = Callable[[str, int, int], None]


@dataclass
class ConversionUsage:
    """What converting a whole document cost."""

    cost: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0
    priced_calls: int = 0

    def add(self, result) -> None:
        self.cost += result.cost
        self.prompt_tokens += result.prompt_tokens
        self.completion_tokens += result.completion_tokens
        calls = int(getattr(result, "calls", 1))
        priced_calls = getattr(result, "priced_calls", None)
        self.calls += calls
        self.priced_calls += int(result.priced) if priced_calls is None else int(priced_calls)


@dataclass(frozen=True)
class ExtractionDiagnostic:
    page: int
    kind: str
    extractor: str
    fallback_reason: str | None = None


@dataclass
class ConversionResult:
    markdown_path: Path
    docx_path: Path | None
    page_markdown: list[str] = field(default_factory=list)
    usage: ConversionUsage = field(default_factory=ConversionUsage)
    diagnostics: list[ExtractionDiagnostic] = field(default_factory=list)


UsageHook = Callable[[ConversionUsage], None]


def _noop(stage: str, done: int, total: int) -> None:  # pragma: no cover
    pass


def _noop_usage(usage: ConversionUsage) -> None:  # pragma: no cover
    pass


def convert_pdf(
    pdf_path: Path,
    work_dir: Path,
    on_progress: ProgressHook = _noop,
    on_usage: UsageHook = _noop_usage,
    mathpix_formats: tuple[str, ...] | None = None,
    multi_column: bool = False,
) -> ConversionResult:
    """Convert `pdf_path`, writing the results into `work_dir`.

    One backend, kept behind this seam rather than called directly: a caller
    should not have to know which service converted the document, and the
    application has already changed backends once.
    """
    return convert_pdf_mathpix(
        pdf_path=pdf_path,
        work_dir=work_dir,
        on_progress=on_progress,
        on_usage=on_usage,
        formats=mathpix_formats,
        multi_column=multi_column,
    )


def _page_limit(doc: fitz.Document) -> int:
    limit = doc.page_count
    if settings.max_pages > 0:
        limit = min(limit, settings.max_pages)
    return limit


def _page_sizes(pdf_path: Path, pages: int) -> list[tuple[float, float]]:
    """Each page's size in PDF points — the frame the viewer draws boxes in."""
    with fitz.open(pdf_path) as doc:
        limit = min(pages, doc.page_count)
        return [(doc.load_page(index).rect.width, doc.load_page(index).rect.height)
                for index in range(limit)]


def _page_total(pdf_path: Path) -> int:
    with fitz.open(pdf_path) as doc:
        total = _page_limit(doc)
    if total == 0:
        raise ValueError("The PDF contains no pages.")
    return total


def _mathpix_config(formats: tuple[str, ...] | None = None) -> dict:
    """What Mathpix is asked to do, and which parts of it are not negotiable.

    `settings.mathpix_options` is passed through untouched and wins every
    collision — it is Mathpix's own option vocabulary, not this application's,
    and an option this codebase has never heard of has to reach Mathpix intact
    for the mode to be worth having. The defaults below are only the ones this
    application needs in order to read the result at all:

      * the maths delimiters, because Mathpix defaults to `\\(…\\)` while every
        other mode here produces and every reader here expects `$…$`. Setting
        them at request time means Mathpix emits what this codebase already
        reads, rather than being translated afterwards by a module that would
        then be standing between Mathpix and the page;
      * page breaks, because every other mode gives each source page its own
        Word page and there is otherwise nothing to split on;
      * `improve_mathpix`, which is off unless asked for. The document is
        someone else's, and the default should not give it away.

    `conversion_formats` is owned by the per-job selection. Provider options
    cannot add an export the user did not choose or take one away.
    """
    options = dict(settings.mathpix_options)
    options.setdefault("math_inline_delimiters", ["$", "$"])
    options.setdefault("math_display_delimiters", ["$$", "$$"])
    options.setdefault("include_page_breaks", True)

    selected = (
        mathpix.requested_formats(settings.mathpix_formats)
        if formats is None
        else mathpix.requestable_formats(formats)
    )
    options["conversion_formats"] = mathpix.conversion_formats(selected)

    # Mathpix counts pages from one, unlike marker's zero-based `page_range`.
    if settings.max_pages > 0 and not options.get("page_ranges"):
        options["page_ranges"] = f"1-{settings.max_pages}"

    metadata = dict(options.get("metadata") or {})
    metadata.setdefault("improve_mathpix", settings.mathpix_improve)
    options["metadata"] = metadata
    return options


def _read_lines(data: bytes | None) -> dict | None:
    """Mathpix's `lines.json`, when it is readable, for the geometry it carries.

    The export stays an untouched raw download; this only reads it. It is the one
    place the true size of every figure is recorded — each page's rendered pixel
    width against the same page's width in points is the resolution Mathpix
    cropped at — so a document that arrives without it is fitted by capping
    oversized images rather than by restoring them.
    """
    if not data:
        return None
    try:
        parsed = json.loads(data.decode("utf-8", "replace"))
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _mathpix_detection(pdf_path: Path, pages: list[str]) -> list:
    """Build page-aligned preview data without exposing Mathpix line boxes.

    ``lines.json`` remains an untouched raw download. The viewer needs only the
    source page dimensions and that page's Markdown, so every page intentionally
    carries an empty ``blocks`` list.
    """
    return detection.from_markdown(_page_sizes(pdf_path, len(pages)), pages)


def _align_mathpix_pages(pages: list[str], total: int) -> list[str]:
    """Keep preview navigation at exactly the source PDF's page count.

    Mathpix is asked for page separators, but missing or surplus separators must
    not shift the source-page viewer out of step. Missing pages are represented
    explicitly as empty Markdown. Surplus segments are retained on the final
    source page, because dropping Mathpix output would be worse than grouping an
    ambiguous trailing segment with the last page.
    """
    if total <= 0:
        return pages
    aligned = list(pages[:total])
    if len(pages) > total:
        aligned[-1] = "\n\n".join(pages[total - 1:])
    elif len(aligned) < total:
        aligned.extend([""] * (total - len(aligned)))
    return aligned


def _remove_stale_mathpix_exports(work_dir: Path, produced: set[str]) -> None:
    """After success, remove prior-run formats Mathpix did not produce this time."""
    raw = work_dir / mathpix.RAW_DIR
    for entry in mathpix.FORMATS:
        if entry.ext not in produced:
            (raw / f"document.{entry.ext}").unlink(missing_ok=True)


def _collect_mathpix_result(
    *,
    client: mathpix.MathpixClient,
    file_id: str,
    pdf_path: Path,
    work_dir: Path,
    total: int,
    options: dict,
    requested: tuple[str, ...],
    on_progress: ProgressHook,
    on_usage: UsageHook,
    multi_column: bool = False,
) -> ConversionResult:
    """Collect and store one submitted Mathpix job while its cleanup is guarded."""
    deadline = time.monotonic() + settings.mathpix_poll_timeout

    def report(state: mathpix.MathpixStatus) -> None:
        # Mathpix counts finished pages; fall back to its percentage when a
        # response carries one but not the other.
        done = state.num_pages_completed or int(round(state.percent_done / 100.0 * total))
        on_progress("transcribing", min(max(done, 0), total), total)

    status = client.poll(file_id, report, deadline)
    on_progress("transcribing", total, total)

    # Everything asked for, plus the formats Mathpix produces without being asked.
    wanted = list(requested) + list(mathpix.ALWAYS)
    fetched: dict[str, bytes] = {}

    def keep(ext: str, data: bytes) -> None:
        mathpix.write_raw(ext, data, work_dir)
        fetched[ext] = data
        on_progress("building", len(fetched), len(wanted))

    on_progress("building", 0, len(wanted))
    missing = client.fetch_all(file_id, wanted, keep, deadline)

    mmd = fetched[mathpix.PREVIEW_REQUIRED].decode("utf-8", "replace")
    markdown, applied = client.download_images(mmd, work_dir)
    segments = mathpix.split_pages(markdown)
    pages = _align_mathpix_pages(segments, total)
    applied = replace(applied, pages=len(pages), paginated=len(segments) > 1)

    # A page Mathpix read as nothing still converts, and the job would otherwise
    # report success over a blank document. Recorded per page, because a backend
    # that has degraded usually empties some pages rather than all of them.
    empty = {number for number, page in enumerate(pages, start=1) if mathpix.is_empty(page)}

    markdown_path = work_dir / "document.md"
    markdown_path.write_text(markdown, encoding="utf-8")

    # `mathpix/document.docx` is already Mathpix's file byte for byte; this is
    # the copy people download, fitted to the measure it is laid out in. Mathpix
    # states its geometry absolutely — images at their crop resolution over 96
    # DPI rather than at the size the figure occupied, tables pinned to a grid
    # summing to exactly the content width — and that only reads correctly at
    # the one measure it assumed. The two files stay side by side so a defect can
    # still be attributed to whichever of them introduced it.
    docx_path: Path | None = None
    fit = docx_fit.Fit(reason="no docx requested")
    if "docx" in fetched:
        docx_path = work_dir / "document.docx"
        document = fetched["docx"]
        if settings.fit_docx:
            document, fit = docx_fit.fit_docx(
                document,
                page_sizes=_page_sizes(pdf_path, total),
                lines=_read_lines(fetched.get("lines.json")),
                max_image_fraction=settings.fit_max_image_fraction,
                wrap_indent_twips=settings.fit_wrap_indent,
                multi_column=multi_column,
            )
        else:
            fit = docx_fit.Fit(reason="fitting disabled")
        docx_path.write_bytes(document)

    (work_dir / mathpix.RAW_DIR / "metadata.json").write_text(
        json.dumps(
            {
                "file_id": file_id,
                "num_pages": status.num_pages or total,
                "options": options,
                "applied": applied.as_dict(),
                "document_docx": (
                    ("mathpix, fitted to measure" if fit.applied else "mathpix, unedited")
                    if docx_path
                    else None
                ),
                "fit": fit.as_dict(),
                "requested_formats": list(requested),
                "formats": sorted(fetched),
                # An absent format is usually a fact about the document rather
                # than a failure — a document with no tables has no .xlsx.
                "formats_missing": missing,
                "page_segments": len(segments),
                "empty_pages": sorted(empty),
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    detection.write(
        _mathpix_detection(pdf_path, pages), work_dir / "detection.json", "mathpix"
    )

    # Do this only after every required local result is safely written. A failed
    # rerun leaves the previous successful downloads intact; a successful rerun
    # cannot advertise a document-specific format left behind by its predecessor.
    _remove_stale_mathpix_exports(work_dir, set(fetched))

    on_progress("done", len(pages), len(pages))

    # Mathpix bills per page rather than per token, so this is an estimate from
    # the page count. `calls` without `priced_calls` is how this codebase says
    # "a real charge, but not one the provider reported" — the UI reads it as
    # unpriced and the history total leaves it out.
    billed = status.num_pages or total
    usage = ConversionUsage(cost=billed * settings.mathpix_page_rate, calls=1)
    on_usage(usage)
    return ConversionResult(
        markdown_path=markdown_path,
        docx_path=docx_path,
        page_markdown=pages,
        usage=usage,
        diagnostics=[
            ExtractionDiagnostic(
                number,
                "document",
                "mathpix",
                "empty_output" if number in empty else None,
            )
            for number in range(1, len(pages) + 1)
        ],
    )


def _write_fit_record(work_dir: Path, fit: docx_fit.Fit) -> None:
    """Keep `mathpix/metadata.json` telling the truth about the delivered file."""
    path = work_dir / mathpix.RAW_DIR / "metadata.json"
    record: dict = {}
    if path.exists():
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            record = parsed
    record["fit"] = fit.as_dict()
    record["document_docx"] = (
        "mathpix, fitted to measure" if fit.applied else "mathpix, unedited"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")


def refit_docx(work_dir: Path, *, multi_column: bool = False) -> docx_fit.Fit:
    """Rebuild `document.docx` from what the job already has on disk.

    Re-running a conversion re-submits the PDF and is billed again in full;
    nothing of the previous run is reused. But every input the fitting needs
    outlives that run — Mathpix's own .docx, its `lines.json` and the source
    PDF's page sizes are all still in the job directory — so changing how the
    document is laid out costs nothing and reaches no network.

    Mathpix's export is read and never written. The only files this touches are
    the delivered `document.docx` and the `fit` block of the job's metadata.

    Raises ``FileNotFoundError`` when the export it rebuilds from is gone, and
    ``ValueError`` when columns were asked for and the source geometry could not
    be read — because delivering a single-column document to someone who asked
    for two is a failure reported as a success.
    """
    source = work_dir / mathpix.RAW_DIR / "document.docx"
    if not source.exists():
        raise FileNotFoundError(
            "This job no longer has Mathpix's own .docx to rebuild from."
        )

    pdf_path = work_dir / "source.pdf"
    page_sizes: list[tuple[float, float]] = []
    if pdf_path.exists():
        page_sizes = _page_sizes(pdf_path, _page_total(pdf_path))

    raw_lines = work_dir / mathpix.RAW_DIR / "document.lines.json"
    lines = _read_lines(raw_lines.read_bytes() if raw_lines.exists() else None)

    document, fit = docx_fit.fit_docx(
        source.read_bytes(),
        page_sizes=page_sizes,
        lines=lines,
        max_image_fraction=settings.fit_max_image_fraction,
        wrap_indent_twips=settings.fit_wrap_indent,
        multi_column=multi_column,
    )
    if multi_column and fit.columns < 2:
        raise ValueError(
            "The source page's column layout could not be read from this job's "
            "stored geometry, so the document was left single-column."
        )

    (work_dir / "document.docx").write_bytes(document)
    _write_fit_record(work_dir, fit)
    return fit


def convert_pdf_mathpix(
    pdf_path: Path,
    work_dir: Path,
    on_progress: ProgressHook = _noop,
    on_usage: UsageHook = _noop_usage,
    formats: tuple[str, ...] | None = None,
    multi_column: bool = False,
) -> ConversionResult:
    """Hand the whole PDF to the Mathpix Files API and write back what it returns.

    This mode exists to show Mathpix's own work. Everything the other modes do to
    a page — locating figures, repairing boxes, recovering structure from
    coordinates — is deliberately absent, and every format Mathpix returns is
    written to `mathpix/` verbatim before anything reads it. If selected and
    produced, `document.docx` is Mathpix's own file, copied byte for byte and not
    built here. The locally rewritten Markdown exists only for the page-aligned
    browser preview.

    Unlike every other backend here this one is a paid remote service, and the
    document leaves the machine. The upload is deleted in ``finally`` once it
    has a remote id, including when polling, downloads, or local writes fail.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    total = _page_total(pdf_path)
    requested = (
        mathpix.requested_formats(settings.mathpix_formats)
        if formats is None
        else mathpix.requestable_formats(formats)
    )
    options = _mathpix_config(requested)
    client = mathpix.MathpixClient()

    on_progress("rendering", total, total)
    on_progress("transcribing", 0, total)
    file_id = client.submit(pdf_path, options)
    try:
        return _collect_mathpix_result(
            client=client,
            file_id=file_id,
            pdf_path=pdf_path,
            work_dir=work_dir,
            total=total,
            options=options,
            requested=requested,
            on_progress=on_progress,
            on_usage=on_usage,
            multi_column=multi_column,
        )
    finally:
        if settings.mathpix_delete:
            client.delete(file_id)
