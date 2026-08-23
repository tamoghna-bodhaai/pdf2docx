"""End-to-end conversion: PDF -> page images -> Markdown -> .docx."""

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
from . import mathpix_client as mathpix
from .config import settings
from .docx_builder import DocxWriter, render_markdown
from .docx_replica import ReplicaWriter
from .docx_structured import StructuredWriter, dominant_font
from .columns import detect_columns
from .figures import place_figures, strip_boxes
from .locate import relocate_figures
from .latex_omml import is_math_latex
from .pdf_extract import PageLayout, Span, TextLine, extract_page, render_size
from .reflow import build as reflow_build
from .pdf_render import RenderedPage, render_pages
from .vision import PageTranscript, build_client, transcribe_math, transcribe_page

# Equation crops sent to the model in one request.
MATH_BATCH = 10

ProgressHook = Callable[[str, int, int], None]


@dataclass
class ConversionUsage:
    """What the remote vision model charged for a whole document."""

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
def _add_locate_usage(transcript: PageTranscript, located_cost: float) -> None:
    """Fold the optional locator request into one page's remote usage."""
    transcript.cost += float(located_cost)
    if not getattr(located_cost, "called", False):
        return
    transcript.prompt_tokens += int(getattr(located_cost, "prompt_tokens", 0))
    transcript.completion_tokens += int(getattr(located_cost, "completion_tokens", 0))
    transcript.calls += 1
    transcript.priced_calls = int(transcript.priced) + int(
        getattr(located_cost, "priced", False)
    )




def convert_pdf(
    pdf_path: Path,
    work_dir: Path,
    title: str | None = None,
    model: str | None = None,
    on_progress: ProgressHook = _noop,
    on_usage: UsageHook = _noop_usage,
    layout: str | None = None,
    columns: str | None = None,
    mathpix_formats: tuple[str, ...] | None = None,
) -> ConversionResult:
    """Convert `pdf_path` to .docx, either as a positioned replica or as flowing text.

    `columns` is only meaningful to the flowing modes — the replica modes put
    every block back at the coordinate it came from, so the source's columns are
    already there and there is nothing left to decide.
    """
    mode = (layout or settings.layout or "structured").lower()
    if mode in ("replica", "structured"):
        return convert_pdf_replica(
            pdf_path=pdf_path,
            work_dir=work_dir,
            model=model,
            on_progress=on_progress,
            on_usage=on_usage,
            structured=mode == "structured",
        )
    if mode == "mathpix":
        return convert_pdf_mathpix(
            pdf_path=pdf_path,
            work_dir=work_dir,
            on_progress=on_progress,
            on_usage=on_usage,
            formats=mathpix_formats,
        )
    return convert_pdf_flow(
        pdf_path=pdf_path,
        work_dir=work_dir,
        title=title,
        model=model,
        on_progress=on_progress,
        on_usage=on_usage,
        columns=columns,
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


def _plain(markdown: str) -> str:
    """Strip Markdown mark-up back to the words it was wrapped around.

    For the searchable layer under a scanned page, where the mark-up characters
    would be read out by anyone who copied the text. Mathematics is left as its
    LaTeX: nothing else states it in one line of plain text.
    """
    text = strip_boxes(markdown)
    text = re.sub(r"^\s{0,3}#{1,6}\s+", "", text, flags=re.M)  # heading marks
    text = re.sub(r"^\s{0,3}>\s?", "", text, flags=re.M)  # quote marks
    text = re.sub(r"^\s*(?:[-*_]\s*){3,}$", "", text, flags=re.M)  # rules
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)  # images
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # links
    text = re.sub(r"(\*\*|__|~~|`+)", "", text)  # emphasis and code
    return re.sub(r"(?<!\w)[*_](?=\S)|(?<=\S)[*_](?!\w)", "", text)


def _scanned_lines(markdown: str, width: float, height: float) -> list[TextLine]:
    """Wrap transcribed text as flat lines for the layer behind a scanned page."""
    lines: list[TextLine] = []
    for raw in _plain(markdown).splitlines():
        text = raw.strip()
        if not text:
            continue
        lines.append(
            TextLine(
                bbox=(0.0, 0.0, width, height),
                spans=[
                    Span(
                        text=text,
                        font="Times New Roman",
                        size=10.0,
                        colour="000000",
                        bold=False,
                        italic=False,
                        superscript=False,
                        math=False,
                        bbox=(0.0, 0.0, width, height),
                    )
                ],
            )
        )
    return lines


