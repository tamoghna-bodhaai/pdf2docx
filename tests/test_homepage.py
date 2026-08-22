"""Static contract for the converter workspace, the sign-in page, and their hooks."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path


INDEX = Path(__file__).parents[1] / "app" / "static" / "index.html"
SCRIPT = INDEX.with_name("app.js")
LOGIN = INDEX.with_name("login.html")
LOGIN_SCRIPT = INDEX.with_name("login.js")


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


def page_contract(path: Path = INDEX) -> PageContract:
    contract = PageContract()
    contract.feed(path.read_text(encoding="utf-8"))
    return contract


def test_homepage_keeps_the_major_interaction_hooks() -> None:
    page = page_contract()
    required = {
        "app-shell", "sidebar", "menu-toggle", "sidebar-backdrop",
        "uploads-nav", "history-nav", "theme-toggle", "user-email", "sign-out",
        "dashboard-view", "dashboard-heading",
        "drop", "picker", "keywarn", "retention-note", "delete-note",
        "upload-progress", "upload-bar-fill", "upload-status",
        "job-card", "job-file", "format-menu", "format-summary", "format-options",
        "included-formats",
        "job-meta", "start", "discard", "start-actions", "run-area", "bar-fill",
        "job-status", "cost-box", "retry", "actions", "download-menu", "download-links",
        "open-comparison", "reset", "history-panel", "history-loading", "history-error",
        "history-retry", "history-table", "history-body", "history-actions",
        "comparison-view", "back-to-uploads", "comparison-file", "comparison-meta",
        "comparison-tabs", "show-source", "show-converted", "preview-pane", "preview-empty",
        "stage-wrap", "stage", "page-image", "overlay", "legend", "pager",
        "prev-page", "page-number", "page-total", "next-page", "page-note",
        "zoom-out", "zoom-fit", "zoom-in", "output-pane", "output-empty", "tabs",
        "scope", "copy", "tab-rendered", "tab-text",
        "confirm-dialog", "confirm-title", "confirm-description", "confirm-cancel",
        "confirm-action",
    }

    assert page.duplicates == set()
    assert required <= page.elements.keys()


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
    assert "picker" not in page.labels

    menu_tag, menu = page.elements["menu-toggle"]
    assert menu_tag == "button"
    assert menu["aria-controls"] == "sidebar"
    assert menu["aria-expanded"] == "false"

    dialog_tag, dialog = page.elements["confirm-dialog"]
    assert dialog_tag == "dialog"
    assert dialog["aria-labelledby"] == "confirm-title"
    assert dialog["aria-describedby"] == "confirm-description"

    for element_id in ("zoom-out", "zoom-in", "prev-page", "next-page"):
        tag, attrs = page.elements[element_id]
        assert tag == "button"
        assert attrs.get("aria-label")


def test_homepage_exposes_only_the_mathpix_workflow() -> None:
    page = page_contract()
    prohibited = {
        "layout", "layout-hint", "columns", "columns-field", "model", "model-note",
        "tab-blocks",
    }
    assert prohibited.isdisjoint(page.elements)

    html = INDEX.read_text(encoding="utf-8")
    script = SCRIPT.read_text(encoding="utf-8")
    assert "Convert PDF" in html
    assert "MATHPIX_APP_KEY" in html
    assert "/api/models" not in script
    assert "body.append('layout'" not in script
    assert "body.append('model'" not in script
    assert "body.append('columns'" not in script
    assert "JSON blocks" not in html
    assert "new XMLHttpRequest" in script
    assert "uploadInFlight" in script
    assert "Resume status updates" in script
    assert "response.status >= 500" in script
    assert "body.append('formats'" in script
    assert "new URLSearchParams" in script
    assert "confirm(" not in script
    assert not (INDEX.parent / "index.html.orig").exists()


# -- the sign-in page ------------------------------------------------------------ #


def test_the_sign_in_page_keeps_its_interaction_hooks() -> None:
    page = page_contract(LOGIN)
    required = {
        "auth-form", "auth-tabs", "tab-signin", "tab-signup", "email", "password",
        "invite-field", "invite-code", "auth-hint", "auth-error", "auth-submit",
        "signup-closed",
    }

    assert page.duplicates == set()
    assert required <= page.elements.keys()

    # The invite field starts hidden; login.js reveals it with the signup tab.
    _, invite = page.elements["invite-field"]
    assert "hidden" in (invite.get("class") or "").split()

    _, password = page.elements["password"]
    assert password["type"] == "password"


def test_the_sign_in_page_loads_nothing_the_workspace_needs() -> None:
    """Its own script only. Nothing about a conversion belongs before sign-in."""
    html = LOGIN.read_text(encoding="utf-8")
    assert "login.js" in html
    assert "app.js" not in html
    assert "katex" not in html
    assert "marked" not in html


def test_the_workspace_bounces_an_expired_session() -> None:
    """Every API call goes through the wrapper that redirects on 401."""
    script = SCRIPT.read_text(encoding="utf-8")
    assert "location.href = '/login'" in script
    # Only the wrapper itself may call fetch directly; everything else uses api().
    assert script.count("await fetch(") == 1
    assert "/api/auth/logout" in script
