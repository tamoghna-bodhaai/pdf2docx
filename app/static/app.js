/* The page: a rail of controls, the source page with what the converter found
   drawn over it, and the conversion itself beside them. */

const $ = (id) => document.getElementById(id);

const drop = $('drop'), picker = $('picker'), hint = $('hint');
const card = $('job-card'), fileEl = $('job-file'), metaEl = $('job-meta');
const barFill = $('bar-fill'), statusEl = $('job-status');
const actions = $('actions'), startActions = $('start-actions'), runArea = $('run-area');
const startBtn = $('start'), discardBtn = $('discard'), resetBtn = $('reset');
const costBox = $('cost-box'), costValue = $('cost-value'), costNote = $('cost-note');
const historyBody = $('history-body'), historyTable = $('history-table');
const historyEmpty = $('history-empty'), historySummary = $('history-summary');
const historyActions = $('history-actions'), historyPanel = $('history-panel');
const layoutSelect = $('layout'), layoutHint = $('layout-hint');
const columnsField = $('columns-field'), columnsSelect = $('columns'), columnsHint = $('columns-hint');
const modelSelect = $('model'), modelNote = $('model-note');

const previewEmpty = $('preview-empty'), stageWrap = $('stage-wrap'), stage = $('stage');
const pageImage = $('page-image'), overlay = $('overlay'), legend = $('legend');
const pager = $('pager'), pageNumber = $('page-number'), pageTotal = $('page-total'), pageNote = $('page-note');
const outputEmpty = $('output-empty'), tabRendered = $('tab-rendered');
const tabText = $('tab-text'), tabBlocks = $('tab-blocks'), tabs = $('tabs');
const scopeBtn = $('scope'), copyBtn = $('copy');

let poll = null;
let currentJob = null;
// How many columns the uploaded PDF is set in — null until a PDF has been read.
let sourceColumns = null;

// The modes that build the document out of one linear stream of text, and so are
// the ones with a column decision left to make. The replica modes put every
// block back at the coordinate it came from, columns and all.
const FLOWING = ['flow', 'marker'];
// What PDF2DOCX_COLUMNS can say for "one column, whatever the source did". It
// speaks a wider vocabulary than these two choices — it can also force a fixed
// count — so anything else starts the page on the choice that reads the source.
const SINGLE_COLUMN = ['natural', 'off', '0', '1', 'none', 'single'];

// ---------------------------------------------------------------- config -- //

fetch('/api/config').then(r => r.json()).then(cfg => {
  hint.textContent = `via ${cfg.provider} · ${cfg.dpi} DPI · ${cfg.concurrency} pages in parallel`
    + (cfg.reasoning_effort ? ` · reasoning ${cfg.reasoning_effort}` : '');
  if (!cfg.api_key_configured) $('keywarn').classList.remove('hidden');
  if (cfg.layout) layoutSelect.value = cfg.layout;
  if (cfg.columns) columnsSelect.value = SINGLE_COLUMN.includes(cfg.columns) ? 'natural' : 'multi';
  describeLayout();
}).catch(() => { hint.textContent = 'Ready'; describeLayout(); });

const LAYOUT_NOTES = {
  structured: 'The PDF\'s own text, fonts, images and tables are rebuilt as flowing Word '
    + 'content: paragraphs you can type into, equations as native Word equations. The page '
    + 'is not reproduced exactly.',
  replica: 'Fonts, sizes, colours, images, diagrams, tables and equations are kept at their '
    + 'original coordinates, page for page. Blocks do not reflow when you edit them.',
  flow: 'The page is read by the model and rewritten as ordinary flowing Word content. '
    + 'Fully editable, but layout and figures are not preserved — and it reports no block '
    + 'geometry, so its pages come back without boxes.',
  marker: 'The whole PDF is converted locally by marker-pdf, and what it returns is kept '
    + 'as it comes — its Markdown is saved untouched beside the document, so what you see '
    + 'is marker\'s own quality. Needs the marker sidecar running; costs nothing.',
};
function describeLayout() {
  layoutHint.textContent = LAYOUT_NOTES[layoutSelect.value] || '';
  columnsField.classList.toggle('hidden', !FLOWING.includes(layoutSelect.value));
  describeColumns();
}
layoutSelect.addEventListener('change', describeLayout);