def convert_pdf_replica(
    pdf_path: Path,
    work_dir: Path,
    model: str | None = None,
    on_progress: ProgressHook = _noop,
    on_usage: UsageHook = _noop_usage,
    structured: bool = False,
) -> ConversionResult:
    """Read the PDF's own content, reading a page with the model only where needed.

    PyMuPDF supplies everything a digital page holds. The model is asked only
    about what the PDF itself cannot answer: a scanned page, which has no text
    to read, and an equation, which is drawn rather than written.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    usage = ConversionUsage()
    diagnostics: list[ExtractionDiagnostic] = []

    on_progress("rendering", 0, 0)
    layouts: list[PageLayout] = []
    with fitz.open(pdf_path) as doc:
        total = _page_limit(doc)
        if total == 0:
            raise ValueError("The PDF contains no pages.")
        detect_math = settings.math_mode != "off"
        strategy = "geometry" if structured else "font"
        for index in range(total):
            layouts.append(extract_page(doc, index, detect_math=detect_math, math_strategy=strategy))
            on_progress("rendering", index + 1, total)

    pages_dir = work_dir / "pages"
    jobs: list[tuple[str, PageLayout]] = []
    for page_layout in layouts:
        if page_layout.scanned:
            jobs.append(("scan", page_layout))
        elif page_layout.maths and detect_math:
            jobs.append(("math", page_layout))

    completed = len(layouts) - len(jobs)
    on_progress("transcribing", completed, total)
    # Built once, before the pool starts, and only when there is something to
    # ask about: a document of digital pages needs no key and makes no call.
    client = build_client() if jobs else None

    def read_scan(page_layout: PageLayout):
        pages_dir.mkdir(parents=True, exist_ok=True)
        path = pages_dir / f"page-{page_layout.number:04d}.png"
        path.write_bytes(page_layout.page_image or b"")
        transcript = transcribe_page(path, page_layout.number, total, client, model)
        if structured:
            page_layout.markdown, located_cost = relocate_figures(
                pdf_path, page_layout.number - 1, transcript.markdown, client, model
            )
            _add_locate_usage(transcript, located_cost)
            page_layout.markdown = place_figures(
                pdf_path,
                page_layout.number - 1,
                page_layout.markdown,
                work_dir,
                render=render_size(page_layout.width, page_layout.height, settings.dpi),
            )
        else:
            page_layout.markdown = strip_boxes(transcript.markdown)
            page_layout.lines = _scanned_lines(
                page_layout.markdown, page_layout.width, page_layout.height
            )
        return [transcript], ExtractionDiagnostic(page_layout.number, "scan", "vision")

    def read_math(page_layout: PageLayout):
        """Read a page's equation crops back as LaTeX, a batch to a request."""
        remote_results: list[PageTranscript] = []
        for start in range(0, len(page_layout.maths), MATH_BATCH):
            chunk = page_layout.maths[start : start + MATH_BATCH]
            result = transcribe_math([item.image for item in chunk], client, model)
            for offset, latex in enumerate(result.latex):
                page_layout.maths[start + offset].latex = latex
            remote_results.append(result)

        # An equation that did not come back as mathematics is left as the image
        # it was cut from, which is at least exactly what the page showed.
        for math_id, item in enumerate(page_layout.maths):
            if item.latex and not is_math_latex(item.latex):
                page_layout.release_math(math_id)

        return remote_results, ExtractionDiagnostic(page_layout.number, "formula", "vision")

    def run(kind: str, page_layout: PageLayout):
        return read_scan(page_layout) if kind == "scan" else read_math(page_layout)

    if jobs:
        workers = max(1, min(settings.concurrency, len(jobs)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(run, kind, page_layout): page_layout for kind, page_layout in jobs}
            for future in as_completed(futures):
                remote_results, diagnostic = future.result()
                for result in remote_results:
                    usage.add(result)
                diagnostics.append(diagnostic)
                completed += 1
                on_progress("transcribing", completed, total)
                on_usage(usage)

    markdown = "\n\n".join(page_layout.content for page_layout in layouts if page_layout.content.strip())
    markdown_path = work_dir / "document.md"
    markdown_path.write_text(markdown, encoding="utf-8")

    on_progress("building", 0, total)
    docx_path = work_dir / "document.docx"
    if structured:
        pages, metrics = reflow_build(layouts)
        structured_writer = StructuredWriter(
            metrics, body_font=dominant_font(pages), base_dir=work_dir
        )
        for index, (page, page_layout) in enumerate(zip(pages, layouts)):
            if page_layout.scanned:
                structured_writer.add_markdown_page(page_layout.markdown)
            else:
                structured_writer.add_page(page, page_layout.maths)
            on_progress("building", index + 1, total)
        structured_writer.save(docx_path)
    else:
        writer = ReplicaWriter()
        for index, page_layout in enumerate(layouts):
            writer.add_page(page_layout)
            on_progress("building", index + 1, total)
        writer.save(docx_path)

    # What the page viewer draws: for `structured`, the very blocks the document
    # was written from; for `replica`, the items it positioned. Neither is
    # recomputed, so the overlay can only ever show what the .docx was made of.
    detection.write(
        detection.from_blocks(pages, layouts) if structured else detection.from_layouts(layouts),
        work_dir / "detection.json",
        "structured" if structured else "replica",
    )
    on_progress("done", total, total)

    return ConversionResult(
        markdown_path=markdown_path,
        docx_path=docx_path,
        page_markdown=[page_layout.content for page_layout in layouts],
        usage=usage,
        diagnostics=sorted(diagnostics, key=lambda item: (item.page, item.kind)),
    )


def _merge(results: list) -> PageTranscript:
    """Fold several billed calls into one object the usage accumulator accepts."""
def _merge(results: list) -> PageTranscript:
    """Fold several billed calls into one object the usage accumulator accepts."""
    merged = PageTranscript(markdown="", calls=0, priced_calls=0)
    for result in results:
        merged.cost += result.cost
        merged.prompt_tokens += result.prompt_tokens
        merged.completion_tokens += result.completion_tokens
        merged.calls += int(getattr(result, "calls", 1))
        priced_calls = getattr(result, "priced_calls", None)
        merged.priced_calls += int(result.priced) if priced_calls is None else int(priced_calls)
    merged.priced = merged.priced_calls >= merged.calls
    return merged
def convert_pdf_flow(
    pdf_path: Path,
    work_dir: Path,
    title: str | None = None,
    model: str | None = None,
    on_progress: ProgressHook = _noop,
    on_usage: UsageHook = _noop_usage,
    columns: str | None = None,
) -> ConversionResult:
    work_dir.mkdir(parents=True, exist_ok=True)

    on_progress("rendering", 0, 0)
    pages: list[RenderedPage] = render_pages(pdf_path, work_dir / "pages")
    total = len(pages)
    if total == 0:
        raise ValueError("The PDF contains no pages.")
    on_progress("rendering", total, total)

    client = build_client()
    transcripts: list[str] = [""] * total
    usage = ConversionUsage()
    completed = 0
    on_progress("transcribing", 0, total)

    def transcript_of(page: RenderedPage) -> PageTranscript:
        """Transcribe the page, then ask again where its figures really are."""
        transcript = transcribe_page(page.path, page.number, total, client, model)
        transcript.markdown, located_cost = relocate_figures(
            pdf_path, page.number - 1, transcript.markdown, client, model
        )
        _add_locate_usage(transcript, located_cost)
        return transcript

    def read(page: RenderedPage) -> PageTranscript:
        transcript = transcript_of(page)
        # Every page here is retyped from its image, so a figure only survives if
        # it is cut out of the page and carried across as a picture.
        transcript.markdown = place_figures(
            pdf_path,
            page.number - 1,
            transcript.markdown,
            work_dir,
            render=(page.width, page.height),
        )
        return transcript

    workers = max(1, min(settings.concurrency, total))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(read, page): page for page in pages}
        # Results are collected here on one thread, so the running total needs no lock.
        for future in as_completed(futures):
            page = futures[future]
            transcript = future.result()
            transcripts[page.number - 1] = transcript.markdown
            usage.add(transcript)
            completed += 1
            on_progress("transcribing", completed, total)
            on_usage(usage)

    markdown = "\n\n".join(text.strip() for text in transcripts if text.strip())
    markdown_path = work_dir / "document.md"
    markdown_path.write_text(markdown, encoding="utf-8")

    on_progress("building", 0, total)
    layout = _column_layout(pdf_path, len(transcripts), columns)
    writer = DocxWriter(title=title, base_dir=work_dir)
    for index, page_markdown in enumerate(transcripts):
        if not page_markdown.strip():
            continue
        writer.start_page(layout[index])
        render_markdown(writer, page_markdown)
        on_progress("building", index + 1, total)

    docx_path = work_dir / "document.docx"
    writer.save(docx_path)

    # This mode retypes each page from its image and reports no geometry, so the
    # viewer gets the pages and their text and draws no boxes over them.
    detection.write(
        detection.from_markdown(_page_sizes(pdf_path, total), transcripts),
        work_dir / "detection.json",
        "flow",
    )
    on_progress("done", total, total)

    return ConversionResult(
        markdown_path=markdown_path,
        docx_path=docx_path,
        page_markdown=transcripts,
        usage=usage,
    )


