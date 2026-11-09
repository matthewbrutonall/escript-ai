# UX specification

**Status:** Visual resemblance, model comparison, chat, segmentation, and line-level editing are confirmed requirements for the Windows release; detailed interaction rules below are the implementation baseline.

## 1. Experience target

The app must be recognisably eScript AI at first glance, not merely a generic transcription tool with the same name. Carry across the eScript AI wordmark, forest-green and parchment palette, rust primary action, rounded button language, sans-serif interface typography, navigation patterns, and familiar editor hierarchy. Adapt proportions and controls for a resizable Windows desktop window, while keeping implementation and runtime independent. Similarity is a required acceptance criterion; workflow parity with every web feature is not.

## 2. Proposed navigation

- **Home / Projects:** recent projects, create/open project, storage location, backup reminder/status.
- **Project workspace:** document list, import, search, project-level AI/context settings.
- **Document workspace:** ordered pages, metadata, text/revision list, export.
- **Page and text review:** image canvas beside editable text; AI actions, history, status, and review state.
- **Settings:** AI providers/models, privacy, storage, updates, accessibility, diagnostics.

The current prototype offers **Save project file**, **Open project file**, and **Export all text** in the project pane. Project text export is a UTF-8 plain-text file with each transcribed page identified by its source filename; selected-page export remains separately available. Saving a project file explains the included images/text/history, size limit, default download location, unencrypted format, and exclusion of the Gemini key. Restoring creates a separate project and keeps the original untouched. The image pane offers fit and zoom controls for close reading.

The core workspace uses project → document → page → region → line. Lines remain associated with editable image geometry and have their own text and review provenance.

The desktop mock must be reviewed side-by-side with the current eScript AI application before calling the visual requirement met. Do not invent a distinct brand palette or replace the product's visual identity with a generic archival aesthetic.

## 3. Main workspace proposal

Desktop layout should support:

- navigation pane for project/document/page selection;
- central image canvas with fit, zoom, pan, segmentation overlays, and page navigation;
- ordered line list and editor with bidirectional selection between text and image geometry;
- action area for segmentation, recognition, selected-model comparison, chat, undo/redo, review, and export;
- job status that persists when the user navigates elsewhere.

Panels should be resizable and usable at a modest laptop screen size. Avoid presenting technical engine details unless they help the user make a meaningful choice.

## 4. State and provenance language

Use plain status labels, with definitions available in the UI:

- **Source**: imported text or image, unchanged.
- **AI draft**: generated text awaiting human review.
- **Edited**: a person changed the draft.
- **Reviewed**: a person checked it against the source.
- **Approved**: optional stronger state if there is a real downstream need; do not add until defined.
- **Failed / cancelled / waiting**: operation state, separate from text review state.

Do not use “trained”, “learned”, “accurate”, or “verified” as a synonym for saved, edited, or accepted.

## 5. Interaction requirements

### AI recognition and model comparison

- State what the AI will do, which page/scope is in scope, and which system/model/approach will be used.
- For any remote operation, show the data-sharing notice before first use and make settings discoverable later.
- Show running status and a useful error. Allow cancellation where supported.
- Save output as a distinct attempt; do not replace another attempt or human version.
- Provide rerun with the same or deliberately changed settings. Do not silently change provider/model or prompt.
- Let users compare selected Gemini models side by side and choose output per line. Never automatically merge conflicting readings.
- Show how many requests a comparison will make; require explicit confirmation before sending them. Never run an unselected model, retry, or fall back.

### Page-scoped chat and guidance

- Presets should be named in user terms and explain their effect.
- A free-text instruction should be optional, scoped to the current run by default, and saved with that attempt.
- Chat is included in this release. Show the page/region/line context sent, model, and billing for every message. Replies and suggested edits remain separate from working text; applying a suggestion requires an explicit selection and action.

### Segmentation and line editing

- Show editable region and line geometry over the source image in documented page coordinates.
- Support selection in both the overlay and ordered text list, visible reading order, and correction of proposed geometry.
- Label machine-proposed layout separately from reviewed geometry. Do not treat unreviewed geometry as authoritative.
- Edit and review text line by line; preserve page-level text assembled from ordered lines and prior versions.

### Editing

- Keyboard-first text editing, undo/redo, save status, and clear focus behavior.
- Preserve earlier versions and identify who/what produced a revision.
- Selecting a line highlights its source geometry; selecting geometry focuses its line text.

### Reusable corrections / context

- If implemented, explain whether an example is saved locally, sent with future prompts, or used for model fine-tuning.
- Require explicit inclusion and provide scope controls (current task, document, project).
- Make review state and removal visible.

### Export

- Show export format, selected text/revision, destination, and whether provenance is included.
- Warn about empty pages or content excluded by the chosen scope.
- Confirm successful write and offer to reveal/open the destination.

## 6. Error and recovery states

Design explicit screens/messages for unsupported/corrupt image; insufficient storage; failed save; unavailable provider; invalid credentials; denied remote transfer; timeout/rate limit; malformed AI response; cancellation; app update failure; schema/project version incompatibility; and incomplete backup/restore.

Errors should tell the user whether their source, edits, and draft were preserved and what action is available.

## 7. Accessibility and localization

The Windows app must offer all twelve UI languages from the main app default catalog: English, Arabic, Hindi, Polish, Italian, Spanish, Portuguese, French, German, Urdu, Turkish, and Telugu. The user chooses and persists the UI language in Settings. Arabic and Urdu use RTL interface direction; transcription direction remains independently controlled so source text is not corrupted by interface direction. Localize all user-facing controls, dialogs, billing/privacy notices, errors, and help. Bundle locale resources for offline use. See `LOCALIZATION_PLAN.md` for coverage and acceptance criteria.

Accessibility baseline: keyboard-only completion of core workflows, accessible names for controls, scalable UI/text, high-contrast support, no color-only status, and screen-reader support for navigation, jobs, and editor state.

## 8. UX questions

See `DECISIONS.md` for remaining implementation choices. Chat, model comparison, segmentation, line-level editing, eScript AI visual resemblance, and full twelve-language localization are confirmed release requirements; localization follows the current core Windows work as described in `ROADMAP.md`.
