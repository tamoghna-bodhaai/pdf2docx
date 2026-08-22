# pdf2docx — Mathpix paper export workspace

Upload one PDF, select the exact Mathpix outputs you need, follow conversion
progress, and inspect each source page beside Mathpix's rendered Markdown.

The web workflow is Mathpix-only. It does not select or initialise an
OpenRouter model, and it does not rebuild Mathpix's DOCX locally.

## What the application does

1. Stores the uploaded PDF in the local job directory and reads its page count.
2. Uploads the PDF to the Mathpix Files API with page breaks and the outputs
   selected for that job.
3. Saves every returned export byte-for-byte under `mathpix/`.
4. Downloads images referenced by Mathpix Markdown into `mathpix/images/` and
   rewrites only the local preview Markdown to those local paths.
5. Splits the preview Markdown on Mathpix page breaks so source-page navigation
   and rendered Markdown stay aligned.
6. Deletes the remote Mathpix upload after exports and preview images are stored,
   unless deletion is explicitly disabled.

When DOCX was selected and produced, `document.docx` is a byte-for-byte copy of
`mathpix/document.docx`, the file Mathpix returned. New jobs do not create a
rebuilt DOCX.

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

Then set the invite codes, which are what let anyone create an account — one
per teammate, comma-separated:

```bash
PDF2DOCX_INVITE_CODES=15737,28903,77883
```

Generate them with:

```bash
python3 -c "import secrets; print(','.join(str(secrets.randbelow(90000)+10000) for _ in range(3)))"
```

Giving each person their own makes it easy to retire a code after it has been
used without disturbing the other invitations. Codes are checked only during
registration; removing one does not disable an account that already exists.
Leaving the list empty closes sign-ups rather than opening them. That default is
deliberate: Mathpix bills per page, so an open sign-up form on a public URL is an
open wallet.

Codes this short are only safe because wrong ones are rate limited — ten per
hour per caller. Without that, five digits is a hundred thousand guesses and a
keypad someone can walk end to end in an afternoon. Use longer codes if you ever
need more than a handful.

Run the server:

```bash
.venv/bin/uvicorn app.main:app --reload --port 8000
```

`PDF2DOCX_COOKIE_SECURE` defaults to `auto`, which reads the scheme off the
request — plain HTTP locally, TLS in front of a deployment — so neither case
needs it set. Force it with `on` or `off` only if something in front of the app
misreports the scheme.

Open <http://localhost:8000>. You will be sent to the sign-in page — create an
account with the invite code, then choose a PDF, select its output formats, and
press **Convert PDF**. Only PDFs are accepted.

## Accounts

One way in: an email and a password. A session is a random token whose hash is
stored in the database, so signing out actually revokes it, and no token ever
reaches the browser — the cookie is `HttpOnly` and JavaScript never sees it.
Passwords are hashed with `hashlib.scrypt`. No dependency is involved; this is
all standard library.

Identity used to be held in Supabase, with Google OAuth as a fallback, and both
have been removed. The reason is worth recording, because it was not about
Supabase working badly. It worked fine — and that was the problem. Supabase
survived a redeploy while the local `users` rows did not, so after every deploy
the password was accepted and the account behind it had vanished, and the user
was asked for an invite code as though they had never been here. One store, on
the volume, cannot disagree with itself that way.

Everything behind the sign-in page is per account. You see your own uploads,
your own conversions, and your own history; another account's job id returns
`404`, not `403`, because a job id is short enough to be worth guessing and a
`403` would confirm it exists.

A code is checked only when an account is created. Withdrawing one, or clearing
the list entirely once everyone has signed up, never signs anyone out — so the
tightest steady state is to empty `PDF2DOCX_INVITE_CODES` once your team is in.
The sign-up tab then disappears from the sign-in page.