function describeColumns() {
  const multi = columnsSelect.querySelector('option[value="multi"]');
  if (sourceColumns === null) {
    multi.disabled = false;
    columnsHint.textContent = 'Read from the PDF when you upload it.';
    return;
  }
  if (sourceColumns < 2) {
    // Not a choice: a source set in one column has no second column for the
    // output to put anything in, so natural is the only thing it can be.
    multi.disabled = true;
    columnsSelect.value = 'natural';
    columnsHint.textContent = 'This PDF is set in a single column, so the document can only be '
      + 'single-column.';
    return;
  }
  multi.disabled = false;
  columnsHint.textContent = columnsSelect.value === 'multi'
    ? `This PDF has ${sourceColumns}-column pages, and each page is set the same way. Where one `
      + 'column breaks to the next is left to Word, which reflows it for the page size you print on.'
    : `This PDF has ${sourceColumns}-column pages; its text is poured into one column instead.`;
}
columnsSelect.addEventListener('change', describeColumns);

fetch('/api/models').then(r => r.json()).then(data => {
  modelSelect.innerHTML = '';
  if (!data.models.length) {
    modelSelect.innerHTML = `<option value="${data.selected}">${data.selected}</option>`;
    modelNote.textContent = data.error ? '(model list unavailable — using the configured default)' : '';
    return;
  }
  for (const m of data.models) {
    const option = document.createElement('option');
    option.value = m.id;
    const price = m.prompt_price_per_mtok != null ? ` — $${m.prompt_price_per_mtok}/M in` : '';
    option.textContent = `${m.name}${price}`;
    if (m.id === data.selected) option.selected = true;
    modelSelect.appendChild(option);
  }
  if (!data.models.some(m => m.id === data.selected)) {
    const option = document.createElement('option');
    option.value = data.selected;
    option.textContent = `${data.selected} (from .env)`;
    option.selected = true;
    modelSelect.prepend(option);
  }
  modelNote.textContent = `(${data.models.length} vision-capable models)`;
}).catch(() => { modelSelect.innerHTML = '<option value="">default from .env</option>'; });

// ---------------------------------------------------------------- upload -- //

drop.addEventListener('click', () => picker.click());
['dragenter', 'dragover'].forEach(evt =>
  drop.addEventListener(evt, e => { e.preventDefault(); drop.classList.add('hot'); }));
['dragleave', 'drop'].forEach(evt =>
  drop.addEventListener(evt, e => { e.preventDefault(); drop.classList.remove('hot'); }));
drop.addEventListener('drop', e => {
  if (e.dataTransfer.files.length) upload(e.dataTransfer.files[0]);
});
picker.addEventListener('change', () => {
  if (picker.files.length) upload(picker.files[0]);
});

resetBtn.addEventListener('click', () => {
  clearInterval(poll);
  card.classList.add('hidden');
  picker.value = '';
  currentJob = null;
  clearViewer();
  loadHistory();
});

discardBtn.addEventListener('click', async () => {
  if (currentJob) await fetch(`/api/jobs/${currentJob}`, { method: 'DELETE' }).catch(() => {});
  card.classList.add('hidden');
  picker.value = '';
  currentJob = null;
  clearViewer();
  loadHistory();
});

startBtn.addEventListener('click', () => startJob(currentJob));

function describe(job) {
  const size = job.size_bytes ? `${(job.size_bytes / 1048576).toFixed(1)} MB · ` : '';
  const mode = { flow: 'editable flow', structured: 'editable document', marker: 'marker' }[job.layout]
    || 'visual replica';
  const columns = FLOWING.includes(job.layout) && job.columns
    ? ` · ${job.columns === 'multi' ? 'multi-column' : 'natural columns'}`
    : '';
  return `${size}${job.pages} page${job.pages === 1 ? '' : 's'} · ${mode}${columns}`;
}

