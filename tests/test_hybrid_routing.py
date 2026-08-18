from __future__ import annotations

from types import SimpleNamespace

import fitz
import pytest

from app import pipeline
from app.extract_kit import ExtractKitError, parse_page_response
from app.pdf_extract import MathItem, PageLayout
from app.vision import MathResult, PageTranscript


class Writer:
    pages = []

    def __init__(self):
        self.pages = []

    def add_page(self, page):
        self.pages.append(page)

    def save(self, path):
        path.write_bytes(b"docx")


def source_pdf(tmp_path):
    path = tmp_path / "source.pdf"
    doc = fitz.open()
    doc.new_page(width=500, height=600)
    doc.save(path)
    doc.close()
    return path


def settings(provider):
    return SimpleNamespace(
        extraction_provider=provider,
        max_pages=0,
        math_mode="auto",
        concurrency=1,
        ocr_confidence_threshold=0.65,
        dpi=180,
    )


def prepare(monkeypatch, provider, layout):
    monkeypatch.setattr(pipeline, "settings", settings(provider))
    monkeypatch.setattr(pipeline, "extract_page", lambda *args, **kwargs: layout)
    monkeypatch.setattr(pipeline, "ReplicaWriter", Writer)


def extracted(confidence=0.95):
    return parse_page_response({
        "width": 1000,
        "height": 1200,
        "blocks": [{
            "type": "paragraph", "bbox": [100, 240, 500, 600],
            "text": "Local OCR", "confidence": confidence,
        }],
    })


def test_local_scan_makes_no_remote_calls_and_keeps_searchable_boxes(tmp_path, monkeypatch):
    layout = PageLayout(number=1, width=500, height=600, scanned=True, page_image=b"scan")
    prepare(monkeypatch, "local", layout)
    monkeypatch.setattr(
        pipeline, "ExtractKitClient", lambda: SimpleNamespace(extract_page=lambda image: extracted())
    )
    monkeypatch.setattr(
        pipeline, "build_client", lambda: pytest.fail("local mode must not build a remote client")
    )

    result = pipeline.convert_pdf_replica(source_pdf(tmp_path), tmp_path / "work")

    assert result.usage.calls == 0
    assert result.diagnostics[0].extractor == "pdf-extract-kit"
    assert layout.lines[0].bbox == pytest.approx((50, 120, 250, 300))
    assert "Local OCR" in result.markdown_path.read_text()


def test_hybrid_low_confidence_scan_falls_back_and_counts_only_remote_call(tmp_path, monkeypatch):
    layout = PageLayout(number=1, width=500, height=600, scanned=True, page_image=b"scan")
    prepare(monkeypatch, "hybrid", layout)
    monkeypatch.setattr(
        pipeline, "ExtractKitClient", lambda: SimpleNamespace(extract_page=lambda image: extracted(0.2))
    )
    monkeypatch.setattr(pipeline, "build_client", lambda: object())
    monkeypatch.setattr(
        pipeline,
        "transcribe_page",
        lambda *args, **kwargs: PageTranscript(
            markdown="Remote OCR", cost=0.01, prompt_tokens=10, completion_tokens=5, priced=True
        ),
    )

    result = pipeline.convert_pdf_replica(source_pdf(tmp_path), tmp_path / "work")

    assert result.usage.calls == 1
    assert result.usage.prompt_tokens == 10
    assert result.diagnostics[0].extractor == "vision"
    assert result.diagnostics[0].fallback_reason == "low_ocr_confidence"
    assert layout.markdown == "Remote OCR"


def test_hybrid_sidecar_error_falls_back_but_local_mode_surfaces_it(tmp_path, monkeypatch):
    class Broken:
        def extract_page(self, image):
            raise ExtractKitError("timeout")

    hybrid_layout = PageLayout(number=1, width=500, height=600, scanned=True, page_image=b"scan")
    prepare(monkeypatch, "hybrid", hybrid_layout)
    monkeypatch.setattr(pipeline, "ExtractKitClient", Broken)
    monkeypatch.setattr(pipeline, "build_client", lambda: object())
    monkeypatch.setattr(
        pipeline, "transcribe_page", lambda *args, **kwargs: PageTranscript(markdown="fallback")
    )
    result = pipeline.convert_pdf_replica(source_pdf(tmp_path), tmp_path / "hybrid")
    assert result.diagnostics[0].fallback_reason == "sidecar_error"
    assert result.usage.calls == 1

    local_layout = PageLayout(number=1, width=500, height=600, scanned=True, page_image=b"scan")
    prepare(monkeypatch, "local", local_layout)
    with pytest.raises(ExtractKitError, match="timeout"):
        pipeline.convert_pdf_replica(source_pdf(tmp_path), tmp_path / "local")


def test_hybrid_equations_use_local_then_vision_only_for_invalid_results(tmp_path, monkeypatch):
    layout = PageLayout(
        number=1,
        width=500,
        height=600,
        maths=[
            MathItem(bbox=(0, 0, 10, 10), image=b"one"),
            MathItem(bbox=(20, 0, 30, 10), image=b"two"),
        ],
    )
    prepare(monkeypatch, "hybrid", layout)
    monkeypatch.setattr(
        pipeline,
        "ExtractKitClient",
        lambda: SimpleNamespace(recognize_formulas=lambda images: [r"x=1", r"\text{words only}"]),
    )
    monkeypatch.setattr(pipeline, "build_client", lambda: object())
    seen = []

    def remote(images, *args, **kwargs):
        seen.extend(images)
        return MathResult(
            latex=[r"y=2"], cost=0.02, prompt_tokens=4, completion_tokens=2, priced=True
        )

    monkeypatch.setattr(pipeline, "transcribe_math", remote)
    result = pipeline.convert_pdf_replica(source_pdf(tmp_path), tmp_path / "work")

    assert seen == [b"two"]
    assert [item.latex for item in layout.maths] == ["x=1", "y=2"]
    assert result.usage.calls == 1
    assert result.diagnostics[0].extractor == "pdf-extract-kit+vision"
    assert result.diagnostics[0].fallback_reason == "invalid_formula"


def test_vision_mode_never_constructs_local_client(tmp_path, monkeypatch):
    layout = PageLayout(number=1, width=500, height=600, scanned=True, page_image=b"scan")
    prepare(monkeypatch, "vision", layout)
    monkeypatch.setattr(
        pipeline, "ExtractKitClient", lambda: pytest.fail("vision mode must not use sidecar")
    )
    monkeypatch.setattr(pipeline, "build_client", lambda: object())
    monkeypatch.setattr(
        pipeline, "transcribe_page", lambda *args, **kwargs: PageTranscript(markdown="vision")
    )
    result = pipeline.convert_pdf_replica(source_pdf(tmp_path), tmp_path / "work")
    assert result.usage.calls == 1
    assert result.diagnostics[0].extractor == "vision"
    assert result.diagnostics[0].fallback_reason is None
