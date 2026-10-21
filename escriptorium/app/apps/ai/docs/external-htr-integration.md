# External HTR transcription integration

This note describes a future action that would send selected document lines
to an `ExternalHTREngineConfig` and write a new transcription layer. That
send and that write are not implemented. Saving an engine row, running the
contract check, or starting the optional PyLaia smoke service does not
transcribe a document. Existing transcription jobs, views, and the
Transcribe action do not read that table and do not call these routes.

The operator procedure that is available now is
`external-htr-operator-workflow.md`. It checks an engine. It does not
implement the action described below.

`external_htr_writeback_plan.py` is separate from that action. It can plan
a new layer from a completed audit. It does not send a request, create the
layer, or write `LineTranscription` rows.

## What is already ready

### ExternalHTREngineConfig

`ExternalHTREngineConfig` stores a name, a base `endpoint_url`, `enabled`
(default false), `tier`, `experimental` (default false), `timeout_seconds`,
and `metadata`. `metadata` must be a JSON object. It is not a place for API
keys, model files, or a model path. Saving the row does not call the
endpoint. The admin **Test connection** action calls capabilities and the
model list only. It does not recognize a line.

The URL is the base only. Routes are joined by the client. A single-label
host such as `http://pylaia-smoke:8766` cannot be saved.

### Client

`htr_engine_client.py` can `GET /v1/capabilities`, `GET /v1/models`,
`GET /v1/models/{model_id}`, and `POST /v1/recognize`. A config that is not
enabled fails before any request is sent. The client does not read
`metadata`, does not follow redirects, does not use environment proxies,
and does not retry. A recognition error is a fixed message. It does not
include the image, the endpoint, or the response body. Transcription jobs
do not import this module.

### Contract check

`python manage.py check_external_htr_engine <config_id>` checks one stored
row. `<config_id>` is the numeric id. The command calls capabilities, the
model list, one model, and one synthetic recognize request, and it stops at
the first failure. A disabled row is not called. An empty model list stops
before model detail and recognize. The recognize image is the constant
`AAAA`, which is not a document image and not a PNG. The command prints one
`OK:` or `FAILED:` line and does not print recognized text. It does not
create a transcription layer.

### PyLaia smoke service

The PyLaia package can serve the same routes. Its default command stays
unavailable and does not decode. Explicit `--backend decode` can call
`pylaia-htr-decode-ctc` when an operator has already mounted a model
directory from outside this repository. The optional file
`docker-compose.pylaia-smoke.yml` is not loaded by `docker compose up`. It
is a separate project, publishes `127.0.0.1:8766` only, and does not join
the app network. App containers cannot use the name `pylaia-smoke` today.
`http://127.0.0.1:8766` is reachable from host `manage.py` only while that
published port is open. The main app image does not install PyLaia.

## Writeback plan

`plan_external_htr_writeback` reads a completed audit, its stored line
results, and the current line primary keys on that part.
`load_external_htr_writeback_plan` loads those rows. The checks themselves
do not need the database.

It accepts only a completed audit whose document and part are present, whose
part belongs to that document, and whose stored provenance, result count,
warning count, and empty-text count match the included rows.
`requested_line_count` and `skipped_line_count` are not compared again. A
successful apply rewrites a skipped row in place and does not change those
two totals. Each included `line_id` must be the current primary key of a
line on that part, as a decimal string. `external_id` is not an identifier.
Duplicate, missing, foreign, deleted, and malformed included ids are
rejected. Skipped rows are left out. Empty text stays in the plan. Included
text longer than 2048 characters, the `LineTranscription.content` limit, is
refused and is not shortened. Planned, sent, failed, cancelled, and
incomplete audits are refused with a fixed code and message. The message does not include an
endpoint, metadata, an image, a response body, or an exception.

The plan proposes a new layer name and a provenance stamp. The stamp is
`external-htr:<engine>:<model_id>` cut to 128 characters. The name also
carries the audit job id, because that stamp is not unique. The full
engine, model id, model version, and API version stay on the plan beside
the stamp. The plan does not look up an existing layer and it does not
overwrite one. The proposed name may already exist. A lookup here would
not reserve it. `import_external_htr_writeback` inserts the layer and
checks `(name, document)` in one transaction, and leaves the existing
row unchanged when that name is taken.

Planning reads stored audit text only. It does not call an engine, open a
page image, crop a line, or read a request file. It does not change the
audit. `check_external_htr_live` and `run_external_htr_audit` still apply
`never_send_offsite` before they call an engine. The plan does not send
anything, so it does not add another off-site check and it does not remove
theirs.

## Writeback helper

`import_external_htr_writeback` takes one stored audit job id. It locks
that job, runs the planner again against the current lines, and creates
one new transcription layer plus one row for each included line. Code and
tests can call it. No command, task, or page calls it.

The audit status stays `completed`. `imported` records that the import
finished, and `imported_transcription` points at the new layer. Both are
set only after every line row has been created. `imported` stays true if
that layer is later removed. The link is then cleared, and another import
of the same audit still creates nothing.

Line text is stored as planned, including an empty string. Text longer
than 2048 characters is refused and is not shortened. Each line's
`version_source` is the provenance stamp. `version_author` is the audit
user's name when that name fits the field. Confidence, timing, and
warnings stay on the audit rows.

