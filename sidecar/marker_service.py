"""marker-pdf sidecar.

marker pulls in torch and the Surya VLM stack, which has no business in the
application's environment and would not co-exist quietly with the pinned Paddle
and torch versions the PDF-Extract-Kit sidecar needs. So it lives here, in its
own interpreter, behind the same small JSON boundary: the application posts a
PDF and gets back whatever marker made of it.

The one design decision worth stating: **the request's config is handed to
marker's own `ConfigParser` unaltered**. That parser is the same object the
`marker` CLI builds its flags into, so every option the CLI has — `use_llm`,
`force_ocr`, `mode`, `format_lines`, `output_format`, and whatever upstream adds
next — is reachable from the application without a change here. The models are
expensive and are built once; the converter is cheap and is rebuilt per request,
because processors and renderer are chosen by the config.

That passthrough is also why this service must stay bound to 127.0.0.1: it will
run whatever marker configuration it is handed.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anyio
from fastapi import FastAPI, File, Form, HTTPException, UploadFile

GPU_SLOTS = max(1, int(os.environ.get("MARKER_GPU_CONCURRENCY", "1")))
MAX_PDF_BYTES = int(os.environ.get("MARKER_MAX_PDF_BYTES", str(256 * 1024 * 1024)))

# The only two options this service imposes, and both can be overridden by the
# request. Pagination is on because the application gives each source page its
# own Word page and marker's separator is the only record of where they end.
SERVICE_DEFAULTS: dict[str, Any] = {
    "output_format": "markdown",
    "paginate_output": True,
}

# Suffix marker's renderers produce, used only to name what comes back.
IMAGE_MEDIA = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


class Engine:
    """Holds the model artifacts, which are slow to build and safe to share."""

    def __init__(self) -> None:
        self.artifacts: dict | None = None
        self.error: str | None = None
        self.device: str = "unknown"
        self.version: str = "unknown"

    def load(self) -> None:
        try:
            import torch
            from marker.models import create_model_dict

            try:
                from importlib.metadata import version

                self.version = version("marker-pdf")
            except Exception:
                self.version = "unknown"

            self.device = "cuda:0" if torch.cuda.is_available() else "cpu"
            self.artifacts = create_model_dict()
            self.error = None
        except Exception as exc:  # surfaced by /health rather than killing the process
            self.artifacts = None
            self.error = f"{type(exc).__name__}: {exc}"

    def convert(self, pdf: Path, config: dict[str, Any]) -> dict[str, Any]:
        """Run marker over one PDF and return its output as it comes."""
        from marker.config.parser import ConfigParser
        from marker.converters.pdf import PdfConverter
        from marker.output import text_from_rendered

        merged = {**SERVICE_DEFAULTS, **config}
        parser = ConfigParser(merged)
        converter = PdfConverter(
            artifact_dict=self.artifacts,
            config=parser.generate_config_dict(),
            processor_list=parser.get_processors(),
            renderer=parser.get_renderer(),
            llm_service=parser.get_llm_service(),
        )
        rendered = converter(str(pdf))
        content, _, images = text_from_rendered(rendered)

        return {
            "format": str(merged.get("output_format") or "markdown"),
            "content": content if isinstance(content, str) else json.dumps(content, default=str),
            "images": _encode_images(images),
            "metadata": _plain(getattr(rendered, "metadata", None)) or {},
            "config": _plain(merged) or {},
        }


def _encode_images(images: Any) -> dict[str, str]:
    """Base64 the images marker extracted, keeping the names it gave them."""
    if not isinstance(images, dict):
        return {}
    encoded: dict[str, str] = {}
    for name, image in images.items():
        key = str(name)
        if Path(key).suffix.lower() not in IMAGE_MEDIA:
            key = f"{key}.png"
        try:
            if isinstance(image, (bytes, bytearray)):
                data = bytes(image)
            else:  # a PIL image, which is what the markdown renderer returns
                import io

                buffer = io.BytesIO()
                image.save(buffer, format=(Path(key).suffix.lstrip(".").upper().replace("JPG", "JPEG")))
                data = buffer.getvalue()
        except Exception:
            continue
        encoded[key] = base64.b64encode(data).decode("ascii")
    return encoded


def _plain(value: Any) -> Any:
    """Reduce marker's objects to something that survives `json.dumps`."""
    if value is None:
        return None
    try:
        return json.loads(json.dumps(value, default=str))
    except (TypeError, ValueError):
        return None


engine = Engine()
gpu_slots = asyncio.Semaphore(GPU_SLOTS)


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        await anyio.to_thread.run_sync(engine.load)
    except Exception:
        # Keep the process alive so /health explains model/configuration errors.
        pass
    yield


app = FastAPI(title="marker-pdf sidecar", version="1.0.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, Any]:
    if engine.artifacts is None:
        raise HTTPException(status_code=503, detail={"status": "error", "error": engine.error})
    return {
        "status": "ready",
        "device": engine.device,
        "gpu_concurrency": GPU_SLOTS,
        "marker_version": engine.version,
    }


def _config(raw: str) -> dict[str, Any]:
    """Parse the request's marker config without judging what is in it.

    Only the shape is checked — an object of JSON values. Which keys are
    meaningful is marker's business, and an allowlist here would quietly undo
    the reason this passthrough exists.
    """
    if not raw or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"config is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="config must be a JSON object")
    if any(not isinstance(key, str) for key in parsed):
        raise HTTPException(status_code=400, detail="config keys must be strings")
    return parsed


@app.post("/convert-document")
async def convert_document(
    file: UploadFile = File(...),
    config: str = Form(default="{}"),
) -> dict[str, Any]:
    if engine.artifacts is None:
        raise HTTPException(status_code=503, detail=engine.error or "models are not ready")

    options = _config(config)
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="the uploaded file is empty")
    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(status_code=413, detail="the uploaded file is too large")

    # marker takes a path, so the upload has to touch disk for the length of the
    # request and no longer.
    with tempfile.TemporaryDirectory(prefix="marker-") as directory:
        pdf = Path(directory) / (Path(file.filename or "document.pdf").name or "document.pdf")
        pdf.write_bytes(data)
        async with gpu_slots:
            try:
                return await anyio.to_thread.run_sync(engine.convert, pdf, options)
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(
                    status_code=500, detail=f"marker conversion failed: {type(exc).__name__}: {exc}"
                ) from exc
