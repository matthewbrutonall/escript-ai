# Contributing to Escript AI

Alpha. Small, reviewable patches are welcome. Do not make
paid API calls from tests or CI.

## AI unit tests (no Django, no network)

From the repository root:

```bash
cd escriptorium/app/apps
python3 -m unittest ai.tests -v
```

`ai/tests/__init__.py` has a `load_tests` hook so that command discovers
`test_*.py`. Equivalent:

```bash
python3 -m unittest discover -s ai/tests -v
```

These tests **must not** touch the network or a provider API. They use
`MockBackend` and fakes. If you add a hosted adapter, mock `requests.post`.

## Where things live

| Concern | Path |
|---------|------|
| Provider adapters (Gemini, Anthropic, OpenAI, local, mock) | `escriptorium/app/apps/ai/backends.py` |
| Dispatch guards (off-site policy, keys, budget, cross-document pks) | `escriptorium/app/apps/ai/dispatch.py` |
| Colour overlay | `escriptorium/app/apps/ai/overlay.py` |
| Overlap / fragment preflight | `escriptorium/app/apps/ai/preflight.py` |
| ORM write + default prompt | `escriptorium/app/apps/ai/pipeline.py` |
| Celery task | `escriptorium/app/apps/ai/tasks.py` |
| DRF serializer / list of backends | `escriptorium/app/apps/ai/serializers.py`, `views.py` |
| Design | `ARCHITECTURE.md` |

Do not inject AI into `DocumentPart.transcribe()` (that path is kraken). Do not
extend `OcrModel` for AI provider configs — use `AIBackendConfig`.

## Hosted APIs

Keys come from the environment (`GEMINI_API_KEY`, `ANTHROPIC_API_KEY`,
`OPENAI_API_KEY`, or the backend’s `key_ref`). They are never stored on
`AIBackendConfig`. A missing key must fail closed, not fall back to another
paid provider.

## Licence

Preserve `escriptorium/LICENSE` (MIT, PSL) and `kraken/LICENSE` (Apache 2.0).
Escript AI original files are MIT; see the root `LICENSE`.
