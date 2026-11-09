import * as gemini from './gemini.js';
import { LANGUAGES, applyLocale, locale, setLocale, t } from './i18n.js';
import { open as openNativeDialog } from '@tauri-apps/plugin-dialog';
import { exists as fileExists, mkdir, readDir, readFile, readTextFile, rename, writeFile, writeTextFile } from '@tauri-apps/plugin-fs';
import { join as joinPath } from '@tauri-apps/api/path';

const DB_NAME = 'escript-ai-desktop';
const DB_VERSION = 3;
const state = {
  db: null,
  project: null,
  pages: [],
  selectedPage: null,
  imageUrl: null,
  imageZoom: 1,
  drawMode: null,
  selectedRegionId: null,
  selectedLineId: null,
  regions: [],
  lines: [],
  saveTimer: null,
  sourceAttemptId: null,
  geminiReady: false,
  reading: false,
};

const app = document.querySelector('#app');

app.innerHTML = `
  <div class="window">
    <header class="titlebar">
      <img class="brand-wordmark" src="/escript-ai-wordmark.svg" alt="Escript AI" />
      <span class="titlebar-divider"></span>
      <span id="project-title" class="titlebar-project">No project open</span>
      <div class="titlebar-spacer"></div>
      <span class="local-badge"><span class="status-dot"></span>Stored on this device</span>
    </header>

    <div class="workspace">
      <nav class="global-nav" aria-label="Main navigation">
        <button class="global-nav-item active" aria-current="page" title="Projects"><span aria-hidden="true">▤</span><small>Projects</small></button>
        <button class="global-nav-item" id="ai-settings" title="AI settings"><span aria-hidden="true">✦</span><small>AI</small></button>
        <div class="global-nav-spacer"></div>
        <button class="global-nav-item" id="about-app" title="About Escript AI"><span aria-hidden="true">i</span><small>About</small></button>
      </nav>
      <aside class="library" aria-label="Project and page navigation">
        <div class="library-top">
          <div class="section-heading">Projects</div>
          <button id="new-project" class="icon-button" aria-label="Create project" title="Create project">＋</button>
        </div>
        <div id="project-switcher" class="project-switcher"></div>
        <div class="pages-heading">
          <span>Pages</span>
          <button id="add-pages" class="text-button" disabled>＋ <span>Add</span></button>
          <input id="image-input" type="file" accept="image/png,image/jpeg,image/webp" multiple hidden />
        </div>
        <div id="page-list" class="page-list"></div>
        <div class="library-foot">
          <div class="local-note-icon" aria-hidden="true">⌂</div>
          <div class="library-foot-copy"><strong>Local project</strong><span id="project-folder-status">Images and drafts stay on this device.</span><div class="project-file-actions"><button id="open-project-folder" class="text-button">Open or choose project folder</button><button id="backup-project" class="text-button" disabled>Save project file</button><button id="restore-project" class="text-button">Open project file</button><button id="export-project-text" class="text-button" disabled>Export all text</button><input id="project-file-input" type="file" accept="application/vnd.escriptai.project+json,.escriptai,application/json" hidden /></div></div>
        </div>
      </aside>

      <main class="reading-desk">
        <div class="desk-toolbar">
          <div class="breadcrumb"><span id="crumb-project">Workspace</span><span class="crumb-slash">/</span><strong id="crumb-page">Choose a page</strong></div>
          <div class="toolbar-actions">
            <button id="export-text" class="quiet-button" disabled>Export text</button>
            <button id="export-page-xml" class="quiet-button" disabled>Export PAGE XML</button>
            <button id="run-ai" class="primary-button" disabled>Read with AI <span aria-hidden="true">↗</span></button>
          </div>
        </div>

        <div id="empty-state" class="empty-state">
          <div class="empty-art" aria-hidden="true"><span class="empty-page-icon">▧</span></div>
          <h1>Choose a document</h1>
          <p>Create a project, then add an image of the page you want to read.</p>
          <button id="empty-new-project" class="primary-button">Create a project</button>
        </div>

        <section id="editor-layout" class="editor-layout" hidden>
          <div class="image-pane">
            <div class="pane-topline"><span>Source image</span><span class="image-tools"><span id="image-dimensions" class="muted-label"></span><button id="zoom-out" class="zoom-button" aria-label="Zoom out" title="Zoom out" disabled>−</button><button id="zoom-reset" class="zoom-button zoom-readout" aria-label="Fit image" title="Fit image">Fit</button><button id="zoom-in" class="zoom-button" aria-label="Zoom in" title="Zoom in" disabled>＋</button></span></div>
            <div id="image-stage" class="image-stage">
              <div id="image-placeholder" class="image-placeholder">Select an image page</div>
              <div id="image-canvas" class="image-canvas" hidden><img id="source-image" alt="Selected archival page" /><svg id="layout-overlay" viewBox="0 0 1 1" preserveAspectRatio="none" aria-label="Editable page regions and lines"></svg></div>
            </div>
            <div class="image-caption"><span id="image-name"></span><span>Original image</span></div>
            <div class="layout-tools"><button id="draw-region" class="quiet-button small-button">Draw region</button><button id="draw-line" class="quiet-button small-button" disabled>Draw line</button><button id="review-layout" class="text-button" disabled>Mark layout reviewed</button><button id="delete-layout" class="text-button" disabled>Delete selection</button><span id="layout-status">Select a page to edit its layout.</span></div>
          </div>

          <section class="text-pane" aria-label="Transcription workspace">
            <div class="text-pane-head">
              <div><div class="pane-topline">Working text</div><div class="draft-state"><span class="draft-dot"></span><span id="draft-label">No text yet</span></div></div>
              <div><button id="chat-open" class="quiet-button small-button">Page chat</button> <button id="history-toggle" class="quiet-button small-button" aria-expanded="false">Attempts <span id="attempt-count" class="count-pill">0</span></button></div>
            </div>
            <label class="visually-hidden" for="transcript">Edit transcription text</label>
            <textarea id="transcript" spellcheck="false" placeholder="Text will appear here after a recognition attempt, or type a transcription yourself."></textarea>
            <div class="editor-footer"><span id="save-state">No unsaved changes</span><span id="character-count">0 characters</span></div>
            <section class="line-editor" aria-label="Line-level transcription">
              <div class="line-editor-heading"><strong>Lines</strong><span id="line-count">0</span></div>
              <div id="line-list" class="line-list"><div class="attempt-empty">Draw a region, then add lines over the image.</div></div>
            </section>

            <div class="recognition-controls">
              <div class="controls-title">Recognition guidance</div>
              <label for="preset">Reading approach</label>
              <select id="preset">
                <option value="diplomatic">Diplomatic transcription</option>
                <option value="expanded">Expand common abbreviations</option>
                <option value="uncertain">Mark uncertain readings</option>
              </select>
              <label for="instruction">Additional instruction <span>Optional</span></label>
              <textarea id="instruction" class="instruction-box" rows="2" placeholder="For example: preserve original spelling and punctuation."></textarea>
              <label for="compare-models">Compare Gemini models <span>comma-separated</span></label>
              <input id="compare-models" class="instruction-box" placeholder="Enter model IDs to compare" />
              <div class="ai-extra-actions"><button id="compare-run" class="quiet-button small-button" disabled>Compare models</button><button id="suggest-layout" class="quiet-button small-button" disabled>Suggest layout with AI</button></div>
              <div class="engine-note"><span class="engine-note-icon">i</span><span id="engine-note-text">Checking AI settings…</span></div>
            </div>

            <div id="attempt-panel" class="attempt-panel" hidden>
              <div class="attempt-heading"><strong>Recognition attempts</strong><button id="close-history" class="icon-button" aria-label="Close attempt history">×</button></div>
              <div id="attempt-list" class="attempt-list"></div>
              <div class="attempt-heading revision-heading"><strong>Saved text versions</strong></div>
              <div id="revision-list" class="attempt-list"></div>
            </div>
          </section>
        </section>
      </main>
    </div>
    <div id="toast" class="toast" role="status" aria-live="polite"></div>
    <dialog id="project-dialog" class="project-dialog">
      <form method="dialog" id="project-form">
        <div class="dialog-kicker">New workspace</div>
        <h2>Create a project</h2>
        <p>Your project will be stored in this app on this device.</p>
        <label for="project-name">Project name</label>
        <input id="project-name" name="project-name" maxlength="100" required placeholder="e.g. Parish register, 1842–1860" />
        <div class="dialog-actions"><button id="cancel-project" class="quiet-button" value="cancel">Cancel</button><button id="create-project" class="primary-button" value="default">Create project</button></div>
      </form>
    </dialog>
    <dialog id="ai-dialog" class="project-dialog">
      <form method="dialog" id="ai-form">
        <div class="dialog-kicker">AI settings</div>
        <h2>Google Gemini</h2>
        <p>Paid API. Each “Read with AI” sends the selected page image to Google and is billed to the account that owns the key. Nothing is sent without your confirmation.</p>
        <label for="ai-model">Model</label>
        <input id="ai-model" maxlength="80" spellcheck="false" />
        <label for="ui-language">Interface language</label>
        <select id="ui-language" class="ui-language-select"></select>
        <div id="ai-key-state" class="ai-key-state"></div>
        <div id="ai-key-fields">
          <label for="ai-key">API key</label>
          <input id="ai-key" type="password" autocomplete="off" spellcheck="false" placeholder="Paste a new key to save or replace it" />
        </div>
        <div class="dialog-actions"><button id="ai-delete-key" class="quiet-button" type="button">Remove key</button><button id="ai-cancel" class="quiet-button" type="button">Cancel</button><button class="primary-button" value="default">Save</button></div>
      </form>
    </dialog>
    <dialog id="ai-confirm" class="project-dialog">
      <form method="dialog">
        <div class="dialog-kicker">Paid request</div>
        <h2>Send this page to Gemini?</h2>
        <p id="ai-confirm-summary"></p>
        <div class="dialog-actions"><button class="quiet-button" value="cancel">Cancel</button><button class="primary-button" value="send">Send and read</button></div>
      </form>
    </dialog>
    <dialog id="replace-confirm" class="project-dialog">
      <form method="dialog">
        <div class="dialog-kicker">Replace working text</div>
        <h2>Use this earlier draft?</h2>
        <p id="replace-confirm-copy">Your current text will be saved in text history first.</p>
        <div class="dialog-actions"><button class="quiet-button" value="cancel">Keep current text</button><button class="primary-button" value="replace">Replace working text</button></div>
      </form>
    </dialog>
    <dialog id="project-backup-confirm" class="project-dialog">
      <form method="dialog">
        <div class="dialog-kicker">Project file</div>
        <h2>Save a portable copy?</h2>
        <p id="project-backup-copy"></p>
        <div class="dialog-actions"><button class="quiet-button" value="cancel">Cancel</button><button class="primary-button" value="save">Save project file</button></div>
      </form>
    </dialog>
    <dialog id="chat-dialog" class="project-dialog chat-dialog">
      <form method="dialog" id="chat-form">
        <div class="dialog-kicker">Page assistant · paid Gemini request</div>
        <h2>Discuss this page</h2>
        <div id="chat-messages" class="chat-messages"></div>
        <label for="chat-input">Message</label>
        <textarea id="chat-input" rows="3" placeholder="Ask about a reading, script, abbreviation, or line…"></textarea>
        <div class="dialog-actions"><button id="chat-close" class="quiet-button" type="button">Close</button><button id="chat-send" class="primary-button" type="button">Send message</button></div>
      </form>
    </dialog>
    <dialog id="comparison-dialog" class="project-dialog comparison-dialog">
      <form method="dialog"><div class="dialog-kicker">Recognition comparison</div><h2>Model results</h2><div id="comparison-results" class="comparison-results"></div><div class="dialog-actions"><button class="quiet-button" value="close">Close</button></div></form>
    </dialog>
  </div>
`;

const languageSelect = document.querySelector('#ui-language');
languageSelect.innerHTML = LANGUAGES.map(([code, name]) => `<option value="${code}">${name}</option>`).join('');
languageSelect.value = locale;
languageSelect.addEventListener('change', () => setLocale(languageSelect.value));
app.querySelectorAll('*').forEach((element) => {
  for (const attribute of ['placeholder', 'title', 'aria-label']) {
    const value = element.getAttribute(attribute);
    if (value) element.setAttribute(attribute, t(value));
  }
  for (const node of element.childNodes) {
    if (node.nodeType === Node.TEXT_NODE && node.textContent.trim()) {
      const leading = node.textContent.match(/^\s*/)?.[0] || '';
      const trailing = node.textContent.match(/\s*$/)?.[0] || '';
      const source = node.textContent.trim();
      node.textContent = leading + t(source) + trailing;
    }
  }
});
applyLocale(app);
document.querySelectorAll('#transcript, #instruction, #chat-input, .line-row textarea, .chat-message p, .attempt-item p, .comparison-card pre').forEach((node) => { node.dir = 'auto'; });