async function upload(file) {
  clearInterval(poll);
  clearViewer();
  actions.classList.add('hidden');
  runArea.classList.add('hidden');
  costBox.classList.add('hidden');
  startActions.classList.remove('hidden');
  card.classList.remove('hidden');
  fileEl.textContent = file.name;
  metaEl.textContent = `${(file.size / 1048576).toFixed(1)} MB · uploading…`;
  startBtn.disabled = true;
  // Whatever the last PDF was set in says nothing about this one.
  sourceColumns = null;
  describeColumns();

  const body = new FormData();
  body.append('file', file);
  body.append('model', modelSelect.value || '');
  body.append('layout', layoutSelect.value || '');
  body.append('columns', columnsChoice());

  let res;
  try {
    res = await fetch('/api/convert', { method: 'POST', body });
  } catch {
    metaEl.textContent = 'Upload failed — is the server still running?';
    return;
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    startActions.classList.add('hidden');
    runArea.classList.remove('hidden');
    setProgress(0, err.detail || 'Upload failed', 'err');
    return;
  }

  const job = await res.json();
  currentJob = job.id;
  // The server read the PDF's own layout while it was storing it; the choice
  // offered from here on is the one that PDF can actually be given.
  sourceColumns = job.source_columns || 1;
  describeColumns();
  metaEl.textContent = `${describe(job)} · ready to convert`;
  startBtn.disabled = false;
  // The pages can be looked through before a single call is made — the source
  // PDF is on the server, and the render does not depend on the conversion.
  openPages(job);
  loadHistory();
}

// Empty unless the chosen mode can act on it, so a replica job is not recorded
// as having made a decision that its own geometry had already made.
function columnsChoice() {
  return FLOWING.includes(layoutSelect.value) ? columnsSelect.value : '';
}

async function startJob(id) {
  if (!id) return;
  startBtn.disabled = true;
  const body = new FormData();
  body.append('model', modelSelect.value || '');
  body.append('layout', layoutSelect.value || '');
  body.append('columns', columnsChoice());

  const res = await fetch(`/api/jobs/${id}/start`, { method: 'POST', body });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    startBtn.disabled = false;
    metaEl.textContent = err.detail || 'Could not start the conversion.';
    return;
  }

  const job = await res.json();
  metaEl.textContent = describe(job);
  startActions.classList.add('hidden');
  runArea.classList.remove('hidden');
  costBox.classList.remove('hidden');
  setProgress(3, 'Queued…', '');
  showCost(job);
  poll = setInterval(() => check(id), 900);
  check(id);
}

function extractionSummary(job) {
  if (job.layout === 'flow') return 'extractor: vision';
  const diagnostics = job.diagnostics || [];
  if (!diagnostics.length) return job.layout === 'marker' ? 'extractor: marker' : 'extractor: PyMuPDF';
  const extractors = [...new Set(diagnostics.map(item => item.extractor))].join(' + ');
  // Blank pages get their own phrasing and their page numbers. They are not a
  // fallback — nothing was retried — and a job that finishes with pages that
  // converted to nothing should not read as an unqualified success.
  const blank = diagnostics.filter(item => item.fallback_reason === 'empty_output').map(item => item.page);
  const fallbacks = [...new Set(diagnostics
    .map(item => item.fallback_reason)
    .filter(reason => reason && reason !== 'empty_output'))];
  let note = '';
  if (blank.length) note += ` · ${blank.length} blank page${blank.length === 1 ? '' : 's'} (${blank.join(', ')})`;
  if (fallbacks.length) note += ` · fallback: ${fallbacks.join(', ').replaceAll('_', ' ')}`;
  return `extractor: ${extractors}${note}`;
}

