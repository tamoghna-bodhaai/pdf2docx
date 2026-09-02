/* Authenticated Mathpix workspace: stage up to a batch of PDFs, choose exact
   outputs, start / pause / cancel each file's conversion, track progress, and
   open a page-aligned source/result comparison. */

const $ = id => document.getElementById(id);
const RUNNING = new Set(['queued', 'rendering', 'transcribing', 'building']);
const TERMINAL = new Set(['done', 'error', 'cancelled']);
const $root = document.documentElement;

let signingOut = false;
let appConfig = null;
let mathpixFormats = [];
let batchMaxFiles = 10;
// The comparison viewer's selected job, and its id. The batch panel keeps its
// own state below; these two are only about what the comparison screen shows.
let currentJob = null;
let currentJobData = null;
let pollTimer = null;
let historyTimer = null;
let historyLoading = false;
let firstHistoryLoad = true;
let uploadInFlight = false;
// How many extra reads a job that reports itself finished but shows no outputs
// gets before the answer is taken at face value.
const SETTLE_TRIES = 6;
let settling = 0;

// The current batch: its id and a Map of jobId -> latest job record. One upload
// makes one batch, whether it carried one file or ten.
let currentBatch = null;
let batchTimer = null;
let batchPolling = false;

async function api(path, options) {
  const response = await fetch(path, options);
  if (response.status === 401 && !signingOut) {
    signingOut = true;
    location.href = '/login';
  }
  return response;
}

function textResponse(xhr) {
  return {
    ok: xhr.status >= 200 && xhr.status < 300,
    status: xhr.status,
    statusText: xhr.statusText,
    json: async () => {
      if (xhr.response && typeof xhr.response === 'object') return xhr.response;
      try { return JSON.parse(xhr.responseText || '{}'); } catch (_) { return {}; }
    },
  };
}

function uploadRequest(url, body, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', url);
    xhr.responseType = 'json';
    xhr.upload.addEventListener('progress', event => {
      if (event.lengthComputable) onProgress(Math.round(event.loaded / event.total * 100));
    });
    xhr.addEventListener('load', () => {
      if (xhr.status === 401 && !signingOut) {
        signingOut = true;
        location.href = '/login';
      }
      resolve(textResponse(xhr));
    });
    xhr.addEventListener('error', () => reject(new Error('Network error')));
    xhr.addEventListener('abort', () => reject(new Error('Upload cancelled')));
    xhr.send(body);
  });
}

// -------------------------------------------------------------------- theme -- //

const themeToggle = $('theme-toggle');
const themeIcon = $('theme-icon');
const themeLabel = $('theme-label');

function syncThemeToggle() {
  const dark = $root.dataset.theme === 'dark';
  const next = dark ? 'light' : 'dark';
  themeIcon.textContent = dark ? '☀' : '☾';
  themeLabel.textContent = dark ? 'Light theme' : 'Dark theme';
  themeToggle.setAttribute('aria-label', `Switch to ${next} theme`);
  themeToggle.setAttribute('aria-pressed', String(dark));
}

function setTheme(theme) {
  $root.dataset.theme = theme;
  try { localStorage.setItem('pdf2docx-theme', theme); } catch (_) {}
  syncThemeToggle();
}

syncThemeToggle();
themeToggle.addEventListener('click', () => {
  setTheme($root.dataset.theme === 'dark' ? 'light' : 'dark');
});

// ------------------------------------------------------------- navigation -- //

const appShell = $('app-shell');
const sidebar = $('sidebar');
const menuToggle = $('menu-toggle');
const sidebarBackdrop = $('sidebar-backdrop');
const dashboardView = $('dashboard-view');
const comparisonView = $('comparison-view');
const mobileNavigation = matchMedia('(max-width: 900px)');

function syncSidebarAccess(open = appShell.classList.contains('sidebar-open')) {
  const hidden = mobileNavigation.matches && !open;
  sidebar.inert = hidden;
  sidebar.setAttribute('aria-hidden', String(hidden));
}

function setDrawer(open) {
  const wasOpen = appShell.classList.contains('sidebar-open');
  appShell.classList.toggle('sidebar-open', open);
  menuToggle.setAttribute('aria-expanded', String(open));
  menuToggle.setAttribute('aria-label', open ? 'Close navigation' : 'Open navigation');
  sidebarBackdrop.setAttribute('aria-hidden', String(!open));
  syncSidebarAccess(open);
  if (!open && wasOpen && mobileNavigation.matches) {
    menuToggle.focus({ preventScroll: true });
  }
}

syncSidebarAccess();
mobileNavigation.addEventListener('change', () => {
  setDrawer(false);
  syncComparisonPaneAccess();
});

menuToggle.addEventListener('click', () => {
  setDrawer(!appShell.classList.contains('sidebar-open'));
});
sidebarBackdrop.addEventListener('click', () => setDrawer(false));

function updateUrl(jobId) {
  const url = new URL(location.href);
  if (jobId) url.searchParams.set('job', jobId);
  else url.searchParams.delete('job');
  history.replaceState({}, '', url.pathname + url.search);
}

function showDashboard(target = 'uploads', update = true) {
  dashboardView.classList.remove('hidden');
  comparisonView.classList.add('hidden');
  $('uploads-nav').classList.toggle('active', target === 'uploads');
  $('history-nav').classList.toggle('active', target === 'history');
  $('uploads-nav').toggleAttribute('aria-current', target === 'uploads');
  $('history-nav').toggleAttribute('aria-current', target === 'history');
  if (update) updateUrl(null);
  renderBatch();
  setDrawer(false);
  const destination = target === 'history' ? $('history-panel') : $('dashboard-heading');
  requestAnimationFrame(() => destination.scrollIntoView({ block: 'start' }));
}

$('uploads-nav').addEventListener('click', () => showDashboard('uploads'));
$('history-nav').addEventListener('click', () => showDashboard('history'));
$('back-to-uploads').addEventListener('click', () => showDashboard('uploads'));

for (const id of ['mobile-upload', 'empty-upload']) {
  $(id).addEventListener('click', () => {
    showDashboard('uploads');
    $('picker').click();
  });
}

document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && appShell.classList.contains('sidebar-open')) setDrawer(false);
});

// -------------------------------------------------------------- configuration -- //