There is no password reset (that needs an email provider), no
account-deletion command, and no admin UI. Accounts and sessions live in
`pdf2docx.db` in `PDF2DOCX_DATA_DIR`, which must be a mounted volume. Do not remove an account with a bare SQLite `DELETE`: an
external client may not enable foreign keys, and database cascades cannot remove
the account's job directories. Account removal therefore needs a maintenance
operation that deletes both that user's rows and the job directories named by
their records.

Two things are rate limited, both in memory: sign-ins, counted per email address
over a quarter hour, and wrong invite codes, counted per caller over an hour.
The sign-in limit is a ceiling on work rather than a lockout — the password is
checked before the limit is consulted, so a correct one always gets in and
resets the count. Typing it wrong a few times never costs you the right one.

## Exports

DOCX is selected by default in the browser but can be removed. Each conversion
stores its own exact selection. `PDF2DOCX_MATHPIX_FORMATS` remains the default
for older API clients that omit the `formats` field; a blank configured default
requests every supported export:

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

History survives server restarts and belongs to the account that created it.
Finished downloads remain available until the entry is deleted or evicted by
`PDF2DOCX_HISTORY_LIMIT`, which is counted per account rather than globally — so
one person's busy week cannot push another's documents off the end.

On the first registration after upgrading a pre-account installation, records
from `history.json` are assigned to that account and imported into the database.
The JSON file is retained as a recovery copy.

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
| `PDF2DOCX_MATHPIX_OPTIONS` | `{}` | Options passed through to Mathpix; a job's exact selected formats take precedence. |
| `PDF2DOCX_MATHPIX_FORMATS` | blank | Default for API clients that omit `formats`. Blank requests all supported optional exports. |
| `PDF2DOCX_MATHPIX_IMPROVE` | `off` | Opt in to Mathpix model-improvement retention. |
| `PDF2DOCX_MATHPIX_DELETE` | `on` | Delete the remote upload after local collection. |
| `PDF2DOCX_MATHPIX_CONNECT_TIMEOUT` | `10` | Connection timeout per API request, in seconds. |
| `PDF2DOCX_MATHPIX_REQUEST_TIMEOUT` | `120` | Response timeout per API request, in seconds. |
| `PDF2DOCX_MATHPIX_POLL_INTERVAL` | `2` | Status/export polling interval, in seconds. |
| `PDF2DOCX_MATHPIX_POLL_TIMEOUT` | `1800` | Whole-document polling deadline, in seconds. |
| `PDF2DOCX_MATHPIX_PAGE_RATE` | `0.0015` | Per-page estimate stored on the job; it is not a provider-reported charge. |
| `PDF2DOCX_DPI` / `PDF2DOCX_MAX_EDGE` | `180` / `2000` | Local source-preview rendering limits. |
| `PDF2DOCX_MAX_PAGES` | `0` | Maximum pages processed; `0` is unlimited. |
| `PDF2DOCX_DATA_DIR` | `~/.pdf2docx` | Source, export, preview, and the `pdf2docx.db` accounts/history database. |
| `PDF2DOCX_HISTORY_LIMIT` | `100` | Retained jobs per account; `0` keeps all. |
| `PDF2DOCX_INVITE_CODES` | blank | Comma-separated; any one creates an account. Blank closes sign-ups. |
| `PDF2DOCX_SESSION_DAYS` | `30` | How long a sign-in lasts. |
| `PDF2DOCX_COOKIE_SECURE` | `on` | Set `off` only for local development over plain HTTP. |
| `PDF2DOCX_MAX_UPLOAD_MB` | `50` | Largest accepted PDF; `0` is unlimited. |

`PDF2DOCX_LAYOUT`, `PDF2DOCX_MODEL`, `PDF2DOCX_COLUMNS`, and OpenRouter settings
remain in the legacy implementation for rollback and direct internal use, but
the web UI ignores them. The API continues accepting the old `model`, `layout`,
and `columns` form fields for client compatibility: model and columns are
ignored, omitted or `mathpix` layout is accepted, and every explicit non-Mathpix
layout is rejected.

