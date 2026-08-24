# pdf2docx — paper export workspace

Upload one PDF, select the exact Mathpix outputs you need, follow conversion
progress, and inspect each source page beside Mathpix's rendered Markdown.

The web workflow is Mathpix-only. It does not select or initialise an
OpenRouter model, and it does not rebuild Mathpix's DOCX locally.

## What the application does

1. Stores the uploaded PDF in the local job directory and reads its page count.
2. Uploads the PDF to the Mathpix Files API with page breaks and the outputs
   selected for that job.
3. Saves every returned export byte-for-byte under `mathpix/`, then writes a
   copy of the DOCX fitted to its own measure as `document.docx`.
4. Downloads images referenced by Mathpix Markdown into `mathpix/images/` and
   rewrites only the local preview Markdown to those local paths.
5. Splits the preview Markdown on Mathpix page breaks so source-page navigation
   and rendered Markdown stay aligned.
6. Deletes the remote Mathpix upload after exports and preview images are stored,
   unless deletion is explicitly disabled.

When DOCX was selected and produced, `mathpix/document.docx` is the file Mathpix
returned, byte for byte, and `document.docx` is that same file fitted to the
measure it is laid out in. New jobs do not create a rebuilt DOCX; the fitted copy
is Mathpix's own document with three geometry defects corrected and nothing else
touched.

## Fitting the DOCX

Mathpix extracts well and states its geometry absolutely, which only reads
correctly at the one measure it assumed — 6.00in, from a US Letter page with
1.25in side margins. Three things follow from that, all of them measurable in a
returned file:

- **Every image is sized at its crop's pixel count over 96 DPI.** Mathpix
  discards how large the figure was on the source page and re-derives a size
  from the crop resolution, which is not 96 DPI — it is whatever Mathpix
  rendered the page at. A figure occupying 1.8in of a two-column paper is
  cropped at ~420px and arrives 4.4in wide.
- **Every table is pinned to an absolute grid** summing to exactly the content
  width, with no `w:tblW`, no `w:tcW` and no `w:tblLayout`. Word therefore has no
  declared width to honour, falls back to autofit, and recomputes the columns
  from cell content — which is what a table "breaking" looks like.
- **No display equation carries a break opportunity.** `m:brkBin` is set and
  `m:wrapIndent` is a full inch, but there is not one `m:brk` in the document, so
  Word has nowhere to wrap a long equation.

Each is the same mistake — an absolute number where a relative one belongs — and
each stops being survivable the moment the document is narrowed, which is why
putting a Mathpix DOCX into two columns breaks images, tables and equations at
once rather than breaking something new.

`app/docx_fit.py` re-expresses that geometry in units that survive a resize. It
changes no text, no maths, no table content, no reading order and not one image
crop. Image sizes are *restored rather than guessed*: `lines.json` reports each
rendered page's pixel width, the PDF reports the same page in points, and the
ratio is the resolution every crop was taken at — so a crop of *n* pixels is
genuinely `n / dpi` inches wide. A document that arrives without usable geometry
has oversized images capped at the measure instead. Tables are restated as
percentages, and long top-level relations in flat equations are given break
points; equations Mathpix encodes as OMML matrices are left alone, because a
matrix is an unbreakable box in Word and a break inside one would do nothing.

Two further repairs have nothing to do with the measure. Every maths argument
Mathpix leaves without a left-hand side — an empty `<m:e/>`, the unused half of a
one-sided script, a matrix row of padding, or a row opening on `=` because its
left side is on the row above — is given a zero-width space. Word supplies that
operand itself and draws nothing; LibreOffice draws its missing-operand
placeholder instead, which is why a chapter of worked examples reads with an
inverted question mark on every other line. On a 41-page textbook that removes
225 of 269 such marks; the rest are LibreOffice's own rendering of `|…|`
delimiters and are correct in Word either way.

### Matching the source page's columns

A two-column book converts to a single-column DOCX at a measure it was never set
in, and narrowing it by hand is what breaks images, tables and equations at once.
The **page layout** toggle produces the columns instead of leaving them to be
added afterwards. With it on, the section is restated as the page the source was
actually laid out on — size, margins, column count and gutter, all read out of
`lines.json` — and everything else is then fitted to that column, because the
measure is read back out of the section rather than passed around. Display
equations and paragraphs are left-aligned so the narrower column is used.

At the source's own column width essentially nothing overflows, because nothing
overflowed in the book: on the same textbook, 53 of 53 figures fit a column and 2
of 559 equations exceed it. Wide tables are fitted *to* the column rather than
spanned across both, which stops the breakage but leaves a full-width table
cramped. Sources whose geometry cannot be read confidently — too few lines, pages
that disagree about their column count, overlapping columns — stay single-column
rather than being laid out on a guess.

What each job changed is recorded under `fit` in `mathpix/metadata.json`,
including the column count and page size when one was applied. Set
`PDF2DOCX_FIT_DOCX=off` to download exactly what Mathpix returned.

