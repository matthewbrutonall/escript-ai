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
