"""Source-level contracts for the Next.js static frontend boundary."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).parents[1]
FRONTEND = ROOT / "frontend"


def _read(relative: str) -> str:
    return (FRONTEND / relative).read_text(encoding="utf-8")


def test_app_router_owns_the_workspace_and_login_routes() -> None:
    assert (FRONTEND / "app" / "layout.tsx").is_file()
    assert (FRONTEND / "app" / "page.tsx").is_file()
    assert (FRONTEND / "app" / "login" / "page.tsx").is_file()
    assert "Workspace" in _read("app/page.tsx")
    assert "LoginForm" in _read("app/login/page.tsx")


def test_production_exports_static_files_and_development_proxies_only_the_api() -> None:
    config = _read("next.config.mjs")
    backend = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert 'output: "export"' in config
    assert 'source: "/api/:path*"' in config
    assert 'destination: "http://127.0.0.1:8000/api/:path*"' in config
    assert "PHASE_DEVELOPMENT_SERVER" in config
    assert 'app.mount("/_next"' in backend
    assert 'FRONTEND_DIR / "index.html"' in backend
    assert 'FRONTEND_DIR / "login.html"' in backend


def test_browser_requests_are_encapsulated_by_the_typed_api_adapter() -> None:
    api = _read("lib/api/client.ts")
    types = _read("lib/api/types.ts")
    assert "fetch(" in api
    assert "new XMLHttpRequest" in api
    assert '"/api/tools/images-to-pdf"' in api
    assert '"/api/tools/split-pdf"' in api
    assert 'export type JobKind = "pdf_to_docx" | "images_to_pdf" | "split_pdf"' in types

    callers = []
    for path in FRONTEND.rglob("*.ts*"):
        if not path.is_file():
            continue
        if any(part in {"node_modules", ".next", "out", "test-results"} for part in path.parts):
            continue
        if path.parent == FRONTEND / "lib" / "api" or path.name.endswith(".test.ts") or path.name.endswith(".test.tsx"):
            continue
        source = path.read_text(encoding="utf-8")
        if re.search(r"\bfetch\(", source) or "XMLHttpRequest" in source:
            callers.append(path.relative_to(FRONTEND).as_posix())
    assert callers == []


def test_theme_and_local_fonts_are_applied_before_the_application_paints() -> None:
    layout = _read("app/layout.tsx")
    css = _read("app/globals.css")
    assert "next/font/local" in layout
    assert "InterVariable.woff2" in layout
    assert "localStorage.getItem('pdf2docx-theme')" in layout
    assert "dangerouslySetInnerHTML" in layout
    assert "--brand: oklch(.50 .17 40)" in css
    assert "prefers-reduced-motion" in css


def test_tools_and_deep_links_share_one_workspace() -> None:
    workspace = _read("components/workspace.tsx")
    url_state = _read("hooks/use-workspace-url.ts")
    assert all(label in workspace for label in ("PDF to DOCX", "Images to PDF", "Split PDF", "History"))
    assert "ToolFrame" in _read("features/pdf-to-docx/pdf-to-docx-tool.tsx")
    assert 'next.set("tool", nextTool)' in url_state
    assert 'next.set("panel", nextPanel ?? panel)' in url_state
    assert 'next.set("job", nextJob)' in url_state


def test_superseded_static_entrypoints_are_gone() -> None:
    static = ROOT / "app" / "static"
    for name in ("index.html", "login.html", "app.js", "login.js", "mmd.js"):
        assert not (static / name).exists()
