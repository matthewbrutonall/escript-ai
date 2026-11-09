# Product specification

**Product:** Escript AI Desktop for Windows (working name)  
**Phase:** Windows release implementation; model comparison, chat, segmentation, and line-level editing are in scope
**Status labels:** Confirmed = directly stated by Matthew; Proposed = a planning recommendation; Open = requires a decision.

## 1. Product statement

Create a standalone Windows application that feels familiar to users of eScript AI while using a simpler and potentially entirely different architecture. It is a desktop shell/workspace for reading image files with AI and letting the user refine the resulting text. It is not a desktop port of the existing Django stack and does not train Kraken or other neural recognition models. “Training the AI” is not the product goal.

## 2. Goals

### Confirmed

1. Build the Windows version first in this isolated subproject directory.
2. Avoid dependencies on the parent web application and its implementation stack.
3. Make the app visibly and recognisably look like eScript AI. This is a confirmed product requirement, not optional polish. Reuse the brand, visual language, familiar navigation and workflow cues while adapting layout to a desktop window. Backend and internal architecture remain independent.
4. Focus on text and AI; exclude KrakenPy/Kraken model training and similar model-training functionality.
5. Complete product and technical planning before implementation.

### Proposed

- Let an archivist create a project, bring in page images, ask an AI system to read them, inspect and refine the resulting text, preserve source and AI provenance, and export useful text.
- Make it easy to see which text came from a source, which was generated, and which was changed or approved by a person.
- Keep user data in a local project by default unless a user explicitly chooses a remote AI provider for a specific operation.
- Deliver a complete page-to-lines workflow with segmentation, line-level review, model comparison, page-scoped chat, correction, provenance, and export. Matthew has already accepted the Gemini image-reading workflow; remaining AI work is feature integration and validation.

## 3. Non-goals for the first release

- Porting Django, Vue, Celery, PostgreSQL, or the current web deployment.
- Hosting a multi-user service or synchronizing projects across machines.
- Kraken segmentation or recognition training, GPU training, checkpoint management, or model qualification.
- Claiming that accepted corrections update a model's weights. Correction reuse is not model training.
- Automatic acceptance of AI text as archival ground truth.
- Mac application delivery. Keep architecture choices portable where reasonable, but do not create a Mac target or promise parity.
- Broad support for every existing external HTR engine unless a specific workflow justifies it.
- Additional billable providers without Matthew's explicit authorization. The first release's model comparison uses Gemini models under the existing authorization.

## 4. Users and jobs

### Primary user (proposed)

An archivist or researcher working alone on a Windows computer with digitised historical material, who wants AI assistance but needs control over text, provenance, privacy, and export.

### Core jobs

1. Start or reopen a local project.
2. Add one or more page images and arrange them into a document.
3. Generate or draw regions and lines, then review and correct their geometry against the page image.
4. Ask Gemini to read the page, region, or selected lines.
5. Compare selected Gemini models side by side; each requested call is disclosed and confirmed before sending.
6. Edit text aligned to lines, preserve versions, and mark human review explicitly.
7. Use page-scoped AI chat for questions and proposed text changes; chat never silently edits working text, and each remote turn is confirmed.
8. Save and reopen the project; export ordered line/page text and supported layout/provenance data.

`DECISIONS.md` records remaining implementation choices. Model comparison, chat, segmentation, and line-level editing are confirmed for this release.

## 5. Product principles

- **eScript AI surface, independent core:** match the recognizable product identity and familiar screen patterns. Adapt the layout for desktop while keeping its visual language. Never make visual resemblance depend on importing or running the web application's code or services.
- **Human review is explicit:** AI output begins as a draft. It must not become “verified” by being saved or exported.
- **Visible provenance:** show whether text is source material, AI-generated, or human-edited, and which operation/model produced it when available.
- **No hidden provider calls:** before data leaves the machine, show what will be sent, to whom, and under which privacy setting.
- **Recoverable work:** autosave or clearly indicate unsaved changes; preserve revision history or a reliable undo path.
- **Simple initial workflow:** prioritize transcribe, correct, save, export over model administration.

## 6. Core workflows (proposed baseline)

### A. First launch and project creation

1. Explain where projects are stored and that project files may contain archival images and text.
2. Let the user create a project folder or open an existing supported project.
3. Ask for a project name and optional collection/context notes.
4. Offer privacy defaults and explain the consequence of remote AI use.
5. Land on the project workspace with a clear next action.

### B. Import and organise material

1. Add images by file picker and drag-and-drop.
2. Detect duplicates and unsupported/corrupt files with actionable messages.
3. Preserve original files by default; create thumbnails/previews separately.
4. Allow ordering, rotation, and basic metadata.
5. Show import progress and recover gracefully from partial import.

Supported formats, limits, OCR preprocessing, and folder monitoring are open decisions.

### C. AI transcription and repeat attempts

1. User selects a page/scope and starts a named recognition attempt.
2. Identify the recognition system and approach. A remote operation requires an explicit transfer and billing notice. Google Gemini is the only paid provider currently authorized for this subproject; no other paid API may be used.
3. Show queued/running/completed/failed state, with cancellation where technically safe.
4. Store every attempt as a separate result. A rerun never overwrites text or erases the earlier attempt.
5. Let the user choose an attempt as the working draft, compare attempts, or copy selected text into the editable version. Comparison between systems is optional scope, not assumed for the first release.
6. Record operation, provider/model identifier, prompt preset/instructions where applicable, timestamp, and available metadata without logging sensitive content unnecessarily.

### D. Guidance and assistant interaction

Users may need to explain script, language, period, abbreviations, layout, transcription conventions, or uncertain passages. Three interaction levels are possible: fixed presets; structured settings plus a free-text instruction; a conversational chat that accumulates context and may request another read. The recommendation is a small preset menu plus a task-scoped optional instruction field. Chat is a later option if users need iterative discussion and the app can keep each change auditable.