# What "one column, whatever the source did" can be called. `natural` is the
# name the browser uses for it: a transcription is one linear stream of text, so
# one flowing column is what the document is naturally in, and asking for the
# source's columns back is the deliberate act. The rest are the older spellings
# `PDF2DOCX_COLUMNS` has always accepted, kept so an existing .env still means
# what it meant.
SINGLE_COLUMN = ("natural", "off", "0", "1", "none", "single")
# ...and what "set each page the way the source page was set" can be called.
# Anything unrecognised means this too, so a typo reads the source rather than
# silently flattening it.
FOLLOW_SOURCE = ("auto", "multi", "multi-column", "multicolumn")


def _column_layout(pdf_path: Path, pages: int, choice: str | None = None) -> list[int]:
    """How many columns each of the `pages` written pages should be set in.

    `choice` is the caller's answer — one conversion's setting, chosen in the
    browser — and `PDF2DOCX_COLUMNS` is what it falls back to. Either may say
    `natural` for one flowing column or `multi` for the source's own columns; a
    bare number forces that many.

    Asking for the source's columns is not the same as getting them: a source
    set in one column is read as one column and written as one, so `multi` on a
    single-column PDF is the same document `natural` would have produced. That
    is why nothing here needs to guard against it.

    Reading the source is best-effort. A layout that cannot be read is not worth
    failing a conversion over — the document still says everything it said
    before, in one column — so anything that goes wrong here comes back as one
    column per page.

    A count that does not line up with the pages actually written is not used
    page by page, because it cannot be trusted to line up page by page either:
    marker returns one long page when pagination is off, and a transcriber may
    drop an empty one. What the source was *mostly* set in is still true of the
    document as a whole, so that is what the whole document gets.
    """
    setting = (choice or settings.columns or "auto").strip().lower()
    if setting in SINGLE_COLUMN:
        return [1] * pages
    if setting.isdigit():
        return [int(setting)] * pages

    try:
        counts = detect_columns(pdf_path, limit=settings.max_pages)
    except Exception:
        return [1] * pages
    if not counts:
        return [1] * pages
    if len(counts) != pages:
        common = max(set(counts), key=counts.count)
        return [common] * pages
    return counts


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

    # Mathpix's own .docx, byte for byte, when this job requested one. The
    # page-aligned MMD preview is the required local result.
    docx_path: Path | None = None
    if "docx" in fetched:
        docx_path = work_dir / "document.docx"
        docx_path.write_bytes(fetched["docx"])

    (work_dir / mathpix.RAW_DIR / "metadata.json").write_text(
        json.dumps(
            {
                "file_id": file_id,
                "num_pages": status.num_pages or total,
                "options": options,
                "applied": applied.as_dict(),
                "document_docx": "mathpix, unedited" if docx_path else None,
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


def convert_pdf_mathpix(
    pdf_path: Path,
    work_dir: Path,
    on_progress: ProgressHook = _noop,
    on_usage: UsageHook = _noop_usage,
    formats: tuple[str, ...] | None = None,
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
        )
    finally:
        if settings.mathpix_delete:
            client.delete(file_id)
