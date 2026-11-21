# Windows release roadmap

**Release scope:** local archival project workflow with segmentation, line-level transcription and correction, Gemini model comparison, and page-scoped AI chat. These four capabilities are confirmed for this release by Matthew (2026-11-08); they are not deferred. Full UI localization in the main app’s twelve default languages is a follow-on release requirement after the current core Windows work. Matthew has accepted that the Gemini image-reading path works. No repeat live read is a release gate.

The Tauri/Vite frontend now contains the confirmed core workflows: Gemini reading/comparison/chat, visible region and line editing, text history, exports, and folder-backed startup recovery. The frontend builds. Edits still pass through IndexedDB during a session before being saved to the folder manifest; persisted filesystem scope restores access across launches. Dual-store failure handling, native Windows compilation, advanced geometry cases, and interactive acceptance remain open. Track completion by the acceptance gates below, not by a successful frontend build alone.

## Stage 1 — Freeze the release contract and project model

- Keep the existing standalone Windows Tauri shell for this release unless a Windows packaging spike proves it unusable.
- Define the project hierarchy as project → document → page → regions → ordered lines.
- Define line identity, reading order, geometry coordinates, text revisions, review state, and provenance in a versioned schema.
- Define segmentation as visible and user-correctable. A line's geometry and text remain linked; unreviewed AI proposals remain drafts.
- Define chat as page-scoped. It may answer questions or propose edits, but cannot silently change line text.
- Define comparison as selectable Gemini models. Each selected model gets a distinct attempt and explicit billing confirmation; there is no invisible fan-out, retry, or other paid provider.
- Preserve the accepted current Gemini read flow and its per-request confirmation.

**Exit:** schema and interaction rules are written into `PRODUCT_SPEC.md`, `TECHNICAL_PLAN.md`, `UX_SPEC.md`, and `DECISIONS.md`; no scope question remains about the four included capabilities.

## Stage 2 — Durable local projects and safe migration

- Harden the current user-visible project folder and versioned manifest as the restart recovery source; resolve dual-store failure handling and final canonical-store decision.
- Add create/open/recent-project, safe import, autosave, close/reopen, and project backup/restore.
- Migrate any browser-prototype project only through an explicit export/import path; never silently discard it.
- Use transactional metadata writes, atomic asset writes, integrity checks, and recovery messages.

**Exit:** a clean Windows install can create a project, import a multi-page document, close/reopen it, and recover from a simulated interrupted write without losing originals or text.

## Stage 3 — Segmentation and line editor

- Represent pages, regions, lines/baselines, reading order, and geometry in source-image coordinates.
- Complete the editable image overlay with resize handles, pan, reorder controls, and robust selection from both image and text list. Current source supports draw, move, resize, delete, zoom, line selection, and manual line reordering.
- Allow segmentation proposals to be generated through the authorized model route or created manually; label their origin and keep them editable.
- Store line text, per-line status, provenance, and append-only revisions. Assemble page text from line order without losing per-line identity.
- Include empty, dense, columnar, marginal, and rotated-page behaviour in the UI design.

**Exit:** an archivist can segment a page, correct its geometry/order, select a line, edit its transcription, and reopen the page with layout/text association intact.

## Stage 4 — Model comparison, chat, and orchestration

- Harden the current Gemini-only request orchestration and attempt records; comparison across up to four user-selected Gemini model IDs is implemented in the frontend.
- Let the user select comparison models, show the exact number of requests and what image/text each receives, confirm before sending, and retain separate outputs and usage metadata.
- Harden the current page-scoped chat and explicit proposal-adoption flow. Every remote message requires a user action and paid-request confirmation.
- Complete interruption/recovery reporting where technically available. Running, completed, and failed states are recorded; never auto-retry or fall back.
- No additional billable provider may be connected without Matthew's separate explicit authorization.

**Exit:** mocked contract and UI flows prove cancellation sends nothing, each confirmed request maps to one saved attempt, results stay separate, and chat cannot silently mutate the transcription. Matthew's prior AI-read acceptance remains valid; do not spend a live request for this gate.

