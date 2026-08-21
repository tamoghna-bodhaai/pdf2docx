# pdf2docx — Mathpix paper export workspace

Upload a PDF, inspect each source page beside Mathpix's rendered Markdown, and
download Mathpix's original DOCX plus every export the document successfully
produced.

The web workflow is Mathpix-only. It does not select or initialise an
OpenRouter model, and it does not rebuild Mathpix's DOCX locally.

## What the application does

1. Stores the uploaded PDF in the local job directory and reads its page count.
2. Uploads the PDF to the Mathpix Files API with page breaks and every supported
   export requested by default.
3. Saves every returned export byte-for-byte under `mathpix/`.
4. Downloads images referenced by Mathpix Markdown into `mathpix/images/` and
   rewrites only the local preview Markdown to those local paths.
5. Splits the preview Markdown on Mathpix page breaks so source-page navigation
   and rendered Markdown stay aligned.
6. Deletes the remote Mathpix upload after exports and preview images are stored,
   unless deletion is explicitly disabled.

`document.docx` is a byte-for-byte copy of `mathpix/document.docx`, the file
Mathpix returned. New jobs do not create a comparison or rebuilt DOCX.

## Privacy and retention

Conversion is remote: the uploaded PDF leaves this machine and is processed by
Mathpix. The defaults are deliberately conservative:

- `improve_mathpix` is false, so the document is not retained for model
  improvement.
- The remote upload is deleted after all available exports and referenced
  preview images are stored locally.
- Local source files, exports, previews, and history remain under
  `PDF2DOCX_DATA_DIR` until deleted from the UI or evicted by the history limit.

