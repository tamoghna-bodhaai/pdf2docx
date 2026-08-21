"""Static contract for the converter workspace and its JavaScript hooks."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path


INDEX = Path(__file__).parents[1] / "app" / "static" / "index.html"


class PageContract(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.elements: dict[str, tuple[str, dict[str, str | None]]] = {}
        self.duplicates: set[str] = set()
        self.workspace_cards: list[str] = []
        self.labels: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        element_id = values.get("id")
        classes = set((values.get("class") or "").split())

        if element_id:
            if element_id in self.elements:
                self.duplicates.add(element_id)
            self.elements[element_id] = (tag, values)
        if "workspace-card" in classes and element_id:
            self.workspace_cards.append(element_id)
        if tag == "label" and values.get("for"):
            self.labels.add(values["for"])


def page_contract() -> PageContract:
    contract = PageContract()
    contract.feed(INDEX.read_text(encoding="utf-8"))
    return contract


def test_homepage_keeps_the_major_interaction_hooks() -> None:
    page = page_contract()
    required = {
        "theme-toggle", "drop", "picker", "layout", "layout-hint", "columns",
        "columns-field", "model", "model-note", "keywarn", "job-card", "job-file",
        "job-meta", "start", "discard", "start-actions", "run-area", "bar-fill",
        "job-status", "cost-box", "actions", "dl-docx", "dl-md", "dl-marker",
        "mathpix-exports", "reset", "history-panel", "history-table",
        "history-body", "history-actions", "preview-pane", "preview-empty",
        "stage-wrap", "stage", "page-image", "overlay", "legend", "pager",
        "prev-page", "page-number", "page-total", "next-page", "page-note",
        "zoom-out", "zoom-fit", "zoom-in", "output-pane", "output-empty", "tabs",
        "scope", "copy", "tab-rendered", "tab-text", "tab-blocks",
    }

    assert page.duplicates == set()
    assert required <= page.elements.keys()
    assert page.workspace_cards == ["controls-pane", "preview-pane", "output-pane"]


def test_upload_and_theme_controls_are_accessible() -> None:
    page = page_contract()

    theme_tag, theme = page.elements["theme-toggle"]
    assert theme_tag == "button"
    assert theme["type"] == "button"
    assert theme["aria-label"] == "Switch to dark theme"
    assert theme["aria-pressed"] == "false"

    drop_tag, drop = page.elements["drop"]
    assert drop_tag == "button"
    assert drop["type"] == "button"
    assert drop["aria-describedby"] == "hint"

    picker_tag, picker = page.elements["picker"]
    assert picker_tag == "input"
    assert picker["type"] == "file"
    assert ".pdf" in (picker["accept"] or "")
    assert {"layout", "columns", "model"} <= page.labels

    for element_id in ("zoom-out", "zoom-in", "prev-page", "next-page"):
        tag, attrs = page.elements[element_id]
        assert tag == "button"
        assert attrs.get("aria-label")
