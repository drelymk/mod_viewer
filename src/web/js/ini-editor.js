// Session-backed INI editor. Applying updates memory only; Export is the
// deliberately separate boundary that writes physical files.

import { alertDialog, confirmDialog } from './dialogs.js';
import PrismLive from '../lib/prism-live/prism-live.mjs';

const $ = (id) => document.getElementById(id);
let modPath = null;
let currentIni = null;
let loadedText = '';
let onApplied = null;
const textEditor = $('ini-editor-text');
const prismEditor = PrismLive.create(textEditor);
prismEditor.wrapper.classList.add('ini-editor-shell');

export function setIniEditorContext(path, changeCallback) {
  modPath = path;
  onApplied = changeCallback;
  $('ini-view-btn').disabled = !path;
}

function showError(message = '') {
  const error = $('ini-editor-error');
  error.textContent = message;
  error.style.display = message ? 'block' : 'none';
}

function hideFileMenu() {
  $('ini-file-menu').classList.remove('show');
}

function jumpToLine(line) {
  const editor = textEditor;
  const lines = editor.value.split('\n');
  const target = Math.max(1, Math.min(Number(line) || 1, lines.length));
  let start = 0;
  for (let i = 1; i < target; i += 1) start += lines[i - 1].length + 1;
  editor.focus();
  editor.setSelectionRange(start, start + lines[target - 1].length);
  const lineHeight = parseFloat(getComputedStyle(editor).lineHeight) || 18;
  editor.scrollTop = Math.max(0, (target - 1) * lineHeight - editor.clientHeight / 3);
  prismEditor.syncScroll();
}

export async function openIniEditor(iniName, line = 1) {
  hideFileMenu();
  if (!modPath || !iniName) return;
  const result = await window.pywebview.api.get_ini_text(modPath, iniName);
  if (result.error) {
    await alertDialog('Could not open INI:\n\n' + result.error);
    return;
  }
  currentIni = result.ini;
  loadedText = result.text;
  $('ini-editor-title').textContent = result.ini;
  $('ini-editor-status').textContent = result.dirty
    ? 'Modified in memory — Export has not written it to disk.'
    : 'Changes stay in memory until Export.';
  textEditor.value = result.text;
  prismEditor.update(true);
  showError();
  $('ini-editor-backdrop').classList.add('show');
  requestAnimationFrame(() => {
    prismEditor.syncStyles();
    jumpToLine(line);
  });
}

async function chooseIni() {
  if (!modPath) return;
  const files = await window.pywebview.api.list_ini_files(modPath);
  if (!files.length) {
    await alertDialog('This mod has no active INI files.');
    return;
  }
  if (files.length === 1) {
    await openIniEditor(files[0].value);
    return;
  }
  const menu = $('ini-file-menu');
  menu.replaceChildren();
  for (const file of files) {
    const button = document.createElement('button');
    button.type = 'button';
    button.role = 'menuitem';
    button.textContent = file.dirty ? `${file.label}  •` : file.label;
    button.title = file.dirty ? 'Modified in memory' : file.label;
    button.addEventListener('click', () => openIniEditor(file.value));
    menu.appendChild(button);
  }
  menu.classList.toggle('show');
}

async function closeEditor() {
  if (textEditor.value !== loadedText) {
    const close = await confirmDialog('Discard the unapplied changes in this editor?');
    if (!close) return;
  }
  $('ini-editor-backdrop').classList.remove('show');
  currentIni = null;
}

async function applyEditor() {
  if (!modPath || !currentIni) return;
  const button = $('ini-editor-apply');
  button.disabled = true;
  showError();
  try {
    const text = textEditor.value;
    const result = await window.pywebview.api.update_ini_text(modPath, currentIni, text);
    if (result.error) {
      showError(result.error);
      return;
    }
    loadedText = text;
    $('ini-editor-status').textContent = result.pending
      ? 'Applied in memory — Export has not written it to disk.'
      : 'Matches the exported file.';
    if (onApplied) await onApplied();
    $('ini-editor-backdrop').classList.remove('show');
    currentIni = null;
  } catch (error) {
    showError(String(error));
  } finally {
    button.disabled = false;
  }
}

$('ini-view-btn').addEventListener('click', chooseIni);
$('ini-editor-close').addEventListener('click', closeEditor);
$('ini-editor-close-x').addEventListener('click', closeEditor);
$('ini-editor-apply').addEventListener('click', applyEditor);
$('ini-editor-backdrop').addEventListener('click', (event) => {
  if (event.target.id === 'ini-editor-backdrop') closeEditor();
});
document.addEventListener('click', (event) => {
  if (!event.target.closest('.ini-view-wrap')) hideFileMenu();
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') {
    if ($('ini-file-menu').classList.contains('show')) hideFileMenu();
    else if ($('ini-editor-backdrop').classList.contains('show')) closeEditor();
  }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's' &&
      $('ini-editor-backdrop').classList.contains('show')) {
    event.preventDefault();
    applyEditor();
  }
});
