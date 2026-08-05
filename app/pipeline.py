"""End-to-end conversion: PDF -> page images -> Markdown -> .docx."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import fitz

from .config import settings
from .docx_builder import DocxWriter, render_markdown
from .docx_replica import ReplicaWriter
from .docx_structured import StructuredWriter, dominant_font
from .figures import place_figures, strip_boxes
from .latex_omml import is_math_latex
from .pdf_extract import PageLayout, Span, TextLine, extract_page
from .reflow import build as reflow_build
from .pdf_render import RenderedPage, render_pages
from .vision import PageTranscript, build_client, transcribe_math, transcribe_page

# Equation crops sent to the model in one request.
MATH_BATCH = 10

ProgressHook = Callable[[str, int, int], None]


@dataclass
class ConversionUsage:
    """What the vision model charged for a whole document."""

    cost: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0  # billed model calls made
    priced_calls: int = 0  # of those, the ones that came back with a price

    def add(self, result) -> None:
        self.cost += result.cost
        self.prompt_tokens += result.prompt_tokens
        self.completion_tokens += result.completion_tokens
        self.calls += 1
        self.priced_calls += 1 if result.priced else 0


@dataclass
class ConversionResult:
    markdown_path: Path
    docx_path: Path
    page_markdown: list[str] = field(default_factory=list)
    usage: ConversionUsage = field(default_factory=ConversionUsage)


UsageHook = Callable[[ConversionUsage], None]


def _noop(stage: str, done: int, total: int) -> None:  # pragma: no cover
    pass


def _noop_usage(usage: ConversionUsage) -> None:  # pragma: no cover
    pass


def convert_pdf(
    pdf_path: Path,
    work_dir: Path,
    title: str | None = None,
    model: str | None = None,
    on_progress: ProgressHook = _noop,
    on_usage: UsageHook = _noop_usage,
    layout: str | None = None,
) -> ConversionResult:
    """Convert `pdf_path` to .docx, either as a positioned replica or as flowing text."""
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
    return convert_pdf_flow(
        pdf_path=pdf_path,
        work_dir=work_dir,
        title=title,
        model=model,
        on_progress=on_progress,
        on_usage=on_usage,
    )


def _page_limit(doc: fitz.Document) -> int:
    limit = doc.page_count
    if settings.max_pages > 0:
        limit = min(limit, settings.max_pages)
    return limit


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
    """Read the PDF's own content into Word.

    Both modes served here share a front end — the same extraction, the same
    equation transcription — and differ only in what is written out: a positioned
    facsimile, or a flowing document that can be edited.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    usage = ConversionUsage()

    on_progress("rendering", 0, 0)
    layouts: list[PageLayout] = []
    with fitz.open(pdf_path) as doc:
        total = _page_limit(doc)
        if total == 0:
            raise ValueError("The PDF contains no pages.")
        detect_math = settings.math_mode != "off"
        # Positioned output can lean on the font names a LaTeX document provides;
        # a flowing one cannot, because it has to find every equation on a page —
        # anything missed is not merely left as an image, it is left as the loose
        # fragments the equation was set from.
        strategy = "geometry" if structured else "font"
        for index in range(total):
            layouts.append(
                extract_page(doc, index, detect_math=detect_math, math_strategy=strategy)
            )
            on_progress("rendering", index + 1, total)

    # Only two things need the model: equations on digital pages, and the words on
    # pages that carry no text layer at all.
    pages_dir = work_dir / "pages"
    jobs: list[tuple[str, PageLayout]] = []
    for layout in layouts:
        if layout.scanned:
            jobs.append(("scan", layout))
        elif layout.maths and detect_math:
            jobs.append(("math", layout))

    completed = len(layouts) - len(jobs)
    on_progress("transcribing", completed, total)

    if jobs:
        client = build_client()
        pages_dir.mkdir(parents=True, exist_ok=True)

        def run(kind: str, layout: PageLayout):
            if kind == "scan":
                path = pages_dir / f"page-{layout.number:04d}.png"
                path.write_bytes(layout.page_image or b"")
                transcript = transcribe_page(path, layout.number, total, client, model)
                if structured:
                    # The page is rebuilt from the transcription, so its figures
                    # have to be cut out of the scan — nothing else carries them.
                    layout.markdown = place_figures(
                        pdf_path, layout.number - 1, transcript.markdown, work_dir / "figures"
                    )
                else:
                    # The replica keeps the raster itself, figures and all. What
                    # the transcription adds there is a selectable, searchable
                    # layer of words underneath it.
                    layout.markdown = strip_boxes(transcript.markdown)
                    layout.lines = _scanned_lines(layout.markdown, layout.width, layout.height)
                return transcript

            results = []
            for start in range(0, len(layout.maths), MATH_BATCH):
                chunk = layout.maths[start : start + MATH_BATCH]
                result = transcribe_math([item.image for item in chunk], client, model)
                for item, latex in zip(chunk, result.latex):
                    item.latex = latex
                results.append(result)

            # Detection had to commit before anything was read. Now that the crops
            # have been transcribed, the ones that turned out to be prose go back
            # to the text path rather than becoming equation objects in Word.
            for math_id, item in enumerate(layout.maths):
                if item.latex and not is_math_latex(item.latex):
                    layout.release_math(math_id)
            return _merge(results)

        workers = max(1, min(settings.concurrency, len(jobs)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(run, kind, layout): layout for kind, layout in jobs}
            for future in as_completed(futures):
                usage.add(future.result())
                completed += 1
                on_progress("transcribing", completed, total)
                on_usage(usage)

    markdown = "\n\n".join(layout.content for layout in layouts if layout.content.strip())
    markdown_path = work_dir / "document.md"
    markdown_path.write_text(markdown, encoding="utf-8")

    on_progress("building", 0, total)
    docx_path = work_dir / "document.docx"
    if structured:
        pages, metrics = reflow_build(layouts)
        structured_writer = StructuredWriter(metrics, body_font=dominant_font(pages))
        for index, (page, layout) in enumerate(zip(pages, layouts)):
            if layout.scanned:
                # No text layer was extracted from this page, so there are no
                # blocks to lay out: its content is the Markdown that was read
                # off the raster, and it is rendered as such.
                structured_writer.add_markdown_page(layout.markdown)
            else:
                structured_writer.add_page(page, layout.maths)
            on_progress("building", index + 1, total)
        structured_writer.save(docx_path)
    else:
        writer = ReplicaWriter()
        for index, layout in enumerate(layouts):
            writer.add_page(layout)
            on_progress("building", index + 1, total)
        writer.save(docx_path)
    on_progress("done", total, total)

    return ConversionResult(
        markdown_path=markdown_path,
        docx_path=docx_path,
        page_markdown=[layout.content for layout in layouts],
        usage=usage,
    )


def _merge(results: list) -> PageTranscript:
    """Fold several billed calls into one object the usage accumulator accepts."""
    merged = PageTranscript(markdown="")
    for result in results:
        merged.cost += result.cost
        merged.prompt_tokens += result.prompt_tokens
        merged.completion_tokens += result.completion_tokens
    merged.priced = all(result.priced for result in results) if results else True
    return merged


def convert_pdf_flow(
    pdf_path: Path,
    work_dir: Path,
    title: str | None = None,
    model: str | None = None,
    on_progress: ProgressHook = _noop,
    on_usage: UsageHook = _noop_usage,
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

    def read(page: RenderedPage) -> PageTranscript:
        transcript = transcribe_page(page.path, page.number, total, client, model)
        # Every page here is retyped from its image, so a figure only survives if
        # it is cut out of the page and carried across as a picture.
        transcript.markdown = place_figures(
            pdf_path, page.number - 1, transcript.markdown, work_dir / "figures"
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
    writer = DocxWriter(title=title)
    for index, page_markdown in enumerate(transcripts):
        if not page_markdown.strip():
            continue
        if index > 0 and any(t.strip() for t in transcripts[:index]):
            writer.add_page_break()
        render_markdown(writer, page_markdown)
        on_progress("building", index + 1, total)

    docx_path = work_dir / "document.docx"
    writer.save(docx_path)
    on_progress("done", total, total)

    return ConversionResult(
        markdown_path=markdown_path,
        docx_path=docx_path,
        page_markdown=transcripts,
        usage=usage,
    )
