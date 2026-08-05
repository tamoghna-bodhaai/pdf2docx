# pdf2docx — PDF → Word, page for page

Rebuilds a PDF as a `.docx` that looks like the original: the same fonts, sizes,
colours, images, diagrams, tables and positions, page for page — with **native Word
equations** you can click into and edit.

```
             ┌── text spans (font, size, colour, bbox) ─┐
PDF ─┬─ PyMuPDF ─┼── images, vector artwork, rules ─────────┼─▶ positioned .docx
     │           └── equation regions ──▶ vision model ─────┘    (python-docx)
     └─ pages with no text layer ──▶ vision model ──▶ text behind an exact page image
```

Three output modes:

| Mode | What you get |
|---|---|
| **`structured`** (default) | The PDF's own content, rebuilt as ordinary Word structure: flowing paragraphs, headings, native equations, real tables and pictures. Editable; the page is not a facsimile. |
| **`replica`** | Every element becomes a floating frame at its exact PDF coordinates. Looks like the original; blocks do not reflow when you edit them. |
| **`flow`** | The model reads each page and rewrites it as ordinary flowing Word content. Fully editable; positions and fonts are not preserved. |

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

## Run

```bash
.venv/bin/uvicorn app.main:app --reload --port 8000
```

Open <http://localhost:8000>, drop in a PDF, pick a model, press **Start OCR**, download
the `.docx`. Uploading only stages the file — nothing is sent to the model until you
press start, so you can swap models (or change your mind) first.

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
| `OPENROUTER_API_KEY` | — | **Required.** |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | Change only if proxying. |
| `OPENROUTER_APP_URL` / `_APP_TITLE` | localhost / PDF to DOCX | Attribution headers OpenRouter uses for its leaderboards. |
| `PDF2DOCX_LAYOUT` | `replica` | `replica` reproduces the page exactly; `flow` rewrites it as ordinary Word content. Also selectable per conversion in the UI. |
| `PDF2DOCX_MATH` | `auto` | `auto` reads equations back as native Word equations; `off` leaves them as pixel-exact images and makes no model calls. |
| `PDF2DOCX_FONT_MAP` | `on` | Map PDF fonts to Times New Roman / Arial / Courier New. `off` keeps the document's own font names — only useful if the reader has them installed. |
| `PDF2DOCX_DIAGRAM_DPI` / `PDF2DOCX_MATH_DPI` | `300` / `320` | Resolution for rasterised diagrams and equation crops. |
| `PDF2DOCX_MODEL` | `anthropic/claude-sonnet-5` | Any vision-capable model id. The web UI also offers a per-conversion picker, populated live from OpenRouter, so you never have to guess an id. |
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

## HTTP API

The browser UI is a thin client over these endpoints:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/config` | Effective settings. |
| `GET` | `/api/models` | Vision-capable OpenRouter models, cheapest first. |
| `POST` | `/api/convert` | Multipart `file` (+ optional `model`, `layout`, `start`). Stages the PDF and returns a job. |
| `POST` | `/api/jobs/{id}/start` | Begin (or re-run) the conversion. Optional `model` / `layout` override. |
| `GET` | `/api/jobs/{id}` | Status, progress, and cost so far. |
| `GET` | `/api/history` | Every stored conversion, newest first, plus the total spent. |
| `GET` | `/api/jobs/{id}/markdown` | Intermediate Markdown as JSON. |
| `GET` | `/api/jobs/{id}/download?format=docx\|md` | The finished file. |
| `DELETE` | `/api/jobs/{id}` | Delete the job and its working directory. |
| `DELETE` | `/api/history` | Delete every job that is not currently running. |

Job status is `ready` (uploaded, awaiting start) → `queued` → `rendering` →
`transcribing` → `building` → `done`, or `error`. Cost fields (`cost`, `cost_known`,
`prompt_tokens`, `completion_tokens`) update as pages complete.

```bash
# Two-step: stage, then start.
curl -F file=@paper.pdf http://localhost:8000/api/convert
curl -X POST -F model=anthropic/claude-sonnet-5 \
     http://localhost:8000/api/jobs/<id>/start

# Or one-shot, for scripting.
curl -F file=@paper.pdf -F model=anthropic/claude-sonnet-5 -F start=true \
     http://localhost:8000/api/convert

curl http://localhost:8000/api/jobs/<id>
curl -OJ "http://localhost:8000/api/jobs/<id>/download?format=docx"
```

## Layout

```
app/
  main.py          FastAPI routes, job registry, background conversion
  history.py       JSON-backed record of past conversions
  pipeline.py      both pipelines: replica (extract → equations → place) and flow
  pdf_extract.py   PDF → positioned layout model: spans, images, artwork, equations
  docx_replica.py  layout model → .docx of absolutely positioned shapes
  pdf_render.py    PyMuPDF rasterisation with a long-edge cap
  vision.py        OpenRouter calls, transcription and equation prompts, per-call cost
  docx_builder.py  Markdown → python-docx (blocks, inline spans, tables)
  latex_omml.py    LaTeX → OMML compiler
  static/index.html  single-page UI
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
- Scanned pages cannot be laid out line by line without OCR bounding boxes. Installing
  Tesseract would let PyMuPDF supply them (`page.get_textpage_ocr()`); without it,
  `replica` keeps the exact page image with the transcription behind, and `structured`
  rebuilds the page from the transcription's own structure rather than the scan's
  geometry — so a scanned page reflows like ordinary Word content, but the line breaks
  and column positions of the original are not reproduced.
- Where a figure sits on a scanned page is the model's estimate, not a measurement. A
  box that is implausible — inside out, a sliver, or most of the page — is dropped and
  the caption is kept on its own. A box that is merely a little tight or loose is used
  as given, so a crop can carry a stray line of text or clip a far-flung label.