async function check(id) {
  const res = await fetch(`/api/jobs/${id}`);
  if (!res.ok) return;
  const job = await res.json();
  const total = job.total || job.pages || 1;
  showCost(job);

  if (job.status === 'error') {
    clearInterval(poll);
    setProgress(0, job.error, 'err');
    loadHistory();
    return;
  }
  if (job.status === 'done') {
    clearInterval(poll);
    setProgress(100, `Done — ${total} page${total === 1 ? '' : 's'} converted.`, 'ok');
    metaEl.textContent = `${describe(job)} · ${extractionSummary(job)}`;
    showDownloads(job);
    openJob(job);
    loadHistory();
    return;
  }

  const labels = {
    queued: 'Queued…',
    rendering: 'Rendering pages to images…',
    transcribing: `Reading page ${Math.min(job.done + 1, total)} of ${total}…`,
    building: 'Building the Word document…',
  };
  const weights = { queued: 0, rendering: 5, transcribing: 10, building: 90 };
  const span = { queued: 0, rendering: 5, transcribing: 80, building: 10 };
  const fraction = total ? job.done / total : 0;
  const pct = (weights[job.status] ?? 0) + span[job.status] * fraction;
  setProgress(Math.max(3, Math.round(pct)), labels[job.status] || job.status, '');
}

function showDownloads(job) {
  $('dl-docx').href = `/api/jobs/${job.id}/download?format=docx`;
  $('dl-md').href = `/api/jobs/${job.id}/download?format=md`;
  // The unedited copy, offered next to the .docx because comparing the two is
  // the point of the marker mode.
  const marker = $('dl-marker');
  marker.href = `/api/jobs/${job.id}/download?format=marker-md`;
  marker.classList.toggle('hidden', !job.has_marker);
  actions.classList.remove('hidden');
}

function money(value, known) {
  if (!known) return '—';
  if (!value) return '$0.00';
  return '$' + value.toFixed(value < 0.01 ? 5 : 4);
}

function showCost(job) {
  costValue.textContent = money(job.cost, job.cost_known);
  const tokens = job.prompt_tokens + job.completion_tokens;
  const calls = job.calls || 0;
  if (job.status === 'done' && calls === 0) {
    costNote.textContent = '· no remote calls';
  } else if (job.cost_known && calls) {
    const tokenNote = tokens ? ` · ${tokens.toLocaleString()} tokens` : '';
    costNote.textContent = `· ${calls} remote call${calls === 1 ? '' : 's'}${tokenNote}`
      + (job.status === 'done' ? '' : ' so far');
  } else if (job.status === 'done') {
    costNote.textContent = `· ${calls} remote call${calls === 1 ? '' : 's'} · price unavailable`;
  } else {
    costNote.textContent = '';
  }
}

function setProgress(pct, text, cls) {
  barFill.style.width = pct + '%';
  statusEl.textContent = text;
  statusEl.className = 'status' + (cls ? ' ' + cls : '');
}

// --------------------------------------------------------------- history -- //

function when(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  const today = new Date().toDateString() === d.toDateString();
  return today
    ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : d.toLocaleDateString([], { month: 'short', day: 'numeric' });
}

function statusPill(job) {
  if (job.status === 'done') return '<span class="pill ok">done</span>';
  if (job.status === 'error') return '<span class="pill err">failed</span>';
  if (job.status === 'ready') return '<span class="pill">not started</span>';
  return `<span class="pill">${job.status}</span>`;
}