Guidance applies to a defined scope, is visible on the attempt, and never silently changes earlier results. Users must understand whether a note applies only to this run or is saved as a project preference.

### E. Review and correction

1. Display source image and text in a comfortable split view.
2. Let the user edit text, undo/redo, save, and compare with previous versions.
3. Mark AI-generated, human-edited, and human-reviewed states distinctly.
4. Preserve attempt history and identify which attempt a correction began from.
5. Reusable examples or correction memory are not in the initial scope unless separately approved; never silently include corrections in a future request.

### F. Segmentation and page structure

Three approaches remain open: page-level transcription with plain text; separate segmentation followed by reading regions/lines; or an AI pipeline that finds regions and reads them together. Page-level is simplest but may be insufficient for dense pages, columns, tables, marginalia, or line-aligned editing. Separate segmentation makes geometry inspectable and replaceable but adds workflow steps. Joint layout-and-text AI is convenient but needs stronger verification and may be harder to correct. The recommendation is to start with page-level reading unless line/region-level text is a first-release requirement. If geometry is needed, prefer a visible, editable layout step over hidden unreviewable segmentation.

### G. Export

1. Let the user select project/document, text layer/version, format, and output location.
2. Provide plain text at minimum (proposed); additional archival formats need decision and requirements.
3. Offer provenance/review metadata separately or embedded where format supports it.
4. Preview output and report skipped pages, empty text, or unsupported metadata before writing.

## 7. Functional requirements

| ID | Requirement | Status |
|---|---|---|
| P-001 | Deliver Windows first as a standalone app in this subdirectory. | Confirmed |
| P-002 | Do not require the parent Django/eScriptorium app or its services. | Confirmed |
| P-003 | Exclude Kraken and neural model training from initial scope. | Confirmed |
| P-013 | The Windows app must visibly resemble eScript AI in brand, visual language, navigation, and core workflow cues. | Confirmed |
| P-004 | Support durable local projects containing page images, layout regions/lines, text, settings, and history. | Release requirement |
| P-005 | Preserve prior text revisions and AI outputs when a person or AI changes line text. | Release requirement |
| P-006 | Identify AI-generated, edited, and reviewed line text with its provenance. | Release requirement |
| P-007 | Require explicit confirmation before each remote Gemini transcription, chat, or model-comparison request. | Release requirement |
| P-008 | Let users inspect and correct segmentation geometry and line text against the source image. | Release requirement |
| P-009 | Compare selected Gemini models and let users choose results for review. | Release requirement |
| P-014 | Provide page-scoped chat that proposes text changes without silently applying them. | Release requirement |
| P-015 | Export ordered line/page text and supported layout/provenance data. | Release requirement |
| P-016 | Let the user select and use the app in every language in the main app default UI catalog: en, ar, hi, pl, it, es, pt, fr, de, ur, tr, te. Arabic and Urdu use RTL interface layout. | Confirmed follow-on release requirement |
| P-010 | Reuse human-approved corrections as context/examples only if enabled and explained. | Open |
| P-011 | Operate fully offline, or define the exact offline capability boundary. | Open |
| P-012 | Install, update, and uninstall cleanly on supported Windows versions. | Proposed |

## 8. Quality attributes

These are proposed requirements to quantify during refinement:

- **Data integrity:** interrupted writes must not corrupt the only copy of a project; use atomic writes/transactions and recovery checks.
- **Responsiveness:** project open, navigation, editing, and autosave should remain responsive on the agreed reference PC and project size.
- **Accessibility:** keyboard navigation, readable contrast, scalable text, and screen-reader labels for core tasks.
- **Reliability:** clear recovery after application crash, provider timeout, or network loss.
- **Privacy:** local storage locations are visible; sensitive data is not written into diagnostic logs by default.
- **Maintainability:** provider integration is behind a replaceable interface; project format is versioned and documented.
- **Portability option:** avoid unnecessary Windows-only business logic where it does not compromise Windows usability; Mac is not promised.

## 9. Acceptance criteria for the first implementation milestone

These criteria are proposed and need Matthew's approval before coding:

1. On a clean supported Windows installation, the user can install, launch, create a project, close, and reopen it.
2. The user can import a representative set of page images and preserve their originals.
3. The user can run the approved Gemini transcription path only after confirming the billed request, see progress/failure, and retain the source image. Development verification uses mocks unless Matthew approves a specific live request.
4. AI output is stored separately from the source/approved text and visibly identified as a draft.
5. The user can correct text, undo/redo, save, close/reopen, and confirm the correction persists.
6. The user can export the selected text and verify the output before writing it.
7. A project can be backed up and restored using the documented procedure.
8. Tests cover project persistence, interrupted operations, provider failure, provenance, privacy boundary, editing, and export.
9. The installer and app do not install or invoke Kraken training or any paid provider other than the user-confirmed Gemini route.

## 10. Success measures (open)

Select measurable measures before release: time from import to usable draft; correction time per page; percentage of pages exported without loss; recovery rate after interruption; user-reported trust/clarity; and recognition quality on a reviewed, representative dataset. Never substitute model agreement for human-verified ground truth.

## 11. Release baseline

Chat, model comparison, visible segmentation, and line-level editing are confirmed parts of this Windows release. Full UI localization across all twelve languages in the main app default catalog is a confirmed follow-on requirement after the current Windows core/recovery/packaging work and before calling the product fully release-ready. Matthew has already accepted the Gemini image-reading workflow; release planning must not wait on repeating a live read. Remaining decisions concern implementation details, supported formats, Windows targets, and release logistics—not whether these four capabilities belong in scope.
