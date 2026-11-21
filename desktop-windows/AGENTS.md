# Escript AI Desktop for Windows — agent instructions

This directory contains the planning and eventual implementation for the independent Windows desktop app. It is a separate subproject: do not assume it depends on, imports from, or deploys the Django/eScriptorium application in the parent repository.

## Current phase

- The first functional implementation is underway after Matthew authorized coding on 2026-11-08.
- The frontend now includes Gemini reading/comparison/chat, visible segmentation and line editing, local text history, exports, and folder-backed startup recovery with Tauri persisted filesystem scope. It remains a prototype: session edits pass through IndexedDB before mirroring, dual-store recovery is unverified, advanced geometry cases remain, and no native Windows build/installer has been produced.
- Keep product decisions that have not been explicitly approved marked as open. Tauri is the interim implementation direction; do not represent it as a final architecture decision.
- The UI must visibly resemble eScript AI from the first implementation slice. Use the product's actual wordmark, colors, typography, navigation, and recognizable editor patterns. This is a confirmed requirement, not future polish. Desktop adaptation is allowed; generic unrelated visual styling is not.
- Windows is the first desktop target. Do not create a Mac subproject in this directory; keep future cross-platform considerations as notes only.
- Preserve the parent project and its existing dirty working tree. Do not stage, commit, push, deploy, or edit parent project files as part of this subproject unless separately asked.
- Paid API exception (Matthew, 2026-11-08): the Google Gemini API, using Matthew's paid key, is wired in as the app's recognition provider. It must stay user-triggered, with a per-request confirmation naming the model and billing, no automatic retry, and no fallback to another provider. The key lives only in the OS credential store (or `GEMINI_API_KEY` for the dev preview), never in page code, browser storage, logs or project files. Agents must not make live Gemini calls themselves without first showing Matthew the estimated cost and getting his go-ahead. All other paid APIs and billable cloud usage remain prohibited.
- Do not include Kraken or neural model training in this app's initial scope. Keep transcription, human correction, prompt/context configuration, and any future learning mechanism clearly distinguished.
- Treat archival images, transcriptions, credentials, and correction examples as sensitive user data. Privacy, retention, export, and deletion requirements must be decided before implementation.
- Confirmed release scope: Gemini model comparison, page-scoped chat, visible segmentation, and line-level editing. Follow-on release requirement: localize the complete user interface into the twelve languages listed in `LOCALIZATION_PLAN.md`, including RTL Arabic and Urdu, after core Windows work. Do not defer these or ask Matthew to reconfirm them. Matthew already accepted the Gemini read workflow; do not make a live paid call as a routine release check.

## Source of truth

- `README.md` explains the subproject and links to its planning documents.
- `PRODUCT_SPEC.md` is the user and workflow requirement baseline.
- `TECHNICAL_PLAN.md` records architecture options and recommendations; unresolved choices stay open until Matthew decides.
- `DECISIONS.md` is the decision register. Never convert an open question into a settled requirement without evidence or user direction.
- `ROADMAP.md` gates implementation on approved decisions and acceptance criteria.
- `IMPLEMENTATION_STATUS.md` records what the current shell can and cannot do.

## Documentation practices

- Keep requirements labelled as confirmed, proposed, or open.
- Record the reason and consequences of any decision.
- Do not claim compatibility with eScript AI just because the interface resembles it. Define interoperability explicitly.
- Treat visual resemblance as a product requirement distinct from technical interoperability. Compare the prototype against the parent product UI before calling the design acceptable.
- Do not claim recognition quality without reviewed ground truth and a documented evaluation method.
- Do not treat AI output as verified archival transcription. Human review and provenance must be visible.
