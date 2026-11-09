// Gemini provider bridge. In the desktop app the key stays in the Windows
// Credential Manager and calls run in Rust; in the Vite browser preview they go
// through a dev-only proxy that reads GEMINI_API_KEY from the server's environment.
// The key is never held in page code or browser storage.

export const DEFAULT_MODEL = 'gemini-2.5-flash';
const MODEL_STORAGE = 'escript-ai-desktop.gemini-model';

const tauri = () => window.__TAURI_INTERNALS__;
export const isDesktop = () => Boolean(tauri());

export function getModel() {
  try { return localStorage.getItem(MODEL_STORAGE) || DEFAULT_MODEL; } catch { return DEFAULT_MODEL; }
}

export function setModel(model) {
  try { localStorage.setItem(MODEL_STORAGE, model.trim() || DEFAULT_MODEL); } catch { /* preference only */ }
}

async function devProxy(path, body) {
  const response = await fetch(`/__gemini/${path}`, {
    method: body ? 'POST' : 'GET',
    headers: body ? { 'content-type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const payload = await response.json().catch(() => ({ error: `Preview proxy returned HTTP ${response.status}` }));
  if (!response.ok) throw new Error(payload.error ?? `HTTP ${response.status}`);
  return payload;
}

export async function keyStatus() {
  if (isDesktop()) return tauri().invoke('gemini_key_status');
  try { return await devProxy('status'); } catch { return { configured: false, source: null }; }
}

export async function saveKey(key) {
  if (!isDesktop()) throw new Error('Keys can only be saved in the desktop app. For the browser preview, start the dev server with GEMINI_API_KEY set.');
  return tauri().invoke('gemini_save_key', { key });
}

export async function deleteKey() {
  if (!isDesktop()) throw new Error('No key is stored by the browser preview.');
  return tauri().invoke('gemini_delete_key');
}

function blobToBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',', 2)[1]);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });
}

export async function readPage({ image, mimeType, prompt, model }) {
  const args = { model, mimeType, imageBase64: await blobToBase64(image), prompt };
  if (isDesktop()) {
    try { return await tauri().invoke('gemini_read_page', args); } catch (error) { throw new Error(String(error)); }
  }
  return devProxy('read', args);
}

const PRESET_PROMPTS = {
  diplomatic: 'Produce a diplomatic transcription: keep original spelling, abbreviations, punctuation, capitalisation and line breaks exactly as written.',
  expanded: 'Transcribe the page, expanding common abbreviations silently, while otherwise keeping original spelling and line breaks.',
  uncertain: 'Transcribe the page diplomatically. Mark uncertain readings as [?word] and illegible passages as [illegible].',
};

export function buildPrompt(preset, instruction) {
  return [
    'You are transcribing a historical archival document from the attached page image.',
    PRESET_PROMPTS[preset] ?? PRESET_PROMPTS.diplomatic,
    'Return only the transcribed text, with no commentary, headings or Markdown.',
    instruction?.trim() ? `Additional instruction from the archivist: ${instruction.trim()}` : '',
  ].filter(Boolean).join('\n');
}

// Rough input-size estimate: Gemini counts large images as 768px tiles of ~258 tokens.
export function estimateImageTokens(width, height) {
  if (!width || !height) return null;
  if (width <= 384 && height <= 384) return 258;
  return Math.ceil(width / 768) * Math.ceil(height / 768) * 258;
}
