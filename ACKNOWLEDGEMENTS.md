# Acknowledgements

**Escript AI** is an independent open-source project. It is **derived from**
[eScriptorium](https://gitlab.com/scripta/escriptorium) and uses
[kraken](https://kraken.re) for layout. It is **not** an official eScriptorium
release, **not** a Scripta/PSL product, and is **not endorsed** by the
eScriptorium or kraken authors unless they say so themselves.

## eScriptorium

Escript AI is built from the eScriptorium web application originally created
by Robin Tissot, PSL, and contributors (Copyright © 2018 Robin Tissot, PSL;
MIT License — [gitlab.com/scripta/escriptorium](https://gitlab.com/scripta/escriptorium),
`escriptorium/LICENSE`).

eScriptorium remains a foundational contribution to open-source HTR. The
editor, data model (`Transcription` / `Line` / `LineTranscription`), documents,
and kraken training/inference loop derive directly from that design. Escript AI
is an independent project: it is not an official eScriptorium release and is
not endorsed by the eScriptorium authors. We keep their licence and copyright
notices while developing a separate AI-assisted transcription path.

We thank Robin Tissot, PSL/Scripta, the eScriptorium contributor community,
and the kraken authors for the editor and segmentation on which this work
stands.

## kraken

Line segmentation (blla) and optional recognition/training are kraken
(Apache License 2.0; Benjamin Kiessling and contributors). See `kraken/LICENSE`.

Escript AI’s design is **kraken segments, AI transcribes**. Without kraken
baselines and masks, colour-keyed assignment has nothing to attach to.

## AI providers (optional)

Hosted backends are optional. The project does not bundle API keys and will not
call a paid API unless the operator supplies a key and starts a job.

- Google Gemini — `GeminiBackend`
- Anthropic Claude — `AnthropicBackend`
- OpenAI (Responses API) — `OpenAIBackend`
- Local OpenAI-compatible servers (Ollama, vLLM, …) — `LocalOpenAIBackend`

## Other libraries on the AI path

- Django, Django REST Framework, Celery — request/task plumbing
- Pillow — colour-keyed overlays
- Shapely — polygon overlap in preflight
- requests — hosted HTTP adapters
- Vue / webpack — Transcribe UI (built on eScriptorium’s frontend)

Python package versions live in `escriptorium/app/requirements.txt`.
