# Escript AI — status

**Alpha.** Independent project derived from eScriptorium.
Not an official eScriptorium release.

_Last updated: 2026-09-12._

## Current state

The `ai/` Django app is in-tree and registered. Colour-keyed region
transcription writes a named layer via the ORM, with `version_source` set to the
provider (e.g. `gemini:gemini-2.5-flash`). Dispatch checks policy, keys, and
part-document match **before** a hosted call.

**AI output is a draft until a human reviews it.** Do not treat it as ground
truth and do not train kraken on an unreviewed layer. New AI layers start
`raw`; a disagreement sample vs an existing kraken layer (when one exists)
must be acknowledged (admin action, exact phrase) before the layer can be
marked `training-eligible`. Disagreement sample when a comparison layer
exists; otherwise a random AI-line sample must be reviewed.
`TrainSerializer` and `core.tasks.train` refuse a gated layer that is still
`raw` or `sampled`. The document dashboard lists the sample and accepts the
acknowledgement phrase. Training collection picks omit AI layers that are
not training-eligible. Manual/kraken layers (no gate) are unchanged.
Few-shot VLM priming is not in this release.

## Providers wired

| Provider | Adapter | Notes |
|----------|---------|--------|
| Google Gemini | `GeminiBackend` | Best empirical results on prose / modern Hindi |
| Anthropic Claude | `AnthropicBackend` | Sonnet 5: omit `temperature` (400 otherwise); thinking counts toward `max_tokens` |
| OpenAI | `OpenAIBackend` | Responses API |
| Local | `LocalOpenAIBackend` | Ollama / vLLM; no API spend |
| Tests | `MockBackend` | No network |

Keys: environment only (`GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`).
See `.env.example`. Missing key → fail closed. No silent spend.

## What the demos showed

- Colour-keyed transcription can write provider-stamped draft layers from
  segmented manuscript images.
- Script coverage is handled by the selected backend and still requires human
  review before reuse as training data.

Scored spike (tight PAGE): 0.5% CER vs human GT — one page, not a benchmark.

## Tests

```bash
cd escriptorium/app/apps
python3 -m unittest ai.tests -v
```

No Django, no network, no API. Guards covered: polygon overlap, fragment-before-overlap,
cross-document pks, never-send-off-site, missing key, provider mapping (including
Claude payload without `temperature`, OpenAI `input_image` body).

## Known limitations

- Quality gate (dual-engine disagreement, training-eligible state) is designed,
  not productized.
- Budget method `has_free_ai_budget` is a stub; env keys are the secret store.
- Websocket “done” toast can fail (`gettext` lazy proxy vs msgpack) even when
  the job succeeded — check the layer.
- Frontend AI group is wired; some eScriptorium builds still use the older
  transcribe wizard.

## Internationalisation (UI)

Public homepage and Django chrome (login, projects, documents, transcribe
wizard) have catalogs for **en, ar, hi, pl, it, es, pt**. Arabic public pages
use `dir="rtl"`.

The Vue 2.7 chrome uses `vue-i18n@8` with the same language list and the
`django_language` cookie. Sidebar language menu (names, not flags) reloads
via `POST /i18n/setlang/`. Covered: global nav, projects/document/images
dashboards, transcribe/segment/export/import/align/edit modals, share and
search panels, ontology and characters cards, training form, collection
manager, editor panel switcher, ontology editor, transcriptions manager,
element details, alignment advanced fields, archive/move modals, and tag
filters. Remaining English: some import-form details, metadata key/value
fields, editor help copy, and the websocket “done” toast. Vue i18n only
shows in **non-legacy** UI.

Language list: `ESC_LANGUAGES` (default `en,ar,hi,pl,it,es,pt`). Switcher uses
language names, not flags.

## Roadmap (next)

1. Remaining Vue strings (modals, ontology cards, editor panels) and RTL CSS
   beyond `html dir`.
2. Table / ledger path, or an honest “skip this page” policy in the UI.
3. Dual-engine disagreement sort + training-eligible flag.
4. Indic manuscript-capable local models; conventions object (dandas, no
   Sanskritizing).
5. Encrypted secret store; real monthly AI budget.
6. Fix the websocket notification on job complete.

## See also

[README.md](README.md) · [ARCHITECTURE.md](ARCHITECTURE.md) · [CONTRIBUTING.md](CONTRIBUTING.md)