Review Mathpix's own terms and data-handling policy before processing sensitive
or third-party documents.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Create Mathpix credentials in the [Mathpix console](https://console.mathpix.com/)
and set at least:

```bash
MATHPIX_APP_KEY=...
```

`MATHPIX_APP_ID` is sent when configured, but only `MATHPIX_APP_KEY` is required
by this application. No OpenRouter credential is needed or used.

Run the server:

```bash
.venv/bin/uvicorn app.main:app --reload --port 8000
```

Open <http://localhost:8000>, choose a PDF, and press **Convert with Mathpix**.
Only PDFs are accepted.

## Exports

All supported requested formats are enabled when
`PDF2DOCX_MATHPIX_FORMATS` is blank:

| Category | Formats |
|---|---|
| Office | `docx`, `pptx`, `xlsx` (tables only) |
| Markup | `md`, `mmd`, `html`, `tex.zip`, `md.zip`, `mmd.zip`, `html.zip` |
| Rendered | `pdf`, `latex.pdf` |
| Data | `lines.json`, `lines.mmd.json` |

Mathpix always produces `mmd`, `lines.json`, and `lines.mmd.json`; they are
fetched without being included in `conversion_formats`. A document-specific
format that Mathpix does not produce, such as `xlsx` for a paper without tables,
is omitted without failing the job. The UI shows download links only for files
that exist.

Raw exports are never rewritten. DOCX, PPTX, and ZIP variants are the
self-contained choices when embedded images matter. The plain markup exports
can retain remote references; the browser preview instead uses the locally
downloaded images.

## Page-aligned preview

The viewer rasterises the source PDF locally on demand. Its preview record has
the source page dimensions, the corresponding Mathpix Markdown, and an empty
`blocks` list. `mathpix/document.lines.json` remains available as an untouched
raw export but is not translated into bounding boxes or drawn over the PDF.

Rendered Markdown uses the bundled Marked and KaTeX assets, including headings,
lists, tables, inline/display equations, and locally stored Mathpix images. The
viewer itself makes no external asset requests.

## History and reruns

History survives server restarts. Finished downloads remain available until the
entry is deleted or evicted by `PDF2DOCX_HISTORY_LIMIT`.

Historical jobs created by the legacy Vision, replica, structured, or Marker
paths remain visible and their existing files remain downloadable. Rerunning any
historical job uses Mathpix and requires `MATHPIX_APP_KEY`. Existing historical
`rebuilt.docx` files remain reachable through the compatibility download route;
new jobs do not create or advertise them.

## Configuration

All active web-workflow settings live in `.env` (see `.env.example`).

| Variable | Default | Notes |
|---|---|---|
| `MATHPIX_APP_KEY` | — | Required for uploads and conversions. |
| `MATHPIX_APP_ID` | — | Optional Mathpix application identifier. |
| `MATHPIX_URL` | `https://api.mathpix.com` | Mathpix API base URL. |
| `PDF2DOCX_MATHPIX_OPTIONS` | `{}` | Options passed through to Mathpix. Application-required DOCX output cannot be disabled. |
| `PDF2DOCX_MATHPIX_FORMATS` | blank | Comma-separated requested exports. Blank requests all; DOCX is always included. |
| `PDF2DOCX_MATHPIX_IMPROVE` | `off` | Opt in to Mathpix model-improvement retention. |
| `PDF2DOCX_MATHPIX_DELETE` | `on` | Delete the remote upload after local collection. |
| `PDF2DOCX_MATHPIX_CONNECT_TIMEOUT` | `10` | Connection timeout per API request, in seconds. |
| `PDF2DOCX_MATHPIX_REQUEST_TIMEOUT` | `120` | Response timeout per API request, in seconds. |
| `PDF2DOCX_MATHPIX_POLL_INTERVAL` | `2` | Status/export polling interval, in seconds. |
| `PDF2DOCX_MATHPIX_POLL_TIMEOUT` | `1800` | Whole-document polling deadline, in seconds. |
| `PDF2DOCX_MATHPIX_PAGE_RATE` | `0.0015` | Per-page estimate stored on the job; it is not a provider-reported charge. |
| `PDF2DOCX_DPI` / `PDF2DOCX_MAX_EDGE` | `180` / `2000` | Local source-preview rendering limits. |
| `PDF2DOCX_MAX_PAGES` | `0` | Maximum pages processed; `0` is unlimited. |
| `PDF2DOCX_DATA_DIR` | `~/.pdf2docx` | Local source, export, preview, and history storage. |
| `PDF2DOCX_HISTORY_LIMIT` | `100` | Retained jobs; `0` keeps all. |

`PDF2DOCX_LAYOUT`, `PDF2DOCX_MODEL`, `PDF2DOCX_COLUMNS`, and OpenRouter settings
remain in the legacy implementation for rollback and direct internal use, but
the web UI ignores them. The API continues accepting the old `model`, `layout`,
and `columns` form fields for client compatibility: model and columns are
ignored, omitted or `mathpix` layout is accepted, and every explicit non-Mathpix
layout is rejected.

## HTTP API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/config` | Effective Mathpix/web settings without secret values. |
| `POST` | `/api/convert` | Stage a multipart PDF; optional `start=true` starts it immediately. |
| `POST` | `/api/jobs/{id}/start` | Start or rerun the stored PDF with Mathpix. |
| `GET` | `/api/jobs/{id}` | Status, progress, available exports, and cost estimate. |
| `GET` | `/api/history` | Stored jobs, newest first. |
| `GET` | `/api/jobs/{id}/detection` | Page dimensions and page-aligned Markdown; Mathpix blocks are empty. |
| `GET` | `/api/jobs/{id}/page/{number}.png` | Locally rendered source page. |
| `GET` | `/api/jobs/{id}/asset/{path}` | A locally stored preview image. |
| `GET` | `/api/jobs/{id}/download?format=docx` | Primary Mathpix DOCX. |
| `GET` | `/api/jobs/{id}/download?format=mathpix-{ext}` | An available untouched Mathpix export. |
| `DELETE` | `/api/jobs/{id}` | Delete one local job and all of its files. |
| `DELETE` | `/api/history` | Delete every job that is not running. |

Example:

```bash
curl -F file=@paper.pdf -F start=true http://localhost:8000/api/convert
curl http://localhost:8000/api/jobs/JOB_ID
curl -OJ 'http://localhost:8000/api/jobs/JOB_ID/download?format=docx'
```

## Tests

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
```

Tests use generated PDFs and fake Mathpix clients; they require no network or
API credentials.
