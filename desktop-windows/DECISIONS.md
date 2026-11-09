# Decision register

Matthew clarified the product center on 2026-11-08: a desktop shell/workspace for reading image files with AI and letting the user refine the text. “Training the AI” is not the product goal. Recommendations below are proposals, not approved implementation decisions. Reply by ID to resolve items; unanswered items remain open.

## Product and workflow decisions

| ID | Decision | Options / trade-off | Recommendation to discuss | Status |
|---|---|---|---|---|
| D-01 | What is the core product promise? | A: image → AI text → user refines; B: multi-system comparison workbench; C: general AI assistant for archival text. | Image-based transcription and line-level review, with model comparison and page-scoped chat in the first release. | Decided by Matthew 2026-11-08: comparison, segmentation, line editing and chat are in this release |
| D-02 | Should the user rerun recognition? | No rerun; safe rerun with same settings; rerun with changed settings/system. | Yes. Every run creates a separate attempt; never overwrite prior text. | Decided by Matthew's “go ahead with the rest” 2026-11-08; implemented as separate attempts |
| D-03 | How many AI systems/models should the first release expose? | One curated option; multiple models; user-configured providers; full comparison workbench. | Include model selection and side-by-side comparison. Initial provider scope remains Gemini models under the existing paid-provider authorization; another billable provider needs Matthew's explicit authorization. | Decided by Matthew 2026-11-08: comparison is in this release |
| D-04 | How should users guide the AI? | Fixed presets; structured controls; free-text instruction; chat; combinations. | Keep presets and instructions, and include page-scoped chat with auditable replies and proposed text. Every remote turn requires its own disclosed user action. | Decided by Matthew 2026-11-08: chat is in this release |
| D-05 | Should the app include segmentation? | No geometry/page-level text; separate segmentation step; joint layout+recognition; hidden automatic layout. | Include a visible segmentation workflow. Proposed baseline: generate or draw regions/lines, review and edit geometry, then recognize selected lines; do not treat unreviewed geometry as authoritative. | Decided by Matthew 2026-11-08: segmentation is in this release |
| D-06 | What text granularity is needed? | Whole page; paragraphs/regions; lines aligned to image; switchable. | Use line-level text aligned to editable page regions and image coordinates, with page text assembled from ordered lines. | Decided by Matthew 2026-11-08: line-level editing is in this release |
| D-07 | Which text tasks belong in first release? | Transcription only; plus correction assistance; translation/summarisation too. | Image transcription and human correction first. | Open / blocking |
| D-08 | Must the Windows app visibly look like eScript AI? | Match brand, palette, navigation, and core workflow cues; adapt the layout to desktop. | Use the actual eScript AI identity and recognizable screen patterns, with desktop adaptations. This does not require backend or feature parity. | Confirmed by Matthew 2026-11-08 |
| D-09 | Who is the first target user? | Matthew solo; archivists generally; institutional team. | Design for one archivist, then validate with representative material. | Open |
| D-10 | Should user corrections influence later requests? | Never; explicit per-run examples; project-scoped examples; fine-tuning. | No automatic reuse. Consider explicit examples later, clearly distinguished from model training. | Open |

## Architecture and AI decisions

| ID | Decision | Options / trade-off | Recommendation to discuss | Status |
|---|---|---|---|---|
| D-11 | Desktop framework | Tauri/Rust; Electron/TypeScript; .NET; Python/Qt. | Tauri 2 + JavaScript UI is the interim shell implementation; compare Windows packaging and runtime before treating it as final. | Interim choice; review open |
| D-12 | Project file form | Single portable package; folder with SQLite/assets; database plus external originals. | Final canonical working format remains open. Session state is stored in IndexedDB and saved into the selected folder as managed assets plus a versioned JSON manifest; Tauri persists access grants and startup restores the manifest into the local cache. `.escriptai` remains a separate unencrypted backup/restore envelope. | Open; folder recovery implemented in source, Windows verification pending |
| D-13 | Original image storage | Copy into project; reference in place; user selectable. | Copy by default to avoid broken paths; optional reference mode only if needed. | Open |
| D-14 | Local AI runtime/model | Bundle; detect installed runtime; user downloads separately; external local service. | Keep runtime pluggable. Select only after licence, hardware, footprint, and quality checks. | Open for optional local AI; Gemini is implemented as the authorized hosted provider |
| D-15 | Job execution | In-process; helper process; local service. | Use simplest safe option; add process isolation only where model/runtime stability requires it. | Open |
| D-16 | Minimum PC | Supported OS, RAM, CPU/GPU, disk. | Define minimum and recommended local-AI hardware separately. | Open / blocking for release |
| D-17 | Encryption at rest | OS account protections; encrypted project; optional encryption. | Threat-model first; protect credentials and keep storage user-controlled at minimum. | Open |

