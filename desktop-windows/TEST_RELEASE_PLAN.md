# Test and release plan

**Status:** release gates for the confirmed Windows scope: durable projects, segmentation, line-level editing, Gemini model comparison, and page-scoped chat. Supported Windows versions remain open.

## 1. Test layers

1. **Unit tests:** project validation, revision rules, state transitions, provider error mapping, serialization, export formatting, privacy policy.
2. **Storage integration tests:** current IndexedDB schema migration, image import, project-folder and project-file validation/restore, checksum mismatch, interrupted writes; if SQLite is selected later, test its transactions and migrations too.
3. **Provider contract tests:** mocked/local test adapter only; malformed results, timeout, cancellation, retry, no fallback, provenance. Never use a billable API.
4. **UI tests:** project lifecycle, import, segmentation edit, bidirectional line/image selection, line correction/history, model comparison, page chat/suggestion adoption, job status, export, accessibility basics.
5. **Packaging tests:** clean Windows VM install, launch, upgrade, downgrade/rollback policy, uninstall, file association if selected, no admin requirement if feasible.
6. **Manual archivist acceptance:** complete representative workflows on real but authorized material; record limitations and no unsupported accuracy claims.

## 2. Data fixtures

- Synthetic images for automated tests.
- A small, permission-cleared archival sample for manual workflows.
- Reviewed transcription ground truth only if recognition quality will be evaluated.
- Avoid storing unpublished or sensitive material in source control or CI artifacts.

## 3. Required failure tests

- unreadable/unsupported/corrupt image and duplicate import;
- low disk space and interrupted import/save/export;
- application close during a running AI job;
- no network, provider unavailable, authentication failure, timeout, malformed result, cancellation;
- denied Gemini transfer and attempted silent fallback;
- comparison request count/model mismatch, partial provider failures, and attempts to adopt one model's line without overwriting other results;
- chat context disclosure, page/line scope changes, and rejected or unapplied suggestions;
- malformed segmentation geometry, out-of-bounds coordinates, reading-order edits, and layout import/export round trip;
- project/database migration failure, corrupted manifest/image checksum, and backup restore mismatch;
- update signature failure and incomplete install;
- oversized page/project and performance boundary.

## 4. Quality and accuracy evaluation

If the product makes recognition-quality claims, use a representative, human-reviewed, versioned test set and report the metric, normalization rules, sample size, and exclusions. Do not use model agreement or unreviewed candidate text as ground truth. Keep process success separate from accuracy.

## 5. Release gates (proposed)

### Alpha

- approved scope and decisions;
- working local project lifecycle and text editing at page and line level;
- visible, editable segmentation linked to ordered lines;
- confirmed Gemini read path plus selectable model comparison and page-scoped chat, each with per-request disclosure, provenance, and failure handling;
- clean installer smoke and backup/restore;
- no paid provider other than the explicitly user-confirmed Gemini route, and no Kraken training functionality.

### Beta

- representative user workflow accepted;
- privacy/security review complete;
- upgrade and recovery tested;
- accessibility and performance targets measured;
- documented limitations and support channel.

### Stable

- signed release artifacts and documented update policy;
- project format migration/backward-compatibility policy;
- repeatable release process and regression suite;
- accepted archival evaluation evidence for any quality claim.

## 6. Localization acceptance

Use `LOCALIZATION_PLAN.md` as the source of truth. Verify selector coverage and persistence for all twelve codes, locale key parity and reviewed coverage, Arabic/Urdu RTL behavior, mixed-direction transcription, Unicode preservation, locale-aware formatting, offline operation, and that UI language changes leave archival project data unchanged. Exercise the representative workflow in each locale with mocked AI responses.

## 7. Windows release decisions

Select supported Windows versions/builds, x64/ARM64, installer type (MSIX/MSI/NSIS/other), code signing, auto-update policy, offline installer availability, administrator privileges, installation scope, and uninstall behavior. These are recorded in `DECISIONS.md`.
