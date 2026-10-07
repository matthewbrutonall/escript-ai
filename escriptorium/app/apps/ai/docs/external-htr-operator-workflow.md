# External HTR operator check

This is a manual check for an operator. It is not a workflow in the
application, and it does not add a Transcribe option. The current
Transcribe action and `ai_transcribe` are unchanged. This check does not
write a transcription layer and does not write `LineTranscription` rows.
The request export and the file-based live check do not write audit rows.

`external-htr-integration.md` describes a future explicit action. That
action is not implemented. Completing the steps below does not implement
it.

Run `manage.py` from `escriptorium/app`. `<config_id>` is the numeric id
of an `ExternalHTREngineConfig` row, not its name.

## 1. Start an external engine

Start the engine yourself. The application does not start it.

One example is the optional PyLaia smoke service. It is a local smoke
check, not a production recognizer. The main application image does not
install PyLaia. `docker compose up` does not read
`escriptorium/docker-compose.pylaia-smoke.yml`. That file is a separate
Compose project, `escript-ai-pylaia-smoke`, and it does not join the
application stack.

Prepare the local image and a model directory outside this repository
before starting the file. `external_engines/pylaia/README.md` has those
steps. This note does not download a model. From `escriptorium/`, with
`PYLAIA_MODEL_DIR` pointing at that directory:

```bash
export PYLAIA_MODEL_DIR=/path/to/model
docker compose -f docker-compose.pylaia-smoke.yml up -d
```

Leave `PYLAIA_MODEL_DIR` set until the container is removed. The published
port is `127.0.0.1:8766` only.

Another engine that speaks the same routes can be used instead. The
reference engine under `reference_engine/` is a fake for contract tests.
It is not this smoke service and it is not a recognizer.

## 2. Store an engine row

In Django admin, under **AI transcription**, open **External HTR
engines** at `/admin/ai/externalhtrengineconfig/`. Create the row by
hand. Saving it does not call the engine and does not add a Transcribe
option. Transcription jobs, views, and the UI do not read this table.

For the smoke service above, while `manage.py` runs on the host and can
open the published port:

- `name`: `PyLaia smoke`
- `endpoint_url`: `http://127.0.0.1:8766`
- `tier`: `research`
- `experimental`: true
- `enabled`: true only while this check is running
- `timeout_seconds`: `180`
- `metadata`: `{}`

`endpoint_url` is the base URL only. Do not add a route, a query, or
user info. Do not use the Compose service name `pylaia-smoke` as the
host. The admin form cannot save a single-label host, and that name is
not on the application network. `metadata` must be a JSON object. Leave
it empty. Do not store an API key or a model path there. The row has no
engine column.

`enabled` must be exactly true or the later commands do not call the
engine. Set it back to false when the check is finished.

## 3. Probe the contract

This step is optional. It does not read a document and it does not write
the request file used below.

```bash
python manage.py check_external_htr_engine <config_id>
```

The command calls capabilities, the model list, one model, and one
synthetic recognize request. It stops at the first failure. A disabled
row is not called. The synthetic image is the constant `AAAA`. It is not
a document image and it is not a PNG. On the PyLaia smoke service, decode
mode can reject that image, so this command can print a recognize failure
while the service is up. That failure is not a result for a document
part.

Success prints `OK: <name> (<step>)` and exits 0. Failure prints
`FAILED: <name> (<step>: <code>) <message>` and exits 1. A missing row
prints `FAILED: engine was not found`. The line does not include
recognized text, the endpoint, stored options, or exception text. This
command does not write a transcription layer.

## 4. Export a request

The document part must already have a page image and line geometry. This
command encodes those lines and writes one JSON file. It does not call
the engine.

```bash
python manage.py export_external_htr_request <config_id> <document_part_id> --engine <engine> --model-id <model_id> --output <path>
```

`--engine` and `--model-id` are the labels to put in the request. Use a
model id that the engine lists. Keep both labels plain: start with a
letter or digit, then only letters, digits, `.`, `_`, and `-`, and stay
within 128 characters. The live check prints those labels only when they
fit that pattern.

The file is compact JSON for `POST /v1/recognize`. It contains standard
base64 PNG crops of the lines that could be sent. Treat it as sensitive
working data. Do not commit it, do not paste it into a log or a message,
and remove it when the check is finished. The command's own line does
not contain the images, the output path, the document title, or the
endpoint.

Success prints one line and exits 0:

```text
OK: external HTR request exported lines=<n> skipped=<n>
```

`lines` is the number of line images in the file. `skipped` is the
number of part lines left out. The command does not print the reason for
each skipped line. An existing output file is left unchanged unless
`--force` is also passed. `--force` replaces only that file, and only
after the new JSON has been written completely.

Failure prints one line and exits 1:

```text
FAILED: external HTR request export <code>
```

A disabled config stops before the page image is opened and before any
file is written. A missing config, a missing part, an image that cannot
be opened, a part with nothing to send, and a write failure use these
codes: `disabled`, `config_not_found`, `part_not_found`,
`image_unavailable`, `nothing_to_send`, `invalid_request`,
`output_exists`, `output_unavailable`, and `internal`. No file is
written when the request cannot be sent. The line does not include a
path or exception text.

## 5. Run the file-based live check

```bash
python manage.py check_external_htr_live <config_id> <request_json_path>
```

This command reads the JSON file from step 4 and sends it to the enabled
engine. It does not open the document again. It does not write an audit
row. It does not write a transcription layer or a `LineTranscription`
row.

A missing config, a disabled config, an unreadable file, or a file that
is not one JSON object fails before the engine is called. A disabled
config also fails before the file is read.

## 6. Read the live-check line

Success prints one line and exits 0:

```text
OK: external HTR live check <engine>:<model_id> results=<n>
```

That line means the engine accepted this request and returned `n`
results. It is evidence that the engine can answer. It is not
transcription output. Recognized text is not printed, and nothing is
saved on the document.

Failure prints one line and exits 1:

```text
FAILED: external HTR live check <code>
```

The line does not include the endpoint, stored options, the document
title or path, the request path, image data, the raw request, the raw
response, or exception text. An unsafe engine or model label is not
printed; the code is `invalid_request` instead.

`plan_external_htr` is a different command. It records a dry-run audit
and does not call an engine. It is not part of this check, and it has
no live mode.

## 7. Stop the smoke service

If step 1 used the optional PyLaia file, stop that project when the
check is finished. From `escriptorium/`, with `PYLAIA_MODEL_DIR` still
set:

```bash
docker compose -f docker-compose.pylaia-smoke.yml down
```

This does not stop the main application stack. Set the engine row's
`enabled` flag back to false, and remove the request JSON file.
