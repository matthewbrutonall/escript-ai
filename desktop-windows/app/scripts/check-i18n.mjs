import fs from 'node:fs/promises';
globalThis.localStorage = { getItem: () => null, setItem() {} };
globalThis.document = { documentElement: {} };
const { LANGUAGES, hasTranslation, keyed } = await import('../src/i18n.js');
const source = await fs.readFile(new URL('../src/main.js', import.meta.url), 'utf8');
const dynamic = [...source.matchAll(/\bt\(\s*'((?:[^'\\]|\\.)*)'/g)].map((match) => match[1]);
const phrases = [...new Set([...Object.keys(keyed), ...dynamic])];
for (const [code] of LANGUAGES) {
  const missing = phrases.filter((phrase) => !hasTranslation(code, phrase));
  if (missing.length) console.log(`${code}:\n${missing.map((phrase) => `  ${phrase}`).join('\n')}`);
}