function openDatabase() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      const hadPages = db.objectStoreNames.contains('pages');
      const hadAttempts = db.objectStoreNames.contains('attempts');
      const hadRegions = db.objectStoreNames.contains('regions');
      const hadLines = db.objectStoreNames.contains('lines');
      const hadChatMessages = db.objectStoreNames.contains('chatMessages');
      const pages = hadPages
        ? request.transaction.objectStore('pages')
        : db.createObjectStore('pages', { keyPath: 'id' });
      if (!db.objectStoreNames.contains('projects')) db.createObjectStore('projects', { keyPath: 'id' });
      if (!hadPages) pages.createIndex('projectId', 'projectId');
      const attempts = hadAttempts
        ? request.transaction.objectStore('attempts')
        : db.createObjectStore('attempts', { keyPath: 'id' });
      if (!hadAttempts) attempts.createIndex('pageId', 'pageId');
      const regions = hadRegions ? request.transaction.objectStore('regions') : db.createObjectStore('regions', { keyPath: 'id' });
      if (!hadRegions) { regions.createIndex('pageId', 'pageId'); regions.createIndex('pageOrder', ['pageId', 'order']); }
      const lines = hadLines ? request.transaction.objectStore('lines') : db.createObjectStore('lines', { keyPath: 'id' });
      if (!hadLines) { lines.createIndex('pageId', 'pageId'); lines.createIndex('regionId', 'regionId'); lines.createIndex('pageOrder', ['pageId', 'order']); }
      const chatMessages = hadChatMessages ? request.transaction.objectStore('chatMessages') : db.createObjectStore('chatMessages', { keyPath: 'id' });
      if (!hadChatMessages) { chatMessages.createIndex('pageId', 'pageId'); chatMessages.createIndex('pageDate', ['pageId', 'createdAt']); }
      if (!db.objectStoreNames.contains('textRevisions')) {
        const revisions = db.createObjectStore('textRevisions', { keyPath: 'id' });
        revisions.createIndex('pageId', 'pageId');
        const pageCursor = pages.openCursor();
        pageCursor.onsuccess = () => {
          const cursor = pageCursor.result;
          if (!cursor) return;
          const page = cursor.value;
          if (page.text?.trim()) revisions.add({ id: makeId(), pageId: page.id, text: page.text, label: page.textSource || 'Existing text', createdAt: page.updatedAt || Date.now() });
          cursor.continue();
        };
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

function transaction(storeName, mode, action) {
  return new Promise((resolve, reject) => {
    const tx = state.db.transaction(storeName, mode);
    const store = tx.objectStore(storeName);
    let value;
    try { value = action(store); } catch (error) { reject(error); return; }
    tx.oncomplete = async () => {
      try {
        if (mode === 'readwrite') await persistProjectFolder(state.project?.id);
        resolve(value?.result ?? value);
      } catch (error) { reject(error); }
    };
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error ?? new Error('Storage operation was interrupted'));
  });
}

function savePageText(targetPage, text, label, sourceAttemptId = targetPage.sourceAttemptId ?? null) {
  return new Promise((resolve, reject) => {
    const tx = state.db.transaction(['pages', 'textRevisions'], 'readwrite');
    const pages = tx.objectStore('pages');
    const revisions = tx.objectStore('textRevisions');
    const request = pages.get(targetPage.id);
    request.onsuccess = () => {
      const current = request.result;
      if (!current) return;
      if ((current.text ?? '') !== text || (current.textSource ?? '') !== (text.trim() ? label : '') || (current.sourceAttemptId ?? null) !== sourceAttemptId) {
        revisions.add({ id: makeId(), pageId: current.id, text, label: text.trim() ? label : 'Cleared text', sourceAttemptId, createdAt: Date.now() });
        pages.put({ ...current, text, textSource: text.trim() ? label : '', sourceAttemptId, updatedAt: Date.now() });
      }
    };
    tx.oncomplete = async () => {
      try { await persistProjectFolder(targetPage.projectId); resolve(); }
      catch (error) { reject(error); }
    };
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error ?? new Error('Text save was interrupted'));
  });
}

function requestValue(request) {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function sha256(bytes) {
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(digest)].map((value) => value.toString(16).padStart(2, '0')).join('');
}

function allFrom(storeName, indexName, value) {
  return new Promise((resolve, reject) => {
    const tx = state.db.transaction(storeName, 'readonly');
    const request = indexName
      ? tx.objectStore(storeName).index(indexName).getAll(value)
      : tx.objectStore(storeName).getAll();
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

function byId(storeName, id) {
  return new Promise((resolve, reject) => {
    const tx = state.db.transaction(storeName, 'readonly');
    const request = tx.objectStore(storeName).get(id);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

const makeId = () => crypto.randomUUID();
const dateLabel = (value) => new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value));

function toast(message) {
  const element = document.querySelector('#toast');
  element.textContent = t(message);
  element.classList.add('visible');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => element.classList.remove('visible'), 3000);
}

function setProject(project) {
  state.project = project;
  state.selectedPage = null;
  state.sourceAttemptId = null;
  state.drawMode = null;
  state.selectedRegionId = null;
  state.selectedLineId = null;
  state.regions = [];
  state.lines = [];
  document.querySelector('#attempt-panel').hidden = true;
  document.querySelector('#project-title').textContent = project?.name ?? t('No project open');
  document.querySelector('#project-folder-status').textContent = project?.folderPath ? `${t('Project folder')} · ${project.folderPath}` : t('Stored on this device; no project folder connected.');
  document.querySelector('#crumb-project').textContent = project?.name ?? t('Workspace');
  document.querySelector('#add-pages').disabled = !project;
  document.querySelector('#empty-state').hidden = Boolean(project);
  document.querySelector('#editor-layout').hidden = !project;
  document.querySelector('#export-text').disabled = true;
  document.querySelector('#export-page-xml').disabled = true;
  document.querySelector('#backup-project').disabled = !project;
  document.querySelector('#export-project-text').disabled = !project;
  updateRunButton();
  document.querySelector('#transcript').value = '';
  document.querySelector('#preset').value = project?.preset ?? 'diplomatic';
  document.querySelector('#instruction').value = project?.instruction ?? '';
  document.querySelector('#compare-models').value = project?.compareModels ?? '';
  document.querySelector('#draft-label').textContent = t('No text yet');
  document.querySelector('#character-count').textContent = `0 ${t('characters')}`;
  document.querySelector('#image-placeholder').hidden = false;
  document.querySelector('#image-canvas').hidden = true;
  document.querySelector('#line-count').textContent = '0';
  document.querySelector('#line-list').innerHTML = `<div class="attempt-empty">${t('Draw a region, then add lines over the image.')}</div>`;
  if (state.imageUrl) URL.revokeObjectURL(state.imageUrl);
  state.imageUrl = null;
  renderProjectSwitcher();
  renderPages();
}

async function renderProjectSwitcher() {
  const projects = (await allFrom('projects')).sort((a, b) => b.updatedAt - a.updatedAt);
  const container = document.querySelector('#project-switcher');
  container.replaceChildren();
  for (const project of projects) {
    const button = document.createElement('button');
    button.className = `project-row${state.project?.id === project.id ? ' selected' : ''}`;
    button.innerHTML = '<span class="project-glyph" aria-hidden="true"></span><span class="project-row-name"></span>';
    button.querySelector('.project-row-name').textContent = project.name;
    button.title = project.name;
    button.addEventListener('click', () => openProject(project.id));
    container.append(button);
  }
  if (!projects.length) {
    const note = document.createElement('div');
    note.className = 'library-empty';
    note.textContent = t('There are no projects to display.');
    container.append(note);
  }
}

async function openProject(id) {
  const stored = await byId('projects', id);
  if (!stored) return;
  const project = { ...stored, updatedAt: Date.now() };
  await transaction('projects', 'readwrite', (store) => store.put(project));
  setProject(project);
  await persistProjectFolder(project.id);
  await renderProjectSwitcher();
  await renderPages();
  toast(`Opened ${project.name}`);
}

async function renderPages() {
  const list = document.querySelector('#page-list');
  list.replaceChildren();
  if (!state.project) return;
  state.pages = (await allFrom('pages', 'projectId', state.project.id)).sort((a, b) => a.order - b.order);
  for (const page of state.pages) {
    const button = document.createElement('button');
    button.className = `page-row${state.selectedPage?.id === page.id ? ' selected' : ''}`;
    const thumb = document.createElement('span');
    thumb.className = 'page-thumb';
    if (page.image) {
      const image = document.createElement('img');
      image.src = URL.createObjectURL(page.image);
      image.alt = '';
      thumb.append(image);
      image.onload = () => URL.revokeObjectURL(image.src);
    }
    const text = document.createElement('span');
    text.className = 'page-row-copy';
    const name = document.createElement('strong');
    name.textContent = page.name;
    const status = document.createElement('span');
    status.textContent = page.text?.trim() ? t(page.textSource || 'Text edited') : t('No transcription');
    text.append(name, status);
    button.append(thumb, text);
    button.addEventListener('click', () => selectPage(page.id));
    list.append(button);
  }
  if (!state.pages.length) {
    const empty = document.createElement('div');
    empty.className = 'pages-empty';
    empty.innerHTML = `<span class="empty-plus">＋</span><span>${t('Import images')}</span>`;
    list.append(empty);
  }
}

async function selectPage(id) {
  const page = await byId('pages', id);
  if (!page) return;
  state.selectedPage = page;
  state.imageZoom = 1;
  state.selectedRegionId = null;
  state.selectedLineId = null;
  state.sourceAttemptId = page.sourceAttemptId ?? null;
  if (state.imageUrl) URL.revokeObjectURL(state.imageUrl);
  state.imageUrl = page.image ? URL.createObjectURL(page.image) : null;
  document.querySelector('#image-canvas').hidden = !state.imageUrl;
  document.querySelector('#image-placeholder').hidden = Boolean(state.imageUrl);
  document.querySelector('#image-name').textContent = page.name;
  document.querySelector('#crumb-page').textContent = page.name;
  document.querySelector('#transcript').value = page.text ?? '';
  document.querySelector('#draft-label').textContent = page.text?.trim() ? (page.textSource ?? t('Edited text')) : t('No text yet');
  document.querySelector('#character-count').textContent = `${(page.text ?? '').length} ${t('characters')}`;
  document.querySelector('#save-state').textContent = t('No unsaved changes');
  document.querySelector('#export-text').disabled = !page.text?.trim();
  document.querySelector('#export-page-xml').disabled = true;
  updateRunButton();
  const img = document.querySelector('#source-image');
  img.onload = () => {
    document.querySelector('#image-dimensions').textContent = `${img.naturalWidth} × ${img.naturalHeight}`;
    fitImage();
  };
  img.src = state.imageUrl ?? '';
  document.querySelector('#image-dimensions').textContent = '';
  await renderPages();
  await renderLayout();
  await renderAttempts();
}

async function renderLayout() {
  const svg = document.querySelector('#layout-overlay');
  const list = document.querySelector('#line-list');
  svg.replaceChildren();
  list.replaceChildren();
  if (!state.selectedPage) return;
  state.regions = (await allFrom('regions', 'pageOrder', IDBKeyRange.bound([state.selectedPage.id, 0], [state.selectedPage.id, Number.MAX_SAFE_INTEGER]))).sort((a, b) => a.order - b.order);
  state.lines = (await allFrom('lines', 'pageOrder', IDBKeyRange.bound([state.selectedPage.id, 0], [state.selectedPage.id, Number.MAX_SAFE_INTEGER]))).sort((a, b) => a.order - b.order);
  const ns = 'http://www.w3.org/2000/svg';
  for (const region of state.regions) {
    const shape = document.createElementNS(ns, 'rect');
    shape.setAttribute('x', region.x); shape.setAttribute('y', region.y); shape.setAttribute('width', region.width); shape.setAttribute('height', region.height);
    shape.dataset.regionId = region.id;
    shape.classList.add('layout-region');
    if (region.id === state.selectedRegionId) shape.classList.add('selected');
    shape.addEventListener('pointerdown', (event) => { if (state.drawMode) return; event.stopPropagation(); state.selectedRegionId = region.id; state.selectedLineId = null; state.layoutMoveDrag = { type: 'region', id: region.id, start: pointerPoint(event), original: region }; event.currentTarget.setPointerCapture(event.pointerId); svg.querySelectorAll('.selected').forEach((item) => item.classList.remove('selected')); event.currentTarget.classList.add('selected'); updateLayoutControls(); });
    svg.append(shape);
  }
  for (const line of state.lines) {
    const shape = document.createElementNS(ns, 'rect');
    shape.setAttribute('x', line.x); shape.setAttribute('y', line.y); shape.setAttribute('width', line.width); shape.setAttribute('height', line.height);
    shape.dataset.lineId = line.id;
    shape.classList.add('layout-line');
    if (line.id === state.selectedLineId) shape.classList.add('selected');
    shape.addEventListener('pointerdown', (event) => { if (state.drawMode) return; event.stopPropagation(); state.selectedLineId = line.id; state.selectedRegionId = line.regionId; state.layoutMoveDrag = { type: 'line', id: line.id, start: pointerPoint(event), original: line }; event.currentTarget.setPointerCapture(event.pointerId); svg.querySelectorAll('.selected').forEach((item) => item.classList.remove('selected')); event.currentTarget.classList.add('selected'); renderLineList(); updateLayoutControls(); });
    svg.append(shape);
  }
  const addResizeHandle = (record, type) => {
    if ((type === 'region' ? record.id !== state.selectedRegionId : record.id !== state.selectedLineId)) return;
    const handle = document.createElementNS(ns, 'circle');
    handle.setAttribute('cx', record.x + record.width); handle.setAttribute('cy', record.y + record.height); handle.setAttribute('r', '0.008');
    handle.classList.add('layout-resize-handle');
    handle.dataset.resizeHandle = `${type}-${record.id}`;
    handle.addEventListener('pointerdown', (event) => {
      if (state.drawMode) return;
      event.preventDefault(); event.stopPropagation();
      state.layoutMoveDrag = { type, id: record.id, start: pointerPoint(event), original: record, resize: true };
      event.currentTarget.setPointerCapture(event.pointerId);
    });
    svg.append(handle);
  };
  state.regions.forEach((region) => addResizeHandle(region, 'region'));
  state.lines.forEach((line) => addResizeHandle(line, 'line'));
  document.querySelector('#line-count').textContent = String(state.lines.length);
  document.querySelector('#export-page-xml').disabled = !state.regions.length;
  if (!state.lines.length) {
    list.innerHTML = `<div class="attempt-empty">${t('Draw a region, then add lines over the image.')}</div>`;
  } else renderLineList();
  updateLayoutControls();
}

function renderLineList() {
  const list = document.querySelector('#line-list');
  list.replaceChildren();
  for (const line of state.lines) {
    const row = document.createElement('div');
    row.className = `line-row${line.id === state.selectedLineId ? ' selected' : ''}`;
    const number = document.createElement('span');
    number.className = 'line-number'; number.textContent = String(line.order + 1);
    const editor = document.createElement('textarea');
    editor.rows = 2; editor.value = line.text ?? ''; editor.setAttribute('aria-label', `Transcription for line ${line.order + 1}`);
    editor.addEventListener('focus', () => {
      state.selectedLineId = line.id; state.selectedRegionId = line.regionId;
      document.querySelectorAll('#layout-overlay .selected').forEach((item) => item.classList.remove('selected'));
      document.querySelector(`#layout-overlay [data-line-id="${line.id}"]`)?.classList.add('selected');
      document.querySelector(`#layout-overlay [data-region-id="${line.regionId}"]`)?.classList.add('selected');
      updateLayoutControls();
    });
    editor.addEventListener('change', async () => {
      const text = editor.value;
      await transaction('lines', 'readwrite', (store) => store.put({ ...line, text, updatedAt: Date.now(), textSource: 'Edited text' }));
      await syncPageTextFromLines(state.lines.map((item) => item.id === line.id ? { ...item, text, textSource: 'Edited text' } : item));
    });
    const orderControls = document.createElement('span'); orderControls.className = 'line-order-controls';
    for (const [direction, label] of [[-1, 'Move line up'], [1, 'Move line down']]) {
      const button = document.createElement('button'); button.type = 'button'; button.className = 'text-button'; button.textContent = direction < 0 ? '↑' : '↓'; button.setAttribute('aria-label', `${label} ${line.order + 1}`); button.disabled = line.order + direction < 0 || line.order + direction >= state.lines.length;
      button.addEventListener('click', async (event) => { event.stopPropagation(); await reorderLine(line.id, direction); });
      orderControls.append(button);
    }
    row.append(number, editor, orderControls);
    row.addEventListener('click', (event) => {
      if (event.target === editor) return;
      state.selectedLineId = line.id; state.selectedRegionId = line.regionId;
      document.querySelectorAll('#layout-overlay .selected').forEach((item) => item.classList.remove('selected'));
      document.querySelector(`#layout-overlay [data-line-id="${line.id}"]`)?.classList.add('selected');
      document.querySelector(`#layout-overlay [data-region-id="${line.regionId}"]`)?.classList.add('selected');
      updateLayoutControls();
    });
    list.append(row);
  }
}

async function reorderLine(lineId, direction) {
  const ordered = [...state.lines].sort((a, b) => a.order - b.order);
  const from = ordered.findIndex((line) => line.id === lineId), to = from + direction;
  if (from < 0 || to < 0 || to >= ordered.length) return;
  [ordered[from], ordered[to]] = [ordered[to], ordered[from]];
  const next = ordered.map((line, order) => ({ ...line, order, updatedAt: Date.now() }));
  await new Promise((resolve, reject) => {
    const tx = state.db.transaction('lines', 'readwrite');
    next.forEach((line) => tx.objectStore('lines').put(line));
    tx.oncomplete = resolve; tx.onerror = () => reject(tx.error); tx.onabort = () => reject(tx.error ?? new Error('Line ordering save was interrupted'));
  });
  state.lines = next;
  await persistProjectFolder(state.selectedPage?.projectId);
  await syncPageTextFromLines(next);
  await renderLayout();
}

function updateLayoutControls() {
  const regionButton = document.querySelector('#draw-region');
  const lineButton = document.querySelector('#draw-line');
  regionButton.classList.toggle('active', state.drawMode === 'region');
  lineButton.classList.toggle('active', state.drawMode === 'line');
  regionButton.textContent = state.drawMode === 'region' ? 'Cancel region' : 'Draw region';
  lineButton.textContent = state.drawMode === 'line' ? 'Cancel line' : 'Draw line';
  lineButton.disabled = !state.selectedRegionId && state.drawMode !== 'line';
  document.querySelector('#delete-layout').disabled = !state.selectedRegionId && !state.selectedLineId;
  const selectedRegion = state.regions.find((region) => region.id === state.selectedRegionId);
  document.querySelector('#review-layout').disabled = !selectedRegion || selectedRegion.reviewState === 'reviewed';
  document.querySelector('#layout-status').textContent = state.drawMode === 'region' ? 'Drag over the page to mark a region.'
    : state.drawMode === 'line' ? 'Drag within the selected region to add a line.'
      : state.selectedLineId ? 'Line selected.' : state.selectedRegionId ? 'Region selected. Add lines inside it.' : 'Select or draw a page region.';
}

function pointerPoint(event) {
  const rect = document.querySelector('#layout-overlay').getBoundingClientRect();
  return { x: Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)), y: Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)) };
}