function renderFormats(selected = ['docx']) {
  const fieldset = $('format-options');
  fieldset.replaceChildren();
  const legend = document.createElement('legend');
  legend.className = 'sr-only';
  legend.textContent = 'Select optional output formats';
  fieldset.appendChild(legend);
  const chosen = new Set(selected);
  const requestable = mathpixFormats.filter(entry => entry.requestable);

  for (const entry of requestable) {
    const label = document.createElement('label');
    label.className = 'format-option';
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.value = entry.ext;
    input.checked = chosen.has(entry.ext);
    input.addEventListener('change', updateFormatSummary);
    const copy = document.createElement('span');
    const name = document.createElement('strong');
    name.textContent = entry.ext.toUpperCase();
    const note = document.createElement('small');
    note.textContent = entry.note || formatDescription(entry.ext);
    copy.append(name, note);
    label.append(input, copy);
    fieldset.appendChild(label);
  }

  const included = mathpixFormats.filter(entry => entry.always || !entry.requestable);
  $('included-formats').replaceChildren();
  const prefix = document.createElement('span');
  prefix.textContent = 'Included';
  const values = document.createElement('strong');
  values.textContent = included.map(entry => entry.ext.toUpperCase()).join(' · ')
    || 'MMD preview';
  $('included-formats').append(prefix, values);
  updateFormatSummary();
}

function formatDescription(ext) {
  const descriptions = {
    docx: 'Editable Word document', md: 'Markdown text', html: 'HTML document',
    pptx: 'PowerPoint presentation', xlsx: 'Spreadsheet for tables',
    pdf: 'Re-rendered PDF', 'latex.pdf': 'LaTeX-rendered PDF',
  };
  return descriptions[ext] || 'Optional export';
}

function selectedFormats() {
  return [...$('format-options').querySelectorAll('input:checked')].map(input => input.value);
}

// Whether to lay the document out in the columns the source page used. Read at
// the moment the conversion starts, the same way the formats are.
function multiColumnSelected() {
  return $('multi-column').checked;
}

function updateFormatSummary() {
  const selected = selectedFormats();
  const summary = $('format-summary');
  if (!selected.length) summary.textContent = 'Preview only';
  else if (selected.length === 1) summary.textContent = `${selected[0].toUpperCase()} selected`;
  else summary.textContent = `${selected.length} formats selected`;
}

async function loadConfig() {
  try {
    const response = await api('/api/config');
    if (!response.ok) throw new Error('Configuration unavailable');
    appConfig = await response.json();
    mathpixFormats = appConfig.mathpix_formats || [];
    batchMaxFiles = Number(appConfig.batch_max_files) || 10;
    renderFormats(['docx']);
    const limits = ['PDF only'];
    if (appConfig.max_upload_mb) limits.push(`up to ${appConfig.max_upload_mb} MB`);
    if (appConfig.max_pages) limits.push(`first ${appConfig.max_pages} pages`);
    $('upload-limits').textContent = limits.join(' · ');
    $('upload-batch-hint').textContent = `Up to ${batchMaxFiles} files at once`;
    $('provider-state-text').textContent = appConfig.mathpix_key_configured
      ? 'Available' : 'Key required';
    $('provider-state-text').parentElement.parentElement.classList.toggle(
      'unavailable', !appConfig.mathpix_key_configured
    );
    $('keywarn').classList.toggle('hidden', appConfig.mathpix_key_configured);
  } catch (_) {
    $('upload-limits').textContent = 'PDF only';
    $('provider-state-text').textContent = 'Unavailable';
    $('keywarn').classList.remove('hidden');
  }
}

document.addEventListener('keydown', event => {
  const openDetails = event.target.closest('details[open]');
  if (event.key === 'Escape' && openDetails) {
    openDetails.open = false;
    const summary = openDetails.querySelector('summary');
    if (summary) summary.focus();
  }
});

document.addEventListener('click', event => {
  document.querySelectorAll('details[open].format-menu, details[open].download-menu').forEach(details => {
    if (!details.contains(event.target)) details.open = false;
  });
});

// -------------------------------------------------------------------- upload -- //

const drop = $('drop');
const picker = $('picker');

function syncUploaderAvailability() {
  const locked = uploadInFlight || Boolean(currentBatch);
  drop.disabled = locked;
  picker.disabled = locked;
  $('mobile-upload').disabled = locked;
  $('empty-upload').disabled = locked;
  drop.setAttribute('aria-busy', String(uploadInFlight));
}

drop.addEventListener('click', () => picker.click());
for (const eventName of ['dragenter', 'dragover']) {
  drop.addEventListener(eventName, event => {
    event.preventDefault();
    drop.classList.add('hot');
  });
}
for (const eventName of ['dragleave', 'drop']) {
  drop.addEventListener(eventName, event => {
    event.preventDefault();
    drop.classList.remove('hot');
  });
}
drop.addEventListener('drop', event => {
  if (event.dataTransfer.files.length) uploadBatch(event.dataTransfer.files);
});
picker.addEventListener('change', () => {
  if (picker.files.length) uploadBatch(picker.files);
});

