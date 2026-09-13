# Escript AI

**Escript AI** helps archives turn scanned pages into editable, reviewable
text.

Upload page images, let the system make a first draft, correct the text in the
editor, and export it when it is ready. The original image stays beside the
transcription, so the work remains checkable. It is built for historical
documents, not quick one-off chat prompts.

The same workflow is designed for handwritten and printed material across the
world’s major scripts: Latin-script languages, Arabic, Hindi and other Indic
scripts, Urdu, and more. The software interface is available in English,
Arabic, Hindi, Polish, Italian, Spanish, Portuguese, French, German, Urdu,
Turkish, and Telugu.

Escript AI is independent open-source software. It is **derived from
[eScriptorium](https://gitlab.com/scripta/escriptorium)** (MIT), an established
open-source platform used for handwritten text recognition, transcription
editing, and model training. Escript AI is **not** an official eScriptorium
release and is **not endorsed** by the eScriptorium authors.

## What it does

For an archivist, the basic loop is:

1. Add scans or photographs of pages.
2. Ask Escript AI to make a draft transcription.
3. Review and correct the text beside the image.
4. Export the finished text and page data.

That is the main product. Building a custom handwriting model is possible, but
it is a later step after you have corrected enough pages. Most users should
start with “image in, draft text out, human review”.

## Why not just paste images into ChatGPT?

You can paste a page into a chat tool, but archival transcription usually needs
more than an answer in a chat window.

Escript AI is designed to keep the work inside an archival workflow:

- It keeps the page image and transcription together.
- It writes text line by line into an editable layer.
- It lets you correct, compare, and export the work.
- It can process many pages as a job, not just one image at a time.
- It records which AI backend made the draft.
- It can stop remote AI from being used on restricted documents.
- It can later turn corrected pages into training data for your own models.

The AI draft is not treated as finished text. It is a starting point for human
review.

## How it works, in plain terms

Escript AI first needs to know where the lines of writing are on the page. It
uses the existing eScriptorium/kraken page-analysis tools for that. Then it
sends small page areas to a reading model, gets text back, and writes that text
into a new transcription layer.

If the page layout is normal prose, this can work well. If the page is a
ledger, table, account book, or full of ditto marks and columns, the software
needs to be more careful. Those pages are not “bad”; they are just a different
layout problem.

Under the hood, this project uses colour-keyed line crops, provider adapters,
dispatch guards, review gates, and optional training paths. You do not need to
understand those details to use the tool, but they are documented for
developers and technical archivists.

## What works / what does not

**Works now**

- Draft transcription into an editable layer.
- Review and correction in the document editor.
- Hosted AI backends: Gemini, Claude, OpenAI, Azure OpenAI, and Mistral.
- Local OpenAI-compatible backends such as Ollama or vLLM.
- Per-user encrypted API keys and monthly budget caps.
- Document policy to block off-site AI for restricted material.
- Review gates before AI-created layers can be used for training.
- Page-analysis review suggestions for suspicious line boxes.

**Still needs judgement**

- AI output is a draft until a human reviews it.
- Tables, ledgers, and complex account books need special handling.
- A few interface strings are still in English.
- Right-to-left language support exists, but still needs polish.
- Training your own model is a power path, not the first thing to do.

## Paid APIs are never called silently

Hosted AI providers run **only** when you start an AI transcription job and a
usable key is available. No key means the job fails closed. Unknown providers
do not fall back to another paid service. Local backends spend no API money.

Copy `.env.example` to `.env` and fill only the providers you use:

```
GEMINI_API_KEY=
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
```

Provider keys can be supplied by environment variable or stored per user in
encrypted form. They are not stored in clear text on backend configuration
rows.

## Running it

Escript AI is a Docker Compose stack. Build **this** repository; do not overlay
the official eScriptorium image, because this project has additional Django
apps, frontend code, migrations, and dependencies.

```bash
cd escriptorium
cp variables.env_example variables.env   # edit secrets
docker compose build
docker compose up -d
```

The image is `escript-ai:local` from `escriptorium/Dockerfile`. It builds the
Vue frontend, installs the Python application, runs migrations, and collects
static files.

Then:

1. Configure secrets in `variables.env` or your deployment environment.
2. Create one or more AI backend configurations in Django admin.
3. Add page images to a document.
4. Run page analysis so the system knows where the writing is.
5. Choose **Transcribe** and select an AI backend.

Developers can read [ARCHITECTURE.md](ARCHITECTURE.md) for the internal design.

## Sample / spike code

- `phase0-spike/` — **synthetic** colour-key demo (`spike.py`) plus a **masks-only** ALTO fixture (`_hard_seg.xml`). Generated PNGs and live-run `_results.json` are gitignored. Unpublished page images are **not** in this repository.
- `escript-ai/` — optional write-path script for a local demo instance. Set `ESCRIPT_AI_DEMO_IMAGE` to a JPEG you are allowed to upload. Credentials live outside git.

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
