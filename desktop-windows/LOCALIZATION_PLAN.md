# Desktop localization plan

**Status:** implementation in progress and catalog coverage complete (2026-11-09). The app now offers all twelve languages, persists the setting, localizes bundled desktop/shared labels, formats dates and numbers by locale, sets RTL for Arabic/Urdu, and keeps archival text independent. Fluent review and per-locale workflow acceptance remain open release gates.

## Source language set

The main app's default interface catalog is defined in `escriptorium/app/escriptorium/settings.py` (`ESC_LANGUAGE_CATALOG` / `ESC_LANGUAGES`) and its Vue message bundle is `escriptorium/front/vue/i18n/messages.js`. The desktop must offer all twelve catalog languages, regardless of a server operator's optional `ESC_LANGUAGES` subset:

| Code | Language | Direction |
|---|---|---|
| en | English | LTR |
| ar | Arabic | RTL |
| hi | Hindi | LTR |
| pl | Polish | LTR |
| it | Italian | LTR |
| es | Spanish | LTR |
| pt | Portuguese | LTR |
| fr | French | LTR |
| de | German | LTR |
| ur | Urdu | RTL |
| tr | Turkish | LTR |
| te | Telugu | LTR |

## Product behavior

- Add a language selector in Settings, with each language named in its own script and a stable code for configuration/support.
- Persist the selected UI language per user on the device and apply it on launch. Switching language updates the interface without changing project data.
- Localize all user-facing UI: navigation, dialogs, buttons, instructions, validation and recovery errors, AI billing/data-transfer confirmations, settings, export/import flows, and help/release content shipped in the app.
- Keep project archival content, transcriptions, page images, model outputs, and exported text untouched by a UI language switch. Do not auto-translate source text.
- Use `Intl` locale-aware date, time, number, and file-size formatting. Keep Unicode normalization and stored project text unchanged.
- Set UI language and direction correctly. Arabic and Urdu use RTL UI layout; use CSS logical properties and verify mirrored navigation/dialog behavior. Keep text-entry direction independent and appropriate to the source script, including mixed-script lines.
- Bundle all locale resources in the installer. Runtime localization must work offline and must not fetch translation services or fonts.
- Port/adapt the main app's existing translations as a starting point, keeping the desktop independent of the parent Vue/Django code. Have a fluent reviewer review terminology and completeness for each language; do not treat a machine-translated catalog as accepted copy.

## Implementation approach

1. ~~Establish locale catalogs and parity checks for bundled desktop/shared labels.~~ Implemented in `app/src/i18n.js`; the audit reports no missing catalog entries.
2. ~~Add saved language preference, locale formatting, RTL behavior, and source-text direction isolation.~~ Implemented.
3. Fluent review of terminology, completeness, and RTL behavior remains.
4. Exercise representative workflows in every locale with AI mocked; no paid calls for localization QA.
5. Keep language choice separate from recognition language/script guidance. Changing UI language must not alter page images, transcription, or stored text.

## Acceptance gate

- All twelve languages appear in the selector and persist across restart.
- Every user-facing screen has complete, reviewed strings; no untranslated-key fallback remains in a release build.
- Arabic and Urdu screens work with correct direction, navigation, dialogs, editable text, selection, and exports.
- Switching languages does not change any project data, source text, line order, AI attempt, or export content.
- Dates, counts, and file sizes format appropriately. The app remains fully usable offline apart from explicitly requested Gemini operations.
- Run a representative workflow in every locale: create/open project, import page, segmentation and line editing, Gemini confirmation disclosure, chat/comparison UI, and export/reopen. Use mocks for AI verification and make no paid calls for localization QA.