The helper inserts the layer and checks `(name, document)` by that
insert. When the name is already taken, the existing layer is left
unchanged and the audit is not marked imported. A repeated or overlapping
import of the same audit creates one layer only.

The helper does not call an engine, open a page image, crop a line, or
read a request file. It does not change Transcribe or `ai_transcribe`.

The operator action in the next section is still not implemented.

## Proposed first integration

Add a new explicit task. Do not change `ai_transcribe` or the current
Transcribe action.

The operator chooses document parts and one enabled
`ExternalHTREngineConfig`. The task loads capabilities and the model list
from that endpoint. The operator chooses one `model_id` from that list.
The id is not a file path, and it is not taken from `metadata`.

For each selected part, crop each existing line with the line mask already
stored on the part. Use the same tight crop the per-line path already uses
(`crop_line`). Do not send the colour-keyed region image used by the
hosted VLM path. Encode each crop as PNG, then as standard base64 with no
`data:` prefix and no whitespace. A line with no usable mask is not sent.

Send those images to `POST /v1/recognize` through `recognize_lines`. Set
`line_id` to the line primary key as a string. Send only preprocessing
fields that this engine's capabilities declare. PyLaia's current skeleton
declares none, so that request uses an empty preprocessing object. Keep
each request inside `max_lines_per_request` and inside the client's
existing body cap.

Write the text into a new `core.Transcription` layer created for this run.
Do not append it to the layer used by the existing Transcribe action, and
do not overwrite a kraken layer. The planner only proposes that layer.
`import_external_htr_writeback` can create it from an audit that is
already stored. This task does not exist yet. Stamp each written line with

`external-htr:<engine>:<model_id>`

truncated to 128 characters, which is the `version_source` limit. The job
record keeps the full `engine`, `model_id`, and `model_version` from the
response provenance. A response whose provenance does not match its header
is not written. `confidence` stays engine-specific and is not a rank.
`timing_ms` of 0 means the engine did not measure that interval.

The layer starts as draft text. Its quality gate, if recorded, starts at
`raw`. This action does not mark the layer training-eligible.

## Safety

Call the endpoint only when `enabled` is true. The default stays false.
Nothing in import, save, or the existing Transcribe button selects an
external engine.

Do not put secrets in `metadata`. The client continues to ignore that
field. The model bundle stays outside the repository and outside the row.

Apply `AIDocumentPolicy` before the first request, in the task, not only
in the UI. `never_send_offsite` already blocks a remote hosted backend.
This action is not that backend, so the new task must perform its own
check. Treat `localhost`, `127.0.0.0/8`, and the literal address `::1`
as local. Treat any other host as off-site, including private LAN
addresses and Docker service names, and do not use DNS to decide. Refuse
the call when the document forbids off-site sends. A missing policy row
stays allowed, and boolean false stays allowed. A non-boolean fails
closed. The missing-row rule is the one the hosted path already uses. Do not
weaken that rule for hosted backends. The operator commands
`check_external_htr_live` and `run_external_htr_audit` already apply
this check before they call an engine.

The monthly USD cap and `AIUsageLedger` record hosted VLM spend. A local
engine has no API price. This action must not invent a token charge and
must not block a local engine for lack of USD budget. The cost is compute
time on the operator's machine. The engine reports pressure with `busy`
or `unavailable`. A paid remote HTR API is not this action.

Today's PyLaia smoke URL is a host loopback port. A worker inside the app
Compose network cannot open that port. The task may call only an endpoint
the worker can already reach. It does not start Compose and it does not
change the app network.

## Failure handling

Write a batch only after its recognize response parses. Lines already
written from an earlier batch stay written. A failed batch does not roll
the document back and does not erase those lines.

The contract rejects a whole response when a requested line is missing
from `results`. That failure applies to the batch, not to earlier batches.
The task may split that same batch into smaller groups, down to one line,
and send those to the same engine. It does not try a different engine, a
hosted VLM, or kraken.

Empty text is a valid result. Store it and flag the line. A line that was
not sent, or whose request failed, stays empty and is flagged separately,
so an operator can tell a blank recognition from a call that never
succeeded. Do not copy text from another layer to fill the gap.

`busy` and `unavailable` are retryable on the same engine, with a small
fixed limit. The current client does not retry by itself. Other errors
are not retried. No failure falls through to another recognizer.

## UI

The first control is a separate **External HTR** action. It is not a new
mode inside the existing Transcribe modal, and it does not change that
modal's provider list. The action asks for parts, one enabled engine, and
one model from that engine's list. Disabled rows are shown as unavailable
and are not called.

A comparison view against kraken or a reviewed layer can come later. It
can reuse the existing disagreement sample. It is not part of the first
action, and confidence is not the comparison.

## Non-goals

- No training, and no automatic promotion of the new layer.
- No model download and no model files in this repository.
- No Compose auto-start, and no change to the default app stack.
- No change to the existing Transcribe action, `ai_transcribe`, or hosted
  VLM behaviour.
- No use of the reference engine as a document recognizer. It remains a
  contract-test double.
