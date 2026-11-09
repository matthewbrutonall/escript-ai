# Implementation status

Updated: 2026-11-08

## Implemented in the Windows subproject

The app is a standalone Tauri 2 + Vite desktop project in `app/`. It does not depend on the parent Django/eScriptorium runtime.

- eScript AI identity, forest-green/parchment/rust palette, and desktop project/editor layout.
- Project creation/switching, PNG/JPEG/WebP import, source-image viewer with fit/zoom, editable transcript, text history, TXT export, and project-wide TXT export.
- Gemini page reading, explicit billing confirmation, saved attempt/provenance/usage, safe adoption into working text, and user-selected comparison of up to four Gemini model IDs. If lines exist, comparisons request line-addressed drafts and let the user adopt them individually; otherwise each model result remains a separate page-level draft.
- Page-scoped Gemini chat with per-turn transfer/billing disclosure. Chat proposals are scoped to existing line IDs and require explicit adoption.
- Visible regions and ordered lines. Users can draw, select, move, resize, delete, review, reorder, and edit line text. Gemini can propose region/line geometry and text; proposals are stored as drafts. PAGE XML and ordered text export are available.
- IndexedDB v3 session/cache for projects, images, attempts, line/region geometry, line text, chat, and text revisions. The native desktop saves each project to a selected folder with managed `assets/` images and an atomic versioned JSON manifest with a SHA-256 envelope and per-image checksums. Tauri’s persisted-scope plugin retains file access grants across launches; startup loads the most recent folder manifest and refreshes the local cache while preserving record IDs. Opening a folder replaces a matching local cache in one IndexedDB transaction.
- `.escriptai` backup/restore v1 and v2, including layout and chat in v2. Gemini credentials are excluded from both project formats.
- Gemini key storage through the native keyring path; browser preview uses only the local development middleware.

- UI localization is implemented in `app/src/i18n.js`: a persistent selector lists all twelve main-app languages, desktop and shared labels have locale catalogs, dates and numbers use the selected locale, Arabic/Urdu set RTL layout direction, and archival/transcription fields retain automatic source-text direction. The catalog coverage audit reports all bundled desktop and shared labels translated in all twelve locales. Fluent review and full per-locale workflow acceptance remain release gates. See `LOCALIZATION_PLAN.md`.

## Verification and limits

- `npm run build` passes after the current implementation changes (Vite 7.3.7). No live Gemini request was made during this work.
- Localization catalog parity audit and frontend build pass. Language-by-language visual and fluent acceptance has not been performed; do not describe the translations as professionally reviewed.
- No automated suite or interactive acceptance run has been performed for the new release-scope workflows. The frontend build does not validate the native Tauri shell.
- Cargo metadata and lockfile validation pass. Native Rust compilation was blocked on this Linux host by missing WebKitGTK development libraries. A Windows compile, installer, clean-install/upgrade/uninstall, and signed release have not been produced. A GitHub Actions workflow now builds and uploads an unsigned NSIS artifact on manual dispatch or pull requests touching the desktop subproject; it has not run yet.
- The folder manifest is the restart recovery source; writes still pass through IndexedDB during a session and are then mirrored. This dual-store design needs failure-injection and Windows acceptance before it is trusted as archival storage. Manifest and image checksums detect corrupted project data during restore. If folder access or recovery fails at startup, the app opens the local cache and displays the recovery error. The Tauri persisted-scope integration is present in Cargo, but the native Rust shell could not be compiled here.
- Geometry supports rectangular draw/move/resize/delete and manual line reordering; polygons and baseline editing remain open. PAGE XML currently exports rectangular regions/lines.
- Model comparison is Gemini-only. Page-level comparisons are whole-page outputs; line-addressed comparison is available when the page already has segmented lines and the model returns valid structured JSON. No extra providers or hidden retries are enabled.
- PDF/TIFF, undo/redo, offline AI, auto-update, code signing, and Mac packaging are not included in the current implementation.

## Next release gates

1. Complete user-facing recovery/error states and refine geometry editing against representative pages.
2. Run non-billable/mock and interactive UI acceptance for project lifecycle, segmentation, line editing, comparison, chat, history, export, and reopen/restore. Never use a paid live call for routine verification.
3. Build and exercise the native app on Windows; verify persisted folder access, restore behavior, keyring, clean install, upgrade, and uninstall.
4. Complete privacy/dependency/security review, support/first-run help, versioning, installer signing decision, and release notes.
