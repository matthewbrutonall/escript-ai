# Data, privacy, and security specification

**Status:** requirements draft. Threat model, remote AI policy, and retention decisions are open.

## 1. Data categories

- Archival images and derived previews.
- Imported/source text and AI-generated text.
- Human edits, review state, annotations, and metadata.
- Project structure, filenames, paths, and timestamps.
- AI request metadata, provider/model, job status, token/usage data where available.
- Credentials or OAuth tokens, if a permitted provider requires them.
- Diagnostic logs and optional support bundles.

Images, text, filenames, and collection descriptions may be confidential or sensitive even when they are not personal data.

The current prototype's `.escriptai` project file is an unencrypted JSON package containing source images, current text, saved text versions, recognition attempts, and their prompts. It excludes the Gemini key and is limited to about 180 MB. The user must protect the downloaded file like the original archival material. This package is a transfer/recovery mechanism, not the final large-project working-storage design.

## 2. Proposed defaults

- Keep project data local and under a user-visible location.
- Do not create an account or sync data by default.
- Do not send content to a remote service until the user explicitly starts an operation and the UI identifies the transfer.
- Do not include source text or image content in normal logs or crash reports.
- Store credentials in an OS-backed secret store, separate from project data.
- Keep originals; make caches and derived files distinguishable and rebuildable.
- Do not include corrections in future requests unless that feature is explicitly enabled and scoped.

These are recommendations for approval, not implementation claims.

## 3. Provider and transfer policy

The desktop subproject has one explicit exception authorized by Matthew: Google Gemini through his API key. It is user-triggered and potentially billable. No other paid API or billable cloud provider is permitted.

- Each Gemini request requires a clear, per-request confirmation naming the model, page being sent, and billing to the key owner's account.
- The app has no automatic retries or provider fallback. The user initiates every request.
- The API key stays in Windows Credential Manager in the desktop app. `GEMINI_API_KEY` is allowed only as a developer-preview environment variable; the key must never enter page code, browser storage, logs, or project files.
- Agents do not make live Gemini calls unless Matthew first sees an estimated cost and explicitly approves that call. Mocks and offline checks are the default for development.
- Any future provider requires a separate decision covering billing, transferred fields, retention, and consent behavior.

## 4. Data lifecycle questions

Decide before implementation:

- default project location and user control over it;
- whether projects are a folder, one file, or both;
- retention of source images and deleted pages;
- deletion behavior for revisions, cached previews, AI job history, examples, credentials, and temporary files;
- backup and restore format, including encryption and integrity validation;
- project portability and whether paths are relative or absolute;
- whether any telemetry/crash report exists and how opt-in is obtained;
- whether local project encryption is required and who controls the key.

## 5. Security requirements (proposed)

- Import only user-selected files and validate content rather than trusting extensions.
- Prevent path traversal and archive extraction escapes.
- Use parameterized database access and safe serialization.
- Restrict shell/native access from any embedded web UI.
- Verify signatures for installers and updates.
- Keep secrets out of logs, exports, project archives, and source control.
- Provide explicit controls to clear credentials and local caches.
- Make support bundle contents inspectable before export.

## 6. Threat model to complete

At minimum consider: accidental data loss; malware or a malicious project file; stolen/lost PC; another local account; compromised provider credentials; remote provider retention; malicious model/runtime; unsafe update; sensitive data in logs; and project copied to removable media. Rank likelihood/impact and choose mitigations proportional to a single-user archival desktop app.

## 7. Privacy acceptance checks

- Offline project create/open/edit/export works without network access (if offline is selected as a requirement).
- Network inspection of a local-only workflow shows no outbound request.
- Gemini's per-request confirmation matches the image and prompt scope actually sent.
- Logs and support bundle contain no raw images/text or credentials by default.
- Project backup/restore preserves assets, revisions, and provenance and reports integrity failures.
- Delete controls explain what is removed and verify credentials/cache cleanup where applicable.
