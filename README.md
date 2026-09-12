# Escript AI

**Escript AI** is an independent open-source tool for turning scans of
handwritten and printed documents into editable text. It finds the lines on a
page, sends them to an AI model that can read images, and gives you a draft
transcription to correct in the editor. Because the AI reads the image itself,
Escript AI can work across the world’s major writing systems. The interface is
available in English, Arabic, Hindi, Polish, Italian, Spanish, and Portuguese.

It is **derived from [eScriptorium](https://gitlab.com/scripta/escriptorium)**
(MIT), an established open-source platform for handwritten text recognition and
scholarly transcription. It is **not** an official eScriptorium release and is
**not endorsed** by the eScriptorium authors. Escript AI has its own roadmap
while preserving the upstream licence and copyright notices.

## What it does

Kraken draws line masks. Escript AI paints each line a colour, sends a small
region image to an AI model that can read images, and writes the replies onto a
named transcription layer — with `version_source` set to the provider (the
stock eScriptorium API cannot do that). You correct in the usual editor.

## What works / what does not

**Works (demo-quality, still draft until a human reviews it)**

- Prose pages with sane, non-overlapping line masks (English letter; modern
  school-hand Hindi).
- Gemini 2.5 Flash, Claude Sonnet 5, OpenAI GPT-5.6 Terra, and local
  OpenAI-compatible backends (Ollama/vLLM).
- Overlap/fragment preflight so junk masks do not skip a whole crop.
- Dispatch guards: never-send-off-site, missing key, cross-document part pks.

**AI output is a draft layer until a human reviews it. Do not train a kraken
model on an unreviewed AI layer.**

## Paid APIs are never called silently

Hosted Gemini / Claude / OpenAI run **only** when you start an `ai_transcribe`
job **and** a key is present in the environment. No key → the job fails closed.
Unknown providers do not fall back to Gemini. Local backends spend no API money.

Copy `.env.example` to `.env` and fill only the providers you use:

```
GEMINI_API_KEY=
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
```

In Django admin, each `AIBackendConfig.key_ref` should match the env var name
(e.g. `GEMINI_API_KEY`). Keys are **not** stored on the config row.

## Setup

You need a running eScriptorium-compatible stack (Docker Compose is the usual
path — see `escriptorium/README.md`) with this tree’s `ai/` app installed:

1. `INSTALLED_APPS` includes `'ai'`.
2. `python manage.py migrate` (includes `ai.0001_initial`).
3. Export keys from `.env` into the **web and Celery** processes.
4. Create an `AIBackendConfig` (admin) for Gemini, Claude, OpenAI, and/or local.
5. Segment pages with kraken, then **Transcribe** → an AI backend.

## Sample / spike code

- `phase0-spike/` — **synthetic** colour-key demo (`spike.py`) plus a **masks-only** ALTO fixture (`_hard_seg.xml`). Generated PNGs and live-run `_results.json` are gitignored. Unpublished page images are **not** in this repository.
- `escript-ai/` — optional write-path script against a **throwaway** eScriptorium instance. Set `ESCRIPT_AI_DEMO_IMAGE` to a JPEG you are allowed to upload. Credentials live in `~/.config/escript-ai/`, not in git.

## Tests

No Django, no network, no API spend:

```bash
cd escriptorium/app/apps
python3 -m unittest ai.tests -v
```

## Docs

- [ARCHITECTURE.md](ARCHITECTURE.md) — design (colour-keyed JSON, dispatch, quality gate)
- [STATUS.md](STATUS.md) — current capability and roadmap
- [ACKNOWLEDGEMENTS.md](ACKNOWLEDGEMENTS.md) — upstream and dependencies
- [CONTRIBUTING.md](CONTRIBUTING.md) — tests, adapters, guards
- [LICENSE](LICENSE) — MIT for Escript AI; upstream licences unchanged

## Acknowledgement — eScriptorium

Escript AI is built from the **eScriptorium** web application originally
created by Robin Tissot, PSL, and contributors (Copyright © 2018 Robin Tissot,
PSL; licensed under the MIT License — see
[gitlab.com/scripta/escriptorium](https://gitlab.com/scripta/escriptorium)
and `escriptorium/LICENSE`).

eScriptorium remains a foundational contribution to open-source handwritten
text recognition. Escript AI’s editor, documents, layers, and kraken
training/inference loop derive directly from that design. Escript AI is an
**independent** project: it is **not** an official eScriptorium release and is
**not endorsed** by the eScriptorium authors. We keep their licence and
copyright notices while developing a separate AI-assisted transcription path.

We thank Robin Tissot, PSL/Scripta, the eScriptorium contributor community,
and the kraken authors for the editor and segmentation on which this work
stands.

A fuller list of upstream and library credits is in
[ACKNOWLEDGEMENTS.md](ACKNOWLEDGEMENTS.md).

## Licence

Escript AI original code is MIT (see `LICENSE`). eScriptorium remains MIT
(`escriptorium/LICENSE`, Copyright (C) 2018 Robin Tissot, PSL). kraken remains
Apache 2.0 (`kraken/LICENSE`).

Maintained by [Boyne Archives](https://boynearchives.ie) and [Archiveshosting.com](https://archiveshosting.com).