async function finishLayoutDraw(start, end) {
  const x = Math.min(start.x, end.x), y = Math.min(start.y, end.y);
  const width = Math.abs(end.x - start.x), height = Math.abs(end.y - start.y);
  if (width < 0.005 || height < 0.002) { state.drawMode = null; updateLayoutControls(); return; }
  if (state.drawMode === 'region') {
    const region = { id: makeId(), pageId: state.selectedPage.id, x, y, width, height, order: state.regions.length, label: `Region ${state.regions.length + 1}`, origin: 'manual', reviewState: 'reviewed', createdAt: Date.now() };
    await transaction('regions', 'readwrite', (store) => store.add(region));
    state.selectedRegionId = region.id;
  } else if (state.drawMode === 'line' && state.selectedRegionId) {
    const region = state.regions.find((item) => item.id === state.selectedRegionId);
    const left = Math.max(x, region.x), top = Math.max(y, region.y);
    const right = Math.min(x + width, region.x + region.width), bottom = Math.min(y + height, region.y + region.height);
    if (right - left < 0.005 || bottom - top < 0.002) { toast('Draw the line inside the selected region.'); state.drawMode = null; updateLayoutControls(); return; }
    const line = { id: makeId(), pageId: state.selectedPage.id, regionId: region.id, x: left, y: top, width: right - left, height: bottom - top, order: state.lines.length, text: '', textSource: '', reviewState: 'draft', geometryReviewState: 'reviewed', origin: 'manual', createdAt: Date.now(), updatedAt: Date.now() };
    await transaction('lines', 'readwrite', (store) => store.add(line));
    state.selectedLineId = line.id;
  }
  state.drawMode = null;
  await renderLayout();
}

async function moveLayoutSelection(event) {
  const drag = state.layoutMoveDrag;
  if (!drag) return;
  const point = pointerPoint(event);
  const dx = point.x - drag.start.x, dy = point.y - drag.start.y;
  const record = drag.original;
  const region = state.regions.find((item) => item.id === (drag.type === 'region' ? drag.id : record.regionId));
  if (drag.resize) {
    const children = drag.type === 'region' ? state.lines.filter((line) => line.regionId === record.id) : [];
    const minWidth = drag.type === 'region' ? Math.max(0.005, ...children.map((line) => line.x + line.width - record.x)) : 0.003;
    const minHeight = drag.type === 'region' ? Math.max(0.002, ...children.map((line) => line.y + line.height - record.y)) : 0.002;
    const maxX = drag.type === 'region' ? 1 : region.x + region.width;
    const maxY = drag.type === 'region' ? 1 : region.y + region.height;
    const width = Math.max(minWidth, Math.min(maxX - record.x, record.width + dx));
    const height = Math.max(minHeight, Math.min(maxY - record.y, record.height + dy));
    state.layoutMoveNext = { ...record, width, height };
    const shape = document.querySelector(`#layout-overlay [data-${drag.type}-id="${drag.id}"]`);
    shape?.setAttribute('width', width); shape?.setAttribute('height', height);
    const handle = document.querySelector(`#layout-overlay [data-resize-handle="${drag.type}-${drag.id}"]`);
    handle?.setAttribute('cx', record.x + width); handle?.setAttribute('cy', record.y + height);
    return;
  }
  const minX = drag.type === 'region' ? 0 : region.x;
  const minY = drag.type === 'region' ? 0 : region.y;
  const maxX = drag.type === 'region' ? 1 - record.width : region.x + region.width - record.width;
  const maxY = drag.type === 'region' ? 1 - record.height : region.y + region.height - record.height;
  const x = Math.max(minX, Math.min(maxX, record.x + dx));
  const y = Math.max(minY, Math.min(maxY, record.y + dy));
  const shape = document.querySelector(`#layout-overlay [data-${drag.type}-id="${drag.id}"]`);
  shape?.setAttribute('x', x); shape?.setAttribute('y', y);
  state.layoutMoveNext = { ...record, x, y };
}

async function persistLayoutMove() {
  const drag = state.layoutMoveDrag, updated = state.layoutMoveNext;
  state.layoutMoveDrag = null; state.layoutMoveNext = null;
  if (!drag || !updated || (updated.x === drag.original.x && updated.y === drag.original.y && updated.width === drag.original.width && updated.height === drag.original.height)) return;
  if (drag.type === 'line') {
    await transaction('lines', 'readwrite', (store) => store.put({ ...updated, origin: 'edited', geometryReviewState: 'draft', updatedAt: Date.now() }));
  } else {
    const children = state.lines.filter((line) => line.regionId === drag.id);
    const dx = updated.x - drag.original.x, dy = updated.y - drag.original.y;
    await new Promise((resolve, reject) => {
      const tx = state.db.transaction(['regions', 'lines'], 'readwrite');
      tx.objectStore('regions').put({ ...updated, origin: 'edited', reviewState: 'draft', updatedAt: Date.now() });
      children.forEach((line) => tx.objectStore('lines').put({ ...line, x: line.x + dx, y: line.y + dy, origin: 'edited', geometryReviewState: 'draft', updatedAt: Date.now() }));
      tx.oncomplete = resolve; tx.onerror = () => reject(tx.error); tx.onabort = () => reject(tx.error ?? new Error('Layout move was interrupted'));
    });
  }
  await persistProjectFolder(state.selectedPage?.projectId);
  await renderLayout();
}

async function markRegionReviewed() {
  const region = state.regions.find((item) => item.id === state.selectedRegionId);
  if (!region) return;
  await new Promise((resolve, reject) => {
    const tx = state.db.transaction(['regions', 'lines'], 'readwrite');
    tx.objectStore('regions').put({ ...region, reviewState: 'reviewed', reviewedAt: Date.now() });
    state.lines.filter((line) => line.regionId === region.id).forEach((line) => tx.objectStore('lines').put({ ...line, geometryReviewState: 'reviewed' }));
    tx.oncomplete = resolve; tx.onerror = () => reject(tx.error); tx.onabort = () => reject(tx.error ?? new Error('Layout review save was interrupted'));
  });
  await persistProjectFolder(state.selectedPage.projectId);
  await renderLayout();
  toast('Region layout marked reviewed');
}