## HTTP API

Conversion, history, and account identity routes require a session cookie and
return `401` without one. `/healthz`, `/login`, `/api/auth/config`,
`/api/auth/signup`, `/api/auth/login`, and the idempotent `/api/auth/logout`
route are reachable signed out.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/auth/signup` | Create an account: `{email, password, invite_code}`. Rate limited per caller. |
| `POST` | `/api/auth/login` | Sign in: `{email, password}`. |
| `POST` | `/api/auth/logout` | Revoke the current session. |
| `GET` | `/api/auth/me` | The signed-in account. |
| `GET` | `/api/auth/config` | Whether sign-ups are open. |
| `GET` | `/healthz` | Liveness check, for the platform. |
| `GET` | `/api/config` | Effective Mathpix/web settings without secret values. |
| `POST` | `/api/convert` | Stage a multipart PDF; optional `start=true` starts it immediately. |
| `POST` | `/api/jobs/{id}/start` | Start or rerun with Mathpix. Optional CSV `formats`; empty requests preview-only, omitted uses the configured default. |
| `GET` | `/api/jobs/{id}` | Status, progress, available exports, and cost estimate. |
| `GET` | `/api/history` | Stored jobs, newest first. |
| `GET` | `/api/jobs/{id}/detection` | Page dimensions and page-aligned Markdown; Mathpix blocks are empty. |
| `GET` | `/api/jobs/{id}/page/{number}.png` | Locally rendered source page. |
| `GET` | `/api/jobs/{id}/asset/{path}` | A locally stored preview image. |
| `GET` | `/api/jobs/{id}/download?format=docx` | Mathpix DOCX when it was selected and produced. |
| `GET` | `/api/jobs/{id}/download?format=mathpix-{ext}` | An available untouched Mathpix export. |
| `DELETE` | `/api/jobs/{id}` | Delete one local job and all of its files. |
| `DELETE` | `/api/history` | Delete every one of your jobs that is not running. |

Example:

```bash
curl -c jar -X POST http://localhost:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"..."}'

curl -b jar -F file=@paper.pdf -F start=true http://localhost:8000/api/convert
curl -b jar http://localhost:8000/api/jobs/JOB_ID
curl -b jar -OJ 'http://localhost:8000/api/jobs/JOB_ID/download?format=docx'
```

## Deploying to Railway

`Dockerfile` and `railway.toml` are the whole configuration. In the Railway
service:

1. **Attach a volume mounted at `/data`.** The container filesystem is wiped on
   every redeploy; the volume is where job directories and `pdf2docx.db` live.
   Without it, every account and every conversion disappears on the next deploy.
2. **Keep it at one replica.** A volume attaches to a single instance, and the
   job registry lives in that process's memory, so a second replica would serve
   a different history and could not reach the first one's files.
3. **Set the variables:** `MATHPIX_APP_KEY`, `MATHPIX_APP_ID`,
   `PDF2DOCX_DATA_DIR=/data`, and `PDF2DOCX_INVITE_CODES`. `PDF2DOCX_DATA_DIR`
   is already `/data` in the image, so it only needs setting if you mount the
   volume elsewhere.

The healthcheck is `/healthz`, which needs no session. It also reports whether
the data directory is ephemeral:

```json
{"ok": true, "storage": {"data_dir": "/data", "ephemeral": false, "mount": true}}
```

`"ephemeral": true` means step 1 was missed and the volume is not attached.
Nothing will look wrong until the next deploy, which will then delete every
account and every conversion — so it is worth reading this once after the first
deploy. The same warning is logged at boot.

A redeploy kills any conversion that is running at the time. That is handled:
the interrupted job is marked as an error on the next boot and can be rerun from
history, and its partial output is cleaned up rather than promoted.

## Tests

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
```

Tests use generated PDFs and fake Mathpix clients; they require no network or
API credentials.