## Provider and privacy decisions

| ID | Decision | Options / trade-off | Recommendation to discuss | Status |
|---|---|---|---|---|
| D-18 | Allowed AI execution route | Local model; subscription/OAuth provider; paid API; user-provided endpoint. | Matthew directed on 2026-11-08 that his paid Gemini API key be wired in. Gemini API is permitted as a user-triggered, confirmed, billed provider; other paid APIs remain prohibited. Local routes remain open for later. | Decided (Gemini) / other routes open |
| D-19 | First system/model and suitability | Select based on authorization, expected quality, cost, and user controls. | Gemini `gemini-2.5-flash` produced the user-confirmed image transcription shown in Matthew's 2026-11-08 screenshot. This confirms the route works for that sample, not its accuracy or general suitability. | Initial model selected; quality and wider suitability open |
| D-20 | Telemetry/crash reporting | None; opt-in redacted diagnostics; always-on telemetry. | None by default; user-reviewed support bundle may be considered. | Open |

## Data, UX, and release decisions

| ID | Decision | Options / trade-off | Recommendation to discuss | Status |
|---|---|---|---|---|
| D-21 | Project hierarchy | Mirror web concepts; project → documents → pages; freeform files. | Simple hierarchy unless real workflow requires collections. | Open |
| D-22 | Text/review states | Draft/edited/reviewed; add approved state. | Draft → edited → reviewed; omit training terminology. | Open |
| D-23 | Attempt history and comparison | Sequential history; side-by-side; diff; no comparison. | Preserve each run and provide side-by-side model results with explicit model labels and user-controlled adoption into line text. | Decided by Matthew 2026-11-08: model comparison is in this release |
| D-24 | Import formats | Raster images; PDF; text; office formats; IIIF. | Select from actual source material; specify limits and multipage behavior. | Open |
| D-25 | Export formats | TXT, DOCX, PDF, TEI/XML, PAGE/ALTO, CSV. | Plain text first, then formats Matthew actually delivers. | Open |
| D-26 | Initial UI languages | English only; multiple languages; RTL. | Include every interface language in the main app default catalog: English, Arabic, Hindi, Polish, Italian, Spanish, Portuguese, French, German, Urdu, Turkish, and Telugu. Provide a user-selectable, persisted UI language; Arabic and Urdu require RTL layout. | Decided by Matthew 2026-11-08; selector, catalogs, locale formatting, and direction handling implemented 2026-11-09; fluent review and workflow acceptance remain |
| D-27 | Windows target | Windows version; x64/ARM64. | Confirm actual target machines and support lifecycle. | Open / blocking for release |
| D-28 | Installer and updates | MSIX/MSI/NSIS; auto/manual updates; signed releases. | Signed installer and user-approved updates, subject to practical signing cost. | Open |
| D-29 | Product identity | Name, publisher, icon, version scheme. | “Escript AI Desktop” is provisional. | Open |
| D-30 | Backup/recovery | Manual copy; in-app backup; scheduled backup. | In-app export/restore before beta. `.escriptai` backup/restore and folder-backed startup recovery are implemented in the frontend; verify recovery and Windows behavior before archival use. | Prototype implemented; verification and final policy open |
| D-31 | Mac roadmap | Portable design; later Mac target; no commitment. | No Mac deliverable now; record portability constraints only. | Confirmed direction |
| D-32 | Success measures | Usability, speed, text quality, data integrity. | Set before alpha; quality needs human-reviewed ground truth. | Open |

## Localization scope — 2026-11-08

Matthew added a confirmed requirement: make the app usable in all twelve languages supported by the main app default catalog. Implementation started before core release verification was complete at Matthew's direction. The language selector and local catalogs are in place; fluent review and localization acceptance gates remain in `LOCALIZATION_PLAN.md`. This must pass before describing the product as fully release-ready.

## Release scope correction — 2026-11-08

Matthew explicitly confirmed that chat, model comparison, segmentation, and line-level editing belong in this release. Do not defer these features or ask him to restate their inclusion. The successful Gemini page-reading workflow has already been accepted by Matthew; remaining AI work is feature integration and non-billable/mocked verification, not proving the API can read an image again. Gemini remains the only currently authorized paid provider. Chat turns and each model-comparison request must identify the model, data sent, and billing before the user confirms; no hidden fan-out or retry.