async function syncPageTextFromLines(lines, source = 'Edited text', sourceAttemptId = null) {
  if (!state.selectedPage) return;
  const ordered = [...lines].sort((a, b) => a.order - b.order);
  const text = ordered.map((line) => line.text ?? '').join('\n');
  await savePageText(state.selectedPage, text, text.trim() ? source : '', sourceAttemptId);
  state.selectedPage = await byId('pages', state.selectedPage.id);
  document.querySelector('#transcript').value = text;
  document.querySelector('#character-count').textContent = `${text.length} ${t('characters')}`;
  document.querySelector('#draft-label').textContent = text.trim() ? source : 'No text yet';
  document.querySelector('#save-state').textContent = t('Saved on this device');
  document.querySelector('#export-text').disabled = !text.trim();
  await renderPages();
}

async function renderAttempts() {
  const list = document.querySelector('#attempt-list');
  const count = document.querySelector('#attempt-count');
  list.replaceChildren();
  if (!state.selectedPage) { count.textContent = '0'; return; }
  const attempts = (await allFrom('attempts', 'pageId', state.selectedPage.id)).sort((a, b) => b.createdAt - a.createdAt);
  count.textContent = String(attempts.length);
  if (!attempts.length) {
    const empty = document.createElement('div');
    empty.className = 'attempt-empty';
    empty.textContent = t('Recognition attempts will be kept here.');
    list.append(empty);
  } else {
    for (const attempt of attempts) {
      const item = document.createElement('article');
      item.className = 'attempt-item';
      const head = document.createElement('div');
      head.className = 'attempt-item-head';
      const title = document.createElement('strong');
      title.textContent = attempt.label;
      const date = document.createElement('span');
      date.textContent = dateLabel(attempt.createdAt);
      const body = document.createElement('p');
      body.textContent = attempt.operation === 'segmentation' && attempt.status === 'completed'
        ? `Suggested layout · ${attempt.regionCount} regions · ${attempt.lineCount} lines (review before use)`
        : attempt.text || (attempt.status === 'interrupted'
        ? 'The app closed before the result was saved. Gemini may have received the request.'
        : attempt.status === 'failed' ? `Failed: ${attempt.error || 'Gemini could not complete this request.'}` : 'This recognition attempt is still running.');
      head.append(title, date);
      item.append(head, body);
      if (attempt.status && attempt.status !== 'completed') {
        const status = document.createElement('small');
        status.className = 'attempt-meta';
        status.textContent = attempt.status === 'failed' ? 'Failed' : attempt.status === 'interrupted' ? 'Interrupted' : 'In progress';
        item.append(status);
      }
      if (attempt.usage?.totalTokens != null) {
        const meta = document.createElement('small');
        meta.className = 'attempt-meta';
        meta.textContent = `${attempt.model || 'Gemini'} · ${new Intl.NumberFormat(locale).format(attempt.usage.promptTokens ?? 0)} in · ${new Intl.NumberFormat(locale).format(attempt.usage.outputTokens ?? 0)} out · ${new Intl.NumberFormat(locale).format(attempt.usage.totalTokens)} tokens`;
        item.append(meta);
      }
      if (attempt.text && attempt.operation !== 'segmentation') {
        const use = document.createElement('button');
        use.className = 'text-button';
        use.textContent = t('Use this text');
        use.addEventListener('click', () => adoptText(attempt.text, 'AI draft', attempt.id));
        item.append(use);
      }
      list.append(item);
    }
  }
  await renderRevisions();
}

function confirmDialog(dialogId) {
  const dialog = document.querySelector(dialogId);
  dialog.returnValue = '';
  dialog.showModal();
  return new Promise((resolve) => dialog.addEventListener('close', () => resolve(dialog.returnValue), { once: true }));
}

async function flushTextSave() {
  clearTimeout(state.saveTimer);
  const page = state.selectedPage;
  if (!page) return;
  const input = document.querySelector('#transcript');
  const source = input.value.trim() ? (document.querySelector('#draft-label').textContent || 'Edited text') : '';
  await savePageText(page, input.value, source, state.sourceAttemptId);
  const updated = await byId('pages', page.id);
  if (state.selectedPage?.id === page.id && updated) {
    state.selectedPage = updated;
    state.sourceAttemptId = updated.sourceAttemptId ?? null;
    document.querySelector('#save-state').textContent = t('Saved on this device');
  }
  await renderPages();
  await renderRevisions();
}

async function adoptText(text, label, sourceAttemptId = null) {
  const input = document.querySelector('#transcript');
  if (input.value !== text && input.value.trim()) {
    const kind = label === 'AI draft' ? 'AI recognition attempt' : 'saved text version';
    document.querySelector('#replace-confirm-copy').textContent = `The current text will be saved in history before this ${kind} replaces it.`;
    if (await confirmDialog('#replace-confirm') !== 'replace') return;
  }
  await flushTextSave();
  if (state.lines.length) {
    const lineTexts = text.replace(/\r/g, '').split('\n');
    if (lineTexts.length === state.lines.length) {
      const updated = state.lines.map((line, index) => ({ ...line, text: lineTexts[index], textSource: label, sourceAttemptId, reviewState: 'draft', updatedAt: Date.now() }));
      await new Promise((resolve, reject) => {
        const tx = state.db.transaction('lines', 'readwrite');
        updated.forEach((line) => tx.objectStore('lines').put(line));
        tx.oncomplete = resolve; tx.onerror = () => reject(tx.error); tx.onabort = () => reject(tx.error ?? new Error('Line text update was interrupted'));
      });
      await syncPageTextFromLines(updated, label, sourceAttemptId);
      document.querySelector('#attempt-panel').hidden = true;
      document.querySelector('#history-toggle').setAttribute('aria-expanded', 'false');
      return;
    }
    toast('This result has a different number of lines; saved as page text without changing your line segmentation.');
  }
  input.value = text;
  state.sourceAttemptId = sourceAttemptId;
  scheduleTextSave(label, sourceAttemptId);
  document.querySelector('#attempt-panel').hidden = true;
  document.querySelector('#history-toggle').setAttribute('aria-expanded', 'false');
}

async function renderRevisions() {
  const list = document.querySelector('#revision-list');
  list.replaceChildren();
  if (!state.selectedPage) return;
  const revisions = (await allFrom('textRevisions', 'pageId', state.selectedPage.id)).sort((a, b) => b.createdAt - a.createdAt);
  if (!revisions.length) {
    const empty = document.createElement('div');
    empty.className = 'attempt-empty';
    empty.textContent = t('Saved edits and adopted drafts will appear here.');
    list.append(empty);
    return;
  }
  for (const revision of revisions) {
    const item = document.createElement('article');
    item.className = 'attempt-item';
    const head = document.createElement('div');
    head.className = 'attempt-item-head';
    const title = document.createElement('strong');
    title.textContent = revision.label;
    const date = document.createElement('span');
    date.textContent = dateLabel(revision.createdAt);
    const body = document.createElement('p');
    body.textContent = revision.text || '(Text cleared)';
    const restore = document.createElement('button');
    restore.className = 'text-button';
    restore.textContent = t('Restore this version');
    restore.disabled = !revision.text;
    restore.addEventListener('click', () => adoptText(revision.text, 'Restored version', revision.sourceAttemptId ?? null));
    head.append(title, date);
    item.append(head, body, restore);
    list.append(item);
  }
}

async function createProject(name) {
  let folderPath = null;
  if (gemini.isDesktop()) {
    folderPath = await openNativeDialog({ directory: true, multiple: false, title: 'Choose an empty folder for this project' });
    if (!folderPath) return;
    if ((await readDir(folderPath)).length) { toast('Choose an empty folder, or open an existing Escript AI project folder.'); return; }
    await mkdir(await joinPath(folderPath, 'assets'), { recursive: true });
  }
  const project = { id: makeId(), name: name.trim(), createdAt: Date.now(), updatedAt: Date.now(), preset: 'diplomatic', instruction: '', compareModels: '', folderPath };
  await transaction('projects', 'readwrite', (store) => store.add(project));
  setProject(project);
  await renderProjectSwitcher();
  await persistProjectFolder(project.id);
  toast(`Created ${project.name}`);
}

const projectFolderWrites = new Map();
function persistProjectFolder(projectId) {
  if (!projectId) return Promise.resolve();
  const previous = projectFolderWrites.get(projectId) ?? Promise.resolve();
  const pending = previous.catch(() => {}).then(() => writeProjectFolderSnapshot(projectId));
  projectFolderWrites.set(projectId, pending);
  return pending.finally(() => { if (projectFolderWrites.get(projectId) === pending) projectFolderWrites.delete(projectId); });
}

async function writeProjectFolderSnapshot(projectId) {
  if (!gemini.isDesktop()) return;
  const project = await byId('projects', projectId);
  if (!project?.folderPath) return;
  if (state.project?.id === projectId) document.querySelector('#project-folder-status').textContent = t('Saving project folder…');
  const pages = (await allFrom('pages', 'projectId', projectId)).sort((a, b) => a.order - b.order);
  const pageIds = new Set(pages.map((page) => page.id));
  const [attempts, revisions, regions, lines, chatMessages] = await Promise.all([
    allFrom('attempts'), allFrom('textRevisions'), allFrom('regions'), allFrom('lines'), allFrom('chatMessages'),
  ]);
  const pageData = [];
  for (const page of pages) {
    const extension = ({ 'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp' })[page.mimeType];
    if (!extension || !page.image) throw new Error(`Missing or unsupported source image for ${page.name}`);
    const imageFile = `${page.id}.${extension}`;
    const assetPath = await joinPath(project.folderPath, 'assets', imageFile);
    const imageBytes = new Uint8Array(await page.image.arrayBuffer());
    const imageSha256 = page.imageSha256 || await sha256(imageBytes);
    if (!page.imageSha256) {
      await new Promise((resolve, reject) => {
        const tx = state.db.transaction('pages', 'readwrite');
        tx.objectStore('pages').put({ ...page, imageSha256 });
        tx.oncomplete = resolve; tx.onerror = () => reject(tx.error); tx.onabort = () => reject(tx.error ?? new Error('Image checksum save was interrupted'));
      });
    }
    if (!await fileExists(assetPath)) await writeFile(assetPath, imageBytes);
    const { image, ...metadata } = page;
    pageData.push({ ...metadata, imageFile, imageSha256 });
  }
  const included = (items) => items.filter((item) => pageIds.has(item.pageId));
  const manifest = {
    format: 'escript-ai-desktop-folder', version: 1, savedAt: Date.now(),
    project: { id: project.id, name: project.name, createdAt: project.createdAt, updatedAt: project.updatedAt, preset: project.preset, instruction: project.instruction, compareModels: project.compareModels ?? '' },
    pages: pageData,
    attempts: included(attempts), revisions: included(revisions), regions: included(regions), lines: included(lines), chatMessages: included(chatMessages),
  };
  const payloadText = JSON.stringify(manifest);
  const envelope = { format: 'escript-ai-desktop-folder', version: 2, sha256: await sha256(new TextEncoder().encode(payloadText)), payload: manifest };
  const temporaryPath = await joinPath(project.folderPath, 'project.json.tmp');
  const manifestPath = await joinPath(project.folderPath, 'project.json');
  await writeTextFile(temporaryPath, JSON.stringify(envelope));
  await rename(temporaryPath, manifestPath);
  if (state.project?.id === projectId) document.querySelector('#project-folder-status').textContent = `${t('Saved on this device')} · ${project.folderPath}`;
}

async function readProjectFolder(folderPath) {
  const manifestPath = await joinPath(folderPath, 'project.json');
  const stored = JSON.parse(await readTextFile(manifestPath));
  let manifest = stored;
  if (stored.format === 'escript-ai-desktop-folder' && stored.version === 2 && stored.payload) {
    if (await sha256(new TextEncoder().encode(JSON.stringify(stored.payload))) !== stored.sha256) throw new Error('The project manifest checksum does not match. The folder was not imported.');
    manifest = stored.payload;
  }
  if (manifest.format !== 'escript-ai-desktop-folder' || manifest.version !== 1 || !manifest.project?.id || !Array.isArray(manifest.pages)) throw new Error('This folder does not contain a supported Escript AI project.');
  const pages = [];
  let totalBytes = 0;
  for (const page of manifest.pages) {
    if (!['image/png', 'image/jpeg', 'image/webp'].includes(page.mimeType) || typeof page.imageFile !== 'string' || page.imageFile.includes('/') || page.imageFile.includes('\\') || page.imageFile.includes('..')) throw new Error('The project folder contains an invalid image reference.');
    const bytes = await readFile(await joinPath(folderPath, 'assets', page.imageFile));
    totalBytes += bytes.byteLength;
    if (totalBytes > 2 * 1024 * 1024 * 1024) throw new Error('This project exceeds the current 2 GB folder-open limit.');
    if (page.imageSha256 && await sha256(bytes) !== page.imageSha256) throw new Error(`The saved image for ${page.name} failed its checksum. The folder was not imported.`);
    pages.push({ ...page, image: new Blob([bytes], { type: page.mimeType }) });
  }
  return {
    format: 'escript-ai-project', version: 2, exportedAt: manifest.savedAt,
    project: manifest.project, pages,
    attempts: manifest.attempts ?? [], revisions: manifest.revisions ?? [],
    regions: manifest.regions ?? [], lines: manifest.lines ?? [], chatMessages: manifest.chatMessages ?? [],
  };
}

