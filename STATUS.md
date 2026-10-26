# Escript AI — status

**Alpha.** Independent project derived from eScriptorium.
Not an official eScriptorium release.

_Last updated: 2026-09-13._

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
| Azure OpenAI | `AzureOpenAIBackend` | Resource URL + deployment name |
| Mistral | `MistralBackend` | Pixtral / `api.mistral.ai` |
| Local | `LocalOpenAIBackend` | Ollama / vLLM; no API spend |
| Tests | `MockBackend` | No network |

Keys: instance env (`GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
and the matching Azure/Mistral refs) plus optional per-user Fernet keys
(`AIUserKey`). User keys beat env. Dispatch resolves the key **with the
requesting user**; a backend that only has an `AIUserKey` is allowed.
Missing key → fail closed. No silent spend.

Monthly cap: `AI_MONTHLY_BUDGET_USD` and/or `AIUserQuota.monthly_usd`.
None or negative = unlimited. **0 = blocked**, even when the job estimate
is 0.

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
cross-document pks (transcribe and seg review), never-send-off-site, missing key
(including per-user), cap 0, provider mapping (including Claude payload without
`temperature`, OpenAI `input_image` body).

## Known limitations

- Colour-key is a prose/line tool. Dense ledgers, tables, and overlapping
  newspaper geometry are the pages that fail; there is no “skip this page”
  in the UI yet.
- Quality gate (sample → acknowledge → `training-eligible`) is on the
  document dashboard. It is not a full dual-engine product: comparison is
  opportunistic, and some review copy is still English.
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

Default `ESC_LANGUAGES`:
`en,ar,hi,pl,it,es,pt,fr,de,ur,tr,te`. Switcher uses language names, not
flags. Arabic and Urdu are RTL (`html dir`); the others are LTR.

Django homepage/chrome and Vue 2.7 (`vue-i18n@8`) share that list via
`json_script` / the `django_language` cookie. Covered: global nav,
projects/document/images dashboards, transcribe/segment/export/import/align/edit
modals, share and search panels, ontology and characters cards, training form,
collection manager, editor panel switcher, ontology editor, transcriptions
manager, element details, alignment advanced fields, archive/move modals, and
tag filters. Remaining English: some import-form details, metadata key/value
fields, editor help copy, and the websocket “done” toast. Vue i18n only
shows in **non-legacy** UI. RTL beyond `html dir` is unfinished.

## Roadmap (next)

1. Table / ledger path, or an honest “skip this page” policy in the UI.
   Clearest product boundary: the wrong layout through colour-key is worse
   than leftover English chrome.
2. Remaining Vue strings and RTL CSS beyond `html dir`.
3. Indic manuscript-capable local models; conventions object (dandas, no
   Sanskritizing).
4. Image few-shot / embedding retrieval for style priming.

## Kraken training limits

Recognition training (`train`, `train_from_collection`, and `train_`) and both segmentation trainers pass epoch limits from the Kraken training config into `KrakenTrainer`. The recognition tasks accept `epochs`, `quit_mode`, and `min_epochs` and forward them. Limits are checked before the trainer is created.

A fixed run uses `epochs` as `max_epochs` and passes `min_epochs` through. `min_epochs` cannot exceed that maximum. An early-stopping run still uses Kraken's own early-stopping callback. Its ceiling is the configured `epochs` value when that value is at least 1. Otherwise the ceiling is `KRAKEN_EARLY_STOP_MAX_EPOCHS` (default 100). `min_epochs` cannot exceed the ceiling. A quit mode other than fixed or early, a non-integer count, a fixed run below 1 epoch, an early run with `epochs` of 0 or below -1, a negative lag, an early-stopping ceiling that is not a positive integer, or a minimum above the ceiling raises `TrainingLimitError` before fitting starts.

Unconfigured runs keep the Kraken class defaults, with that ceiling applied to early stopping. App-launched PP-OCRv6 recognition is a fixed 100-epoch schedule and BLLA segmentation is a fixed 50-epoch schedule. VGSL recognition uses early stopping and, with its default `epochs=-1`, stops at the application ceiling of 100. Shorter schedules via `epochs`, `quit_mode`, and `min_epochs` are available only to direct task callers; the UI, API, and `OcrModel` methods do not expose those overrides. `KRAKEN_EARLY_STOP_MAX_EPOCHS` bounds the early-stopping fallback; it does not shorten fixed app-launched runs.

Each fit writes checkpoints in its own `runs/<id>/` directory inside the model directory. The run keeps one file, the best `val_metric` checkpoint, and pruning stays inside that directory. That file is recorded as one model version, and the version names that run's file. The checkpoint stays on disk and is the conversion source. A later run does not overwrite or delete a file named by an earlier version.

A named best checkpoint that is missing fails the task, emits a training-error event and user notice, and clears training state. Existing models and version files are preserved. A model that started without a weight file or version history is removed if that checkpoint is missing, so an unusable new model is not left available.

An empty best-checkpoint path also fails the task and sends the existing no-convergence warning. It preserves models that started with weights or history; only a model that started with neither is discarded. A failed run restores the original file field, including an originally empty value. Rejecting unsupported D-FINE fine-tuning also preserves the supplied model and its history.

Cancellation revokes the Celery task and sets training off. This application does not resume a fit, so the trainer does not write a last checkpoint for Lightning to pick up later.

Training eligibility gates, held-out page partitioning, progress events, and the CPU/GPU device setting are unchanged. Held-out `ketos` report parsing is unchanged.

Training follow-ups remain: collection segmentation can attempt restoration from an empty default-model path; cancelled/crashed runs can leave unreferenced run artifacts; and invalid-limit failures do not have consistent user-facing reporting across task types.

## See also

[README.md](README.md) · [ARCHITECTURE.md](ARCHITECTURE.md) · [CONTRIBUTING.md](CONTRIBUTING.md)
