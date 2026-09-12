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
Same-document few-shot text (pinned then recent corrections) is appended
to the prompt. If no comparison layer exists, a cheap on-instance kraken
recognizer is tried; failure falls back to a random sample. Reviewed pages can be held out of `core.tasks.train`. After training, held-out
lines are compiled to a binary eval set and `ketos test -f binary` runs if
`ketos` is on PATH. Collection training aborts if every item was held out.

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
- Monthly AI cap: set `AI_MONTHLY_BUDGET_USD` and/or `AIUserQuota`. Per-user
  keys (`AIUserKey`) are Fernet-encrypted; they beat instance env keys.
- Frontend AI group is wired; some eScriptorium builds still use the older
  transcribe wizard.
- Colour-key assignment that misses half the keys (or invents extra keys)
  now falls back to per-line crops, then a Passim-style snap of the VLM
  dump onto a kraken comparison layer. Unmatched lines stay empty.
- “Fix this” re-reads one line from the sample-review table (optional
  guide text). Same spend/policy guards as a full job.
- AI segmentation review flags overlapping/missing/wrong-order lines as
  suggestions. Geometry overlap (IoU ≥ 0.20) is marked spurious even if
  the VLM only talks about type. Accept deletes spurious lines and stamps
  line types; it never invents new masks. Missed/order stays a to-do.

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

Language list: `ESC_LANGUAGES` (default
`en,ar,hi,pl,it,es,pt,fr,de,ur,tr,te`). Switcher uses language names, not
flags. Arabic and Urdu are RTL (`html dir`); Telugu is LTR.

## Roadmap (next)

1. Remaining Vue strings (modals, ontology cards, editor panels) and RTL CSS
   beyond `html dir`.
2. Table / ledger path, or an honest “skip this page” policy in the UI.
3. Multi-tenant key store + extra providers (rest of Phase 4).
4. Indic manuscript-capable local models; conventions object (dandas, no
   Sanskritizing).
5. Encrypted secret store; real monthly AI budget.
6. Image few-shot / embedding retrieval for style priming.

## See also

[README.md](README.md) · [ARCHITECTURE.md](ARCHITECTURE.md) · [CONTRIBUTING.md](CONTRIBUTING.md)