async function openProjectFolder() {
  if (!gemini.isDesktop()) { toast('Open project folders in the Windows desktop app.'); return; }
  const folderPath = await openNativeDialog({ directory: true, multiple: false, title: 'Open an Escript AI project folder' });
  if (!folderPath) return;
  try {
    const manifestPath = await joinPath(folderPath, 'project.json');
    if (!await fileExists(manifestPath)) {
      if (!state.project) throw new Error('Create or open a local project before choosing a new project folder.');
      if ((await readDir(folderPath)).length) throw new Error('Choose an empty folder, or select a folder that already contains an Escript AI project.');
      await mkdir(await joinPath(folderPath, 'assets'), { recursive: true });
      const connected = { ...state.project, folderPath };
      await transaction('projects', 'readwrite', (store) => store.put(connected));
      setProject(connected);
      await persistProjectFolder(connected.id);
      toast(`Connected project folder for ${connected.name}`);
      return;
    }
    await restoreProject(await readProjectFolder(folderPath), folderPath);
  } catch (error) {
    toast(`Could not open project folder: ${String(error?.message ?? error).slice(0, 300)}`);
  }
}

async function addImages(files) {
  if (!state.project) return;
  const supportedTypes = new Set(['image/png', 'image/jpeg', 'image/webp']);
  const supported = [...files].filter((file) => supportedTypes.has(file.type));
  const rejected = files.length - supported.length;
  if (!supported.length) { toast('Choose one or more image files.'); return; }
  const firstOrder = state.pages.length;
  await new Promise((resolve, reject) => {
    const tx = state.db.transaction('pages', 'readwrite');
    supported.forEach((file, offset) => tx.objectStore('pages').add({
      id: makeId(), projectId: state.project.id, order: firstOrder + offset,
      name: file.name, mimeType: file.type, image: file, text: '', textSource: '', createdAt: Date.now(),
    }));
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error ?? new Error('Image import was interrupted'));
  });
  await persistProjectFolder(state.project.id);
  await renderPages();
  if (!state.selectedPage && state.pages.length) await selectPage(state.pages[0].id);
  toast(`Added ${supported.length} ${supported.length === 1 ? 'page' : 'pages'}${rejected ? `; skipped ${rejected} unsupported file${rejected === 1 ? '' : 's'}` : ''}`);
}

function scheduleTextSave(source = 'Edited text', sourceAttemptId = state.sourceAttemptId) {
  const input = document.querySelector('#transcript');
  const text = input.value;
  const targetPage = state.selectedPage;
  document.querySelector('#character-count').textContent = `${text.length} ${t('characters')}`;
  document.querySelector('#draft-label').textContent = text.trim() ? source : 'No text yet';
  document.querySelector('#save-state').textContent = t('Saving…');
  document.querySelector('#export-text').disabled = !text.trim();
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(async () => {
    if (!targetPage) return;
    await savePageText(targetPage, text, source, sourceAttemptId);
    const page = await byId('pages', targetPage.id);
    if (state.selectedPage?.id === targetPage.id && page) {
      state.selectedPage = page;
      state.sourceAttemptId = page.sourceAttemptId ?? null;
    }
    await renderPages();
    if (state.selectedPage?.id === targetPage.id) {
      document.querySelector('#save-state').textContent = t('Saved on this device');
      await renderRevisions();
    }
  }, 350);
}

async function exportText() {
  await flushTextSave();
  if (!state.selectedPage?.text?.trim()) return;
  const blob = new Blob([state.selectedPage.text], { type: 'text/plain;charset=utf-8' });
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = `${state.selectedPage.name.replace(/\.[^.]+$/, '')}.txt`;
  link.click();
  URL.revokeObjectURL(link.href);
  toast('Text exported');
}

function xmlEscape(value) {
  return String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&apos;');
}

function rectanglePoints(box, width, height) {
  const left = Math.round(box.x * width), top = Math.round(box.y * height);
  const right = Math.round((box.x + box.width) * width), bottom = Math.round((box.y + box.height) * height);
  return `${left},${top} ${right},${top} ${right},${bottom} ${left},${bottom}`;
}

async function exportPageXml() {
  if (!state.selectedPage || !state.regions.length) return;
  await flushTextSave();
  const image = document.querySelector('#source-image');
  const page = state.selectedPage;
  const regions = [...state.regions].sort((a, b) => a.order - b.order);
  const lines = [...state.lines].sort((a, b) => a.order - b.order);
  const regionXml = regions.map((region) => {
    const regionLines = lines.filter((line) => line.regionId === region.id).map((line) => `      <TextLine id="line-${xmlEscape(line.id)}"><Coords points="${rectanglePoints(line, image.naturalWidth, image.naturalHeight)}"/><TextEquiv index="0"><Unicode>${xmlEscape(line.text ?? '')}</Unicode></TextEquiv></TextLine>`).join('\n');
    return `    <TextRegion id="region-${xmlEscape(region.id)}" textLineOrder="top-to-bottom"><Coords points="${rectanglePoints(region, image.naturalWidth, image.naturalHeight)}"/>\n${regionLines}\n    </TextRegion>`;
  }).join('\n');
  const readingOrder = regions.map((region, index) => `<RegionRefIndexed index="${index}" regionRef="region-${xmlEscape(region.id)}"/>`).join('');
  const xml = `<?xml version="1.0" encoding="UTF-8"?>\n<PcGts xmlns="http://schema.primaresearch.org/PAGE/gts/pagecontent/2019-07-15"><Metadata><Creator>Escript AI Desktop</Creator><Created>${new Date().toISOString()}</Created><LastChange>${new Date().toISOString()}</LastChange></Metadata><Page imageFilename="${xmlEscape(page.name)}" imageWidth="${image.naturalWidth}" imageHeight="${image.naturalHeight}"><ReadingOrder><OrderedGroup id="page-order">${readingOrder}</OrderedGroup></ReadingOrder>\n${regionXml}\n</Page></PcGts>`;
  const blob = new Blob([xml], { type: 'application/xml;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a'); link.href = url; link.download = `${page.name.replace(/\.[^.]+$/, '')}.xml`; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  toast('PAGE XML exported for this page');
}

async function exportProjectText() {
  if (!state.project) return;
  await flushTextSave();
  const project = await byId('projects', state.project.id);
  const pages = (await allFrom('pages', 'projectId', project.id)).sort((a, b) => a.order - b.order);
  const transcribed = pages.filter((page) => page.text?.trim());
  if (!transcribed.length) { toast('There is no text to export yet.'); return; }
  const contents = transcribed.map((page) => `${page.name}\n${'='.repeat(Math.min(page.name.length, 80))}\n\n${page.text.trim()}\n`).join('\n\n');
  const blob = new Blob([contents], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `${project.name.replace(/[^\p{L}\p{N}._ -]/gu, '_')}-transcriptions.txt`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  toast(`Exported text from ${transcribed.length} ${transcribed.length === 1 ? 'page' : 'pages'}`);
}

function fitImage() {
  const image = document.querySelector('#source-image');
  const stage = document.querySelector('#image-stage');
  if (!state.selectedPage?.image || !image.naturalWidth || !stage.clientWidth || !stage.clientHeight) return;
  const fitScale = Math.min((stage.clientWidth - 38) / image.naturalWidth, (stage.clientHeight - 38) / image.naturalHeight, 1);
  const scale = fitScale * state.imageZoom;
  const canvas = document.querySelector('#image-canvas');
  canvas.style.width = `${Math.round(image.naturalWidth * scale)}px`;
  canvas.style.height = `${Math.round(image.naturalHeight * scale)}px`;
  image.style.width = '100%';
  image.style.height = '100%';
  document.querySelector('#zoom-reset').textContent = `${Math.round(state.imageZoom * 100)}%`;
  document.querySelector('#zoom-out').disabled = state.imageZoom <= 0.5;
  document.querySelector('#zoom-in').disabled = state.imageZoom >= 4;
}

function blobAsDataUrl(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });
}

function dataUrlAsBlob(dataUrl, expectedType) {
  const [header, encoded] = String(dataUrl).split(',', 2);
  if (!header?.startsWith(`data:${expectedType};base64`) || !encoded) throw new Error('Project file contains an invalid image.');
  const binary = atob(encoded);
  const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
  return new Blob([bytes], { type: expectedType });
}

