"""Validated client and layout adapter for the PDF-Extract-Kit sidecar.

The sidecar deals exclusively in rendered-image pixels. This module is the
boundary that validates its untrusted HTTP responses and converts those pixels
to the application's Markdown/PDF-point contracts.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

import httpx

from .config import settings
from .latex_omml import is_math_latex, looks_incomplete
from .pdf_extract import Span, TextLine
from .xml_text import xml_safe

Box = tuple[float, float, float, float]
_TEXT_TYPES = {"text", "paragraph", "title", "heading", "list", "caption", "table"}
_FORMULA_TYPES = {"formula", "equation", "display_formula", "inline_formula"}
_FIGURE_TYPES = {"figure", "image", "chart", "diagram"}
_ALLOWED_TYPES = _TEXT_TYPES | _FORMULA_TYPES | _FIGURE_TYPES


class ExtractKitError(RuntimeError):
    """The local extractor is unavailable or returned an invalid result."""


@dataclass(frozen=True)
class ExtractedBlock:
    type: str
    bbox: Box
    text: str = ""
    latex: str = ""
    confidence: float = 1.0
    level: int = 0


@dataclass(frozen=True)
class ExtractedPage:
    width: int
    height: int
    blocks: tuple[ExtractedBlock, ...]


@dataclass(frozen=True)
class OrderedPage:
    blocks: tuple[ExtractedBlock, ...]
    ambiguous: bool = False
    reason: str | None = None


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ExtractKitError(f"{name} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ExtractKitError(f"{name} must be numeric") from exc
    if not math.isfinite(result):
        raise ExtractKitError(f"{name} must be finite")
    return result


def _parse_bbox(raw: Any, width: int, height: int, index: int) -> Box:
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        raise ExtractKitError(f"blocks[{index}].bbox must contain four numbers")
    x0, y0, x1, y1 = (_number(value, f"blocks[{index}].bbox") for value in raw)
    if x0 < 0 or y0 < 0 or x1 <= x0 or y1 <= y0:
        raise ExtractKitError(f"blocks[{index}].bbox is invalid")
    if x1 > width or y1 > height:
        raise ExtractKitError(f"blocks[{index}].bbox lies outside the page")
    return (max(0.0, x0), max(0.0, y0), min(float(width), x1), min(float(height), y1))


def parse_page_response(payload: Any) -> ExtractedPage:
    """Validate the JSON response from ``POST /extract-page``."""
    if not isinstance(payload, dict):
        raise ExtractKitError("extract-page response must be an object")
    width = int(_number(payload.get("width"), "width"))
    height = int(_number(payload.get("height"), "height"))
    if width <= 0 or height <= 0:
        raise ExtractKitError("page dimensions must be positive")
    raw_blocks = payload.get("blocks")
    if not isinstance(raw_blocks, list):
        raise ExtractKitError("blocks must be an array")

    blocks: list[ExtractedBlock] = []
    for index, raw in enumerate(raw_blocks):
        if not isinstance(raw, dict):
            raise ExtractKitError(f"blocks[{index}] must be an object")
        kind = str(raw.get("type") or "").strip().lower()
        if kind not in _ALLOWED_TYPES:
            raise ExtractKitError(f"blocks[{index}].type is unsupported: {kind!r}")
        bbox = _parse_bbox(raw.get("bbox"), width, height, index)
        text = raw.get("text") or ""
        latex = raw.get("latex") or ""
        if not isinstance(text, str) or not isinstance(latex, str):
            raise ExtractKitError(f"blocks[{index}] text/latex must be strings")
        confidence = _number(raw.get("confidence", 1.0), f"blocks[{index}].confidence")
        if not 0.0 <= confidence <= 1.0:
            raise ExtractKitError(f"blocks[{index}].confidence must be between 0 and 1")
        try:
            level = int(raw.get("level") or 0)
        except (TypeError, ValueError) as exc:
            raise ExtractKitError(f"blocks[{index}].level must be an integer") from exc
        blocks.append(
            ExtractedBlock(
                type=kind,
                bbox=bbox,
                text=xml_safe(text).strip(),
                latex=xml_safe(latex).strip(),
                confidence=confidence,
                level=max(0, min(level, 6)),
            )
        )
    return ExtractedPage(width=width, height=height, blocks=tuple(blocks))


def parse_formula_response(payload: Any, expected: int) -> list[str | None]:
    """Validate ``POST /recognize-formulas`` without trusting batch cardinality."""
    values = payload.get("latex") if isinstance(payload, dict) else payload
    if not isinstance(values, list) or len(values) != expected:
        raise ExtractKitError(f"formula response must contain exactly {expected} results")
    parsed: list[str | None] = []
    for index, value in enumerate(values):
        if value is None or value == "":
            parsed.append(None)
        elif isinstance(value, str):
            parsed.append(xml_safe(value).strip() or None)
        else:
            raise ExtractKitError(f"latex[{index}] must be a string or null")
    return parsed


def _intersection(a: Box, b: Box) -> float:
    width = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    height = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    return width * height


def _area(box: Box) -> float:
    return (box[2] - box[0]) * (box[3] - box[1])


def _horizontal_overlap(a: Box, b: Box) -> float:
    overlap = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    narrower = min(a[2] - a[0], b[2] - b[0])
    return overlap / narrower if narrower else 0.0


def _column_groups(blocks: list[ExtractedBlock]) -> list[list[ExtractedBlock]]:
    groups: list[list[ExtractedBlock]] = []
    for block in sorted(blocks, key=lambda item: (item.bbox[0], item.bbox[1])):
        for group in groups:
            if any(_horizontal_overlap(block.bbox, other.bbox) >= 0.25 for other in group):
                group.append(block)
                break
        else:
            groups.append([block])
    # Merge groups transitively when a late block bridges them.
    changed = True
    while changed:
        changed = False
        for left in range(len(groups)):
            for right in range(left + 1, len(groups)):
                if any(
                    _horizontal_overlap(a.bbox, b.bbox) >= 0.25
                    for a in groups[left]
                    for b in groups[right]
                ):
                    groups[left].extend(groups.pop(right))
                    changed = True
                    break
            if changed:
                break
    return sorted(groups, key=lambda group: min(item.bbox[0] for item in group))


def order_blocks(page: ExtractedPage) -> OrderedPage:
    """Resolve common one/two-column pages and flag irregular layouts.

    Full-width headings, rules, figures, and formulas divide a page into vertical
    bands. Within each band a single column is sorted top-to-bottom; two columns
    are read down the left and then down the right. Overlapping detector regions,
    three or more columns, and interleaved columns are deliberately ambiguous so
    hybrid mode can ask the vision model for reading order instead.
    """
    blocks = list(page.blocks)
    for index, block in enumerate(blocks):
        for other in blocks[index + 1 :]:
            overlap = _intersection(block.bbox, other.bbox)
            if overlap and overlap / min(_area(block.bbox), _area(other.bbox)) > 0.18:
                return OrderedPage(tuple(), True, "overlapping_blocks")

    full = [block for block in blocks if (block.bbox[2] - block.bbox[0]) >= page.width * 0.72]
    full.sort(key=lambda item: (item.bbox[1], item.bbox[0]))
    remaining = [block for block in blocks if block not in full]
    ordered: list[ExtractedBlock] = []
    cursor = 0.0
    for separator in [*full, None]:
        bottom = separator.bbox[1] if separator is not None else float(page.height + 1)
        band = [block for block in remaining if block.bbox[1] >= cursor and block.bbox[1] < bottom]
        groups = _column_groups(band)
        if len(groups) > 2:
            return OrderedPage(tuple(), True, "too_many_columns")
        if len(groups) == 2:
            left, right = groups
            left_edge = max(item.bbox[2] for item in left)
            right_edge = min(item.bbox[0] for item in right)
            if left_edge > right_edge:
                return OrderedPage(tuple(), True, "irregular_columns")
            # Columns should occupy a shared vertical band; otherwise ordinary
            # staggered blocks were incorrectly inferred as separate columns.
            ly0, ly1 = min(b.bbox[1] for b in left), max(b.bbox[3] for b in left)
            ry0, ry1 = min(b.bbox[1] for b in right), max(b.bbox[3] for b in right)
            if min(ly1, ry1) <= max(ly0, ry0):
                return OrderedPage(tuple(), True, "staggered_columns")
        for group in groups:
            ordered.extend(sorted(group, key=lambda item: (item.bbox[1], item.bbox[0])))
        if separator is not None:
            ordered.append(separator)
            cursor = separator.bbox[3]
    return OrderedPage(tuple(ordered))


def ocr_confidence(blocks: Iterable[ExtractedBlock]) -> float:
    """Character-weighted OCR confidence, ignoring figures and formula crops."""
    weighted = total = 0.0
    for block in blocks:
        if block.type not in _TEXT_TYPES or not block.text:
            continue
        weight = max(1, len(re.sub(r"\s+", "", block.text)))
        weighted += block.confidence * weight
        total += weight
    return weighted / total if total else 1.0


def _grid_box(block: ExtractedBlock, page: ExtractedPage) -> str:
    x0, y0, x1, y1 = block.bbox
    values = (x0 * 1000 / page.width, y0 * 1000 / page.height,
              x1 * 1000 / page.width, y1 * 1000 / page.height)
    return ",".join(str(round(value)) for value in values)


def blocks_to_markdown(page: ExtractedPage, blocks: Iterable[ExtractedBlock]) -> str:
    """Convert ordered local blocks into the Markdown contract used by writers."""
    output: list[str] = []
    for block in blocks:
        if block.type in _FORMULA_TYPES:
            if block.latex:
                output.append(f"$${block.latex}$$")
        elif block.type in _FIGURE_TYPES:
            caption = block.text or "Figure"
            output.append(f"> _Figure: {caption}_ <!--box: {_grid_box(block, page)}-->")
        elif block.type in {"title", "heading"}:
            level = block.level or (1 if block.type == "title" else 2)
            if block.text:
                output.append(f"{'#' * level} {block.text}")
        elif block.type == "list":
            if block.text:
                for line in block.text.splitlines():
                    output.append(line if re.match(r"^\s*(?:[-*+] |\d+[.)] )", line) else f"- {line}")
        elif block.text:
            output.append(block.text)
    return "\n\n".join(part for part in output if part.strip())


def blocks_to_lines(
    page: ExtractedPage,
    blocks: Iterable[ExtractedBlock],
    pdf_width: float,
    pdf_height: float,
) -> list[TextLine]:
    """Scale OCR text boxes from rendered pixels into PDF points."""
    sx, sy = pdf_width / page.width, pdf_height / page.height
    lines: list[TextLine] = []
    for block in blocks:
        value = block.text if block.type in _TEXT_TYPES else block.latex
        if not value:
            continue
        x0, y0, x1, y1 = block.bbox
        box = (x0 * sx, y0 * sy, x1 * sx, y1 * sy)
        for text in value.splitlines():
            text = text.strip()
            if not text:
                continue
            lines.append(TextLine(bbox=box, spans=[Span(
                text=text, font="Times New Roman", size=10.0, colour="000000",
                bold=False, italic=False, superscript=False, math=False, bbox=box,
            )]))
    return lines


def valid_formula_latex(value: str | None) -> bool:
    body = (value or "").strip()
    if not body or body.startswith("$") or body.endswith("$"):
        return False
    if looks_incomplete(body):
        return False
    return is_math_latex(body)


@dataclass
class ExtractKitClient:
    base_url: str = field(default_factory=lambda: settings.extract_kit_url)
    connect_timeout: float = field(default_factory=lambda: settings.extract_kit_connect_timeout)
    request_timeout: float = field(default_factory=lambda: settings.extract_kit_request_timeout)

    @property
    def timeout(self) -> httpx.Timeout:
        return httpx.Timeout(self.request_timeout, connect=self.connect_timeout)

    def health(self) -> dict[str, Any]:
        try:
            response = httpx.get(f"{self.base_url}/health", timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ExtractKitError(f"PDF-Extract-Kit health check failed: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("status") not in {"ok", "ready"}:
            raise ExtractKitError("PDF-Extract-Kit is not ready")
        return payload

    def extract_page(self, image: bytes, filename: str = "page.png") -> ExtractedPage:
        try:
            response = httpx.post(
                f"{self.base_url}/extract-page",
                files={"image": (filename, image, "image/png")},
                timeout=self.timeout,
            )
            response.raise_for_status()
            return parse_page_response(response.json())
        except ExtractKitError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise ExtractKitError(f"PDF-Extract-Kit page extraction failed: {exc}") from exc

    def recognize_formulas(self, images: list[bytes]) -> list[str | None]:
        if not images:
            return []
        files = [("images", (f"formula-{index}.png", image, "image/png"))
                 for index, image in enumerate(images, start=1)]
        try:
            response = httpx.post(
                f"{self.base_url}/recognize-formulas", files=files, timeout=self.timeout
            )
            response.raise_for_status()
            return parse_formula_response(response.json(), len(images))
        except ExtractKitError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise ExtractKitError(f"PDF-Extract-Kit formula recognition failed: {exc}") from exc
