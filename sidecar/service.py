"""Python 3.10 PDF-Extract-Kit sidecar.

Models are constructed once during FastAPI lifespan startup and all GPU work is
serialized. The main Python 3.12 application communicates only through this
small, versioned JSON/image boundary.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anyio
from fastapi import FastAPI, File, HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError

UPSTREAM_ROOT = Path(os.environ.get("PDF_EXTRACT_KIT_ROOT", "/opt/PDF-Extract-Kit"))
MODEL_ROOT = Path(os.environ.get("PDF_EXTRACT_KIT_MODEL_ROOT", "/models"))
GPU_SLOTS = max(1, int(os.environ.get("PDF_EXTRACT_KIT_GPU_CONCURRENCY", "1")))


def _path(name: str, default: Path) -> str:
    return os.environ.get(name, str(default))


def _bbox(item: dict[str, Any]) -> list[float] | None:
    poly = item.get("poly")
    if not isinstance(poly, (list, tuple)) or len(poly) < 6:
        return None
    try:
        x0, y0, x1, y1 = float(poly[0]), float(poly[1]), float(poly[4]), float(poly[5])
    except (TypeError, ValueError):
        return None
    if x1 <= x0 or y1 <= y0:
        return None
    return [x0, y0, x1, y1]


def _inside(inner: list[float], outer: list[float]) -> bool:
    cx, cy = (inner[0] + inner[2]) / 2, (inner[1] + inner[3]) / 2
    return outer[0] <= cx <= outer[2] and outer[1] <= cy <= outer[3]


class Engine:
    """Persistent wrapper around the upstream task/model registry."""

    def __init__(self) -> None:
        self.pipeline = None
        self.formula_model = None
        self.device = "unknown"
        self.error: str | None = None

    def load(self) -> None:
        try:
            sys.path.insert(0, str(UPSTREAM_ROOT))
            sys.path.insert(0, str(UPSTREAM_ROOT / "project" / "pdf2markdown" / "scripts"))
            # Importing task packages registers both task and model constructors.
            import pdf_extract_kit.tasks.layout_detection  # noqa: F401
            import pdf_extract_kit.tasks.formula_detection  # noqa: F401
            import pdf_extract_kit.tasks.formula_recognition  # noqa: F401
            import pdf_extract_kit.tasks.ocr  # noqa: F401
            from pdf_extract_kit.utils.config_loader import initialize_tasks_and_models
            from pdf2markdown import PDF2MARKDOWN

            config = {
                "tasks": {
                    "layout_detection": {
                        "model": "layout_detection_yolo",
                        "model_config": {
                            "img_size": 1024,
                            "conf_thres": 0.25,
                            "iou_thres": 0.45,
                            "device": os.environ.get("PDF_EXTRACT_KIT_DEVICE", "cuda"),
                            "model_path": _path(
                                "PDF_EXTRACT_KIT_LAYOUT_MODEL",
                                MODEL_ROOT / "models/Layout/YOLO/doclayout_yolo_ft.pt",
                            ),
                        },
                    },
                    "formula_detection": {
                        "model": "formula_detection_yolo",
                        "model_config": {
                            "img_size": 1280,
                            "conf_thres": 0.25,
                            "iou_thres": 0.45,
                            "batch_size": 1,
                            "model_path": _path(
                                "PDF_EXTRACT_KIT_FORMULA_DETECTION_MODEL",
                                MODEL_ROOT / "models/MFD/YOLO/yolo_v8_ft.pt",
                            ),
                        },
                    },
                    "formula_recognition": {
                        "model": "formula_recognition_unimernet",
                        "model_config": {
                            # Keep the batch conservative for an 8 GB card.
                            "batch_size": int(os.environ.get("PDF_EXTRACT_KIT_FORMULA_BATCH", "8")),
                            "cfg_path": _path(
                                "PDF_EXTRACT_KIT_UNIMERNET_CONFIG",
                                UPSTREAM_ROOT / "pdf_extract_kit/configs/unimernet.yaml",
                            ),
                            "model_path": _path(
                                "PDF_EXTRACT_KIT_UNIMERNET_MODEL",
                                MODEL_ROOT / "models/MFR/unimernet_tiny",
                            ),
                        },
                    },
                    "ocr": {
                        "model": "ocr_ppocr",
                        "model_config": {
                            "lang": os.environ.get("PDF_EXTRACT_KIT_OCR_LANG", "ch"),
                            "show_log": False,
                            "use_gpu": os.environ.get("PDF_EXTRACT_KIT_DEVICE", "cuda") != "cpu",
                            "det_model_dir": _path(
                                "PDF_EXTRACT_KIT_OCR_DET_MODEL",
                                MODEL_ROOT / "models/OCR/PaddleOCR/det/ch_PP-OCRv4_det",
                            ),
                            "rec_model_dir": _path(
                                "PDF_EXTRACT_KIT_OCR_REC_MODEL",
                                MODEL_ROOT / "models/OCR/PaddleOCR/rec/ch_PP-OCRv4_rec",
                            ),
                            "det_db_box_thresh": 0.3,
                        },
                    },
                }
            }
            tasks = initialize_tasks_and_models(config)
            layout = tasks["layout_detection"].model
            detector = tasks["formula_detection"].model
            recognizer = tasks["formula_recognition"].model
            ocr = tasks["ocr"].model
            self.pipeline = PDF2MARKDOWN(layout, detector, recognizer, ocr)
            self.formula_model = recognizer
            self.device = str(getattr(recognizer, "device", "unknown"))
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            raise

    def _normalise(self, result: dict[str, Any], width: int, height: int) -> dict[str, Any]:
        detections = result.get("layout_dets") or []
        regions: list[tuple[dict[str, Any], list[float]]] = []
        text_spans: list[tuple[dict[str, Any], list[float]]] = []
        formulas: list[tuple[dict[str, Any], list[float]]] = []
        for item in detections:
            box = _bbox(item)
            if box is None:
                continue
            category = str(item.get("category_type") or "").lower()
            if category == "text":
                text_spans.append((item, box))
            elif category in {"inline", "isolated", "isolate_formula"}:
                formulas.append((item, box))
            else:
                regions.append((item, box))

        assigned: set[int] = set()
        blocks: list[dict[str, Any]] = []
        text_categories = {
            "title": "title",
            "plain text": "paragraph",
            "figure_caption": "caption",
            "table_caption": "caption",
            "table_footnote": "caption",
            "formula_caption": "caption",
        }
        for region, box in regions:
            category = str(region.get("category_type") or "").lower()
            if category in text_categories:
                matches = [
                    (index, span, span_box)
                    for index, (span, span_box) in enumerate(text_spans)
                    if index not in assigned and _inside(span_box, box)
                ]
                matches.sort(key=lambda value: (value[2][1], value[2][0]))
                if matches:
                    assigned.update(index for index, _, _ in matches)
                    text = " ".join(str(span.get("text") or "").strip() for _, span, _ in matches).strip()
                    confidence = sum(float(span.get("score", 0.0)) for _, span, _ in matches) / len(matches)
                    if text:
                        blocks.append({
                            "type": text_categories[category], "bbox": box,
                            "text": text, "confidence": round(confidence, 4),
                            "level": 1 if category == "title" else 0,
                        })
            elif category in {"figure", "table"}:
                blocks.append({
                    "type": "figure", "bbox": box,
                    "text": "Table" if category == "table" else "Figure",
                    "confidence": float(region.get("score", 1.0)),
                })

        # Preserve OCR lines that were not contained by a layout block.
        for index, (span, box) in enumerate(text_spans):
            if index in assigned:
                continue
            text = str(span.get("text") or "").strip()
            if text:
                blocks.append({
                    "type": "paragraph", "bbox": box, "text": text,
                    "confidence": float(span.get("score", 0.0)),
                })
        for formula, box in formulas:
            latex = str(formula.get("latex") or "").strip()
            blocks.append({
                "type": "formula", "bbox": box, "latex": latex,
                "confidence": float(formula.get("score", 0.0)),
            })
        blocks.sort(key=lambda block: (block["bbox"][1], block["bbox"][0]))
        return {"width": width, "height": height, "blocks": blocks}

    def extract_page(self, image: Image.Image) -> dict[str, Any]:
        assert self.pipeline is not None
        rgb = image.convert("RGB")
        result = self.pipeline.process_single_pdf([rgb])[0]
        return self._normalise(result, rgb.width, rgb.height)

    def recognize_formulas(self, images: list[Image.Image]) -> list[str | None]:
        assert self.formula_model is not None
        with tempfile.TemporaryDirectory(prefix="pdf2docx-formulas-") as directory:
            paths: list[str] = []
            for index, image in enumerate(images):
                path = Path(directory) / f"{index:04d}.png"
                image.convert("RGB").save(path)
                paths.append(str(path))
            values = self.formula_model.predict(paths, "")
        # Upstream can skip a corrupt crop; keep response cardinality stable.
        return [str(values[index]).strip() if index < len(values) else None for index in range(len(images))]


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


app = FastAPI(title="PDF-Extract-Kit sidecar", version="1.0.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, Any]:
    if engine.pipeline is None:
        raise HTTPException(status_code=503, detail={"status": "error", "error": engine.error})
    return {"status": "ready", "device": engine.device, "gpu_concurrency": GPU_SLOTS}


async def _image(upload: UploadFile) -> Image.Image:
    try:
        data = await upload.read()
        with Image.open(__import__("io").BytesIO(data)) as opened:
            return opened.convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(status_code=400, detail="invalid image") from exc


@app.post("/extract-page")
async def extract_page(image: UploadFile = File(...)) -> dict[str, Any]:
    if engine.pipeline is None:
        raise HTTPException(status_code=503, detail=engine.error or "models are not ready")
    page = await _image(image)
    async with gpu_slots:
        return await anyio.to_thread.run_sync(engine.extract_page, page)


@app.post("/recognize-formulas")
async def recognize_formulas(images: list[UploadFile] = File(...)) -> dict[str, Any]:
    if engine.formula_model is None:
        raise HTTPException(status_code=503, detail=engine.error or "models are not ready")
    crops = [await _image(upload) for upload in images]
    async with gpu_slots:
        latex = await anyio.to_thread.run_sync(engine.recognize_formulas, crops)
    return {"latex": latex}