async function backupProject() {
  if (!state.project) return;
  await flushTextSave();
  const project = await byId('projects', state.project.id);
  const pages = (await allFrom('pages', 'projectId', project.id)).sort((a, b) => a.order - b.order);
  const pageIds = new Set(pages.map((page) => page.id));
  const allAttempts = await allFrom('attempts');
  const allRevisions = await allFrom('textRevisions');
  const allRegions = await allFrom('regions');
  const allLines = await allFrom('lines');
  const allChat = await allFrom('chatMessages');
  const attempts = allAttempts.filter((item) => pageIds.has(item.pageId));
  const revisions = allRevisions.filter((item) => pageIds.has(item.pageId));
  const regions = allRegions.filter((item) => pageIds.has(item.pageId));
  const lines = allLines.filter((item) => pageIds.has(item.pageId));
  const chatMessages = allChat.filter((item) => pageIds.has(item.pageId));
  const imageBytes = pages.reduce((total, page) => total + (page.image?.size ?? 0), 0);
  const estimate = Math.ceil(imageBytes * 1.4 + 1024 * (pages.length + attempts.length + revisions.length + 1));
  if (estimate > 180 * 1024 * 1024) {
    toast('This prototype limits a project file to about 180 MB. Large projects need folder-based storage.');
    return;
  }
  document.querySelector('#project-backup-copy').textContent = `This file will include ${pages.length} page images, text, saved versions, and recognition attempts (about ${(estimate / 1048576).toFixed(1)} MB). It will download to your configured downloads location. It is not encrypted and does not include your Gemini key.`;
  if (await confirmDialog('#project-backup-confirm') !== 'save') return;
  try {
    const packageData = {
      format: 'escript-ai-project', version: 2, exportedAt: Date.now(),
      project: { name: project.name, createdAt: project.createdAt, updatedAt: project.updatedAt, preset: project.preset, instruction: project.instruction, compareModels: project.compareModels ?? '' },
      pages: await Promise.all(pages.map(async (page) => ({
        id: page.id, order: page.order, name: page.name, mimeType: page.mimeType, createdAt: page.createdAt, sourceAttemptId: page.sourceAttemptId ?? null,
        image: page.image ? await blobAsDataUrl(page.image) : null, text: page.text ?? '', textSource: page.textSource ?? '',
      }))),
      attempts,
      revisions,
      regions,
      lines,
      chatMessages,
    };
    const blob = new Blob([JSON.stringify(packageData)], { type: 'application/vnd.escriptai.project+json' });
    if (blob.size > 180 * 1024 * 1024) throw new Error('This project file exceeds the prototype limit of about 180 MB.');
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${project.name.replace(/[^\p{L}\p{N}._ -]/gu, '_')}.escriptai`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast('Project file saved');
  } catch (error) {
    toast(`Could not save project file: ${error.message}`);
  }
}

async function restoreProject(file, folderPath = null, silent = false) {
  if (!file) return;
  if (typeof file.text === 'function' && file.size > 180 * 1024 * 1024) { toast('This prototype can open project files up to about 180 MB.'); return; }
  try {
    const data = typeof file.text === 'function' ? JSON.parse(await file.text()) : file;
    if (data.format !== 'escript-ai-project' || ![1, 2].includes(data.version) || !data.project?.name || !Array.isArray(data.pages) || !Array.isArray(data.attempts) || !Array.isArray(data.revisions)) {
      throw new Error('This is not a supported Escript AI project file.');
    }
    const importedRegions = data.regions ?? [];
    const importedLines = data.lines ?? [];
    const importedChat = data.chatMessages ?? [];
    if (![importedRegions, importedLines, importedChat].every(Array.isArray)) throw new Error('The project file contains invalid layout or chat data.');
    const retainIds = Boolean(folderPath);
    const mappedId = (id) => retainIds ? id : makeId();
    const pageMap = new Map(data.pages.map((page) => [page.id, mappedId(page.id)]));
    const attemptMap = new Map(data.attempts.map((item) => [item.id, mappedId(item.id)]));
    const regionMap = new Map(importedRegions.map((item) => [item.id, mappedId(item.id)]));
    const lineMap = new Map(importedLines.map((item) => [item.id, mappedId(item.id)]));
    if (new Set(data.pages.map((page) => page.id)).size !== data.pages.length
      || new Set(data.attempts.map((item) => item.id)).size !== data.attempts.length
      || data.attempts.some((item) => typeof item.id !== 'string')
      || data.pages.some((page) => typeof page.id !== 'string' || typeof page.name !== 'string' || !page.name || !pageMap.get(page.id)
        || !['image/png', 'image/jpeg', 'image/webp'].includes(page.mimeType) || !(typeof page.image === 'string' || (page.image instanceof Blob && page.image.type === page.mimeType))
        || typeof page.text !== 'string' || typeof page.textSource !== 'string')) {
      throw new Error('The project file has a missing or unsupported page image.');
    }
    if (data.attempts.some((item) => typeof item.text !== 'string' || typeof item.label !== 'string')
      || data.revisions.some((item) => typeof item.text !== 'string' || typeof item.label !== 'string')) {
      throw new Error('The project file contains invalid text history.');
    }
    if (data.pages.some((page) => page.sourceAttemptId && !attemptMap.has(page.sourceAttemptId))
      || data.revisions.some((item) => item.sourceAttemptId && !attemptMap.has(item.sourceAttemptId))
      || importedRegions.some((item) => item.sourceAttemptId && !attemptMap.has(item.sourceAttemptId))
      || importedLines.some((item) => item.sourceAttemptId && !attemptMap.has(item.sourceAttemptId))) {
      throw new Error('The project file contains a broken link to an AI attempt.');
    }
    if ([...data.attempts, ...data.revisions].some((item) => !pageMap.has(item.pageId))) throw new Error('The project file contains history for an unknown page.');
    const validBox = (item) => [item.x, item.y, item.width, item.height].every((value) => Number.isFinite(value) && value >= 0 && value <= 1) && item.width > 0 && item.height > 0 && item.x + item.width <= 1.000001 && item.y + item.height <= 1.000001;
    if (new Set(importedRegions.map((item) => item.id)).size !== importedRegions.length || importedRegions.some((item) => typeof item.id !== 'string' || !pageMap.has(item.pageId) || !validBox(item))) throw new Error('The project file contains invalid regions.');
    const regionById = new Map(importedRegions.map((region) => [region.id, region]));
    if (new Set(importedLines.map((item) => item.id)).size !== importedLines.length || importedLines.some((item) => {
      const region = regionById.get(item.regionId);
      return typeof item.id !== 'string' || !pageMap.has(item.pageId) || !region || region.pageId !== item.pageId || typeof item.text !== 'string' || !validBox(item)
        || item.x < region.x || item.y < region.y || item.x + item.width > region.x + region.width + 0.000001 || item.y + item.height > region.y + region.height + 0.000001;
    })) throw new Error('The project file contains invalid lines.');
    if (new Set(importedChat.map((item) => item.id)).size !== importedChat.length || importedChat.some((item) => typeof item.id !== 'string' || !pageMap.has(item.pageId) || !['user', 'assistant'].includes(item.role) || typeof item.text !== 'string')) throw new Error('The project file contains invalid chat history.');
    const project = { ...data.project, id: retainIds ? data.project.id : makeId(), name: folderPath ? data.project.name : `${data.project.name} (restored)`, updatedAt: Date.now(), folderPath };
    const pages = data.pages.map((page) => ({ ...page, id: pageMap.get(page.id), projectId: project.id, sourceAttemptId: page.sourceAttemptId ? attemptMap.get(page.sourceAttemptId) : null, image: typeof page.image === 'string' ? dataUrlAsBlob(page.image, page.mimeType) : page.image }));
    const attempts = data.attempts.map((item) => ({ ...item, id: attemptMap.get(item.id), pageId: pageMap.get(item.pageId) }));
    const revisions = data.revisions.map((item) => ({ ...item, id: mappedId(item.id), pageId: pageMap.get(item.pageId), sourceAttemptId: item.sourceAttemptId ? attemptMap.get(item.sourceAttemptId) : null }));
    const regions = importedRegions.map((item) => ({ ...item, id: regionMap.get(item.id), pageId: pageMap.get(item.pageId), sourceAttemptId: item.sourceAttemptId ? attemptMap.get(item.sourceAttemptId) : null }));
    const lines = importedLines.map((item) => ({ ...item, id: lineMap.get(item.id), pageId: pageMap.get(item.pageId), regionId: regionMap.get(item.regionId), sourceAttemptId: item.sourceAttemptId ? attemptMap.get(item.sourceAttemptId) : null }));
    const chatMessages = importedChat.map((item) => ({ ...item, id: mappedId(item.id), pageId: pageMap.get(item.pageId) }));
    const existingProject = retainIds ? await byId('projects', project.id) : null;
    const oldPages = existingProject ? await allFrom('pages', 'projectId', project.id) : [];
    const oldPageIds = new Set(oldPages.map((page) => page.id));
    const [oldAttempts, oldRevisions, oldRegions, oldLines, oldChats] = existingProject ? await Promise.all([
      allFrom('attempts'), allFrom('textRevisions'), allFrom('regions'), allFrom('lines'), allFrom('chatMessages'),
    ]).then(([a, r, g, l, c]) => [a.filter((item) => oldPageIds.has(item.pageId)), r.filter((item) => oldPageIds.has(item.pageId)), g.filter((item) => oldPageIds.has(item.pageId)), l.filter((item) => oldPageIds.has(item.pageId)), c.filter((item) => oldPageIds.has(item.pageId))]) : [[], [], [], [], []];
    await new Promise((resolve, reject) => {
      const tx = state.db.transaction(['projects', 'pages', 'attempts', 'textRevisions', 'regions', 'lines', 'chatMessages'], 'readwrite');
      const projectsStore = tx.objectStore('projects');
      if (existingProject) {
        projectsStore.delete(project.id);
        oldPages.forEach((item) => tx.objectStore('pages').delete(item.id));
        oldAttempts.forEach((item) => tx.objectStore('attempts').delete(item.id));
        oldRevisions.forEach((item) => tx.objectStore('textRevisions').delete(item.id));
        oldRegions.forEach((item) => tx.objectStore('regions').delete(item.id));
        oldLines.forEach((item) => tx.objectStore('lines').delete(item.id));
        oldChats.forEach((item) => tx.objectStore('chatMessages').delete(item.id));
      }
      projectsStore.put(project);
      pages.forEach((page) => tx.objectStore('pages').add(page));
      attempts.forEach((item) => tx.objectStore('attempts').add(item));
      revisions.forEach((item) => tx.objectStore('textRevisions').add(item));
      regions.forEach((item) => tx.objectStore('regions').add(item));
      lines.forEach((item) => tx.objectStore('lines').add(item));
      chatMessages.forEach((item) => tx.objectStore('chatMessages').add(item));
      tx.oncomplete = resolve;
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error ?? new Error('Project restore was interrupted'));
    });
    await openProject(project.id);
    if (folderPath) await persistProjectFolder(project.id);
    if (pages.length) await selectPage(pages[0].id);
    if (!silent) toast(`Opened ${project.name}`);
  } catch (error) {
    if (silent) throw error;
    toast(`Could not open project file: ${error.message}`);
  }
}

function setCurrentProjectPreferences() {
  if (!state.project) return;
  state.project = {
    ...state.project,
    preset: document.querySelector('#preset').value,
    instruction: document.querySelector('#instruction').value,
    compareModels: document.querySelector('#compare-models').value,
    updatedAt: Date.now(),
  };
  transaction('projects', 'readwrite', (store) => store.put(state.project)).then(renderProjectSwitcher);
}

function showProjectDialog() {
  const dialog = document.querySelector('#project-dialog');
  document.querySelector('#project-name').value = '';
  dialog.showModal();
  document.querySelector('#project-name').focus();
}

document.querySelector('#new-project').addEventListener('click', showProjectDialog);
document.querySelector('#empty-new-project').addEventListener('click', showProjectDialog);
document.querySelector('#project-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const name = document.querySelector('#project-name').value.trim();
  if (!name) return;
  document.querySelector('#project-dialog').close();
  await createProject(name);
});
document.querySelector('#cancel-project').addEventListener('click', () => document.querySelector('#project-dialog').close());
document.querySelector('#add-pages').addEventListener('click', () => document.querySelector('#image-input').click());
document.querySelector('#backup-project').addEventListener('click', backupProject);
document.querySelector('#open-project-folder').addEventListener('click', openProjectFolder);
document.querySelector('#export-project-text').addEventListener('click', exportProjectText);
document.querySelector('#draw-region').addEventListener('click', () => { state.drawMode = state.drawMode === 'region' ? null : 'region'; updateLayoutControls(); });
document.querySelector('#draw-line').addEventListener('click', () => {
  if (!state.selectedRegionId) { toast('Select a region first.'); return; }
  state.drawMode = state.drawMode === 'line' ? null : 'line'; updateLayoutControls();
});
document.querySelector('#delete-layout').addEventListener('click', async () => {
  let remaining = state.lines;
  if (state.selectedLineId) {
    await transaction('lines', 'readwrite', (store) => store.delete(state.selectedLineId));
    remaining = state.lines.filter((line) => line.id !== state.selectedLineId).map((line, order) => ({ ...line, order }));
    await new Promise((resolve, reject) => {
      const tx = state.db.transaction('lines', 'readwrite');
      remaining.forEach((line) => tx.objectStore('lines').put(line));
      tx.oncomplete = resolve; tx.onerror = () => reject(tx.error); tx.onabort = () => reject(tx.error ?? new Error('Line ordering save was interrupted'));
    });
    state.selectedLineId = null;
  } else if (state.selectedRegionId) {
    const lines = state.lines.filter((line) => line.regionId === state.selectedRegionId);
    await new Promise((resolve, reject) => {
      const tx = state.db.transaction(['regions', 'lines'], 'readwrite');
      tx.objectStore('regions').delete(state.selectedRegionId);
      lines.forEach((line) => tx.objectStore('lines').delete(line.id));
      tx.oncomplete = resolve; tx.onerror = () => reject(tx.error); tx.onabort = () => reject(tx.error ?? new Error('Layout deletion interrupted'));
    });
    remaining = state.lines.filter((line) => line.regionId !== state.selectedRegionId).map((line, order) => ({ ...line, order }));
    await new Promise((resolve, reject) => {
      const tx = state.db.transaction('lines', 'readwrite');
      remaining.forEach((line) => tx.objectStore('lines').put(line));
      tx.oncomplete = resolve; tx.onerror = () => reject(tx.error); tx.onabort = () => reject(tx.error ?? new Error('Line ordering save was interrupted'));
    });
    state.selectedRegionId = null;
  }
  await syncPageTextFromLines(remaining);
  await persistProjectFolder(state.selectedPage?.projectId);
  await renderLayout();
});
document.querySelector('#layout-overlay').addEventListener('pointerdown', (event) => {
  if (!state.drawMode || !state.selectedPage) return;
  event.preventDefault();
  event.currentTarget.setPointerCapture(event.pointerId);
  state.layoutDragStart = pointerPoint(event);
});
document.querySelector('#layout-overlay').addEventListener('pointerup', async (event) => {
  if (state.layoutMoveDrag) { await persistLayoutMove(); return; }
  if (!state.layoutDragStart) return;
  const start = state.layoutDragStart; state.layoutDragStart = null;
  await finishLayoutDraw(start, pointerPoint(event));
});
document.querySelector('#layout-overlay').addEventListener('pointermove', moveLayoutSelection);
document.querySelector('#layout-overlay').addEventListener('pointercancel', () => { state.layoutDragStart = null; state.layoutMoveDrag = null; state.layoutMoveNext = null; });
document.querySelector('#review-layout').addEventListener('click', markRegionReviewed);
document.querySelector('#restore-project').addEventListener('click', () => document.querySelector('#project-file-input').click());
document.querySelector('#project-file-input').addEventListener('change', async (event) => {
  await restoreProject(event.target.files[0]);
  event.target.value = '';
});
document.querySelector('#image-input').addEventListener('change', async (event) => {
  await addImages(event.target.files);
  event.target.value = '';
});
document.querySelector('#transcript').addEventListener('input', () => scheduleTextSave());
document.querySelector('#export-text').addEventListener('click', exportText);
document.querySelector('#export-page-xml').addEventListener('click', exportPageXml);
document.querySelector('#preset').addEventListener('change', setCurrentProjectPreferences);
document.querySelector('#instruction').addEventListener('input', setCurrentProjectPreferences);
document.querySelector('#compare-models').addEventListener('change', setCurrentProjectPreferences);
document.querySelector('#history-toggle').addEventListener('click', async () => {
  const panel = document.querySelector('#attempt-panel');
  panel.hidden = !panel.hidden;
  document.querySelector('#history-toggle').setAttribute('aria-expanded', String(!panel.hidden));
  if (!panel.hidden) await renderAttempts();
});
document.querySelector('#close-history').addEventListener('click', () => {
  document.querySelector('#attempt-panel').hidden = true;
  document.querySelector('#history-toggle').setAttribute('aria-expanded', 'false');
});
document.querySelector('#run-ai').addEventListener('click', readWithAi);
document.querySelector('#compare-run').addEventListener('click', compareModels);
document.querySelector('#suggest-layout').addEventListener('click', suggestLayoutWithAi);
document.querySelector('#chat-open').addEventListener('click', openChat);
document.querySelector('#chat-close').addEventListener('click', () => document.querySelector('#chat-dialog').close());
document.querySelector('#chat-send').addEventListener('click', sendChatMessage);
document.querySelector('#chat-input').addEventListener('keydown', (event) => { if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') sendChatMessage(); });
document.querySelector('#ai-settings').addEventListener('click', showAiDialog);
document.querySelector('#ai-cancel').addEventListener('click', () => document.querySelector('#ai-dialog').close());
document.querySelector('#ai-delete-key').addEventListener('click', async () => {
  try { await gemini.deleteKey(); toast('Gemini key removed from this device'); } catch (error) { toast(error.message); }
  document.querySelector('#ai-dialog').close();
  await refreshAiStatus();
});
document.querySelector('#ai-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  gemini.setModel(document.querySelector('#ai-model').value);
  const key = document.querySelector('#ai-key').value.trim();
  try {
    if (key) { await gemini.saveKey(key); toast('Gemini key saved to Windows Credential Manager'); } else toast('AI settings saved');
  } catch (error) { toast(error.message); return; }
  document.querySelector('#ai-key').value = '';
  document.querySelector('#ai-dialog').close();
  await refreshAiStatus();
});
document.querySelector('#about-app').addEventListener('click', () => toast('Escript AI Desktop · Windows prototype'));
document.querySelector('#zoom-out').addEventListener('click', () => { state.imageZoom = Math.max(0.5, state.imageZoom / 1.25); fitImage(); });
document.querySelector('#zoom-in').addEventListener('click', () => { state.imageZoom = Math.min(4, state.imageZoom * 1.25); fitImage(); });
document.querySelector('#zoom-reset').addEventListener('click', () => { state.imageZoom = 1; fitImage(); });
new ResizeObserver(() => fitImage()).observe(document.querySelector('#image-stage'));

document.querySelector('.workspace').addEventListener('dragover', (event) => {
  if (state.project) event.preventDefault();
});
document.querySelector('.workspace').addEventListener('drop', async (event) => {
  if (!state.project) return;
  event.preventDefault();
  await addImages(event.dataTransfer.files);
});

function updateRunButton() {
  const button = document.querySelector('#run-ai');
  button.disabled = state.reading || !state.geminiReady || !state.selectedPage?.image;
  button.title = state.geminiReady ? `Read this page with ${gemini.getModel()} (paid)` : 'Add a Gemini API key in AI settings';
  const ready = !state.reading && state.geminiReady && Boolean(state.selectedPage?.image);
  document.querySelector('#compare-run').disabled = !ready;
  document.querySelector('#suggest-layout').disabled = !ready;
}

async function refreshAiStatus() {
  const status = await gemini.keyStatus();
  state.geminiReady = status.configured;
  const where = status.source === 'environment' ? 'key from GEMINI_API_KEY' : 'key in Windows Credential Manager';
  document.querySelector('#engine-note-text').textContent = status.configured
    ? `Google Gemini (${gemini.getModel()}, ${where}). Paid: each reading sends the page image to Google and is billed. You confirm every request.`
    : 'No AI system is connected. Open AI settings to add a Gemini API key. Your text and images stay on this device until you send a page.';
  updateRunButton();
  return status;
}

async function showAiDialog() {
  const status = await gemini.keyStatus();
  document.querySelector('#ai-model').value = gemini.getModel();
  const desktop = gemini.isDesktop();
  document.querySelector('#ai-key-fields').hidden = !desktop;
  document.querySelector('#ai-delete-key').hidden = !desktop || status.source !== 'credential-store';
  document.querySelector('#ai-key-state').textContent = status.configured
    ? (status.source === 'environment' ? 'Using GEMINI_API_KEY from the environment.' : 'A key is saved in Windows Credential Manager.')
    : (desktop ? 'No key saved yet.' : 'Browser preview: start the dev server with GEMINI_API_KEY set to enable reading.');
  document.querySelector('#ai-dialog').showModal();
}

function confirmPaidRequest(summary) {
  const dialog = document.querySelector('#ai-confirm');
  document.querySelector('#ai-confirm-summary').textContent = summary;
  dialog.returnValue = '';
  dialog.showModal();
  return new Promise((resolve) => dialog.addEventListener('close', () => resolve(dialog.returnValue === 'send'), { once: true }));
}

async function readWithAi() {
  const page = state.selectedPage;
  if (!page?.image || !state.geminiReady || state.reading) return;
  const model = gemini.getModel();
  const img = document.querySelector('#source-image');
  const imageTokens = gemini.estimateImageTokens(img.naturalWidth, img.naturalHeight);
  const sizeMb = (page.image.size / 1048576).toFixed(1);
  const ok = await confirmPaidRequest(`“${page.name}” (${new Intl.NumberFormat(locale).format(sizeMb)} MB${imageTokens ? `, roughly ${new Intl.NumberFormat(locale).format(imageTokens)} input tokens for the image` : ''}) will be sent to Google ${model}. The request is billed to your Gemini API account. There is no automatic retry.`);
  if (!ok) return;
  state.reading = true;
  updateRunButton();
  document.querySelector('#draft-label').textContent = t('Reading with {model}…', { model });
  const preset = document.querySelector('#preset').value;
  const instruction = document.querySelector('#instruction').value;
  const prompt = gemini.buildPrompt(preset, instruction);
  const attempt = {
    id: makeId(), pageId: page.id, createdAt: Date.now(), label: `Gemini · ${model}`, text: '', status: 'running',
    provider: 'google-gemini', model, preset, instruction, prompt, usage: null,
  };
  try {
    await transaction('attempts', 'readwrite', (store) => store.add(attempt));
    await persistProjectFolder(page.projectId);
    if (state.selectedPage?.id === page.id) await renderAttempts();
    const result = await gemini.readPage({ image: page.image, mimeType: page.mimeType, prompt, model });
    const completed = {
      ...attempt, status: 'completed', model: result.model, text: result.text, completedAt: Date.now(),
      finishReason: result.finishReason ?? null,
      usage: { promptTokens: result.promptTokens ?? null, outputTokens: result.outputTokens ?? null, totalTokens: result.totalTokens ?? null },
    };
    await transaction('attempts', 'readwrite', (store) => store.put(completed));
    await persistProjectFolder(page.projectId);
    if (state.selectedPage?.id === page.id) {
      if (!document.querySelector('#transcript').value.trim()) {
        await adoptText(result.text, 'AI draft', completed.id);
      }
      await renderAttempts();
    }
    toast(`${t('Read with AI')} ${result.model}${result.totalTokens != null ? ` · ${new Intl.NumberFormat(locale).format(result.totalTokens)} tokens` : ''}. ${t('Saved under Attempts.')}`);
  } catch (error) {
    const message = String(error?.message ?? error).replace(/AIza[\w-]{20,}/g, '[redacted key]').slice(0, 500);
    await transaction('attempts', 'readwrite', (store) => store.put({ ...attempt, status: 'failed', error: message, completedAt: Date.now() })).catch(() => {});
    await persistProjectFolder(page.projectId).catch(() => {});
    if (state.selectedPage?.id === page.id) await renderAttempts();
    toast(message);
  } finally {
    state.reading = false;
    const label = document.querySelector('#draft-label');
    if (label.textContent.startsWith('Reading with')) label.textContent = document.querySelector('#transcript').value.trim() ? (state.selectedPage?.textSource || 'Edited text') : 'No text yet';
    updateRunButton();
  }
}

async function compareModels() {
  const page = state.selectedPage;
  if (!page?.image || !state.geminiReady || state.reading) return;
  const models = [...new Set(document.querySelector('#compare-models').value.split(/[\n,]/).map((item) => item.trim()).filter(Boolean))];
  if (!models.length) { toast('Enter one or more Gemini model IDs to compare.'); return; }
  if (models.length > 4) { toast('Compare up to four models at a time.'); return; }
  const modelList = models.join(', ');
  const pageLines = [...state.lines];
  const transfer = pageLines.length ? 'Each sends the page image, line IDs, existing line text, and transcription prompt.' : 'Each sends the page image and transcription prompt.';
  if (!await confirmPaidRequest(`This comparison will make ${models.length} separate billed request${models.length === 1 ? '' : 's'} to Google for “${page.name}”: ${modelList}. ${transfer} There is no automatic retry.`)) return;
  state.reading = true; updateRunButton();
  const basePrompt = gemini.buildPrompt(document.querySelector('#preset').value, document.querySelector('#instruction').value);
  const prompt = pageLines.length
    ? `${basePrompt}\n\nTranscribe each supplied line separately. Keep each line ID exactly. Return only JSON: {"lines":[{"lineId":"existing ID","text":"transcription"}]}. Existing lines and current text:\n${pageLines.map((line) => `${line.id}\t${line.text || '(empty)'}`).join('\n')}`
    : basePrompt;
  const results = [];
  try {
    for (const model of models) {
      const attempt = { id: makeId(), pageId: page.id, createdAt: Date.now(), label: `Comparison · ${model}`, text: '', status: 'running', provider: 'google-gemini', model, operation: 'comparison', prompt, usage: null };
      await transaction('attempts', 'readwrite', (store) => store.add(attempt));
      await persistProjectFolder(page.projectId);
      try {
        const result = await gemini.readPage({ image: page.image, mimeType: page.mimeType, prompt, model });
        const lineResults = pageLines.length ? parseComparisonLines(result.text, new Set(pageLines.map((line) => line.id))) : [];
        const completed = { ...attempt, status: 'completed', model: result.model, text: result.text, lineResults, completedAt: Date.now(), usage: { promptTokens: result.promptTokens ?? null, outputTokens: result.outputTokens ?? null, totalTokens: result.totalTokens ?? null } };
        await transaction('attempts', 'readwrite', (store) => store.put(completed));
        await persistProjectFolder(page.projectId);
        results.push(completed);
      } catch (error) {
        const failed = { ...attempt, status: 'failed', error: String(error?.message ?? error).slice(0, 500), completedAt: Date.now() };
        await transaction('attempts', 'readwrite', (store) => store.put(failed));
        await persistProjectFolder(page.projectId);
        results.push(failed);
      }
      if (state.selectedPage?.id === page.id) await renderAttempts();
    }
  } finally {
    state.reading = false; updateRunButton();
  }
  if (state.selectedPage?.id === page.id) {
    await renderComparisonResults(results);
    document.querySelector('#comparison-dialog').showModal();
  }
  const succeeded = results.filter((item) => item.status === 'completed').length;
  toast(`Comparison finished: ${succeeded} of ${results.length} models returned text.`);
}

async function renderComparisonResults(attempts) {
  const container = document.querySelector('#comparison-results');
  container.replaceChildren();
  for (const attempt of attempts) {
    const card = document.createElement('article'); card.className = 'comparison-card';
    const title = document.createElement('strong'); title.textContent = attempt.model;
    const text = document.createElement('pre'); text.textContent = attempt.text || `Request ${attempt.status}: ${attempt.error || 'No transcription returned.'}`;
    card.append(title, text);
    for (const proposal of attempt.lineResults ?? []) {
      const line = state.lines.find((item) => item.id === proposal.lineId);
      if (!line) continue;
      const item = document.createElement('div'); item.className = 'comparison-line';
      const label = document.createElement('strong'); label.textContent = `${t('Lines')} ${line.order + 1}`;
      const value = document.createElement('p'); value.textContent = proposal.text;
      const useLine = document.createElement('button'); useLine.className = 'text-button'; useLine.textContent = t('Use this line');
      useLine.addEventListener('click', async () => { await applyChatProposal(line, proposal.text, 'Model comparison', attempt.id); });
      item.append(label, value, useLine); card.append(item);
    }
    if (attempt.usage?.totalTokens != null) {
      const usage = document.createElement('small'); usage.className = 'attempt-meta';
      usage.textContent = `${attempt.usage.promptTokens ?? '?'} input · ${attempt.usage.outputTokens ?? '?'} output · ${attempt.usage.totalTokens} total tokens`;
      card.append(usage);
    }
    if (attempt.status === 'completed') {
      const use = document.createElement('button'); use.className = 'text-button'; use.textContent = t('Use this result');
      use.addEventListener('click', async () => { await adoptText(attempt.text, 'AI draft', attempt.id); document.querySelector('#comparison-dialog').close(); });
      card.append(use);
    }
    container.append(card);
  }
}

function parseComparisonLines(text, validLineIds) {
  try {
    const cleaned = text.replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/, '');
    const parsed = JSON.parse(cleaned.slice(cleaned.indexOf('{'), cleaned.lastIndexOf('}') + 1));
    if (!Array.isArray(parsed.lines)) return [];
    return parsed.lines.filter((item) => validLineIds.has(item.lineId) && typeof item.text === 'string').slice(0, 2000);
  } catch { return []; }
}

async function openChat() {
  if (!state.selectedPage) { toast('Select a page first.'); return; }
  await renderChatMessages();
  document.querySelector('#chat-dialog').showModal();
  document.querySelector('#chat-input').focus();
}

async function renderChatMessages() {
  const container = document.querySelector('#chat-messages');
  container.replaceChildren();
  if (!state.selectedPage) return;
  const messages = (await allFrom('chatMessages', 'pageId', state.selectedPage.id)).sort((a, b) => a.createdAt - b.createdAt);
  for (const message of messages) {
    const bubble = document.createElement('article'); bubble.className = `chat-message ${message.role}`;
    const label = document.createElement('strong'); label.textContent = message.role === 'user' ? 'You' : message.model ? `Gemini · ${message.model}` : 'Assistant';
    const body = document.createElement('p'); body.textContent = message.reply || message.text;
    bubble.append(label, body); container.append(bubble);
    for (const proposal of message.proposals ?? []) {
      const line = state.lines.find((item) => item.id === proposal.lineId);
      if (!line || message.pageId !== state.selectedPage?.id) continue;
      const use = document.createElement('button'); use.className = 'text-button chat-proposal'; use.textContent = `Review suggestion for line ${line.order + 1}`;
      use.addEventListener('click', () => applyChatProposal(line, proposal.text));
      bubble.append(use);
    }
  }
  container.scrollTop = container.scrollHeight;
}

async function sendChatMessage() {
  const page = state.selectedPage;
  const input = document.querySelector('#chat-input');
  const text = input.value.trim();
  if (!page?.image || !text || !state.geminiReady || state.reading) return;
  const model = gemini.getModel();
  const history = (await allFrom('chatMessages', 'pageId', page.id)).sort((a, b) => a.createdAt - b.createdAt).slice(-10);
  const transcript = document.querySelector('#transcript').value;
  const selectedLine = state.lines.find((line) => line.id === state.selectedLineId);
  const validLineIds = new Set(state.lines.map((line) => line.id));
  const lineContext = state.lines.map((line) => `id=${line.id}; line ${line.order + 1}: ${line.text || '(empty)'}`).join('\n');
  const context = `Page: ${page.name}\nCurrent page transcription:\n${transcript || '(none)'}\nLine transcriptions and IDs:\n${lineContext || '(no lines segmented)'}\n${selectedLine ? `Selected line ID: ${selectedLine.id}\n` : ''}Recent conversation:\n${history.map((item) => `${item.role}: ${item.text}`).join('\n')}\nNew user message: ${text}`;
  if (!await confirmPaidRequest(`This sends “${page.name}”, its image, current page and line text, recent chat, and your message to Google ${model}. This is one billed request. Gemini's reply will not change your transcription until you choose a line suggestion.`)) return;
  state.reading = true;
  document.querySelector('#chat-send').disabled = true;
  input.value = '';
  try {
    await transaction('chatMessages', 'readwrite', (store) => store.add({ id: makeId(), pageId: page.id, role: 'user', text, model, createdAt: Date.now(), status: 'sent' }));
    await persistProjectFolder(page.projectId);
    await renderChatMessages();
    const prompt = `You are an archival transcription assistant. Answer the user's question and, when useful, propose corrections for specific line IDs listed in the context. Do not invent readings when uncertain. Return only JSON with this shape: {"reply":"your answer","proposals":[{"lineId":"an existing line ID","text":"proposed replacement text"}]}. Use an empty proposals array when no line edit is proposed. A proposal is not applied; the archivist must review and choose it.\n\n${context}`;
    const result = await gemini.readPage({ image: page.image, mimeType: page.mimeType, prompt, model });
    const parsed = parseChatResult(result.text, validLineIds);
    await transaction('chatMessages', 'readwrite', (store) => store.add({ id: makeId(), pageId: page.id, role: 'assistant', text: result.text, reply: parsed.reply, proposals: parsed.proposals, model: result.model, createdAt: Date.now(), status: 'completed', usage: { promptTokens: result.promptTokens ?? null, outputTokens: result.outputTokens ?? null, totalTokens: result.totalTokens ?? null } }));
    await persistProjectFolder(page.projectId);
  } catch (error) {
    await transaction('chatMessages', 'readwrite', (store) => store.add({ id: makeId(), pageId: page.id, role: 'assistant', text: `Request failed: ${String(error?.message ?? error).slice(0, 500)}`, model, createdAt: Date.now(), status: 'failed' }));
    await persistProjectFolder(page.projectId).catch(() => {});
  } finally {
    state.reading = false;
    document.querySelector('#chat-send').disabled = false;
    await renderChatMessages();
  }
}

function parseChatResult(text, validLineIds) {
  try {
    const cleaned = text.replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/, '');
    const parsed = JSON.parse(cleaned.slice(cleaned.indexOf('{'), cleaned.lastIndexOf('}') + 1));
    const proposals = Array.isArray(parsed.proposals) ? parsed.proposals.filter((item) => validLineIds.has(item.lineId) && typeof item.text === 'string').slice(0, 50) : [];
    return { reply: typeof parsed.reply === 'string' ? parsed.reply : text, proposals };
  } catch { return { reply: text, proposals: [] }; }
}