async function loadHistory() {
  let data;
  try {
    data = await (await fetch('/api/history')).json();
  } catch {
    return;
  }

  historyBody.innerHTML = '';
  const has = data.jobs.length > 0;
  historyTable.classList.toggle('hidden', !has);
  historyEmpty.classList.toggle('hidden', has);
  historyActions.innerHTML = '';

  historySummary.textContent = has
    ? `${data.count} · ${money(data.total_cost, true)} total`
    : '';

  for (const job of data.jobs) {
    const tr = document.createElement('tr');
    tr.dataset.job = job.id;
    if (job.id === currentJob) tr.classList.add('open');
    tr.innerHTML = `
      <td class="name" title="${job.filename.replace(/"/g, '&quot;')}">${job.filename}</td>
      <td>${when(job.created_at)}</td>
      <td class="num">${job.pages || '—'}</td>
      <td>${{ flow: 'flow', structured: 'editable', marker: 'marker' }[job.layout] || 'replica'}</td>
      <td class="num">${money(job.cost, job.cost_known)}</td>
      <td>${statusPill(job)}</td>
      <td class="num"><button class="link" data-del="${job.id}">delete</button></td>`;
    if (job.status === 'error' && job.error) tr.title = job.error;
    // Anywhere but the delete button opens the job in the viewer.
    tr.addEventListener('click', event => {
      if (event.target.closest('button[data-del]')) return;
      reopen(job);
    });
    historyBody.appendChild(tr);
  }

  historyBody.querySelectorAll('button[data-del]').forEach(btn =>
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      const id = btn.dataset.del;
      const res = await fetch(`/api/jobs/${id}`, { method: 'DELETE' });
      if (!res.ok) { btn.disabled = false; return; }
      if (id === currentJob) { card.classList.add('hidden'); currentJob = null; clearViewer(); }
      loadHistory();
    }));

  if (has) {
    const clear = document.createElement('button');
    clear.textContent = 'Clear history';
    clear.className = 'tiny';
    clear.addEventListener('click', async () => {
      if (!confirm('Delete every stored conversion and its files?')) return;
      await fetch('/api/history', { method: 'DELETE' });
      card.classList.add('hidden');
      currentJob = null;
      clearViewer();
      loadHistory();
    });
    historyActions.appendChild(clear);
  }
}

/** Put a finished (or unstarted) job from the history back on the page. */
function reopen(job) {
  clearInterval(poll);
  currentJob = job.id;
  card.classList.remove('hidden');
  fileEl.textContent = job.filename;
  metaEl.textContent = `${describe(job)} · ${job.status === 'done' ? extractionSummary(job) : job.status}`;
  runArea.classList.add('hidden');
  costBox.classList.add('hidden');
  actions.classList.add('hidden');
  startActions.classList.toggle('hidden', job.status !== 'ready');
  startBtn.disabled = job.status !== 'ready';
  sourceColumns = job.source_columns || null;
  describeColumns();
  if (job.status === 'done') showDownloads(job);
  openJob(job);
  loadHistory();
}

// ---------------------------------------------------------------- viewer -- //

// What the converter saw, as written to the job's detection.json, plus the state
// of the two panes reading it.
let doc = null;          // { mode, pages: [...] } — null when there is nothing loaded
let viewerJob = null;    // the job the panes are showing
let pageCount = 0;
let pageIndex = 0;       // 0-based
let hiddenKinds = new Set();
let activeTab = 'rendered';
let wholeDoc = false;
let wholeMarkdown = null; // fetched lazily, the whole document in one string
let zoom = 1;
let selected = null;      // the block index highlighted in both panes

const SVG = 'http://www.w3.org/2000/svg';
const KIND_ORDER = ['heading', 'paragraph', 'text', 'equation', 'figure', 'table', 'rule'];

function kindColour(kind) {
  const value = getComputedStyle(document.documentElement).getPropertyValue('--k-' + kind);
  return value.trim() || '#888';
}

function clearViewer() {
  doc = null; viewerJob = null; pageCount = 0; pageIndex = 0; selected = null;
  wholeMarkdown = null; hiddenKinds = new Set();
  stageWrap.classList.add('hidden');
  pager.classList.add('hidden');
  previewEmpty.classList.remove('hidden');
  outputEmpty.classList.remove('hidden');
  for (const el of [tabRendered, tabText, tabBlocks]) { el.classList.add('hidden'); el.innerHTML = ''; }
  legend.innerHTML = '';
  overlay.innerHTML = '';
  pageImage.removeAttribute('src');
}

/** A job whose pages can be rendered but whose conversion has nothing to show yet. */
function openPages(job) {
  viewerJob = job.id;
  doc = null;
  pageCount = job.pages || 0;
  pageIndex = 0;
  wholeMarkdown = null;
  selected = null;
  if (!pageCount) return clearViewer();
  previewEmpty.classList.add('hidden');
  stageWrap.classList.remove('hidden');
  pager.classList.remove('hidden');
  showPage(0);
}

