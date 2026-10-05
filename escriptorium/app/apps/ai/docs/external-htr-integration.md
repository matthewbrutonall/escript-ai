# External HTR transcription integration

This note describes a future action that would send selected document lines
to an `ExternalHTREngineConfig`. It is not implemented. Saving an engine
row, running the contract check, or starting the optional PyLaia smoke
service does not transcribe a document. Existing transcription jobs, views,
and the Transcribe action do not read that table and do not call these
routes.

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
do not overwrite a kraken layer. Stamp each written line with

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
check. Treat `localhost` and `127.0.0.0/8` as local. Treat any other host
as off-site and refuse it when the document forbids off-site sends. A
missing policy row stays allowed, which is the rule the hosted path
already uses. Do not weaken that rule for hosted backends.

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
