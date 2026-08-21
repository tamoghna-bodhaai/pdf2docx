"""Remote model selection must never resolve to Claude."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from app import locate, vision
from app.config import Settings
from app.model_policy import (
    DEFAULT_MODEL,
    ModelPolicyError,
    is_model_allowed,
    require_model_allowed,
)


@pytest.mark.parametrize(
    "model",
    [
        "anthropic/claude-sonnet-5",
        "Anthropic/another-model",
        "third-party/claude-proxy",
        "openrouter/auto",
        "openrouter/free",
    ],
)
def test_claude_and_dynamic_router_models_are_rejected(model):
    assert is_model_allowed(model) is False
    with pytest.raises(ModelPolicyError):
        require_model_allowed(model)


def test_the_default_is_an_explicit_non_claude_model():
    assert DEFAULT_MODEL == "google/gemini-3.6-flash"
    assert require_model_allowed(DEFAULT_MODEL) == DEFAULT_MODEL


def test_the_openrouter_picker_excludes_disallowed_models(monkeypatch):
    entries = [
        ("anthropic/claude-sonnet-5", "Claude"),
        ("openrouter/auto", "Auto"),
        ("google/gemini-3.6-flash", "Gemini"),
        ("openai/gpt-5", "GPT"),
    ]
    payload = {
        "data": [
            {
                "id": model_id,
                "name": name,
                "architecture": {"input_modalities": ["text", "image"]},
                "pricing": {"prompt": "0.000001"},
            }
            for model_id, name in entries
        ]
    }
    response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: payload,
    )
    monkeypatch.setattr(vision.httpx, "get", lambda *args, **kwargs: response)

    assert [item["id"] for item in vision.list_vision_models()] == [
        "google/gemini-3.6-flash",
        "openai/gpt-5",
    ]


def test_transcription_rejects_claude_before_reading_or_calling(tmp_path):
    client = SimpleNamespace()
    missing_image = tmp_path / "not-read.png"

    with pytest.raises(ModelPolicyError):
        vision.transcribe_page(
            missing_image,
            1,
            1,
            client,
            model="anthropic/claude-sonnet-5",
        )


def test_math_transcription_rejects_claude_before_calling():
    client = SimpleNamespace()

    with pytest.raises(ModelPolicyError):
        vision.transcribe_math(
            [b"image"],
            client,
            model="anthropic/claude-sonnet-5",
        )


def test_figure_location_rejects_claude_before_rendering_or_calling(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        locate,
        "settings",
        replace(locate.settings, locate_figures=True, figure_model=""),
    )
    client = SimpleNamespace()
    markdown = "> _Figure: diagram_ <!--box: 10,20,30,40-->"

    with pytest.raises(ModelPolicyError):
        locate.relocate_figures(
            tmp_path / "not-read.pdf",
            0,
            markdown,
            client,
            model="anthropic/claude-sonnet-5",
        )


def test_legacy_environment_values_fall_back_safely(monkeypatch):
    monkeypatch.setenv("PDF2DOCX_MODEL", "anthropic/claude-sonnet-5")
    monkeypatch.setenv("PDF2DOCX_FIGURE_MODEL", "openrouter/auto")

    configured = Settings()

    assert configured.model == DEFAULT_MODEL
    assert configured.figure_model == ""