/** A finished job: its pages, its blocks and its text. */
async function openJob(job) {
  if (!job.has_detection) return openPages(job);
  let data;
  try {
    const res = await fetch(`/api/jobs/${job.id}/detection`);
    if (!res.ok) return openPages(job);
    data = await res.json();
  } catch {
    return openPages(job);
  }
  viewerJob = job.id;
  doc = data;
  pageCount = data.pages.length;
  pageIndex = 0;
  wholeMarkdown = null;
  selected = null;
  hiddenKinds = new Set();
  if (!pageCount) return openPages(job);
  previewEmpty.classList.add('hidden');
  outputEmpty.classList.add('hidden');
  stageWrap.classList.remove('hidden');
  pager.classList.remove('hidden');
  showPage(0);
}

function currentPage() {
  return doc && doc.pages[pageIndex] ? doc.pages[pageIndex] : null;
}

function showPage(index) {
  if (!viewerJob || !pageCount) return;
  pageIndex = Math.max(0, Math.min(index, pageCount - 1));
  selected = null;
  pageImage.src = `/api/jobs/${viewerJob}/page/${pageIndex + 1}.png`;
  pageNumber.value = String(pageIndex + 1);
  pageTotal.textContent = String(pageCount);
  applyZoom();
  drawOverlay();
  drawLegend();
  renderOutput();
}

// ------------------------------------------------------------ the overlay -- //

function drawOverlay() {
  overlay.innerHTML = '';
  const page = currentPage();
  if (!page) {
    pageNote.textContent = doc ? '' : 'Not converted yet — the page is shown as it is.';
    return;
  }
  overlay.setAttribute('viewBox', `0 0 ${page.width} ${page.height}`);

  const blocks = page.blocks || [];
  if (!blocks.length) {
    pageNote.textContent = doc.mode === 'flow'
      ? 'This mode reads the page as an image and reports no block geometry.'
      : 'Nothing was detected on this page.';
    return;
  }
  pageNote.textContent = `${blocks.length} block${blocks.length === 1 ? '' : 's'}`
    + (page.ordered ? ' · numbered in reading order' : ' · positioned, not ordered')
    + (page.scanned ? ' · scanned page' : '');

  // A badge is sized in the page's own units so it stays the same size on the
  // page however the page itself is scaled.
  const unit = Math.max(page.width, page.height) / 90;

  for (const block of blocks) {
    if (hiddenKinds.has(block.kind)) continue;
    const [x0, y0, x1, y1] = block.bbox;
    const colour = kindColour(block.kind);

    const rect = document.createElementNS(SVG, 'rect');
    rect.setAttribute('x', x0); rect.setAttribute('y', y0);
    rect.setAttribute('width', Math.max(0, x1 - x0));
    rect.setAttribute('height', Math.max(0, y1 - y0));
    rect.setAttribute('fill', colour);
    rect.setAttribute('fill-opacity', block.index === selected ? '0.28' : '0.11');
    rect.setAttribute('stroke', colour);
    rect.setAttribute('stroke-width', block.index === selected ? unit * 0.34 : unit * 0.14);
    rect.setAttribute('rx', unit * 0.15);
    rect.dataset.index = block.index;
    const label = block.text ? `${block.kind} — ${block.text}` : block.kind;
    const title = document.createElementNS(SVG, 'title');
    title.textContent = label;
    rect.appendChild(title);
    rect.addEventListener('click', () => select(block.index, 'overlay'));
    overlay.appendChild(rect);

    const badge = document.createElementNS(SVG, 'g');
    badge.setAttribute('class', 'badge');
    const dot = document.createElementNS(SVG, 'circle');
    dot.setAttribute('cx', x1 - unit * 0.7); dot.setAttribute('cy', y0 + unit * 0.7);
    dot.setAttribute('r', unit * 0.7);
    dot.setAttribute('fill', colour);
    dot.setAttribute('fill-opacity', page.ordered ? '0.92' : '0.45');
    const text = document.createElementNS(SVG, 'text');
    text.setAttribute('x', x1 - unit * 0.7); text.setAttribute('y', y0 + unit * 0.7);
    text.setAttribute('text-anchor', 'middle');
    text.setAttribute('dominant-baseline', 'central');
    text.setAttribute('font-size', unit * 0.8);
    text.setAttribute('fill', '#fff');
    text.textContent = String(block.index + 1);
    badge.append(dot, text);
    overlay.appendChild(badge);
  }
}

