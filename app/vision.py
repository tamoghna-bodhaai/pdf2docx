"""Transcribe page images to Markdown through OpenRouter's OpenAI-compatible API."""

from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from openai import APIConnectionError, APIStatusError, OpenAI, RateLimitError

from .config import settings
from .xml_text import xml_safe

SYSTEM_PROMPT = """\
You are a precise document transcription engine. You are given one rendered page \
of a PDF as an image, and you reproduce its content as clean GitHub-Flavored \
Markdown that preserves the original structure as faithfully as the format allows.

Rules:
- Transcribe ALL visible body content in natural reading order. Never summarise, \
translate, paraphrase, or add commentary of your own.
- Headings become `#`/`##`/`###`... matching the visual hierarchy of the page.
- Bold becomes `**bold**`, italic becomes `*italic*`, strikethrough `~~text~~`.
- Bulleted lists use `-`; numbered lists use `1.`; nest with two spaces per level.
- Tables become GitHub pipe tables, including the `|---|` separator row. For a \
merged cell, repeat its value in each spanned column.
- Mathematics uses LaTeX: `$...$` for inline math and `$$...$$` on their own lines \
for display equations. Always use dollar delimiters — never `\\(...\\)` or `\\[...\\]`. \
Transcribe every symbol, subscript, superscript, fraction, and limit exactly.
- Code, terminal output, and other monospaced blocks go in fenced ``` blocks.
- A figure, photo, chart, or diagram becomes a single line of the form \
`> _Figure: <its caption if printed, otherwise a one-sentence description>_ \
<!--box: x0,y0,x1,y1-->`, where the four numbers are the bounding box of the figure \
on the page as integers from 0 to 1000, measured from the top-left corner (x across, \
y down). The box must enclose the whole figure — every arrow, label and axis value \
that belongs to it — and nothing else. Give a box for every figure; omit the comment \
only if you genuinely cannot locate it. Text labels and axis values inside the figure \
belong inside its reported box. Do not repeat them as Markdown beneath the figure line; \
they will be preserved in the cropped figure.
- Omit running headers, running footers, page numbers, and watermarks.
- Rejoin words that are hyphenated across a line break, and join lines that are part \
of the same paragraph. Keep a real paragraph break as a blank line.
- If the page is blank or has no transcribable content, output nothing at all.

Output the Markdown for this page and nothing else. Do not wrap the whole page in a \
code fence, and do not add a preamble such as "Here is the transcription".
"""

_FENCE_RE = re.compile(r"^\s*```(?:markdown|md)?\s*\n(?P<body>.*)\n\s*```\s*$", re.S)

MODELS_URL = "/models"


class TranscriptionError(RuntimeError):
    pass


@dataclass
class PageTranscript:
    """One transcribed page, plus what the provider charged for it."""

    markdown: str
    cost: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    priced: bool = False  # False when the provider reported no cost for this page
    calls: int = 1
    priced_calls: int | None = None


@dataclass
class MathResult:
    """LaTeX read back from a batch of equation crops, in the order submitted."""

    latex: list[str | None] = field(default_factory=list)
    cost: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    priced: bool = False
    calls: int = 1
    priced_calls: int | None = None


MATH_PROMPT = """\
You convert images of mathematical expressions into LaTeX.

You are given one or more cropped images, each containing a single expression taken \
from a document. Return a JSON array of strings — exactly one entry per image, in the \
same order you received them.

Rules:
- Each entry is the LaTeX body only. Do not wrap it in $, $$, \\(, or \\[.
- Transcribe every symbol, subscript, superscript, fraction, radical, limit and \
delimiter exactly as shown. Do not simplify, solve, or rename anything.
- Use standard LaTeX commands (\\frac, \\sqrt, \\sum, \\int, \\partial, \\alpha, …).
- For a multi-line or aligned expression use an `aligned` environment.
- If an image is unreadable or holds no mathematics, use an empty string for it.

Output the JSON array and nothing else."""

