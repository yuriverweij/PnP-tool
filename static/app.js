/* ── PnP Tool - add bleed — app.js ── */

'use strict';

// ── Card size presets ──
const PRESETS = {
  poker: { w: 63,   h: 88  },
  tarot: { w: 70,   h: 120 },
  mini:  { w: 41,   h: 63  },
};

const PAGE_SIZES = {
  A4:     { w: 210,   h: 297   },
  Letter: { w: 215.9, h: 279.4 },
  A3:     { w: 297,   h: 420   },
};

// ── State ──
const state = {
  frontFiles: [],        // File[]  ordered list of front images
  singleBackFile: null,  // File | null
  multiBackFiles: [],    // File[]  individual backs
  defaultBackFile: null, // File | null
  overrideFiles: {},     // { [index: string]: File }
  cardWmm: 63,
  cardHmm: 88,
};

// ── DOM helpers ──
const $ = id => document.getElementById(id);
const show = el => el.classList.remove('hidden');
const hide = el => el.classList.add('hidden');

// ── Init ──
document.addEventListener('DOMContentLoaded', () => {
  setupDropZone('drop-fronts',       'file-fronts',       'browse-fronts',       onFrontsSelected);
  setupDropZone('drop-single-back',  'file-single-back',  'browse-single-back',  onSingleBackSelected);
  setupDropZone('drop-multi-back',   'file-multi-back',   'browse-multi-back',   onMultiBacksSelected);
  setupDropZone('drop-default-back', 'file-default-back', 'browse-default-back', onDefaultBackSelected);

  document.querySelectorAll('input[name="back-mode"]').forEach(r =>
    r.addEventListener('change', onBackModeChange));

  $('preset-select').addEventListener('change', onPresetChange);
  $('card-w').addEventListener('input', onCustomSizeChange);
  $('card-h').addEventListener('input', onCustomSizeChange);

  $('bleed-mm').addEventListener('input', updateGridInfo);
  $('page-size').addEventListener('change', updateGridInfo);
  $('margin-mm').addEventListener('change', updateGridInfo);

  $('remove-single-back').addEventListener('click',  () => removeSingleBack());
  $('remove-default-back').addEventListener('click', () => removeDefaultBack());

  $('btn-process').addEventListener('click', onProcess);
  $('btn-reset').addEventListener('click', resetSession);
});

