# Technical plan and architecture options

**Status:** planning baseline; Tauri/Gemini prototype is in progress, and final architecture decisions remain open.  
**Constraint:** standalone Windows app; no runtime dependency on the parent Django application. Gemini is the sole explicitly authorized, user-confirmed paid provider. No other paid API calls. No Kraken training.

## 1. Architecture goals

- Install and run as a familiar desktop application on Windows.
- Keep project data locally owned, inspectable, exportable, and recoverable.
- Support image display and text editing without requiring a local web server or separate database service unless evidence justifies one.
- Isolate AI providers behind a small interface so engines can change without changing the project format or editor.
- Make an offline/local-AI path possible, while being honest about model size, hardware, licensing, and performance.
- Preserve optional future Mac feasibility without making it a first-release commitment.

## 2. Proposed logical components

1. **Desktop shell:** window lifecycle, menus, file dialogs, system integration, installer/update hooks.
2. **UI:** projects/documents navigation, image viewer, text editor, job state, settings, export preview.
3. **Application service layer:** project operations, import, transcription orchestration, review/version actions, export.
4. **Project store:** versioned local project database and managed asset directory.
5. **Recognition orchestrator:** named attempts, system/model selection, prompt/settings snapshot, execution, cancellation, errors, provenance, and result comparison.
6. **Layout/segmentation service:** stores proposed and user-corrected regions/lines in source-image coordinates, reading order, origin, and review status.
7. **Local inference adapter (optional):** calls an approved local model runtime; model downloads and licences are explicit.
8. **Diagnostics:** redacted operational logs, crash reports only with consent, exportable support bundle.

These are logical boundaries. Whether all are in one process or some use a local helper process is open.

## 3. Desktop framework options

| Option | Advantages | Trade-offs / risks | Planning view |
|---|---|---|---|
| **Tauri 2 + TypeScript UI + Rust core** | Small native shell; explicit commands and permissions; potential Mac path; good fit for local-first app | Rust learning/maintenance; native plugin capability and Windows packaging need validation; web UI still needs careful accessibility | Leading option if a small installer and future Mac option matter |
| **Electron + TypeScript** | Mature ecosystem; broad UI component and packaging choices; easier web-team hiring | Larger memory/disk footprint; Chromium and Node runtime; security boundaries require discipline | Strong option if UI speed and ecosystem outweigh footprint |
| **.NET desktop (WinUI 3 / WPF)** | Native Windows integration; mature Windows packaging and accessibility support | Windows-only architecture may make later Mac a rewrite; UI ecosystem and web-like interaction differ | Strong option if Windows quality is the clear priority and Mac remains distant |
| **Python + Qt (PySide)** | Fast local processing integration; familiar for AI tooling; can package with PyInstaller | Large/fragile bundles; native UI polish and update strategy; packaging/security complexity | Consider if substantial Python-only local AI dependencies are required |

**Interim implementation direction (2026-11-08):** Tauri 2 with a small JavaScript UI is being used for the first vertical slice. Rust and Node are available in the current development environment, .NET is not installed, and a later Mac target remains possible. This is reversible and is not a final architecture decision. Compare the working Windows build and packaging experience with .NET before committing the long-term platform strategy.

## 4. Storage options

### Recommended starting direction (proposed)

- A project is a user-visible folder/package with a versioned SQLite database and managed assets.
- Store imported originals unchanged by default; keep derived previews/cache separate and rebuildable.
- Use SQLite transactions for metadata and text revisions; write imported files through temp files and atomic rename.
- Maintain schema version and forward migration path. Backup copies should be consistent and include the database plus assets.
- Project format should be documented and allow a portable backup, with no hidden dependency on machine-specific paths.

### Alternatives to evaluate

- One SQLite file with assets in adjacent folders: transparent, easy backup, but user may accidentally separate assets from the DB.
- One packaged project directory: clearer unit of portability, but requires careful file lifecycle and migration handling.
- Embedded database plus content-addressed asset store: deduplication/integrity benefits, more implementation complexity.

### Data entities (proposed)

- Project: id, title, created/updated time, settings, schema version.
- Document: id, title, order, archival metadata, optional notes.
- PageAsset: original path or managed asset id, hash, media type, dimensions, orientation, import metadata.
- Region: page association, polygon, type, reading order, origin, review status.
- Line: region/page association, polygon and optional baseline, reading order, text, review status, source operation.
- TextRevision: immutable or append-only revision, text, author type, timestamp, source operation.
- AIJob: operation, provider/model, state, timestamps, safe error code, provenance and usage metadata.
- ReviewState: draft/reviewed/approved states, reviewer and timestamp.
- ReusableExample (optional): explicitly selected source/correction pair with scope and deletion controls.

