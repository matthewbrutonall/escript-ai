# `ai` — Escript AI transcription app

Self-contained Django app that adds AI-assisted transcription alongside kraken.
**Additive: no change to `core` models.** Writes `LineTranscription` rows via
the ORM (the way `DocumentPart.transcribe` already does).

This is part of **Escript AI**, an independent project **derived from**
eScriptorium. It is not an official eScriptorium module.

Design: `../../../../ARCHITECTURE.md`. Tests and contribution rules:
`../../../../CONTRIBUTING.md`.

## Layout

| File | Role |
|------|------|
| `models.py` | `AIBackendConfig`, `AIJob`, `AIUsageLedger`, `AIDocumentPolicy`, `AILayerGate`, `AILineDisagreement` |
| `backends.py` | Gemini, Anthropic, OpenAI, local OpenAI-compatible, Mock |
| `overlay.py` | Colour-keyed region renderer |
| `preflight.py` | Overlap / fragment guards — **before** spend |
| `conventions.py` | Diplomatic toggles appended to the prompt |
| `triage.py` | AI vs kraken CER + disagreement sample |
| `gate.py` | Layer state `raw → sampled → training-eligible` |
| `assignment.py` | Colour-key JSON health; scrambled crops do not stamp |
| `passim_fallback.py` | Witness→OCR align; unmatched lines stay empty |
| `fixthis.py` | One-line re-read prompt + parse |
| `seg_review.py` | Numbered-box segmentation judgements; never writes masks |
| `backends.py` | Gemini, Anthropic, OpenAI, local, Azure, Mistral |
| `seg_apply.py` | Accept deletes spurious lines and stamps Line.typology |
| `budget.py` | Monthly USD cap vs AIUsageLedger |
| `secrets.py` | Fernet per-user keys; env/default fallback |
| `pipeline.py` | Crops → backend → ORM write with real `version_source` |
| `dispatch.py` | Off-site policy, keys, budget, cross-document part pks |
| `tasks.py` | Celery `ai_transcribe` |
| `serializers.py` / `views.py` | DRF: transcribe, `GET /api/ai-backends/`, document `ai-gates` |
| `htr_engine_contract.py` | External HTR engine contract, v1. Not imported by existing transcription jobs, views, or adapters |
| `htr_engine_client.py` | GET capabilities and models, plus `recognize_lines` (`POST /v1/recognize`). Not imported by transcription jobs |
| `docs/htr-engine-contract.md` | Wrapper-facing examples for that contract. Not a running service |
| `docs/external-htr-integration.md` | Planning note for a future explicit external HTR action. Not implemented. Transcription jobs do not use it |
| `external_htr_request.py` | Builds a `/v1/recognize` body from line geometry. Does not call an engine, read an engine config, or write a transcription |
| `reference_engine/` | Fake contract-test engine. Returns `LINE {line_id}`. Optional loopback server: `python -m ai.reference_engine.server`. Not a recognizer, not for production, and not used by transcription jobs |
| `htr_engine_contract_check.py` | Operator check: capabilities, models, one model, one synthetic recognize. Not used by transcription jobs |
| `management/commands/check_external_htr_engine.py` | CLI for that check: `check_external_htr_engine <config_id>`. Not used by transcription jobs |
| `external_engines/pylaia/` | PyLaia wrapper skeleton and backend seam. Default backend: empty model list, recognize returns `unavailable`. `DecodePyLaiaBackend` runs only when a caller injects it, or when that package's server is started with `--backend decode`. The default server command does not. `python -m ai.external_engines.pylaia.check_model` checks a model directory and does not run recognition. No PyLaia dependency in the app image, the default server is not a recognizer, and transcription jobs do not use it. PyLaia support is currently a skeleton because its runtime belongs in a separate engine container. A prototype Dockerfile is in that directory. It is not wired into Compose and is not built by default. A manual `ExternalHTREngineConfig` row can point at a reachable smoke service; see `external_engines/pylaia/README.md`. Transcription jobs still do not read that row |

## Tests (no Django, no network, no API)

```bash
cd escriptorium/app/apps
python3 -m unittest ai.tests -v
```

Do not add tests that call Gemini, Anthropic, or OpenAI. Mock `requests.post`.

## Keys

Hosted backends read `os.environ[config.key_ref]` (typically `GEMINI_API_KEY`,
`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`). Unknown providers raise; they do not
fall back to Gemini. Local backends need no key.

## Provenance

`version_source` is `editable=False` on the stock REST serializer. This app
sets it in the ORM (`stamp_line_transcription`), e.g.
`anthropic:claude-sonnet-5`.
