# pdf2docx — PDF → Word, page for page

Rebuilds a PDF as a `.docx` that looks like the original: the same fonts, sizes,
colours, images, diagrams, tables and positions, page for page — with **native Word
equations** you can click into and edit.

```
     ┌─ digital page ─▶ PyMuPDF ─▶ text spans, images, vectors, rules ┐
PDF ─┼─ equation crop ─▶ vision (OpenRouter) ─────────────────────────┼─▶ .docx
     ├─ scanned page ──▶ vision (OpenRouter) ─────────────────────────┤
     ├─ marker mode ───▶ marker-pdf sidecar, kept as it comes ────────┤
     └─ mathpix mode ──▶ Mathpix Files API ──▶ its own .docx ──────────┘
```

Five output modes:

| Mode | What you get |
|---|---|
| **`structured`** (default) | The PDF's own content, rebuilt as ordinary Word structure: flowing paragraphs, headings, native equations, real tables and pictures. Editable; the page is not a facsimile. |
| **`replica`** | Every element becomes a floating frame at its exact PDF coordinates. Looks like the original; blocks do not reflow when you edit them. |
| **`flow`** | The model reads each page and rewrites it as ordinary flowing Word content. Fully editable; positions and fonts are not preserved. |
| **`marker`** | The whole PDF is converted locally by [marker-pdf](https://github.com/datalab-to/marker) and what it returns is written as it comes. Fully local, costs nothing, and its own Markdown is kept beside the document so you can see marker's quality rather than this codebase's reading of it. |
| **`mathpix`** | The whole PDF is converted by the [Mathpix Files API](https://docs.mathpix.com/guides/files-api-overview), which returns its own Word file with native equations already in it. **That file is the download** — this codebase does not build it — along with every other format Mathpix renders. Paid, remote, and the document leaves your machine. |

## How faithful is "replica"?

Measured by converting a LaTeX paper, rendering the `.docx` back to PDF and comparing
word positions against the source:

```
mean horizontal drift   0.11 pt      max 0.95 pt
mean vertical drift     0.73 pt
page size               exact
```

Getting there means not throwing the PDF away and re-imagining it:

- **Text** keeps its font, size, colour, weight and slant, and each line is placed at
  its own coordinates. Because the reader rarely has the document's original font, each
  run is measured and its character spacing adjusted so a substitute font still occupies
  the original width — otherwise every word after the first walks out of place.
- **Images** are lifted out of the PDF and re-embedded at their exact rectangles.
- **Diagrams and charts** — vector artwork, including the glyph-drawn kind LaTeX's
  `picture` environment produces — are rasterised at 300 DPI on a transparent
  background and placed at their exact rectangles.
- **Rules** (table ruling, underlines, borders, fraction bars) are re-drawn as Word
  shapes rather than pictures, so they never sit on top of the text.
- **Tables** become real Word tables when the grid is detected reliably. When it is not
  — a table ruled only at its outer edge resolves as one giant cell — the detection is
  *discarded* rather than trusted, and the cells fall back to positioned text plus
  ruling, which still looks identical.
- **Equations** are found by their maths fonts, cropped, and read back as LaTeX by the
  vision model, then written as native Word equations at their original position.
  Anything the model cannot read stays as the pixel-exact crop, so nothing is lost.
- **Scanned pages** (no text layer) are read by the vision model, and what happens to
  the transcription depends on the mode. In `replica` the page keeps its exact image
  with the transcription as a plain-text layer behind it — the page looks untouched and
  the text is still selectable and searchable. In `structured` the page is *rebuilt*
  from the transcription: its headings, emphasis, lists, tables and equations become
  real Word structure, and each figure is cut out of the scan at diagram resolution and
  placed back in the text where it was, with its caption underneath.

## Why the equations are the interesting part

Most PDF converters either flatten equations to images or mangle them into text.
This one transcribes them as LaTeX and then compiles that LaTeX to **OMML**
(Office Math Markup Language) — the same format Word's own equation editor uses.
`$\frac{\partial u}{\partial t} = \alpha \frac{\partial^2 u}{\partial x^2}$` arrives
in Word as a real equation object.

Supported constructs: fractions and binomials, super/subscripts, radicals with
degrees, big operators with limits (∑ ∏ ∫ ∮ ⋃ …), `\lim`/`\max`/`\sup` with
under-scripts, auto-sizing delimiters (`\left(…\right)`), accents, over/underlines,
over/underbraces, matrices (`pmatrix`, `bmatrix`, `vmatrix`, …), `cases`, `aligned`,
upright text (`\text`, `\mathrm`), bold (`\mathbf`), the Greek alphabet, and the
usual operator/relation/arrow vocabulary. Anything unrecognised degrades to visible
upright text, so content is never silently dropped.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env
# then put your key in .env — create one at https://openrouter.ai/keys
```


### Optional marker-pdf sidecar

The `marker` output mode converts the whole PDF locally with
[marker-pdf](https://github.com/datalab-to/marker), which does layout, OCR,
tables and mathematics in one pass. It runs in its own environment and its own
process, because torch and the Surya VLM stack have no business in the
application's environment. Set it up once with
[the marker sidecar guide](sidecar/README-marker.md), then:

```bash
sidecar/start-marker.sh          # port 8011; carries the working backend settings
curl --fail http://127.0.0.1:8011/health
```

and pick **marker** in the output menu. No API key is involved and nothing is
billed. Measured here on an RTX 5060: four digital pages in about a second, three
scanned pages — OCR, mathematics and figure extraction — in about fourteen.

The mode exists to show marker's own work, so almost nothing is done to what it
returns:

- marker's output is written to `marker/document.md` in the job directory **byte
  for byte**, along with its images under their own filenames and a
  `marker/metadata.json` recording the effective configuration.
- The `document.md` the `.docx` is built from differs from that file in exactly
  one respect: image references are given a directory prefix so the writers can
  resolve them. `diff` the two and that is all you will find. The count is in
  `metadata.json` under `applied`.
- Pages are split on marker's own separator, so each source page still becomes
  its own Word page.
- None of the figure-locating, box-repairing, mark-up-stripping or
  structure-recovering work the other modes do runs here. It exists to fix up
  vision-model output, and it would hide what marker actually did.

Everything marker can be told to do is reachable without touching the code:
`PDF2DOCX_MARKER_OPTIONS` is passed straight to marker's own configuration
parser, which is the same one its CLI builds its flags into.

```bash
PDF2DOCX_MARKER_OPTIONS='{"mode":"fast","force_ocr":true,"format_lines":true}'
```

marker-pdf's code is Apache-2.0, but its **model weights carry a modified AI Pubs
Open Rail-M licence with a revenue-based restriction on commercial use** — get
legal review before relying on this mode commercially. Note also that its Surya
server wants most of an 8 GB GPU to itself; where it will not fit, `TORCH_DEVICE=cpu`
works and is much slower.

### Mathpix

The `mathpix` output mode hands the whole PDF to the [Mathpix Files
API](https://docs.mathpix.com/guides/files-api-overview). Get credentials from
the [Mathpix console](https://console.mathpix.com/) and set them under the same
names Mathpix's own client reads, so an existing setup already works:

```bash
MATHPIX_APP_ID=...
MATHPIX_APP_KEY=...
```

Then pick **Mathpix** in the output menu. No OpenRouter key is involved.

This is the only backend here that is a paid remote service — Mathpix list
around **$1.50 per 1,000 pages**, and **the document is uploaded to them**. Two
defaults follow from that: retention is off (`improve_mathpix` is sent as
`false` unless `PDF2DOCX_MATHPIX_IMPROVE=on`), and the job deletes what it
uploaded once the results are downloaded (`PDF2DOCX_MATHPIX_DELETE=off` to keep
it). The cost the UI reports is an **estimate** derived from the page count,
shown as unpriced, because Mathpix bills per page rather than per token and the
API does not report a charge.

Like `marker`, the mode exists to show Mathpix's own work:

- **`document.docx` is Mathpix's file, copied byte for byte.** Nothing here
  builds it. `rebuilt.docx` beside it *is* this codebase's render of the same
  Markdown, so the two can be compared — that comparison is the point.
- Every format Mathpix returned is written to `mathpix/` before anything reads
  it, with a `mathpix/metadata.json` recording the options sent, which formats
  arrived, and why any are missing.
- Exactly two edits are made to Mathpix's Markdown, both counted in that file:
  its CDN-hosted crops are downloaded into the job so the preview and the
  writers can resolve them, and the document is split on Mathpix's own
  `\pagebreak` so each source page gets its own Word page.
- **The maths is not translated.** `math_inline_delimiters` is set at request
  time, so Mathpix emits the `$…$` this codebase already reads instead of its
  default `\(…\)`. Nothing rewrites it afterwards.
- The page viewer draws Mathpix's own line geometry, read from `lines.json` —
  which costs nothing extra, since Mathpix produces it whether or not it is
  asked for.

**Every format Mathpix offers is requested and downloadable**, because Mathpix
converts the document once and renders each format from that same job:

| | |
|---|---|
| Documents | `docx`, `pptx`, `xlsx` (tables only) |
| Markup | `md`, `mmd`, `html`, `tex.zip`, `md.zip`, `mmd.zip`, `html.zip` |
| Rendered | `pdf` (HTML pipeline), `latex.pdf` (LaTeX pipeline) |
| Data | `lines.json`, `lines.mmd.json` |

`mmd`, `lines.json` and `lines.mmd.json` arrive whether asked for or not. A
format Mathpix does not produce for a given document — there is no `.xlsx`
without tables — is simply absent, recorded with its reason, and its button is
not drawn. Narrow the list with `PDF2DOCX_MATHPIX_FORMATS` if you would rather
not ask for all of them; `docx` is always included.

Everything Mathpix can be told to do is reachable without touching the code:
`PDF2DOCX_MATHPIX_OPTIONS` is passed straight through, and wins over the
defaults above.

```bash
PDF2DOCX_MATHPIX_OPTIONS='{"idiomatic_eqn_arrays":true,"enable_tables_fallback":true}'
```

## Run

```bash
.venv/bin/uvicorn app.main:app --reload --port 8000
```

Open <http://localhost:8000>, drop in a PDF, pick a model, press **Start OCR**, download
the `.docx`. Uploading only stages the file — nothing is sent to the model until you
press start, so you can swap models (or change your mind) first.

## Tests

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
```

No network and no API key: the fixtures are PDFs built on the fly at a known
resolution, so what a crop should measure is arithmetic rather than a judgement.

## History and cost

Every conversion is recorded locally, so the list of what you have converted — and what
it cost — survives a server restart:

- **Cost per PDF.** OpenRouter reports the amount actually billed for each page; the app
  sums them and shows a running total while the conversion is in flight, then the final
  figure per document. A provider that reports no price shows `—` rather than a guess.
- **Downloads stay live.** Finished `.docx` and `.md` files are re-downloadable from the
  history list later, not just in the session that produced them.
- **Re-run.** Starting a stored job again re-transcribes it, resetting its cost — handy
  for retrying a failure or comparing models on the same document.

History lives in `PDF2DOCX_DATA_DIR` (default `~/.pdf2docx`): a `history.json` index
alongside one folder per job holding the source PDF and its output. Deleting an entry in
the UI deletes its folder too, and `PDF2DOCX_HISTORY_LIMIT` evicts the oldest entries so
the directory cannot grow without bound.

## Configuration

All settings live in `.env` (see `.env.example`).

| Variable | Default | Notes |
|---|---|---|
| `OPENROUTER_API_KEY` | — | Required by `structured`, `replica` and `flow`. Not used by `marker` (local) or `mathpix` (its own credentials). |
| `MATHPIX_APP_ID` / `MATHPIX_APP_KEY` | — | Required by the `mathpix` mode, and used by nothing else. |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | Change only if proxying. |
| `OPENROUTER_APP_URL` / `_APP_TITLE` | localhost / PDF to DOCX | Attribution headers OpenRouter uses for its leaderboards. |
| `PDF2DOCX_MARKER_URL` | `http://127.0.0.1:8011` | marker sidecar base URL, used only by the `marker` mode. |
| `PDF2DOCX_MARKER_CONNECT_TIMEOUT` / `_REQUEST_TIMEOUT` | `2` / `900` | Connection and inference timeouts in seconds. The request timeout covers a whole document, not one page. |
| `PDF2DOCX_MARKER_OPTIONS` | `{}` | JSON object passed straight through to marker's own configuration — anything its CLI accepts, including options added upstream after this was written. Malformed JSON is ignored. |
| `PDF2DOCX_MATHPIX_OPTIONS` | `{}` | JSON object passed straight through to Mathpix's own options — anything `POST /v3/pdf` documents, including options added upstream after this was written. Malformed JSON is ignored. |
| `PDF2DOCX_MATHPIX_FORMATS` | — | Which exports to request, comma separated. Blank means all of them; `docx` is always included. |
| `PDF2DOCX_MATHPIX_IMPROVE` | `off` | Let Mathpix retain the document to improve their models. |
| `PDF2DOCX_MATHPIX_DELETE` | `on` | Delete the upload from Mathpix's storage once its results are downloaded. |
| `PDF2DOCX_MARKER_EXTRA_FORMATS` | *(blank)* | Extra renderers (`html`, `json`, `chunks`) to run purely so their output can be read. Each is a second conversion of the same PDF. |
| `PDF2DOCX_LAYOUT` | `structured` | `structured` rebuilds the content as editable Word structure; `replica` reproduces the page exactly; `flow` rewrites it as ordinary Word content; `marker` converts it locally with marker-pdf; `mathpix` converts it with the Mathpix Files API and returns Mathpix's own file. Also selectable per conversion in the UI. |
| `PDF2DOCX_COLUMNS` | `auto` | `auto` (the UI's **multi-column**) sets each page of the output in as many columns as the source page was set in; `off` (the UI's **natural**) keeps one column throughout; a number forces that many. Applies to the flowing modes (`flow`, `marker`, `mathpix`) — the replica modes reproduce the page's geometry already. Also selectable per conversion in the UI, which overrides this. |
| `PDF2DOCX_MATH` | `auto` | `auto` reads equations back as native Word equations; `off` leaves them as pixel-exact images and makes no model calls. |
| `PDF2DOCX_FONT_MAP` | `on` | Map PDF fonts to Times New Roman / Arial / Courier New. `off` keeps the document's own font names — only useful if the reader has them installed. |
| `PDF2DOCX_DIAGRAM_DPI` / `PDF2DOCX_MATH_DPI` | `300` / `320` | Resolution for rasterised diagrams and equation crops. |
| `PDF2DOCX_CROP_NATIVE` | `on` | Cap a figure cut out of a scan at the resolution the scan itself holds. A 90 DPI page has no 300 DPI detail to give, and rendering it at 300 enlarges only its grain. Vector artwork has no ceiling and is always cut at `DIAGRAM_DPI`; `off` cuts everything at `DIAGRAM_DPI`. |
| `PDF2DOCX_LOCATE` | `on` | Ask a second time where a scanned page's figures are, reading their boxes off a coordinate grid ruled over the page. Costs one extra request per page that has figures. `off` trusts the boxes the transcription reported. |
| `PDF2DOCX_FIGURE_MODEL` | *(blank)* | Model for that locating pass. Blank uses `PDF2DOCX_MODEL`. Reading a box off a ruled line is a different skill from transcribing, so a small transcription model can be paired with a capable one here. |
| `PDF2DOCX_MODEL` | `google/gemini-3.6-flash` | Any explicit vision-capable non-Anthropic model id. The picker excludes Claude and dynamic router aliases. Disallowed environment values fall back to this default; per-conversion API overrides are rejected, so no conversion path can call Claude. |
| `PDF2DOCX_REASONING_EFFORT` | *(unset)* | `low`/`medium`/`high`, passed through to reasoning-capable models. Omitted entirely when blank. |
| `PDF2DOCX_MAX_TOKENS` | `16000` | Output budget per page. |
| `PDF2DOCX_DPI` / `PDF2DOCX_MAX_EDGE` | `180` / `2000` | Higher DPI reads small equations better and costs more image tokens; `MAX_EDGE` caps the long edge in pixels. |
| `PDF2DOCX_CONCURRENCY` | `4` | Pages transcribed in parallel. |
| `PDF2DOCX_MAX_PAGES` | `0` | `0` = unlimited. |
| `PDF2DOCX_DATA_DIR` | `~/.pdf2docx` | Where conversion history and finished documents are kept. |
| `PDF2DOCX_HISTORY_LIMIT` | `100` | Conversions to retain; the oldest are deleted with their files. `0` = keep everything. |

Model choice matters a lot for equation-heavy scans — if output quality
disappoints, try a stronger model before tuning anything else.

## What survives the round trip

In **`flow`** mode the page is rewritten from the model's Markdown, so what survives is
what Markdown can express: headings · bold / italic / strikethrough · inline code ·
fenced code blocks · bulleted and numbered lists (up to three nesting levels) · pipe
tables with column alignment · block quotes · figure captions · horizontal rules ·
links · inline and display equations · figures. Fonts, colours and positions are not
preserved. A figure is located by the model, cut out of the page at diagram resolution
and placed inline with its caption beneath it; where the model cannot say where the
figure is, the caption is kept on its own. That the rest is not preserved is a
deliberate consequence of the read-and-rewrite approach: the output is editable text,
not a facsimile.

In **`replica`** mode the page is rebuilt from the PDF's own contents, so fonts, sizes,
colours, images, diagrams, rules, tables, equations and coordinates all survive — see
[How faithful is "replica"?](#how-faithful-is-replica) above. Each source page becomes
its own Word page in both modes.

In **`marker`** mode what survives is whatever marker chose to emit, rendered through the
same Markdown writer `flow` uses: headings, emphasis, lists, tables, code, figures and
`$$`-fenced equations, which become native Word equations. Fonts, colours and coordinates
are not preserved — marker does not report them. Nothing here repairs or second-guesses
marker's output, so a defect in the document is a defect in the conversion, which is the
whole reason the mode exists.

In **`mathpix`** mode the question does not arise for the deliverable, because the
`.docx` you download is Mathpix's own file and this codebase never opens it. What the
table above describes applies instead to `rebuilt.docx` — the same Markdown writer, run
over Mathpix's Markdown, so the two renders of one document can be compared side by side.

## HTTP API

The browser UI is a thin client over these endpoints:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/config` | Effective settings. |
| `GET` | `/api/models` | Vision-capable OpenRouter models, cheapest first. |
| `POST` | `/api/convert` | Multipart `file` (+ optional `model`, `layout`, `columns`, `start`). Stages the PDF and returns a job. |
| `POST` | `/api/jobs/{id}/start` | Begin (or re-run) the conversion. Optional `model` / `layout` / `columns` override. |
| `GET` | `/api/jobs/{id}` | Status, progress, and cost so far. |
| `GET` | `/api/history` | Every stored conversion, newest first, plus the total spent. |
| `GET` | `/api/jobs/{id}/markdown` | Intermediate Markdown as JSON. Figure references are relative to the job's working directory. |
| `GET` | `/api/jobs/{id}/download?format=docx\|md\|marker-*\|mathpix-*\|rebuilt-docx` | The finished file. The `marker-*` and `mathpix-*` formats return that backend's own unedited output and are present only for jobs converted in that mode; `mathpix-{ext}` covers every export in the table above. A format this document has not got returns 409. |
| `DELETE` | `/api/jobs/{id}` | Delete the job and its working directory. |
| `DELETE` | `/api/history` | Delete every job that is not currently running. |

`columns` is `natural` (one flowing column) or `multi` (each page set in as many
columns as the source page was set in); anything else, including omitting it,
leaves `PDF2DOCX_COLUMNS` in charge. It reaches the flowing modes (`flow`,
`marker`, `mathpix`) only — the replica modes reproduce the page's geometry already. The
staged job reports `source_columns`, the most columns any of the PDF's first
pages is set in, read from the PDF at upload: where that is `1` the source has no
second column to give back, and `natural` is the only thing the output can be.

Job status is `ready` (uploaded, awaiting start) → `queued` → `rendering` →
`transcribing` → `building` → `done`, or `error`. Cost fields (`cost`, `cost_known`, `calls`,
`prompt_tokens`, `completion_tokens`) update as pages complete.
Completed jobs also expose per-page `diagnostics` with the selected `extractor`
and any `fallback_reason`; only actual remote calls contribute to cost and token
totals.

```bash
# Two-step: stage, then start.
curl -F file=@paper.pdf http://localhost:8000/api/convert
curl -X POST -F model=google/gemini-3.6-flash \
     http://localhost:8000/api/jobs/<id>/start

# Or one-shot, for scripting.
curl -F file=@paper.pdf -F model=google/gemini-3.6-flash -F start=true \
     http://localhost:8000/api/convert

curl http://localhost:8000/api/jobs/<id>
curl -OJ "http://localhost:8000/api/jobs/<id>/download?format=docx"
```

## Layout

```
app/
  main.py          FastAPI routes, job registry, background conversion
  history.py       JSON-backed record of past conversions
  pipeline.py      all four pipelines: replica/structured, flow, marker, mathpix
  marker_client.py validated boundary to the marker sidecar, and the two edits
                   made to its output (image prefixes, page splitting)
  mathpix_client.py validated boundary to the Mathpix Files API, the table of
                   every format it offers, and the two edits made to its output
                   (image downloads, page splitting)
  pdf_extract.py   PDF → positioned layout model: spans, images, artwork, equations
  docx_replica.py  layout model → .docx of absolutely positioned shapes
  pdf_render.py    PyMuPDF rasterisation with a long-edge cap
  columns.py       how many columns each source page is set in, read from the
                   PDF's own text blocks — or from the ink, when it is a scan
  vision.py        OpenRouter calls, transcription and equation prompts, per-call cost
  docx_builder.py  Markdown → python-docx (blocks, inline spans, tables)
  latex_omml.py    LaTeX → OMML compiler
  static/index.html  single-page UI

sidecar/
  marker_service.py marker-pdf sidecar (port 8011), config passed through to marker
```

## Notes and limits

- The job registry is in memory and mirrored to `history.json`. Finished work survives a
  restart, but a conversion that was *running* when the server stopped is marked as
  interrupted rather than resumed — press start again to re-run it. Fine for a local
  tool; swap in a real queue and object store to run it as a service.
- Reported cost is whatever OpenRouter billed, which includes any provider markup and
  cached-token discounts. It is not an estimate, but it is only as accurate as what the
  upstream provider reports back.
- Word's equation editor is the target, so equations render in Word and in
  LibreOffice; other readers vary.
- In `flow` mode, very wide or heavily multi-column layouts are transcribed in the
  model's chosen reading order — check those pages. `replica` mode has no reading order
  to get wrong, since it places everything by coordinate.
- `replica` output is a mosaic of floating frames. That is what makes it match the
  original, and it is also why editing behaves differently from a normal document:
  typing in one frame does not reflow its neighbours, and text does not wrap between
  frames. Use `flow` when you want to rework the prose rather than preserve the page.
- A page border or full-page background box is dropped rather than rasterised, because
  covering the page with a picture would bury the text underneath it.
- A scanned page has no text to read, so it is transcribed by the vision model.
  `replica` keeps the exact page image with a flat searchable transcription
  behind it, so the page still looks like the scan and can still be searched;
  `structured` rebuilds the transcription as ordinary Word structure instead.
- Where a figure sits on a scanned page is asked twice. The boxes that arrive with the
  transcription are its least reliable output: on the page this was built against they
  averaged 0.13 IoU against boxes measured by hand, which crops the paragraph beside the
  diagram rather than the diagram. So the page is rendered again with a numbered grid
  ruled over it and a model is asked to *read* each box off the rules, with nothing else
  to do — which is what `PDF2DOCX_LOCATE` controls. Located boxes are matched back to the
  transcription's figure lines by reading order, aligned as whole sequences so that one
  badly placed box cannot shift the rest.
- How well that second look works depends on the model, and not in the direction price
  suggests: over repeated trials on the same page `google/gemini-3.6-flash` averaged 0.55
  in one benchmark. The hardest figures are the sparse ones — a
  free-body sketch that is a few arrows and a label in a field of white, with no frame or
  axis to bound it. If your
  figures come out wrong, this is the setting to change first, via
  `PDF2DOCX_FIGURE_MODEL`. Note also that a reasoning model spends its token budget
  thinking before it answers: the request is budgeted for that, and a page's figures cost
  roughly a cent to locate. The pass reads a ruled line, so it is also sensitive to
  `PDF2DOCX_MAX_EDGE`, which bounds how large the page is sent.
- Everything below still applies to whichever box wins. Where a figure sits on a scanned
  page is the model's estimate, not a measurement. A
  box that is implausible — inside out, a sliver, or most of the page — is dropped and
  the caption is kept on its own. A box that is merely a little tight or loose is used
  as given, so a crop can carry a stray line of text or clip a far-flung label. Nothing
  downstream can recover from a box that points at the wrong part of the page, so this
  is the one place where model choice shows up as visibly wrong output rather than as
  slightly worse output — a weak model will box the equation above the diagram.
- The box is asked for on a 0–1000 grid, but models often answer in the pixels of the
  page image they were shown instead. Both readings are accepted: a coordinate past 1000
  gives the convention away, and
  the unit is then settled for the whole page at once rather than box by box, since a
  model does not switch units halfway down a page. Numbers that fit neither the grid nor
  the render are dropped rather than forced to fit. One case cannot be resolved and is
  not: a box reported in pixels that happens to lie entirely within the render's top-left
  1000×1000 is indistinguishable from a grid box, and is read as one.
- A figure crop is written under the job's working directory and referred to relative to
  it — `figures/page-0001-figure-1.png` — never by the path it happens to have on the
  machine that made it. The Markdown outlives that process: it is saved beside the
  document and served over the API, so an absolute path would be useless to anything
  that reads it elsewhere and would publish the host's directory layout to whoever
  downloads the `.md`. The writers resolve references against the working directory and
  refuse any that climb out of it, since the Markdown they are reading came back from a
  model pointed at an untrusted page.
- A figure is placed at the size the page drew it at, and cut at the resolution the page
  can actually supply. Both were once assumed rather than established: a crop was
  rendered at `DIAGRAM_DPI` and written to a PNG that PyMuPDF stamps `96` DPI whatever
  the render zoom was, so Word — which sizes a picture as its pixel count over its
  declared resolution — placed every figure `DIAGRAM_DPI / 96` times too large, with no
  extra detail in it to justify the size.
