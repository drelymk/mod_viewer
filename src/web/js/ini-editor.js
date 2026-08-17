// Session-backed INI editor. Applying updates memory only; Export is the
// deliberately separate boundary that writes physical files.

import { alertDialog, confirmDialog } from './dialogs.js';
import { registerIniHighlightMode } from './ini-highlight.js';

const $ = (id) => document.getElementById(id);
let modPath = null;
let currentIni = null;
let loadedText = '';
let onApplied = null;
const textEditor = $('ini-editor-text');
window.ace.config.set('basePath', 'lib/ace');
registerIniHighlightMode();
const iniEditor = window.ace.edit(textEditor, {
  mode: 'ace/mode/mod_viewer_ini',
  theme: 'ace/theme/tomorrow_night',
  fontFamily: 'Consolas, "Courier New", monospace',
  fontSize: 12,
  showPrintMargin: false,
  showGutter: true,
  showFoldWidgets: true,
  highlightActiveLine: true,
  highlightGutterLine: true,
  highlightSelectedWord: true,
  displayIndentGuides: false,
  useSoftTabs: true,
  tabSize: 4,
  wrap: false,
  scrollPastEnd: 0.1,
  enableKeyboardAccessibility: true,
});
iniEditor.session.setUseWorker(false);
iniEditor.renderer.setScrollMargin(8, 8);
iniEditor.textInput.getElement().setAttribute('aria-label', 'INI file contents');

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
  const target = Math.max(1, Math.min(Number(line) || 1, iniEditor.session.getLength()));
  const row = target - 1;
  const Range = window.ace.require('ace/range').Range;
  iniEditor.focus();
  iniEditor.selection.setRange(new Range(row, 0, row, iniEditor.session.getLine(row).length));
  iniEditor.scrollToLine(row, true, true, () => {});
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
  iniEditor.setValue(result.text, -1);
  iniEditor.clearSelection();
  showError();
  $('ini-editor-backdrop').classList.add('show');
  requestAnimationFrame(() => {
    iniEditor.resize(true);
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
  if (iniEditor.getValue() !== loadedText) {
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
    const text = iniEditor.getValue();
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
iniEditor.commands.addCommand({
  name: 'saveIniEditor',
  bindKey: { win: 'Ctrl-S', mac: 'Command-S' },
  exec: applyEditor,
});
iniEditor.commands.addCommand({
  name: 'closeIniEditor',
  bindKey: { win: 'Esc', mac: 'Esc' },
  exec: closeEditor,
});
// Ace's search field first returns focus to the editor on Escape and only
// hides itself on a later press. Close it in one press, while preventing that
// same key event from also reaching the modal-level Escape handler.
document.addEventListener('keydown', (event) => {
  const search = textEditor.querySelector('.ace_search');
  if (event.key === 'Escape' && search && getComputedStyle(search).display !== 'none') {
    event.preventDefault();
    event.stopPropagation();
    iniEditor.searchBox.hide();
    iniEditor.focus();
  }
}, true);
$('ini-editor-backdrop').addEventListener('click', (event) => {
  if (event.target.id === 'ini-editor-backdrop') closeEditor();
});
document.addEventListener('click', (event) => {
  if (!event.target.closest('.ini-view-wrap')) hideFileMenu();
});
document.addEventListener('keydown', (event) => {
  const inAce = event.target instanceof Element && event.target.closest('#ini-editor-text');
  if (event.key === 'Escape') {
    if ($('ini-file-menu').classList.contains('show')) hideFileMenu();
    else if (!inAce && $('ini-editor-backdrop').classList.contains('show')) closeEditor();
  }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's' &&
      !inAce && $('ini-editor-backdrop').classList.contains('show')) {
    event.preventDefault();
    applyEditor();
  }
});
