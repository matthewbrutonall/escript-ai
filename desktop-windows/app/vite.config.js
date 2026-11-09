import { defineConfig } from 'vite';

const GEMINI_API = 'https://generativelanguage.googleapis.com/v1beta/models';

// Dev-only bridge so the browser preview can use Gemini without exposing the key
// to page code. The desktop app uses the Rust commands in src-tauri/src/gemini.rs.
function geminiDevProxy() {
  const readJson = (req) => new Promise((resolve, reject) => {
    let raw = '';
    req.on('data', (chunk) => { raw += chunk; });
    req.on('end', () => { try { resolve(JSON.parse(raw)); } catch (error) { reject(error); } });
    req.on('error', reject);
  });
  const send = (res, status, payload) => {
    res.statusCode = status;
    res.setHeader('content-type', 'application/json');
    res.end(JSON.stringify(payload));
  };
  return {
    name: 'gemini-dev-proxy',
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use('/__gemini', async (req, res) => {
        const key = process.env.GEMINI_API_KEY?.trim();
        if (req.url === '/status') return send(res, 200, { configured: Boolean(key), source: key ? 'environment' : null });
        if (req.url !== '/read' || req.method !== 'POST') return send(res, 404, { error: 'Not found' });
        if (!key) return send(res, 400, { error: 'GEMINI_API_KEY is not set for the dev server.' });
        try {
          const { model, mimeType, imageBase64, prompt } = await readJson(req);
          if (!/^[A-Za-z0-9._-]{1,80}$/.test(model ?? '')) return send(res, 400, { error: 'Invalid model name.' });
          if (!['image/png', 'image/jpeg', 'image/webp'].includes(mimeType)) return send(res, 400, { error: 'Unsupported image type.' });
          const upstream = await fetch(`${GEMINI_API}/${model}:generateContent`, {
            method: 'POST',
            headers: { 'content-type': 'application/json', 'x-goog-api-key': key },
            body: JSON.stringify({
              contents: [{ role: 'user', parts: [{ inline_data: { mime_type: mimeType, data: imageBase64 } }, { text: prompt }] }],
              generationConfig: { temperature: 0 },
            }),
            signal: AbortSignal.timeout(180_000),
          });
          const payload = await upstream.json();
          if (!upstream.ok) return send(res, upstream.status, { error: `Gemini refused the request (HTTP ${upstream.status}): ${payload.error?.message ?? 'unknown error'}` });
          const candidate = payload.candidates?.[0];
          const text = (candidate?.content?.parts ?? []).map((part) => part.text ?? '').join('');
          if (!text.trim()) return send(res, 502, { error: `Gemini returned no text (${candidate?.finishReason ?? payload.promptFeedback?.blockReason ?? 'no text returned'}).` });
          const usage = payload.usageMetadata ?? {};
          return send(res, 200, {
            text, model, finishReason: candidate?.finishReason ?? null,
            promptTokens: usage.promptTokenCount ?? null, outputTokens: usage.candidatesTokenCount ?? null, totalTokens: usage.totalTokenCount ?? null,
          });
        } catch (error) {
          return send(res, 502, { error: `Gemini could not be reached: ${error.message}` });
        }
      });
    },
  };
}

export default defineConfig({
  clearScreen: false,
  plugins: [geminiDevProxy()],
  server: {
    host: '127.0.0.1',
    port: 1420,
    strictPort: true,
    watch: { ignored: ['**/src-tauri/**'] },
  },
});
