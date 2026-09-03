"""Static contract for the converter workspace, the sign-in page, and their hooks."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path


INDEX = Path(__file__).parents[1] / "app" / "static" / "index.html"
SCRIPT = INDEX.with_name("app.js")
STYLESHEET = INDEX.with_name("app.css")
LOGIN = INDEX.with_name("login.html")
LOGIN_SCRIPT = INDEX.with_name("login.js")


class PageContract(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.elements: dict[str, tuple[str, dict[str, str | None]]] = {}
        self.duplicates: set[str] = set()
        self.workspace_cards: list[str] = []
        self.labels: set[str] = set()
        self.stylesheets: list[str] = []

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
        if tag == "link" and values.get("rel") == "stylesheet" and values.get("href"):
            self.stylesheets.append(values["href"])


def page_contract(path: Path = INDEX) -> PageContract:
    contract = PageContract()
    contract.feed(path.read_text(encoding="utf-8"))
    return contract


def test_homepage_keeps_the_major_interaction_hooks() -> None:
    page = page_contract()
    required = {
        "app-shell", "sidebar", "menu-toggle", "sidebar-backdrop",
        "uploads-nav", "history-nav", "theme-toggle", "user-email", "sign-out",
        "notification-toggle", "notification-status", "toast-region",
        "dashboard-view", "dashboard-heading",
        "drop", "picker", "keywarn",
        "upload-progress", "upload-bar-fill", "upload-status", "upload-batch-hint",
        "format-menu", "format-summary", "format-options",
        "included-formats", "multi-column", "multi-column-field",
        "batch-panel", "batch-title", "batch-summary", "batch-badge", "batch-notice",
        "batch-controls", "batch-list", "batch-start", "batch-pause", "batch-resume",
        "batch-cancel", "batch-download", "batch-clear", "batch-new",
        "history-panel", "history-loading", "history-error",
        "history-retry", "history-table", "history-body", "history-actions",
        "comparison-view", "back-to-uploads", "comparison-file", "comparison-meta",
        "comparison-download-menu", "comparison-download-links",
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

    notification_tag, notification = page.elements["notification-toggle"]
    assert notification_tag == "button"
    assert notification["type"] == "button"
    assert notification["aria-label"] == "Enable desktop notifications"
    assert notification["aria-pressed"] == "false"

    toast_tag, toast = page.elements["toast-region"]
    assert toast_tag == "div"
    assert toast["role"] == "status"
    assert toast["aria-live"] == "polite"

    drop_tag, drop = page.elements["drop"]
    assert drop_tag == "button"
    assert drop["type"] == "button"
    assert drop["aria-describedby"] == "hint"

    picker_tag, picker = page.elements["picker"]
    assert picker_tag == "input"
    assert picker["type"] == "file"
    assert ".pdf" in (picker["accept"] or "")
    # Batch upload: the picker takes several files at once.
    assert "multiple" in picker
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
    assert "Convert all" in html
    # The page never names the conversion provider. Env-var names and vendor
    # branding are for the operator, not for whoever is converting a PDF.
    assert "mathpix" not in html.lower()
    assert "/api/models" not in script
    assert "body.append('layout'" not in script
    assert "body.append('model'" not in script
    assert "body.append('columns'" not in script
    assert "JSON blocks" not in html
    assert "new XMLHttpRequest" in script
    assert "uploadInFlight" in script
    assert "response.status >= 500" in script
    assert "body.append('formats'" in script
    # The column toggle travels the same way the formats do, and under a name
    # the older `columns` field never had.
    assert "body.append('multi_column'" in script
    # Batch conversion: the whole batch and each file can be started, paused,
    # and cancelled.
    assert "/api/convert/batch" in script
    assert "/api/batches/" in script
    assert "/package.zip" in script
    assert "Download all (.zip)" in script
    assert "Download batch ZIP" in INDEX.read_text(encoding="utf-8")
    assert "Notification.requestPermission()" in script
    assert "localStorage.setItem(NOTIFICATION_PREFERENCE" in script
    assert "beginNewConversion" in script
    assert "Manage batch" in script
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


def test_both_pages_load_the_same_bundled_inter_stylesheet() -> None:
    workspace = page_contract()
    sign_in = page_contract(LOGIN)
    versioned_stylesheet = "/static/app.css?v=20260901-batch-ui"

    assert versioned_stylesheet in workspace.stylesheets
    assert sign_in.stylesheets == [versioned_stylesheet]

    css = STYLESHEET.read_text(encoding="utf-8")
    assert css.count('font-family: "Inter";') == 2
    assert css.count("font-weight: 100 900;") == 2
    assert css.count("font-display: swap;") == 2
    assert 'url("vendor/inter/InterVariable.woff2")' in css
    assert 'url("vendor/inter/InterVariable-Italic.woff2")' in css
    assert "--font-sans: Inter, ui-sans-serif, system-ui" in css
    assert '--font-mono: "JetBrains Mono", ui-monospace' in css


def test_the_workspace_bounces_an_expired_session() -> None:
    """Every API call goes through the wrapper that redirects on 401."""
    script = SCRIPT.read_text(encoding="utf-8")
    assert "location.href = '/login'" in script
    # Only the wrapper itself may call fetch directly; everything else uses api().
    assert script.count("await fetch(") == 1
    assert "/api/auth/logout" in script


def test_new_conversion_only_detaches_the_panel_and_opens_the_picker() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    action = script.split("function beginNewConversion()", 1)[1].split(
        "// --------------------------------------------------------- confirmation dialog", 1
    )[0]

    assert "currentBatch = null" in action
    assert "renderFormats(['docx'])" in action
    assert "$('multi-column').checked = false" in action
    assert "picker.value = ''" in action
    assert "picker.click()" in action
    assert "api(" not in action
    assert "DELETE" not in action
    assert "cancel" not in action.lower()