function formatSize(bytes) {
  if (!bytes) return '0 B';
  if (bytes < 1048576) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / 1048576).toFixed(1)} MB`;
}

function describe(job) {
  const parts = [];
  if (job.size_bytes) parts.push(formatSize(job.size_bytes));
  if (job.pages) parts.push(`${job.pages} page${job.pages === 1 ? '' : 's'}`);
  return parts.join(' · ');
}

function setUploadProgress(percent, label) {
  const progress = $('upload-progress');
  progress.classList.remove('hidden');
  progress.setAttribute('aria-valuenow', String(percent));
  $('upload-bar-fill').style.width = `${percent}%`;
  $('upload-status').textContent = label || `Uploading… ${percent}%`;
}

function showUploadError(message) {
  setUploadProgress(0, message);
  $('upload-progress').classList.add('error');
  uploadInFlight = false;
  syncUploaderAvailability();
}

async function uploadBatch(fileList) {
  if (uploadInFlight || currentBatch) return;
  stopBatchPolling();
  showDashboard('uploads');

  let files = [...fileList].filter(file => file.name.toLowerCase().endsWith('.pdf'));
  const skippedNonPdf = files.length !== fileList.length;
  let overLimit = false;
  if (files.length > batchMaxFiles) {
    files = files.slice(0, batchMaxFiles);
    overLimit = true;
  }
  if (!files.length) {
    showUploadError('Choose one or more PDF files.');
    return;
  }
  if (appConfig && appConfig.max_upload_mb) {
    const cap = appConfig.max_upload_mb * 1048576;
    const oversized = files.filter(file => file.size > cap);
    if (oversized.length) {
      showUploadError(
        `${oversized.length === 1 ? oversized[0].name + ' is' : oversized.length + ' files are'} `
        + `larger than the ${appConfig.max_upload_mb} MB limit.`
      );
      return;
    }
  }

  uploadInFlight = true;
  currentJob = null;
  currentJobData = null;
  syncUploaderAvailability();
  $('upload-progress').classList.remove('error');
  setUploadProgress(0, `Uploading ${files.length} file${files.length === 1 ? '' : 's'}…`);

  const body = new FormData();
  for (const file of files) body.append('files', file);

  let response;
  try {
    response = await uploadRequest('/api/convert/batch', body, percent => setUploadProgress(percent));
  } catch (_) {
    showUploadError('Upload failed. Check your connection and try again.');
    return;
  }
  uploadInFlight = false;
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    showUploadError(error.detail || 'The PDFs could not be uploaded.');
    return;
  }

  const data = await response.json();
  setUploadProgress(100, 'Upload complete');
  setTimeout(() => $('upload-progress').classList.add('hidden'), 250);

  currentBatch = { id: data.batch_id, jobs: new Map((data.jobs || []).map(job => [job.id, job])) };
  const notes = [];
  if (skippedNonPdf) notes.push('non-PDF files were skipped');
  if (overLimit) notes.push(`only the first ${batchMaxFiles} files were kept`);
  for (const bad of data.rejected || []) notes.push(`${bad.filename}: ${bad.detail}`);
  renderBatch();
  if (notes.length) {
    $('batch-notice').textContent = notes.join(' · ');
    $('batch-notice').classList.remove('hidden');
  }
  renderFormats(['docx']);
  $('multi-column').checked = false;
  syncUploaderAvailability();
  loadHistory();
}

function clearBatch() {
  stopBatchPolling();
  currentBatch = null;
  uploadInFlight = false;
  picker.value = '';
  $('batch-panel').classList.add('hidden');
  $('batch-list').replaceChildren();
  $('upload-progress').classList.add('hidden');
  $('upload-progress').classList.remove('error');
  renderFormats(['docx']);
  syncUploaderAvailability();
  loadHistory();
}

// --------------------------------------------------------- confirmation dialog -- //

const confirmDialog = $('confirm-dialog');

function requestConfirmation({ title, description, action = 'Delete' }) {
  $('confirm-title').textContent = title;
  $('confirm-description').textContent = description;
  $('confirm-action').textContent = action;
  confirmDialog.returnValue = 'cancel';
  confirmDialog.showModal();
  return new Promise(resolve => {
    confirmDialog.addEventListener('close', () => {
      resolve(confirmDialog.returnValue === 'confirm');
    }, { once: true });
  });
}

async function deleteHistoryJob(job, trigger) {
  const approved = await requestConfirmation({
    title: `Delete ${job.filename}?`,
    description: 'This removes the source PDF and every stored output. This cannot be undone.',
  });
  if (!approved) return;
  trigger.disabled = true;
  let response;
  try { response = await api(`/api/jobs/${job.id}`, { method: 'DELETE' }); } catch (_) {}
  if (!response || !response.ok) {
    trigger.disabled = false;
    showHistoryError('Couldn’t delete that conversion', 'Nothing was removed. Check your connection and try again.');
    return;
  }
  if (currentBatch && currentBatch.jobs.has(job.id)) {
    currentBatch.jobs.delete(job.id);
    if (!currentBatch.jobs.size) clearBatch();
    else renderBatch();
  }
  if (job.id === currentJob) resetComparison();
  loadHistory();
}

// --------------------------------------------------------------- the batch -- //

const BADGE_CLASS = {
  ready: '', paused: 'paused', queued: 'working', rendering: 'working',
  transcribing: 'working', building: 'working', done: 'complete',
  error: 'error', cancelled: 'cancelled',
};

function progressFor(job) {
  const total = job.total || job.pages || 1;
  const weights = { queued: 2, rendering: 5, transcribing: 10, building: 90 };
  const spans = { queued: 0, rendering: 5, transcribing: 80, building: 10 };
  const fraction = total ? Math.max(0, Math.min(1, job.done / total)) : 0;
  if (job.status === 'done') return 100;
  return Math.max(2, Math.round((weights[job.status] || 0) + (spans[job.status] || 0) * fraction));
}

function stageLabel(job) {
  const total = job.total || job.pages || 1;
  const labels = {
    ready: 'Ready to convert',
    paused: 'Paused',
    queued: 'Waiting for a free slot…',
    rendering: 'Preparing the source PDF…',
    transcribing: `Processing page ${Math.min(job.done + 1, total)} of ${total}…`,
    building: 'Downloading selected outputs and preview data…',
    done: 'Complete',
    error: job.error || 'The conversion failed.',
    cancelled: 'Cancelled',
  };
  return labels[job.status] || job.status;
}

function shortStatus(job) {
  return {
    ready: 'Ready', paused: 'Paused', queued: 'Queued', rendering: 'Rendering',
    transcribing: `${progressFor(job)}%`, building: 'Building', done: 'Complete',
    error: 'Failed', cancelled: 'Cancelled',
  }[job.status] || job.status;
}

function batchList() {
  return currentBatch ? [...currentBatch.jobs.values()] : [];
}

function actionButton(label, className, handler) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = className || '';
  button.textContent = label;
  button.addEventListener('click', handler);
  return button;
}

function renderBatchRow(job) {
  const row = document.createElement('li');
  row.className = 'batch-row';
  row.dataset.job = job.id;

  const main = document.createElement('div');
  main.className = 'batch-row-main';
  const name = document.createElement('strong');
  name.textContent = job.filename;
  const meta = document.createElement('small');
  const detail = describe(job);
  meta.textContent = RUNNING.has(job.status)
    ? `${stageLabel(job)}${detail ? ' · ' + detail : ''}`
    : (detail ? `${stageLabel(job)} · ${detail}` : stageLabel(job));
  main.append(name, meta);

  const badge = document.createElement('span');
  badge.className = `status-badge ${BADGE_CLASS[job.status] || ''}`.trim();
  badge.textContent = shortStatus(job);

  const bar = document.createElement('div');
  bar.className = 'progress-track batch-row-progress';
  const fill = document.createElement('i');
  fill.style.width = `${job.status === 'done' ? 100 : (RUNNING.has(job.status) ? progressFor(job) : 0)}%`;
  bar.appendChild(fill);
  bar.classList.toggle('hidden', !RUNNING.has(job.status) && job.status !== 'done');

  const actions = document.createElement('div');
  actions.className = 'row-actions';

  if (job.status === 'ready') {
    actions.append(
      actionButton('Pause', '', () => holdJob(job.id)),
      actionButton('Remove', '', () => removeJob(job)),
    );
  } else if (job.status === 'paused') {
    actions.append(
      actionButton('Resume', 'primary', () => resumeJob(job.id)),
      actionButton('Remove', '', () => removeJob(job)),
    );
  } else if (job.status === 'queued') {
    actions.append(
      actionButton('Pause', '', () => holdJob(job.id)),
      actionButton('Cancel', '', () => cancelJob(job.id)),
    );
  } else if (RUNNING.has(job.status)) {
    actions.append(actionButton('Cancel', '', () => cancelJob(job.id)));
  } else if (job.status === 'done') {
    actions.append(actionButton('Open', 'primary', () => openComparison(job)));
    actions.appendChild(rowDownloads(job));
    actions.append(actionButton('Remove', '', () => removeJob(job)));
  } else if (job.status === 'error') {
    actions.append(
      actionButton('Try again', 'primary', () => startJob(job.id)),
      actionButton('Open', '', () => openComparison(job)),
      actionButton('Remove', '', () => removeJob(job)),
    );
  } else if (job.status === 'cancelled') {
    actions.append(
      actionButton('Try again', 'primary', () => startJob(job.id)),
      actionButton('Remove', '', () => removeJob(job)),
    );
  }

  row.append(main, badge, bar, actions);
  return row;
}

function rowDownloads(job) {
  const details = document.createElement('details');
  details.className = 'download-menu';
  const summary = document.createElement('summary');
  summary.textContent = 'Download';
  const links = document.createElement('div');
  links.className = 'download-links';
  renderDownloadLinks(job, links);
  details.append(summary, links);
  return details;
}

function batchSummaryText(jobs) {
  const counts = {};
  for (const job of jobs) counts[job.status] = (counts[job.status] || 0) + 1;
  const converting = (counts.rendering || 0) + (counts.transcribing || 0) + (counts.building || 0);
  const parts = [`${jobs.length} file${jobs.length === 1 ? '' : 's'}`];
  if (converting) parts.push(`${converting} converting`);
  if (counts.queued) parts.push(`${counts.queued} queued`);
  if (counts.paused) parts.push(`${counts.paused} paused`);
  if (counts.done) parts.push(`${counts.done} done`);
  if (counts.error) parts.push(`${counts.error} failed`);
  if (counts.cancelled) parts.push(`${counts.cancelled} cancelled`);
  return parts.join(' · ');
}

function renderBatch() {
  const panel = $('batch-panel');
  if (!currentBatch) {
    panel.classList.add('hidden');
    return;
  }
  panel.classList.remove('hidden');
  const jobs = batchList();

  $('batch-summary').textContent = batchSummaryText(jobs);
  $('batch-list').replaceChildren(...jobs.map(renderBatchRow));

  const anyReady = jobs.some(job => job.status === 'ready');
  const anyPaused = jobs.some(job => job.status === 'paused');
  const anyQueued = jobs.some(job => job.status === 'queued');
  const anyRunning = jobs.some(job => RUNNING.has(job.status));
  const anyLive = jobs.some(job => !TERMINAL.has(job.status));
  const anyDone = jobs.some(job => TERMINAL.has(job.status));

  const badge = $('batch-badge');
  badge.className = 'status-badge ' + (anyRunning ? 'working'
    : anyReady ? '' : anyPaused ? 'paused' : jobs.every(j => j.status === 'done') ? 'complete'
    : anyLive ? 'working' : 'error');
  badge.textContent = anyRunning ? 'Converting'
    : anyReady ? 'Ready' : anyPaused ? 'Paused'
    : jobs.every(j => j.status === 'done') ? 'Complete' : 'Finished';

  $('batch-controls').querySelector('.format-field').classList.toggle('hidden', !anyReady && !anyPaused);
  $('batch-start').classList.toggle('hidden', !anyReady);
  $('batch-start').disabled = !anyReady;
  $('batch-pause').classList.toggle('hidden', !(anyReady || anyQueued));
  $('batch-resume').classList.toggle('hidden', !anyPaused);
  $('batch-cancel').classList.toggle('hidden', !anyLive);
  $('batch-clear').classList.toggle('hidden', !anyDone);

  maybePollBatch();
}

function patchBatch(jobs) {
  if (!currentBatch) return;
  const seen = new Set();
  for (const job of jobs) {
    currentBatch.jobs.set(job.id, job);
    seen.add(job.id);
  }
  for (const id of [...currentBatch.jobs.keys()]) {
    if (!seen.has(id)) currentBatch.jobs.delete(id);
  }
  if (!currentBatch.jobs.size) { clearBatch(); return; }
  renderBatch();
}

// ----- batch and per-file controls ----- //

$('batch-start').addEventListener('click', startBatch);
$('batch-pause').addEventListener('click', () => batchAction('pause'));
$('batch-resume').addEventListener('click', () => batchAction('resume'));
$('batch-cancel').addEventListener('click', async () => {
  const approved = await requestConfirmation({
    title: 'Cancel the whole batch?',
    description: 'Every file that has not finished is abandoned. Files already sent to the '
      + 'conversion service may still be billed.',
    action: 'Cancel all',
  });
  if (approved) batchAction('cancel');
});
$('batch-clear').addEventListener('click', clearFinished);

async function startBatch() {
  if (!currentBatch) return;
  $('batch-start').disabled = true;
  settling = 0;
  const body = new FormData();
  body.append('formats', selectedFormats().join(','));
  body.append('multi_column', multiColumnSelected() ? 'true' : 'false');
  let response;
  try {
    response = await api(`/api/batches/${currentBatch.id}/start`, { method: 'POST', body });
  } catch (_) {
    $('batch-notice').textContent = 'The start request was interrupted. Reconnecting…';
    $('batch-notice').classList.remove('hidden');
    schedulePollBatch(1500);
    return;
  }
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    $('batch-notice').textContent = error.detail || 'The batch could not start.';
    $('batch-notice').classList.remove('hidden');
    $('batch-start').disabled = false;
    return;
  }
  $('batch-notice').classList.add('hidden');
  patchBatch((await response.json()).jobs || []);
  schedulePollBatch(0);
  loadHistory();
}

async function batchAction(action) {
  if (!currentBatch) return;
  let response;
  try {
    response = await api(`/api/batches/${currentBatch.id}/${action}`, { method: 'POST' });
  } catch (_) { return; }
  if (response.ok) {
    patchBatch((await response.json()).jobs || []);
    schedulePollBatch(action === 'resume' ? 0 : 600);
    loadHistory();
  }
}

async function jobControl(id, action, extra) {
  let response;
  try {
    response = await api(`/api/jobs/${id}/${action}`, extra || { method: 'POST' });
  } catch (_) { return null; }
  if (response.ok) {
    const job = await response.json();
    if (currentBatch && currentBatch.jobs.has(id)) {
      currentBatch.jobs.set(id, job);
      renderBatch();
    }
    schedulePollBatch(action === 'resume' || action === 'start' ? 0 : 600);
    loadHistory();
    return job;
  }
  return null;
}

const holdJob = id => jobControl(id, 'pause');
const resumeJob = id => jobControl(id, 'resume');
const cancelJob = id => jobControl(id, 'cancel');

async function startJob(id) {
  settling = 0;
  const body = new FormData();
  body.append('formats', selectedFormats().join(','));
  body.append('multi_column', multiColumnSelected() ? 'true' : 'false');
  await jobControl(id, 'start', { method: 'POST', body });
}

async function removeJob(job) {
  if (job.status === 'done') {
    const approved = await requestConfirmation({
      title: `Remove ${job.filename}?`,
      description: 'This deletes the source PDF and every stored output for this file.',
      action: 'Remove file',
    });
    if (!approved) return;
  }
  let response;
  try { response = await api(`/api/jobs/${job.id}`, { method: 'DELETE' }); } catch (_) {}
  if (!response || !response.ok) {
    $('batch-notice').textContent = 'That file could not be removed. Try again.';
    $('batch-notice').classList.remove('hidden');
    return;
  }
  if (currentBatch) {
    currentBatch.jobs.delete(job.id);
    if (!currentBatch.jobs.size) clearBatch();
    else renderBatch();
  }
  if (job.id === currentJob) resetComparison();
  loadHistory();
}

async function clearFinished() {
  if (!currentBatch) return;
  const finished = batchList().filter(job => TERMINAL.has(job.status));
  for (const job of finished) {
    try { await api(`/api/jobs/${job.id}`, { method: 'DELETE' }); } catch (_) {}
    currentBatch.jobs.delete(job.id);
  }
  if (!currentBatch.jobs.size) clearBatch();
  else renderBatch();
  loadHistory();
}

// ----- batch polling ----- //

function stopBatchPolling() {
  clearTimeout(batchTimer);
  batchTimer = null;
  batchPolling = false;
}

function schedulePollBatch(delay = 1000) {
  clearTimeout(batchTimer);
  batchTimer = setTimeout(pollBatch, delay);
}

function maybePollBatch() {
  if (batchPolling || !currentBatch) return;
  if (batchList().some(job => RUNNING.has(job.status))) schedulePollBatch(1000);
}

async function pollBatch() {
  clearTimeout(batchTimer);
  batchTimer = null;
  if (!currentBatch) return;
  batchPolling = true;
  let response;
  try {
    response = await api(`/api/batches/${currentBatch.id}`);
  } catch (_) {
    batchPolling = false;
    schedulePollBatch(2500);
    return;
  }
  batchPolling = false;
  if (!response.ok) {
    if (response.status >= 500) schedulePollBatch(2500);
    return;
  }
  const data = await response.json();
  patchBatch(data.jobs || []);
  const jobs = batchList();
  const stillGoing = jobs.some(job => RUNNING.has(job.status));
  const settlingDone = jobs.some(job => job.status === 'done' && !job.has_md);
  if (stillGoing) { settling = 0; schedulePollBatch(1000); }
  else if (settlingDone && settling < SETTLE_TRIES) { settling += 1; schedulePollBatch(800); }
  else { settling = 0; loadHistory(); }
}

// ------------------------------------------------------------ downloads -- //

function renderDownloadLinks(job, container) {
  container.replaceChildren();
  const available = new Set(job.mathpix_formats || []);
  for (const entry of mathpixFormats) {
    if (!available.has(entry.ext)) continue;
    const link = document.createElement('a');
    link.href = `/api/jobs/${job.id}/download?format=${entry.ext === 'docx' ? 'docx' : `mathpix-${entry.ext}`}`;
    link.textContent = `Download ${entry.ext.toUpperCase()}`;
    if (entry.note) link.title = entry.note;
    container.appendChild(link);
  }
  if (!container.children.length) {
    const note = document.createElement('span');
    note.textContent = 'No downloadable output was produced.';
    container.appendChild(note);
  }
}

function money(value, known) {
  if (!value && known) return '$0.00';
  if (!value) return '—';
  const amount = '$' + Number(value).toFixed(value < 0.01 ? 5 : 4);
  return known ? amount : `~${amount}`;
}

function estimatedCost(job) {
  if (job.cost) return job.cost;
  const rate = appConfig && Number(appConfig.mathpix_page_rate || 0);
  return rate ? rate * (job.pages || 0) : 0;
}

// ------------------------------------------------------------------- history -- //

function when(iso) {
  if (!iso) return '—';
  const date = new Date(iso);
  const options = { month: 'short', day: 'numeric', year: date.getFullYear() === new Date().getFullYear() ? undefined : 'numeric' };
  return date.toLocaleDateString([], options);
}

function outputNames(job) {
  if (job.status === 'ready') return 'Choose formats';
  const values = job.status === 'done' ? job.mathpix_formats : job.requested_formats;
  const names = values || [];
  if (!names.length) return 'MMD preview';
  return names.slice(0, 3).map(ext => ext.toUpperCase()).join(' · ')
    + (names.length > 3 ? ` +${names.length - 3}` : '');
}

function showHistoryError(title, message) {
  $('history-error').querySelector('strong').textContent = title;
  $('history-error').querySelector('p').textContent = message;
  $('history-error').classList.remove('hidden');
}

function historyStatus(job) {
  const wrapper = document.createElement('span');
  wrapper.className = `history-status ${job.status}`;
  if (RUNNING.has(job.status)) {
    wrapper.textContent = `${stageLabel(job).replace('…', '')} · ${progressFor(job)}%`;
  } else {
    wrapper.textContent = {
      done: 'Complete', error: 'Failed', ready: 'Ready',
      paused: 'Paused', cancelled: 'Cancelled',
    }[job.status] || job.status;
  }
  return wrapper;
}

function buildHistoryRow(job) {
  const row = document.createElement('tr');
  row.dataset.job = job.id;
  if (job.id === currentJob) row.classList.add('open');

  const documentCell = document.createElement('td');
  const link = document.createElement('a');
  link.className = 'history-document';
  link.href = `/?job=${encodeURIComponent(job.id)}`;
  const name = document.createElement('strong');
  name.textContent = job.filename;
  const meta = document.createElement('small');
  meta.textContent = describe(job) || 'PDF document';
  link.append(name, meta);
  link.addEventListener('click', event => {
    event.preventDefault();
    openComparison(job);
  });
  documentCell.appendChild(link);

  const dateCell = document.createElement('td'); dateCell.textContent = when(job.created_at);
  const pagesCell = document.createElement('td'); pagesCell.textContent = job.pages || '—';
  const outputsCell = document.createElement('td'); outputsCell.textContent = outputNames(job);
  const statusCell = document.createElement('td'); statusCell.appendChild(historyStatus(job));
  const costCell = document.createElement('td'); costCell.textContent = money(estimatedCost(job), job.cost_known);
  const actionCell = document.createElement('td');
  if (RUNNING.has(job.status)) {
    const stop = document.createElement('button');
    stop.type = 'button';
    stop.className = 'icon-action';
    stop.textContent = 'Cancel';
    stop.setAttribute('aria-label', `Cancel ${job.filename}`);
    stop.addEventListener('click', async () => {
      stop.disabled = true;
      try { await api(`/api/jobs/${job.id}/cancel`, { method: 'POST' }); } catch (_) {}
      loadHistory();
      if (currentBatch && currentBatch.jobs.has(job.id)) schedulePollBatch(400);
    });
    actionCell.appendChild(stop);
  } else {
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'icon-action';
    remove.setAttribute('aria-label', `Delete ${job.filename}`);
    remove.title = `Delete ${job.filename}`;
    remove.textContent = 'Delete';
    remove.addEventListener('click', () => deleteHistoryJob(job, remove));
    actionCell.appendChild(remove);
  }

  row.append(documentCell, dateCell, pagesCell, outputsCell, statusCell, costCell, actionCell);
  row.addEventListener('click', event => {
    if (event.target.closest('a, button')) return;
    openComparison(job);
  });
  return row;
}

async function loadHistory() {
  if (historyLoading) return null;
  historyLoading = true;
  if (firstHistoryLoad) $('history-loading').classList.remove('hidden');
  $('history-error').classList.add('hidden');
  clearTimeout(historyTimer);

  try {
    const response = await api('/api/history');
    if (!response.ok) throw new Error('History unavailable');
    const data = await response.json();
    $('history-body').replaceChildren(...data.jobs.map(buildHistoryRow));
    const hasJobs = data.jobs.length > 0;
    $('history-table').classList.toggle('hidden', !hasJobs);
    $('history-empty').classList.toggle('hidden', hasJobs);
    $('history-summary').textContent = hasJobs
      ? `${data.count} conversion${data.count === 1 ? '' : 's'}` : '';
    renderHistoryActions(hasJobs);
    if (data.jobs.some(job => RUNNING.has(job.status))) {
      historyTimer = setTimeout(loadHistory, 1400);
    }
    return data;
  } catch (_) {
    $('history-table').classList.add('hidden');
    $('history-empty').classList.add('hidden');
    showHistoryError('Couldn’t load conversion history', 'Check your connection and try again.');
    return null;
  } finally {
    historyLoading = false;
    firstHistoryLoad = false;
    $('history-loading').classList.add('hidden');
  }
}

function renderHistoryActions(hasJobs) {
  const container = $('history-actions');
  container.replaceChildren();
  if (!hasJobs) return;
  const clear = document.createElement('button');
  clear.type = 'button';
  clear.textContent = 'Clear completed history';
  clear.addEventListener('click', async () => {
    const approved = await requestConfirmation({
      title: 'Clear conversion history?',
      description: 'Every conversion that is not currently running will be deleted with its source and outputs.',
      action: 'Clear history',
    });
    if (!approved) return;
    clear.disabled = true;
    let response;
    try { response = await api('/api/history', { method: 'DELETE' }); } catch (_) {}
    if (response && response.ok) {
      if (currentBatch) clearBatch();
      resetComparison();
      loadHistory();
    } else {
      clear.disabled = false;
      showHistoryError('Couldn’t clear conversion history', 'Nothing was removed. Check your connection and try again.');
    }
  });
  container.appendChild(clear);
}

$('history-retry').addEventListener('click', loadHistory);

// ----------------------------------------------------------- comparison mode -- //

function resetComparison() {
  stopPolling();
  currentJob = null;
  currentJobData = null;
  clearViewer();
  if (!comparisonView.classList.contains('hidden')) showDashboard('uploads');
}

async function openComparison(job, update = true) {
  currentJob = job.id;
  currentJobData = job;
  dashboardView.classList.add('hidden');
  comparisonView.classList.remove('hidden');
  $('uploads-nav').classList.remove('active');
  $('history-nav').classList.remove('active');
  $('uploads-nav').removeAttribute('aria-current');
  $('history-nav').removeAttribute('aria-current');
  $('comparison-file').textContent = job.filename;
  $('comparison-meta').textContent = `${describe(job)} · ${when(job.created_at)} · ${outputNames(job)}`;
  renderDownloadLinks(job, $('comparison-download-links'));
  setComparisonPane('source');
  if (update) updateUrl(job.id);
  setDrawer(false);
  await openJob(job);
  if (RUNNING.has(job.status)) schedulePoll(job.id);
}

async function restoreJobFromUrl() {
  const id = new URLSearchParams(location.search).get('job');
  if (!id) return;
  try {
    const response = await api(`/api/jobs/${encodeURIComponent(id)}`);
    if (!response.ok) throw new Error('Unavailable job');
    await openComparison(await response.json(), false);
  } catch (_) {
    updateUrl(null);
    showDashboard('history', false);
    showHistoryError('That conversion is unavailable', 'It may have been deleted or belong to another account.');
  }
}

function schedulePoll(id, delay = 900) {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(() => check(id), delay);
}

function stopPolling() {
  clearTimeout(pollTimer);
  pollTimer = null;
}

async function check(id) {
  clearTimeout(pollTimer);
  pollTimer = null;
  let response;
  try { response = await api(`/api/jobs/${id}`); } catch (_) {
    schedulePoll(id, 2500);
    return;
  }
  if (!response.ok) {
    if (response.status >= 500) schedulePoll(id, 2500);
    return;
  }
  const job = await response.json();
  const comparisonOpen = !comparisonView.classList.contains('hidden') && viewerJob === id;
  if (job.id === currentJob) currentJobData = job;
  if (comparisonOpen) {
    $('comparison-meta').textContent = `${describe(job)} · ${when(job.created_at)} · ${outputNames(job)}`;
    renderDownloadLinks(job, $('comparison-download-links'));
    if (job.status === 'done') await openJob(job);
  }
  if (RUNNING.has(job.status)) { settling = 0; schedulePoll(id); }
  else if (job.status === 'done' && !job.has_md && settling < SETTLE_TRIES) {
    settling += 1;
    schedulePoll(id, 700);
  } else { settling = 0; stopPolling(); }
}

function setComparisonPane(name) {
  const source = name === 'source';
  $('comparison-tabs').dataset.active = name;
  $('show-source').setAttribute('aria-selected', String(source));
  $('show-converted').setAttribute('aria-selected', String(!source));
  $('show-source').tabIndex = source ? 0 : -1;
  $('show-converted').tabIndex = source ? -1 : 0;
  $('preview-pane').classList.toggle('mobile-hidden', !source);
  $('output-pane').classList.toggle('mobile-hidden', source);
  syncComparisonPaneAccess();
}

function syncComparisonPaneAccess() {
  const mobile = mobileNavigation.matches;
  const source = $('show-source').getAttribute('aria-selected') === 'true';
  for (const [pane, active] of [[$('preview-pane'), source], [$('output-pane'), !source]]) {
    pane.inert = mobile && !active;
    if (mobile) pane.setAttribute('aria-hidden', String(!active));
    else pane.removeAttribute('aria-hidden');
  }
}

$('show-source').addEventListener('click', () => setComparisonPane('source'));
$('show-converted').addEventListener('click', () => setComparisonPane('converted'));
$('comparison-tabs').addEventListener('keydown', event => {
  if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
  const next = $('show-source').getAttribute('aria-selected') === 'true' ? 'converted' : 'source';
  setComparisonPane(next);
  $(next === 'source' ? 'show-source' : 'show-converted').focus();
  event.preventDefault();
});

window.addEventListener('popstate', () => {
  const id = new URLSearchParams(location.search).get('job');
  if (id) restoreJobFromUrl();
  else showDashboard('uploads', false);
});

// -------------------------------------------------------------------- viewer -- //

let doc = null;
let viewerJob = null;
let pageCount = 0;
let pageIndex = 0;
let hiddenKinds = new Set();
let activeTab = 'rendered';
let wholeDoc = false;
let wholeMarkdown = null;
let zoom = 1;
let selected = null;
let viewerMode = null;

const SVG = 'http://www.w3.org/2000/svg';
const KIND_ORDER = ['heading', 'paragraph', 'text', 'equation', 'figure', 'table', 'rule'];
const previewEmpty = $('preview-empty');
const stageWrap = $('stage-wrap');
const stage = $('stage');
const pageImage = $('page-image');
const overlay = $('overlay');
const legend = $('legend');
const pager = $('pager');
const pageNumber = $('page-number');
const pageTotal = $('page-total');
const pageNote = $('page-note');
const outputEmpty = $('output-empty');
const tabRendered = $('tab-rendered');
const tabText = $('tab-text');
const tabs = $('tabs');

function kindColour(kind) {
  const value = getComputedStyle($root).getPropertyValue(`--k-${kind}`);
  return value.trim() || '#888';
}

function clearViewer() {
  doc = null; viewerJob = null; viewerMode = null; pageCount = 0; pageIndex = 0;
  selected = null; wholeMarkdown = null; wholeDoc = false; hiddenKinds = new Set();
  stageWrap.classList.add('hidden');
  pager.classList.add('hidden');
  previewEmpty.classList.remove('hidden');
  outputEmpty.classList.remove('hidden');
  for (const element of [tabRendered, tabText]) {
    element.classList.add('hidden');
    element.replaceChildren();
  }
  legend.replaceChildren();
  overlay.replaceChildren();
  pageImage.removeAttribute('src');
}

function openPages(job) {
  viewerJob = job.id;
  viewerMode = job.layout;
  doc = null;
  pageCount = job.pages || 0;
  pageIndex = 0;
  wholeMarkdown = null;
  selected = null;
  outputEmpty.classList.remove('hidden');
  if (!pageCount) return clearViewer();
  previewEmpty.classList.add('hidden');
  stageWrap.classList.remove('hidden');
  pager.classList.remove('hidden');
  showPage(0);
}

async function openJob(job) {
  if (!job.has_detection) return openPages(job);
  try {
    const response = await api(`/api/jobs/${job.id}/detection`);
    if (!response.ok) return openPages(job);
    doc = await response.json();
  } catch (_) {
    return openPages(job);
  }
  viewerJob = job.id;
  viewerMode = job.layout || doc.mode;
  pageCount = doc.pages.length;
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
  pageImage.alt = `Source PDF page ${pageIndex + 1} of ${pageCount}`;
  pageNumber.value = String(pageIndex + 1);
  pageTotal.textContent = String(pageCount);
  $('prev-page').disabled = pageIndex === 0;
  $('next-page').disabled = pageIndex === pageCount - 1;
  applyZoom();
  drawOverlay();
  drawLegend();
  renderOutput();
}

function drawOverlay() {
  overlay.replaceChildren();
  const mathpix = viewerMode === 'mathpix' || (doc && doc.mode === 'mathpix');
  overlay.classList.toggle('hidden', mathpix);
  legend.classList.toggle('hidden', mathpix);
  const page = currentPage();
  if (mathpix) {
    pageNote.textContent = 'Source page aligned with the converted content beside it.';
    return;
  }
  if (!page) {
    pageNote.textContent = doc ? '' : 'Source page ready. Converted content is not available yet.';
    return;
  }
  overlay.setAttribute('viewBox', `0 0 ${page.width} ${page.height}`);
  const blocks = page.blocks || [];
  if (!blocks.length) {
    pageNote.textContent = 'No block geometry is available for this page.';
    return;
  }
  pageNote.textContent = `${blocks.length} detected block${blocks.length === 1 ? '' : 's'}`;
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
    rect.addEventListener('click', () => { selected = block.index; drawOverlay(); });
    const title = document.createElementNS(SVG, 'title');
    title.textContent = block.text ? `${block.kind} — ${block.text}` : block.kind;
    rect.appendChild(title);
    overlay.appendChild(rect);
  }
}

function drawLegend() {
  legend.replaceChildren();
  const page = currentPage();
  const present = new Set((page && page.blocks || []).map(block => block.kind));
  for (const kind of KIND_ORDER) {
    if (!present.has(kind)) continue;
    const button = document.createElement('button');
    button.type = 'button';
    button.setAttribute('aria-pressed', String(!hiddenKinds.has(kind)));
    const swatch = document.createElement('span');
    swatch.className = 'legend-swatch';
    swatch.style.background = kindColour(kind);
    button.append(swatch, document.createTextNode(kind));
    button.addEventListener('click', () => {
      hiddenKinds.has(kind) ? hiddenKinds.delete(kind) : hiddenKinds.add(kind);
      drawOverlay(); drawLegend();
    });
    legend.appendChild(button);
  }
}

function resolveImages(markdown) {
  return markdown.replace(
    /(!\[[^\]]*\]\()(?!https?:|data:|\/)([^)\s]+)/g,
    (_, head, target) => `${head}/api/jobs/${viewerJob}/asset/${target}`
  );
}

function scrub(root) {
  root.querySelectorAll('script, style, iframe, object, embed, link, form').forEach(element => element.remove());
  root.querySelectorAll('*').forEach(element => {
    for (const attribute of [...element.attributes]) {
      const name = attribute.name.toLowerCase();
      if (name.startsWith('on')) element.removeAttribute(attribute.name);
      if ((name === 'href' || name === 'src') && /^\s*javascript:/i.test(attribute.value)) {
        element.removeAttribute(attribute.name);
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

// Mathpix does not wrap a paragraph across lines, so every newline it writes is
// one it means. Without this they collapse and a page of separate answers —
// "(a) …", "(b) …" — arrives as one unbroken sentence.
const MARKED_OPTIONS = { gfm: true, breaks: true };

// Markdown has no bullet that reads "(a)", so a labelled list is built out of
// ordinary list items whose first child is the label. Marking the list itself
// here is what lets the stylesheet drop the bullet that would sit beside it.
function dressLists(root) {
  root.querySelectorAll('.mmd-item-label').forEach(label => {
    const list = label.closest('ul, ol');
    if (list) list.classList.add('mmd-labelled');
  });
}

async function markdownForScope() {
  if (!wholeDoc) {
    const page = currentPage();
    return page ? page.markdown || '' : '';
  }
  if (wholeMarkdown === null) {
    try {
      const response = await api(`/api/jobs/${viewerJob}/markdown`);
      wholeMarkdown = response.ok ? (await response.json()).markdown || '' : '';
    } catch (_) { wholeMarkdown = ''; }
  }
  return wholeMarkdown;
}

async function renderOutput() {
  if (!viewerJob) return;
  const markdown = await markdownForScope();
  const hasContent = Boolean(markdown.trim()) || Boolean(doc);
  outputEmpty.classList.toggle('hidden', hasContent);
  for (const [name, element] of [['rendered', tabRendered], ['text', tabText]]) {
    element.classList.toggle('hidden', !hasContent || name !== activeTab);
  }
  if (!hasContent) return;
  if (activeTab === 'rendered') {
    if (markdown.trim()) {
      // Mathpix returns Markdown with LaTeX still in it. `mmd.prepare` rewrites
      // the parts Markdown cannot express and sets the mathematics aside, so
      // that neither this step nor Markdown itself can reinterpret it; `restore`
      // puts it back afterwards, exactly as written, for KaTeX to typeset.
      const converted = mmd.prepare(markdown);
      const html = marked.parse(resolveImages(converted.markdown), MARKED_OPTIONS);
      tabRendered.innerHTML = mmd.restore(html, converted.math);
      scrub(tabRendered);
      dressLists(tabRendered);
      renderMathInElement(tabRendered, { delimiters: MATH_DELIMITERS, throwOnError: false });
    } else {
      tabRendered.innerHTML = '<p class="empty-page">This source page produced no converted content.</p>';
    }
  } else {
    tabText.textContent = markdown || '(empty page)';
  }
}

tabs.addEventListener('click', event => {
  const button = event.target.closest('button[data-tab]');
  if (!button) return;
  activeTab = button.dataset.tab;
  for (const other of tabs.children) {
    const selected = other === button;
    other.setAttribute('aria-selected', String(selected));
    other.tabIndex = selected ? 0 : -1;
  }
  renderOutput();
});

tabs.addEventListener('keydown', event => {
  if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
  const buttons = [...tabs.querySelectorAll('button')];
  const current = buttons.findIndex(button => button.getAttribute('aria-selected') === 'true');
  const next = buttons[(current + 1) % buttons.length];
  next.click(); next.focus(); event.preventDefault();
});

$('scope').addEventListener('click', () => {
  wholeDoc = !wholeDoc;
  $('scope').textContent = wholeDoc ? 'Whole document' : 'This page';
  renderOutput();
});

$('copy').addEventListener('click', async () => {
  const button = $('copy');
  try {
    await navigator.clipboard.writeText(await markdownForScope());
    button.textContent = 'Copied';
  } catch (_) { button.textContent = 'Copy failed'; }
  setTimeout(() => { button.textContent = 'Copy'; }, 1200);
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
  if (!pageCount || comparisonView.classList.contains('hidden')) return;
  const typing = /^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement.tagName);
  if (typing || event.metaKey || event.ctrlKey || event.altKey) return;
  if (event.key === 'ArrowLeft') { showPage(pageIndex - 1); event.preventDefault(); }
  if (event.key === 'ArrowRight') { showPage(pageIndex + 1); event.preventDefault(); }
});

// ------------------------------------------------------------------- account -- //

api('/api/auth/me').then(response => response.ok ? response.json() : null).then(user => {
  if (!user) return;
  // The address is what identifies the account, and the only thing an account
  // has: sign-up asks for an email and a password and nothing else.
  $('user-email').textContent = user.email;
  $('user-email').title = user.email;

  const avatar = document.querySelector('.account-avatar');
  if (!avatar) return;
  avatar.textContent = user.email.slice(0, 1).toUpperCase();
}).catch(() => {});

$('sign-out').addEventListener('click', async () => {
  signingOut = true;
  await api('/api/auth/logout', { method: 'POST' }).catch(() => {});
  location.href = '/login';
});

// ---------------------------------------------------------------- initialization -- //

Promise.all([loadConfig(), loadHistory()]).then(restoreJobFromUrl);