async function applyChatProposal(line, text, source = 'AI chat proposal', sourceAttemptId = null) {
  if (line.text?.trim()) {
    document.querySelector('#replace-confirm-copy').textContent = `Line ${line.order + 1} currently contains text. It will be saved in the page's text history before this chat suggestion replaces it.`;
    if (await confirmDialog('#replace-confirm') !== 'replace') return;
  }
  await transaction('lines', 'readwrite', (store) => store.put({ ...line, text, textSource: source, sourceAttemptId, reviewState: 'draft', updatedAt: Date.now() }));
  await syncPageTextFromLines(state.lines.map((item) => item.id === line.id ? { ...item, text, textSource: source, sourceAttemptId, reviewState: 'draft' } : item), source, sourceAttemptId);
  await renderLayout();
}

async function suggestLayoutWithAi() {
  const page = state.selectedPage;
  if (!page?.image || !state.geminiReady || state.reading) return;
  const model = gemini.getModel();
  if (!await confirmPaidRequest(`Send “${page.name}” to Google ${model} for one billed layout suggestion? Gemini will return page regions and transcription lines. The proposed geometry and text will be marked for your review and will not overwrite existing work.`)) return;
  const attempt = { id: makeId(), pageId: page.id, createdAt: Date.now(), label: `Layout · ${model}`, text: '', status: 'running', provider: 'google-gemini', model, operation: 'segmentation', usage: null };
  await transaction('attempts', 'readwrite', (store) => store.add(attempt));
  await persistProjectFolder(page.projectId);
  state.reading = true; updateRunButton();
  const prompt = 'Analyze the attached historical page for readable text layout. Return only valid JSON, no Markdown, in this shape: {"regions":[{"label":"main text","x":0,"y":0,"width":1000,"height":1000,"lines":[{"x":0,"y":0,"width":1000,"height":40,"text":"line transcription"}]}]}. Geometry uses integer coordinates from 0 to 1000 relative to the full image. Include regions in reading order and lines in reading order. Use tight rectangles around each line. Preserve original spelling and punctuation. Use empty text for illegible lines; do not omit visible text. These are proposals and will be reviewed by a human.';
  try {
    const result = await gemini.readPage({ image: page.image, mimeType: page.mimeType, prompt, model });
    const raw = result.text.replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/, '');
    const parsed = JSON.parse(raw.slice(raw.indexOf('{'), raw.lastIndexOf('}') + 1));
    if (!Array.isArray(parsed.regions) || !parsed.regions.length || parsed.regions.length > 100) throw new Error('The suggested layout had no usable regions.');
    const within = (value) => Number.isFinite(value) && value >= 0 && value <= 1000;
    const box = (item) => [item.x, item.y, item.width, item.height].every(within) && item.width > 0 && item.height > 0 && item.x + item.width <= 1000 && item.y + item.height <= 1000;
    if (parsed.regions.some((region) => !box(region) || !Array.isArray(region.lines) || region.lines.length > 1000 || region.lines.some((line) => !box(line) || line.x < region.x || line.y < region.y || line.x + line.width > region.x + region.width || line.y + line.height > region.y + region.height || typeof line.text !== 'string'))) throw new Error('Gemini returned invalid region or line coordinates. No layout was saved.');
    const regionRecords = parsed.regions.map((region, index) => ({ id: makeId(), pageId: page.id, x: region.x / 1000, y: region.y / 1000, width: region.width / 1000, height: region.height / 1000, order: index, label: String(region.label || `Region ${index + 1}`).slice(0, 120), origin: 'gemini', reviewState: 'proposed', sourceAttemptId: attempt.id, createdAt: Date.now() }));
    const lineRecords = parsed.regions.flatMap((region, regionIndex) => region.lines.map((line, lineIndex) => ({ id: makeId(), pageId: page.id, regionId: regionRecords[regionIndex].id, x: line.x / 1000, y: line.y / 1000, width: line.width / 1000, height: line.height / 1000, order: lineRecordsCount(parsed.regions, regionIndex, lineIndex), text: line.text, textSource: 'AI draft', reviewState: 'draft', origin: 'gemini', sourceAttemptId: attempt.id, createdAt: Date.now(), updatedAt: Date.now() })));
    await new Promise((resolve, reject) => {
      const tx = state.db.transaction(['regions', 'lines', 'attempts'], 'readwrite');
      regionRecords.forEach((region) => tx.objectStore('regions').add(region));
      lineRecords.forEach((line) => tx.objectStore('lines').add(line));
      tx.objectStore('attempts').put({ ...attempt, status: 'completed', text: result.text, completedAt: Date.now(), regionCount: regionRecords.length, lineCount: lineRecords.length, usage: { promptTokens: result.promptTokens ?? null, outputTokens: result.outputTokens ?? null, totalTokens: result.totalTokens ?? null } });
      tx.oncomplete = resolve; tx.onerror = () => reject(tx.error); tx.onabort = () => reject(tx.error ?? new Error('Layout save was interrupted'));
    });
    await persistProjectFolder(page.projectId);
    if (state.selectedPage?.id === page.id) { await syncPageTextFromLines(lineRecords, 'AI draft', attempt.id); await renderLayout(); await renderAttempts(); }
    toast(`Added ${regionRecords.length} proposed regions and ${lineRecords.length} lines. Review them against the page.`);
  } catch (error) {
    const message = String(error?.message ?? error).replace(/AIza[\w-]{20,}/g, '[redacted key]').slice(0, 500);
    await transaction('attempts', 'readwrite', (store) => store.put({ ...attempt, status: 'failed', error: message, completedAt: Date.now() })).catch(() => {});
    await persistProjectFolder(page.projectId).catch(() => {});
    if (state.selectedPage?.id === page.id) await renderAttempts();
    toast(message);
  } finally { state.reading = false; updateRunButton(); }
}