Use the explicit page → region → line model for this release. Preserve a stable line ID through text edits and geometry corrections so comparison, chat suggestions, history, and export continue to refer to the same line.

## 5. Recognition attempts and guidance (proposed)

Each attempt should be an immutable record of input scope, system/model, task, prompt preset and user instruction, relevant project context, output, timestamps, and status. Attempts are distinct from editable working text. A user can select an attempt as a draft, but the system does not overwrite earlier output or silently combine results.

Release capabilities:

1. Safe reruns with immutable per-attempt provenance.
2. Multiple selectable Gemini models, separately recorded and comparable per line.
3. Prompt presets and task-scoped instructions.
4. Page-scoped conversational assistant whose suggestions require explicit adoption.

All four capabilities are in this release. The provider boundary remains replaceable, but only Gemini is currently authorized as a paid service. A comparison selection must be explicit and its request count disclosed before confirmation.

## 6. AI provider interface (proposed)

An internal interface should define:

- `capabilities`: image transcription, text assistance, context support, streaming/progress, cancellation, offline/remote.
- `prepare`: show an auditable summary of data to be sent and provider/model identity.
- `run`: execute one operation and return result plus structured metadata.
- `cancel`: best-effort cancellation and clear status.
- `errors`: distinguish authentication, policy refusal, timeout, unavailable model, rate limit, invalid response, and user cancellation.

Rules:

- Never silently switch providers or fall back to a paid service.
- Never send a project, page, or correction set without a user-triggered operation and an applicable policy decision.
- Keep raw page content and recognized text out of normal diagnostic logs.
- Record provenance in the project, not only in a transient log.
- Provider-specific features must not leak into core project state.
- Gemini is the sole paid-provider exception for this subproject, with a billed-request confirmation every time, no retry or fallback, and no live agent call without a shown estimate and Matthew's approval. Any other provider must be local or separately authorized as non-billable.

## 7. Layout and segmentation boundary

Segmentation is an explicit, required capability with its own output, review state, and provenance. Use source-image pixel coordinates with normalized-coordinate conversion at the UI boundary. Keep layout proposal separate from user-confirmed geometry. Each line is linked to editable geometry and independently editable text; page-level reading order derives from ordered lines. Validate bounds and stable IDs on import, save, and export. Do not make undocumented model coordinate guesses look authoritative.

## 8. Local AI options

Local AI could mean bundled model/runtime, user-installed runtime, or an external local service. Each has different support, footprint, licence, and update implications. Before choosing:

- define minimum CPU/RAM/GPU and expected model size;
- confirm Windows GPU support, driver requirements, and CPU fallback;
- confirm model and runtime licences for distribution;
- decide whether models are bundled, downloaded by the user, or detected locally;
- test clean install, offline use, model integrity, and uninstall/retention behavior;
- measure on representative archival material with human-reviewed ground truth.

No model or runtime is selected by this plan.

## 9. Security boundaries

- Store API credentials using Windows Credential Manager or an equivalent OS-backed secret store; never put secrets in the project file or plaintext config.
- Use least privilege in the desktop shell and restrict webview/native commands to explicit operations.
- Validate paths and imported files; avoid arbitrary shell execution and unsafe archive extraction.
- Avoid remote content in embedded webviews unless required; prevent navigation to untrusted origins.
- Protect project data at rest according to the selected threat model; encryption-at-rest requirements remain open.
- Provide a clear delete path for projects, caches, credentials, and reusable examples.
- Redact logs by default and gate diagnostic bundle export behind user review.

## 10. Update and support model

Open decisions: signed installer format, update channel, whether updates are automatic or user-approved, code-signing identity, crash reporting, support bundle contents, and long-term support policy. The app should be operable without an account unless a chosen feature proves otherwise.

## 11. Technical validation spikes (after scope approval)

1. Framework spike: create/open project, render a large page image, edit/save text, reopen, package installer.
2. Storage spike: atomic project backup/restore, schema migration, interrupted import recovery.
3. AI spike: one permitted recognition route, privacy preview, repeat attempt, settings snapshot, progress/error states, provenance persistence.
4. Performance spike: representative project size on the agreed minimum Windows device.
5. Usability spike: archivist completes import → transcribe → correct → export without shell commands.

## 12. Architecture decisions still required

See `DECISIONS.md`. Release scope for model comparison, chat, segmentation, and line-level editing is approved. Project storage, geometry proposal mechanism, supported image formats, Windows target, and update/signing choices still need implementation decisions.