function drawLegend() {
  legend.innerHTML = '';
  const page = currentPage();
  const present = new Set((page && page.blocks || []).map(block => block.kind));
  for (const kind of KIND_ORDER) {
    if (!present.has(kind)) continue;
    const count = page.blocks.filter(block => block.kind === kind).length;
    const button = document.createElement('button');
    button.className = 'tiny';
    button.setAttribute('aria-pressed', String(!hiddenKinds.has(kind)));
    button.innerHTML = `<span class="swatch" style="background:${kindColour(kind)}"></span>`
      + `${kind} <span class="meta">${count}</span>`;
    button.addEventListener('click', () => {
      hiddenKinds.has(kind) ? hiddenKinds.delete(kind) : hiddenKinds.add(kind);
      drawOverlay(); drawLegend();
    });
    legend.appendChild(button);
  }
}

// ---------------------------------------------------------------- output -- //

/** Point a document's own image references at the job that holds them. */
function resolveImages(markdown) {
  return markdown.replace(
    /(!\[[^\]]*\]\()(?!https?:|data:|\/)([^)\s]+)/g,
    (_, head, target) => `${head}/api/jobs/${viewerJob}/asset/${target}`
  );
}

/** Strip anything a document should never be able to run in this page. */
function scrub(root) {
  root.querySelectorAll('script, style, iframe, object, embed, link, form').forEach(el => el.remove());
  root.querySelectorAll('*').forEach(el => {
    for (const attr of [...el.attributes]) {
      const name = attr.name.toLowerCase();
      if (name.startsWith('on')) el.removeAttribute(attr.name);
      if ((name === 'href' || name === 'src') && /^\s*javascript:/i.test(attr.value)) {
        el.removeAttribute(attr.name);
      }
    }
  });
}

const MATH_DELIMITERS = [
  { left: '$$', right: '$$', display: true },
  { left: '\\[', right: '\\]', display: true },
  { left: '\\(', right: '\\)', display: false },
  { left: '$', right: '$', display: false },
];

async function markdownForScope() {
  if (!wholeDoc) {
    const page = currentPage();
    return page ? page.markdown || '' : '';
  }
  if (wholeMarkdown === null) {
    try {
      const res = await fetch(`/api/jobs/${viewerJob}/markdown`);
      wholeMarkdown = res.ok ? (await res.json()).markdown || '' : '';
    } catch {
      wholeMarkdown = '';
    }
  }
  return wholeMarkdown;
}

async function renderOutput() {
  if (!viewerJob) return;
  const markdown = await markdownForScope();
  const has = Boolean(markdown.trim()) || Boolean(doc);
  outputEmpty.classList.toggle('hidden', has);
  for (const [name, el] of [['rendered', tabRendered], ['text', tabText], ['blocks', tabBlocks]]) {
    el.classList.toggle('hidden', !has || name !== activeTab);
  }
  if (!has) return;

  if (activeTab === 'rendered') {
    if (markdown.trim()) {
      tabRendered.innerHTML = marked.parse(resolveImages(markdown));
      scrub(tabRendered);
      renderMathInElement(tabRendered, { delimiters: MATH_DELIMITERS, throwOnError: false });
    } else {
      tabRendered.innerHTML = '<p class="meta">This page converted to nothing.</p>';
    }
  } else if (activeTab === 'text') {
    tabText.textContent = markdown || '(empty)';
  } else {
    renderBlocks();
  }
}