_JSON_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*\n(?P<body>.*)\n\s*```\s*$", re.S)


def build_client() -> OpenAI:
    """An OpenAI-protocol client pointed at OpenRouter."""
    if not settings.api_key:
        raise TranscriptionError(
            "OPENROUTER_API_KEY is not set. Add it to your .env file "
            "(create a key at https://openrouter.ai/keys)."
        )
    return OpenAI(
        base_url=settings.base_url,
        api_key=settings.api_key,
        default_headers=settings.headers,
        timeout=httpx.Timeout(600.0, connect=15.0),
        max_retries=2,
    )


def list_vision_models() -> list[dict]:
    """Vision-capable models currently offered by OpenRouter, cheapest first."""
    response = httpx.get(
        settings.base_url.rstrip("/") + MODELS_URL,
        headers=settings.headers,
        timeout=20.0,
    )
    response.raise_for_status()

    models: list[dict] = []
    for entry in response.json().get("data", []):
        architecture = entry.get("architecture") or {}
        modalities = architecture.get("input_modalities") or []
        if "image" not in modalities:
            continue
        pricing = entry.get("pricing") or {}
        try:
            prompt_price = float(pricing.get("prompt"))
        except (TypeError, ValueError):
            prompt_price = None
        # OpenRouter uses -1 for routed/variable pricing (e.g. openrouter/auto).
        per_mtok = round(prompt_price * 1_000_000, 3) if prompt_price and prompt_price >= 0 else None
        models.append(
            {
                "id": entry.get("id"),
                "name": entry.get("name") or entry.get("id"),
                "context_length": entry.get("context_length"),
                "prompt_price_per_mtok": per_mtok,
            }
        )
    # Cheapest first; variable-priced models last.
    models.sort(key=lambda m: (m["prompt_price_per_mtok"] is None,
                               m["prompt_price_per_mtok"] or 0.0,
                               m["id"] or ""))
    return models


def _strip_outer_fence(text: str) -> str:
    match = _FENCE_RE.match(text.strip())
    return match.group("body") if match else text.strip()


def _request_body(image_b64: str, page_number: int, total_pages: int, model: str) -> dict:
    body: dict = {
        "model": model,
        "max_tokens": settings.max_tokens,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{image_b64}"},
                    },
                    {
                        "type": "text",
                        "text": (
                            f"This is page {page_number} of {total_pages}. "
                            "Transcribe it as Markdown following the rules."
                        ),
                    },
                ],
            },
        ],
    }
    # `usage.include` makes OpenRouter append a final streaming chunk carrying the
    # token counts and the actual amount billed for the request.
    extra: dict = {"usage": {"include": True}}
    if settings.reasoning_effort:
        # OpenRouter passes this through to reasoning-capable models and
        # ignores it for the rest.
        extra["reasoning"] = {"effort": settings.reasoning_effort}
    body["extra_body"] = extra
    return body


def _usage_fields(usage: object) -> tuple[float, int, int, bool]:
    """(cost, prompt_tokens, completion_tokens, priced) from a usage payload.

    `cost` is an OpenRouter extension to the OpenAI usage object, so it is read
    defensively: models proxied from elsewhere may not report one.
    """
    if hasattr(usage, "model_dump"):
        data = usage.model_dump()
    elif isinstance(usage, dict):
        data = usage
    else:
        data = {}

    def _number(key: str) -> float | None:
        try:
            value = data.get(key)
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    cost = _number("cost")
    prompt_tokens = int(_number("prompt_tokens") or 0)
    completion_tokens = int(_number("completion_tokens") or 0)
    return (cost or 0.0), prompt_tokens, completion_tokens, cost is not None


def transcribe_math(
    images: list[bytes],
    client: OpenAI,
    model: str | None = None,
    attempts: int = 2,
) -> MathResult:
    """Read a batch of equation crops back as LaTeX.

    Never raises: an equation that cannot be read comes back as `None` so the
    caller can fall back to the image, which is still visually exact.
    """
    if not images:
        return MathResult(latex=[])

    model = model or settings.model
    content: list[dict] = []
    for index, data in enumerate(images, start=1):
        content.append({"type": "text", "text": f"Equation {index}:"})
        encoded = base64.standard_b64encode(data).decode("ascii")
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}}
        )
    content.append(
        {"type": "text", "text": f"Return a JSON array of exactly {len(images)} LaTeX strings."}
    )

    body: dict = {
        "model": model,
        "max_tokens": min(settings.max_tokens, 400 + 260 * len(images)),
        "messages": [
            {"role": "system", "content": MATH_PROMPT},
            {"role": "user", "content": content},
        ],
        "extra_body": {"usage": {"include": True}},
    }

    result = MathResult(latex=[None] * len(images))
    for attempt in range(attempts):
        try:
            response = client.chat.completions.create(**body)
        except (RateLimitError, APIConnectionError, APIStatusError):
            if attempt < attempts - 1:
                time.sleep(2**attempt)
                continue
            return result

        usage = getattr(response, "usage", None)
        if usage is not None:
            result.cost, result.prompt_tokens, result.completion_tokens, result.priced = (
                _usage_fields(usage)
            )

        text = (response.choices[0].message.content or "").strip() if response.choices else ""
        match = _JSON_FENCE_RE.match(text)
        if match:
            text = match.group("body").strip()
        start, end = text.find("["), text.rfind("]")
        if start == -1 or end <= start:
            return result
        try:
            parsed = json.loads(text[start : end + 1])
        except ValueError:
            return result
        if not isinstance(parsed, list):
            return result

        for index in range(min(len(images), len(parsed))):
            value = parsed[index]
            if isinstance(value, str) and value.strip():
                result.latex[index] = xml_safe(value).strip()
        return result

    return result


def transcribe_page(
    image_path: Path,
    page_number: int,
    total_pages: int,
    client: OpenAI,
    model: str | None = None,
    attempts: int = 3,
) -> PageTranscript:
    """Return the Markdown transcription of a single rendered page, with its cost."""
    model = model or settings.model
    image_b64 = base64.standard_b64encode(image_path.read_bytes()).decode("ascii")
    body = _request_body(image_b64, page_number, total_pages, model)

    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            # Streaming keeps the connection alive across long page transcriptions.
            stream = client.chat.completions.create(stream=True, **body)
            chunks: list[str] = []
            finish_reason: str | None = None
            cost, prompt_tokens, completion_tokens, priced = 0.0, 0, 0, False
            for event in stream:
                error = getattr(event, "error", None)
                if error:
                    message = error.get("message") if isinstance(error, dict) else str(error)
                    raise TranscriptionError(f"page {page_number}: {message}")
                usage = getattr(event, "usage", None)
                if usage is not None:
                    cost, prompt_tokens, completion_tokens, priced = _usage_fields(usage)
                if not event.choices:
                    continue
                choice = event.choices[0]
                if choice.delta and choice.delta.content:
                    chunks.append(choice.delta.content)
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
        except (RateLimitError, APIConnectionError) as exc:
            last_error = exc
            if attempt == attempts - 1:
                break
            time.sleep(2**attempt)
            continue
        except APIStatusError as exc:
            if exc.status_code >= 500 and attempt < attempts - 1:
                last_error = exc
                time.sleep(2**attempt)
                continue
            detail = getattr(exc, "message", None) or str(exc)
            raise TranscriptionError(f"page {page_number}: {detail}") from exc

        text = "".join(chunks)
        if finish_reason == "content_filter":
            raise TranscriptionError(
                f"page {page_number}: the model declined to transcribe this page"
            )
        if finish_reason == "length" and not text.strip():
            raise TranscriptionError(
                f"page {page_number}: hit the output token limit before producing text; "
                "raise PDF2DOCX_MAX_TOKENS"
            )
        if not text.strip() and finish_reason is None:
            last_error = TranscriptionError("empty response from the model")
            if attempt < attempts - 1:
                time.sleep(2**attempt)
                continue
            break
        return PageTranscript(
            markdown=xml_safe(_strip_outer_fence(text)),
            cost=cost,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            priced=priced,
        )

    raise TranscriptionError(f"page {page_number}: {last_error}") from last_error