Changing your mind about columns does not mean paying for the document again:
`POST /api/jobs/{id}/refit` rebuilds the delivered DOCX from the exports the job
already has and reaches no network.

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
the volume, cannot disagree with itself that way. See
[Deploying to Railway](#deploying-to-railway) for what actually keeps it.

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
`pdf2docx.db` in `PDF2DOCX_DATA_DIR`, which must be a mounted volume — the
application refuses to start on Railway without one. Do not remove an account with a bare SQLite `DELETE`: an
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

Historical jobs created before the move to Mathpix remain visible, and the
files they still have remain downloadable — including a `rebuilt.docx`, which
nothing produces now but the compatibility download route still serves.
Rerunning any historical job uses Mathpix and requires `MATHPIX_APP_KEY`.

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
| `PDF2DOCX_DATA_DIR` | volume, else `~/.pdf2docx` | Source, export, preview, and the `pdf2docx.db` accounts/history database. Leave unset: on Railway the path comes from `RAILWAY_VOLUME_MOUNT_PATH`. |
| `PDF2DOCX_HISTORY_LIMIT` | `100` | Retained jobs per account; `0` keeps all. |
| `PDF2DOCX_INVITE_CODES` | blank | Comma-separated; any one creates an account. Blank closes sign-ups. |
| `PDF2DOCX_SESSION_DAYS` | `30` | How long a sign-in lasts. |
| `PDF2DOCX_COOKIE_SECURE` | `auto` | Reads the scheme off the request. `on`/`off` force it either way. |
| `PDF2DOCX_MAX_UPLOAD_MB` | `50` | Largest accepted PDF; `0` is unlimited. |

The API still accepts the old `model`, `layout`, and `columns` form fields so
an older client keeps working. `model` and `columns` are ignored; an omitted or
`mathpix` layout is accepted, and every explicit non-Mathpix layout is rejected
rather than silently reinterpreted. `PDF2DOCX_LAYOUT`, `PDF2DOCX_MODEL`,
`PDF2DOCX_COLUMNS` and the OpenRouter settings no longer exist — the backends
they configured were removed once Mathpix became the only one.

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
| `POST` | `/api/jobs/{id}/start` | Start or rerun with Mathpix. Optional CSV `formats`; empty requests preview-only, omitted uses the configured default. Optional `multi_column` lays the document out in the source page's columns. |
| `POST` | `/api/jobs/{id}/refit` | Rebuild the delivered DOCX from the job's stored exports, optionally with `multi_column`. No Mathpix call and no charge. |
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

`Dockerfile` and `railway.toml` are the whole configuration. One thing matters
more than the rest:

### The volume

**Attach a volume mounted at `/data`:**

```bash
railway volume add -m /data
```

The container filesystem is rebuilt on every deploy. The volume is separate
storage that is re-attached to the new container, and it is where job
directories and `pdf2docx.db` live. Without it, every account, every session and
every converted document is destroyed by the next deploy.

This used to be a step you could miss, and missing it was invisible until the
deploy that emptied the disk. Two changes make that impossible now:

- **The path is read from the volume, not configured separately.** Railway sets
  `RAILWAY_VOLUME_MOUNT_PATH` only when a volume is genuinely attached, and that
  is where the application reads it from. `PDF2DOCX_DATA_DIR` should stay unset;
  the location and the proof that it is durable are one fact, so there is no
  second value to disagree with it.
- **A deployment with no volume refuses to start.** The container exits, the
  healthcheck goes unanswered, Railway marks the deploy failed — and keeps the
  previous deployment serving. A failed deploy costs nothing; a successful one
  writing to disposable storage costs every account.

`.railway/railway.ts.example` declares the volume in code, so the mount path and
size can be reviewed in a pull request rather than remembered. Read its header
before adopting it: Railway's IaC manages the whole project, so the service
names have to match what is already deployed.

**Turn on daily volume backups.** A volume survives redeploys; it does not
survive being wiped or deleted. Backups are the only cover for that, and they
are a dashboard setting.

### The rest

1. **Keep it at one replica.** A volume attaches to a single instance, and the
   job registry lives in that process's memory, so a second replica would serve
   a different history and could not reach the first one's files.
2. **Set the variables:** `MATHPIX_APP_KEY`, `MATHPIX_APP_ID`, and
   `PDF2DOCX_INVITE_CODES`. Leave `PDF2DOCX_DATA_DIR` unset.

The healthcheck is `/healthz`, which needs no session. It reports where the data
lives and how much room is left:

```json
{"ok": true, "storage": {
  "data_dir": "/data", "volume": "pdf2docx-data", "mount_path": "/data",
  "free_bytes": 10737418240, "ephemeral": false, "mount": true}}
```

A missing volume can no longer get this far on Railway. `free_bytes` is the one
to watch instead: `PDF2DOCX_HISTORY_LIMIT` bounds jobs per account, not bytes,
and a full volume is the next way conversions start failing.

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