function renderBlocks() {
  tabBlocks.innerHTML = '';
  const page = currentPage();
  const blocks = (page && page.blocks) || [];
  if (!blocks.length) {
    tabBlocks.innerHTML = '<p class="meta">This mode reports no block geometry for the page.</p>';
    return;
  }
  for (const block of blocks) {
    const [x0, y0, x1, y1] = block.bbox;
    const row = document.createElement('div');
    row.className = 'block' + (block.index === selected ? ' on' : '');
    row.style.borderLeftColor = kindColour(block.kind);
    row.dataset.index = block.index;
    const round = (value) => Math.round(value);
    const extra = block.kind === 'heading' && block.level ? ` · h${block.level}` : '';
    const label = block.label ? ` · ${block.label}` : '';
    row.innerHTML = `<div class="top"><span class="kind">${block.index + 1}. ${block.kind}</span>`
      + `<span>${round(x0)}, ${round(y0)} → ${round(x1)}, ${round(y1)}${extra}${label}</span></div>`;
    if (block.text) {
      const text = document.createElement('div');
      text.className = 'text';
      text.textContent = block.text;
      row.appendChild(text);
    }
    row.addEventListener('click', () => select(block.index, 'list'));
    tabBlocks.appendChild(row);
  }
}

/** Highlight one block in both panes, whichever pane it was chosen in. */
function select(index, from) {
  selected = selected === index ? null : index;
  drawOverlay();
  if (activeTab !== 'blocks' && from === 'overlay') {
    activeTab = 'blocks';
    for (const button of tabs.children) {
      button.setAttribute('aria-selected', String(button.dataset.tab === activeTab));
    }
    renderOutput();
    return;
  }
  if (activeTab === 'blocks') {
    renderBlocks();
    const row = tabBlocks.querySelector(`.block[data-index="${selected}"]`);
    if (row && from === 'overlay') row.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }
}

// ------------------------------------------------------------- the chrome -- //

tabs.addEventListener('click', event => {
  const button = event.target.closest('button[data-tab]');
  if (!button) return;
  activeTab = button.dataset.tab;
  for (const other of tabs.children) {
    other.setAttribute('aria-selected', String(other === button));
  }
  renderOutput();
});

scopeBtn.addEventListener('click', () => {
  wholeDoc = !wholeDoc;
  scopeBtn.textContent = wholeDoc ? 'Whole document' : 'This page';
  renderOutput();
});

copyBtn.addEventListener('click', async () => {
  const markdown = activeTab === 'blocks'
    ? JSON.stringify((currentPage() || {}).blocks || [], null, 2)
    : await markdownForScope();
  try {
    await navigator.clipboard.writeText(markdown);
    copyBtn.textContent = 'Copied';
    setTimeout(() => { copyBtn.textContent = 'Copy'; }, 1200);
  } catch {
    copyBtn.textContent = 'Copy failed';
    setTimeout(() => { copyBtn.textContent = 'Copy'; }, 1200);
  }
});

$('prev-page').addEventListener('click', () => showPage(pageIndex - 1));
$('next-page').addEventListener('click', () => showPage(pageIndex + 1));
pageNumber.addEventListener('change', () => {
  const wanted = parseInt(pageNumber.value, 10);
  showPage(Number.isFinite(wanted) ? wanted - 1 : pageIndex);
});

function applyZoom() {
  stage.style.maxWidth = 'none';
  stage.style.width = `${Math.round(zoom * 100)}%`;
}
$('zoom-in').addEventListener('click', () => { zoom = Math.min(4, zoom * 1.25); applyZoom(); });
$('zoom-out').addEventListener('click', () => { zoom = Math.max(0.25, zoom / 1.25); applyZoom(); });
$('zoom-fit').addEventListener('click', () => { zoom = 1; applyZoom(); });

document.addEventListener('keydown', event => {
  if (!pageCount) return;
  const typing = /^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement.tagName);
  if (typing || event.metaKey || event.ctrlKey || event.altKey) return;
  if (event.key === 'ArrowLeft') { showPage(pageIndex - 1); event.preventDefault(); }
  if (event.key === 'ArrowRight') { showPage(pageIndex + 1); event.preventDefault(); }
});

loadHistory();