## Stage 5 — Export, provenance, and usability completion

- Complete page and project text export in reading order, preserving Unicode and line breaks.
- Add a layout-preserving export for regions/lines and define included metadata/provenance; document supported interchange format(s).
- Keep current selected-page and all-text TXT export, and make destination, skipped pages, and output scope clear.
- Finish undo/redo, review state, keyboard operation, resizable panels, image zoom/pan, and clear save/job status.
- Complete side-by-side visual review against eScript AI and adjust while preserving the confirmed identity.

**Exit:** export round-trips line order and geometry as specified; core workflow is usable by keyboard; Matthew accepts visual and workflow fit.

## Stage 6 — Windows security, build, and packaging

- Build and run the Tauri app on a supported Windows machine; settle target Windows versions and x64 target for the first release unless the actual target requires otherwise.
- Review shell permissions, file handling, archive/path validation, dependency licenses, keyring behaviour, CSP, logs, and update/uninstall data retention.
- Produce the chosen NSIS installer, test clean install/upgrade/reinstall/uninstall, and determine code signing. Do not describe an unsigned installer as a frictionless public release.
- Document Gemini setup, billing, data transfer, key removal, and project backup in first-run and help materials.

**Exit:** exact release build installs and launches on a clean Windows machine; project files remain user-controlled; no credential or archival text leaks into logs or project package by default.

## Stage 7 — Core Windows verification and handoff to localization

- Run unit, storage, provider-mock, UI, security, and packaging checks; no paid live calls are used for release verification.
- Exercise the core workflow across segmentation → line recognition → comparison → chat proposal → human correction/review → export → folder restore.
- Record recognition-quality evidence only when it uses human-reviewed ground truth. Matthew has accepted the current image-reading route; that does not establish feature completion or accuracy.
- Close critical data-loss, billing/privacy, and native Windows build issues before beginning the localization pass.

**Exit:** core English Windows workflow and installer candidate pass acceptance; source remains ready for locale extraction.

## Stage 8 — Localize the UI in all main-app languages

- Deliver English, Arabic, Hindi, Polish, Italian, Spanish, Portuguese, French, German, Urdu, Turkish, and Telugu. The catalog source is recorded in `LOCALIZATION_PLAN.md`.
- Extract all visible UI strings into bundled locale catalogs and port/adapt the main app's translations without importing its Vue/Django runtime.
- Add a user-selectable, persisted language setting. Localize settings, help, error/recovery messages, export/import flow, Gemini billing/data-transfer confirmations, comparison, chat, and segmentation controls.
- Implement RTL interface layout for Arabic and Urdu. Keep text-entry direction and archival source text independent from UI direction; preserve Unicode and never auto-translate project data.
- Review every locale with fluent speakers, test offline bundle availability, and exercise the representative workflow in all twelve languages using mocked AI responses.

**Exit:** each locale is complete and reviewed, language choice survives restart, RTL workflows pass, and switching UI language leaves project content unchanged.

## Stage 9 — Final release verification and handoff

- Run final regression, storage recovery, privacy/security, accessibility, and installer checks after localization.
- Exercise representative workflows in all twelve languages; test clean install/upgrade/uninstall and folder restoration on supported Windows versions.
- Publish the versioned, localized installer, release notes, limitations, recovery instructions, billing/data-transfer guide, and support procedure.

**Exit:** no open critical release defect; Matthew accepts the localized Windows release.

## Schedule guidance

The requested scope is larger than a few-day polish pass. A private alpha may be assembled quickly only if the project schema, storage, and packaging work are unusually smooth; line-level geometry, comparison, and chat are substantial features. Prioritize the stages in order and report actual gates passed. Do not remove a confirmed feature to make a date appear achievable.

## Later, outside this Windows release

- Mac application/subdirectory, cloud sync, multi-user collaboration, Kraken/model training, automatic learning from corrections, and other paid providers.