// ── Drop zone setup ──
function setupDropZone(zoneId, inputId, btnId, handler) {
  const zone  = $(zoneId);
  const input = $(inputId);
  const btn   = $(btnId);

  btn.addEventListener('click', () => input.click());
  input.addEventListener('change', () => handler(Array.from(input.files)));

  zone.addEventListener('dragover',  e => { e.preventDefault(); zone.classList.add('drag-over'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
  zone.addEventListener('drop', e => {
    e.preventDefault();
    zone.classList.remove('drag-over');
    handler(Array.from(e.dataTransfer.files).filter(f => f.type.startsWith('image/')));
  });
  zone.addEventListener('click', e => {
    if (e.target === zone || e.target.classList.contains('drop-inner')) input.click();
  });
}

// ── Front images ──
async function onFrontsSelected(files) {
  if (!files.length) return;
  state.frontFiles = [...state.frontFiles, ...files];
  renderFrontList();
  show($('step-backs'));
  show($('step-output'));
  updateGridInfo();

  // Auto-detect card size from first new image
  if (files.length > 0) {
    const detected = await detectSize(files[0]);
    applyDetected(detected);
  }
}

function renderFrontList() {
  const list = $('fronts-list');
  list.innerHTML = '';
  state.frontFiles.forEach((file, i) => {
    const url  = URL.createObjectURL(file);
    const item = createThumb(url, i + 1, () => {
      state.frontFiles.splice(i, 1);
      renderFrontList();
      renderOverrideList();
      updateProcessBtn();
    });
    item.draggable = true;
    item.addEventListener('dragstart', e => {
      e.dataTransfer.setData('text/plain', i);
      item.classList.add('dragging');
    });
    item.addEventListener('dragend', () => item.classList.remove('dragging'));
    item.addEventListener('dragover', e => { e.preventDefault(); item.classList.add('drag-target'); });
    item.addEventListener('dragleave', () => item.classList.remove('drag-target'));
    item.addEventListener('drop', e => {
      e.preventDefault();
      item.classList.remove('drag-target');
      const from = parseInt(e.dataTransfer.getData('text/plain'));
      if (from === i) return;
      const moved = state.frontFiles.splice(from, 1)[0];
      state.frontFiles.splice(i, 0, moved);
      // Also move overrides
      const ovFrom = state.overrideFiles[String(from)];
      const ovTo   = state.overrideFiles[String(i)];
      delete state.overrideFiles[String(from)];
      delete state.overrideFiles[String(i)];
      if (ovFrom) state.overrideFiles[String(i)]    = ovFrom;
      if (ovTo)   state.overrideFiles[String(from)] = ovTo;
      renderFrontList();
      renderOverrideList();
    });
    list.appendChild(item);
  });

  if (state.frontFiles.length) {
    show(list);
    show($('size-section'));
  } else {
    hide(list);
  }
  updateProcessBtn();
}

function createThumb(url, num, onRemove) {
  const div  = document.createElement('div');
  div.className = 'card-thumb';

  const img  = document.createElement('img');
  img.src = url;
  img.alt = `Card ${num}`;

  const badge  = document.createElement('span');
  badge.className = 'card-num';
  badge.textContent = num;

  const rmBtn = document.createElement('button');
  rmBtn.className = 'remove-card';
  rmBtn.type = 'button';
  rmBtn.textContent = '✕';
  rmBtn.addEventListener('click', e => { e.stopPropagation(); onRemove(); });

  div.append(img, badge, rmBtn);
  return div;
}

// ── Size detection ──
async function detectSize(file) {
  const fd = new FormData();
  fd.append('file', file);
  try {
    const res  = await fetch('/api/detect-size', { method: 'POST', body: fd });
    if (!res.ok) return null;
    return await res.json();
  } catch { return null; }
}

function applyDetected(info) {
  if (!info || !info.has_dpi_metadata) {
    $('size-hint').textContent = 'No DPI metadata found — please select or enter card size.';
    return;
  }
  const { detected_width_mm: w, detected_height_mm: h, suggested_preset: preset } = info;
  if (preset && PRESETS[preset]) {
    $('preset-select').value = preset;
    state.cardWmm = PRESETS[preset].w;
    state.cardHmm = PRESETS[preset].h;
    hide($('custom-w-group'));
    hide($('custom-h-group'));
    $('size-hint').textContent = `Detected ${w} × ${h} mm — using ${preset} preset.`;
  } else {
    $('preset-select').value = 'custom';
    $('card-w').value = w;
    $('card-h').value = h;
    state.cardWmm = w;
    state.cardHmm = h;
    show($('custom-w-group'));
    show($('custom-h-group'));
    $('size-hint').textContent = `Detected ${w} × ${h} mm.`;
  }
  updateGridInfo();
}

function onPresetChange() {
  const val = $('preset-select').value;
  if (val === 'custom') {
    show($('custom-w-group'));
    show($('custom-h-group'));
  } else {
    hide($('custom-w-group'));
    hide($('custom-h-group'));
    state.cardWmm = PRESETS[val].w;
    state.cardHmm = PRESETS[val].h;
    updateGridInfo();
  }
}

function onCustomSizeChange() {
  state.cardWmm = parseFloat($('card-w').value) || 63;
  state.cardHmm = parseFloat($('card-h').value) || 88;
  updateGridInfo();
}

// ── Grid info ──
function updateGridInfo() {
  const bleed   = parseFloat($('bleed-mm').value) || 0;
  const margin  = parseFloat($('margin-mm').value) || 5;
  const pageKey = $('page-size').value;
  const page    = PAGE_SIZES[pageKey];
  const bw = state.cardWmm + 2 * bleed;
  const bh = state.cardHmm + 2 * bleed;

  const uw = page.w - 2 * margin;
  const uh = page.h - 2 * margin;

  const colsN = Math.floor(uw / bw);
  const rowsN = Math.floor(uh / bh);
  const colsR = Math.floor(uw / bh);
  const rowsR = Math.floor(uh / bw);

  const useRotated = bw !== bh && rowsR * colsR > rowsN * colsN;
  const cols = useRotated ? colsR : colsN;
  const rows = useRotated ? rowsR : rowsN;

  if (cols < 1 || rows < 1) {
    $('grid-info').textContent = 'Cards too large to fit on selected page with current settings.';
    $('btn-process').disabled = true;
  } else {
    const note = useRotated ? ' (cards auto-rotated 90° for better fit)' : '';
    $('grid-info').textContent =
      `${rows} × ${cols} cards per page (${rows * cols} total per front/back sheet)${note}. ` +
      `Bleed card: ${bw.toFixed(1)} × ${bh.toFixed(1)} mm.`;
    updateProcessBtn();
  }
}

// ── Back mode ──
function onBackModeChange() {
  const mode = document.querySelector('input[name="back-mode"]:checked').value;
  $('mode-single').classList.toggle('hidden',   mode !== 'single');
  $('mode-individual').classList.toggle('hidden', mode !== 'individual');
  $('mode-override').classList.toggle('hidden', mode !== 'default_override');
  updateProcessBtn();
  updateGridInfo();
}

// ── Single back ──
function onSingleBackSelected(files) {
  if (!files.length) return;
  state.singleBackFile = files[0];
  $('single-back-img').src = URL.createObjectURL(files[0]);
  show($('single-back-preview'));
  updateProcessBtn();
}
function removeSingleBack() {
  state.singleBackFile = null;
  $('single-back-img').src = '';
  hide($('single-back-preview'));
  updateProcessBtn();
}

// ── Multi backs ──
function onMultiBacksSelected(files) {
  if (!files.length) return;
  state.multiBackFiles = [...state.multiBackFiles, ...files];
  renderBackList();
}
function renderBackList() {
  const list = $('backs-list');
  list.innerHTML = '';
  state.multiBackFiles.forEach((file, i) => {
    const url  = URL.createObjectURL(file);
    const item = createThumb(url, i + 1, () => {
      state.multiBackFiles.splice(i, 1);
      renderBackList();
    });
    list.appendChild(item);
  });
  state.multiBackFiles.length ? show(list) : hide(list);
  updateProcessBtn();
}

// ── Default back + overrides ──
function onDefaultBackSelected(files) {
  if (!files.length) return;
  state.defaultBackFile = files[0];
  $('default-back-img').src = URL.createObjectURL(files[0]);
  show($('default-back-preview'));
  renderOverrideList();
  updateProcessBtn();
}
function removeDefaultBack() {
  state.defaultBackFile = null;
  $('default-back-img').src = '';
  hide($('default-back-preview'));
  hide($('override-list'));
  updateProcessBtn();
}

function renderOverrideList() {
  const container = $('override-list');
  if (!state.defaultBackFile || !state.frontFiles.length) { hide(container); return; }
  show(container);
  container.innerHTML = '';

  state.frontFiles.forEach((frontFile, i) => {
    const row = document.createElement('div');
    row.className = 'override-row';

    const frontImg = document.createElement('img');
    frontImg.src = URL.createObjectURL(frontFile);
    frontImg.alt = `Front ${i + 1}`;

    const overrideFile = state.overrideFiles[String(i)];
    const backImg = document.createElement('img');
    backImg.src = overrideFile
      ? URL.createObjectURL(overrideFile)
      : URL.createObjectURL(state.defaultBackFile);
    backImg.alt = 'Back';

    const label = document.createElement('span');
    label.className = 'card-label';
    label.textContent = `Card ${i + 1}`;

    const badge = document.createElement('span');
    badge.className = 'back-indicator' + (overrideFile ? ' custom' : '');
    badge.textContent = overrideFile ? 'custom' : 'default';

    const btnGroup = document.createElement('div');
    btnGroup.className = 'override-btn-group';

    const changeBtn = document.createElement('button');
    changeBtn.type = 'button';
    changeBtn.className = 'icon-btn';
    changeBtn.textContent = 'Change';
    changeBtn.addEventListener('click', () => {
      const picker = document.createElement('input');
      picker.type = 'file';
      picker.accept = 'image/*';
      picker.onchange = () => {
        if (picker.files.length) {
          state.overrideFiles[String(i)] = picker.files[0];
          renderOverrideList();
        }
      };
      picker.click();
    });
    btnGroup.appendChild(changeBtn);

    if (overrideFile) {
      const resetBtn = document.createElement('button');
      resetBtn.type = 'button';
      resetBtn.className = 'icon-btn';
      resetBtn.textContent = 'Reset';
      resetBtn.addEventListener('click', () => {
        delete state.overrideFiles[String(i)];
        renderOverrideList();
      });
      btnGroup.appendChild(resetBtn);
    }

    row.append(frontImg, backImg, label, badge, btnGroup);
    container.appendChild(row);
  });
}

// ── Process button state ──
function updateProcessBtn() {
  const mode = document.querySelector('input[name="back-mode"]:checked')?.value;
  let ready = state.frontFiles.length > 0;

  if (mode === 'single')           ready = ready && !!state.singleBackFile;
  if (mode === 'individual')       ready = ready && state.multiBackFiles.length > 0;
  if (mode === 'default_override') ready = ready && !!state.defaultBackFile;
  // mode === 'none' requires only fronts, already handled by the base check

  $('btn-process').disabled = !ready;
}

// ── Reset ──
function resetSession() {
  // Clear state
  state.frontFiles    = [];
  state.singleBackFile  = null;
  state.multiBackFiles  = [];
  state.defaultBackFile = null;
  state.overrideFiles   = {};
  state.cardWmm = 63;
  state.cardHmm = 88;

  // Hide dynamic sections
  hide($('fronts-list'));
  hide($('size-section'));
  hide($('step-backs'));
  hide($('step-output'));
  hide($('backs-list'));
  hide($('single-back-preview'));
  hide($('default-back-preview'));
  hide($('override-list'));
  hide($('status-area'));

  // Reset back mode to single
  document.querySelector('input[name="back-mode"][value="single"]').checked = true;
  $('mode-single').classList.remove('hidden');
  $('mode-individual').classList.add('hidden');
  $('mode-override').classList.add('hidden');

  // Reset card size
  $('preset-select').value = 'poker';
  hide($('custom-w-group'));
  hide($('custom-h-group'));
  $('size-hint').textContent = '';

  // Clear previews/imgs
  $('single-back-img').src = '';
  $('default-back-img').src = '';
  $('fronts-list').innerHTML = '';
  $('backs-list').innerHTML = '';

  // Reset file inputs so the same files can be re-selected
  ['file-fronts', 'file-single-back', 'file-multi-back', 'file-default-back'].forEach(id => {
    $(id).value = '';
  });

  $('btn-process').disabled = true;
  $('grid-info').textContent = '';
}

// ── Process ──
async function onProcess() {
  const mode = document.querySelector('input[name="back-mode"]:checked').value;

  const bleedMm         = parseFloat($('bleed-mm').value)          || 3;
  const sourceMm        = parseFloat($('source-mm').value)         || 1;
  const trimMm          = parseFloat($('trim-mm').value)           || 0;
  const marginMm        = parseFloat($('margin-mm').value)         || 5;
  const pageSize        = $('page-size').value;
  const flipDir         = $('flip-dir').value;
  const jpegQuality     = parseInt($('jpeg-quality').value)       || 95;
  const cutMarksFronts  = $('cut-marks-fronts').checked;
  const cutMarksBacks   = $('cut-marks-backs').checked;
  const cutMarkLength   = parseFloat($('cut-mark-length').value)    || 3;
  const cutMarkThick    = parseFloat($('cut-mark-thickness').value) || 0.2;

  const cardW = state.cardWmm;
  const cardH = state.cardHmm;

  const fd = new FormData();

  state.frontFiles.forEach(f => fd.append('fronts', f));
  fd.append('back_mode',         mode);
  fd.append('card_width_mm',     cardW);
  fd.append('card_height_mm',    cardH);
  fd.append('output_page_size',  pageSize);
  fd.append('output_margin_mm',  marginMm);
  fd.append('bleed_mm',          bleedMm);
  fd.append('source_mm',         sourceMm);
  fd.append('trim_mm',           trimMm);
  fd.append('flip_direction',       flipDir);
  fd.append('jpeg_quality',          jpegQuality);
  fd.append('cut_marks_fronts',     cutMarksFronts);
  fd.append('cut_marks_backs',      cutMarksBacks);
  fd.append('cut_mark_length_mm',   cutMarkLength);
  fd.append('cut_mark_thickness_mm', cutMarkThick);

  if (mode === 'single') {
    fd.append('default_back', state.singleBackFile);

  } else if (mode === 'individual') {
    state.multiBackFiles.forEach(f => fd.append('backs', f));

  } else if (mode === 'default_override') {
    fd.append('default_back', state.defaultBackFile);

    // Encode overrides as base64 JSON
    const overridesB64 = {};
    for (const [idx, file] of Object.entries(state.overrideFiles)) {
      const b64 = await fileToBase64(file);
      overridesB64[idx] = b64;
    }
    fd.append('override_backs', JSON.stringify(overridesB64));
  }

  // Show spinner
  const statusArea = $('status-area');
  const statusMsg  = $('status-msg');
  show(statusArea);
  statusMsg.textContent = 'Processing…';
  statusMsg.className = '';
  $('spinner').style.display = '';
  $('btn-process').disabled = true;

  try {
    const res = await fetch('/api/process', { method: 'POST', body: fd });
    if (!res.ok) {
      let detail = 'Processing failed.';
      try { detail = (await res.json()).detail || detail; } catch {}
      statusMsg.textContent = detail;
      statusMsg.className = 'msg-error';
      $('spinner').style.display = 'none';
      updateProcessBtn();
      return;
    }

    const blob = await res.blob();
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href     = url;
    a.download = 'pnp_output.pdf';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 60_000);

    statusMsg.textContent = `Done! ${state.frontFiles.length} cards processed.`;
    statusMsg.className = 'msg-success';
    $('spinner').style.display = 'none';
  } catch (err) {
    statusMsg.textContent = 'Network error: ' + err.message;
    statusMsg.className = 'msg-error';
    $('spinner').style.display = 'none';
  }

  updateProcessBtn();
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload  = () => resolve(reader.result.split(',')[1]);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}