function lineRecordsCount(regions, regionIndex, lineIndex) {
  return regions.slice(0, regionIndex).reduce((total, region) => total + region.lines.length, 0) + lineIndex;
}

async function start() {
  refreshAiStatus().catch(() => {});
  try {
    state.db = await openDatabase();
    const attempts = await allFrom('attempts');
    const interrupted = attempts.filter((attempt) => attempt.status === 'running');
    if (interrupted.length) {
      await new Promise((resolve, reject) => {
        const tx = state.db.transaction('attempts', 'readwrite');
        const store = tx.objectStore('attempts');
        interrupted.forEach((attempt) => store.put({ ...attempt, status: 'interrupted', error: 'The app closed before a result was saved. Gemini may have received the request.', completedAt: Date.now() }));
        tx.oncomplete = resolve;
        tx.onerror = () => reject(tx.error);
        tx.onabort = () => reject(tx.error ?? new Error('Attempt recovery was interrupted'));
      });
    }
    const projects = await allFrom('projects');
    if (projects.length) {
      const recent = projects.sort((a, b) => b.updatedAt - a.updatedAt)[0];
      if (gemini.isDesktop() && recent.folderPath) {
        try {
          await restoreProject(await readProjectFolder(recent.folderPath), recent.folderPath, true);
          return;
        } catch (error) {
          await openProject(recent.id);
          document.querySelector('#project-folder-status').textContent = `Folder recovery unavailable: ${String(error?.message ?? error).slice(0, 120)}. Local copy opened.`;
        }
      } else await openProject(recent.id);
    }
    else {
      setProject(null);
      await renderProjectSwitcher();
    }
  } catch (error) {
    console.error(error);
    toast('Local storage could not be opened. Check browser storage settings.');
  }
}

start();
