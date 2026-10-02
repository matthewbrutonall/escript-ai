# External HTR engine contract (v1)

This is the HTTP contract for a process that recognises line images.
Escript AI does not serve these routes, does not call them, and does not
ship an engine container. A wrapper, for example a small FastAPI service
in front of PyLaia or TrOCR, owns the process and the dependencies.

The shapes are checked by `../htr_engine_contract.py`. That module is
standard-library code. Current transcription jobs do not import it.
A wrapper may import the parsers in its own tests.

DRetHTR is a research-tier engine. A wrapper for it sets `tier` to
`research` and `experimental` to `true`. The routes are the same as
for any other external HTR engine.

`api_version` is the string `"1"` on every object below. Objects are
closed: an unknown field is `invalid_request`. Name strings are
non-empty and have no surrounding space. `document_id`, `part_id`, and
`line_id` are a non-empty string or a non-negative integer. Booleans
are not identifiers. Integers are read as decimal strings (`1` and
`"1"` are the same id).

## Routes

Served by the engine process:

| Method | Path | Body |
|--------|------|------|
| `GET` | `/v1/capabilities` | capabilities object |
| `GET` | `/v1/models` | model list |
| `GET` | `/v1/models/{model_id}` | one model |
| `POST` | `/v1/recognize` | recognize request, recognize response |

## Capabilities

`tier` is `production`, `api`, or `research`. `tasks` must include
`recognize_lines`. That is the only task name in v1. `accepts` must
include `image/png`. `image_transport` must be `base64`. `reports` must
include `timing_ms`. `confidence` and `warnings` are optional entries
in `reports`.

```json
{
  "api_version": "1",
  "engine": "example",
  "tier": "production",
  "tasks": ["recognize_lines"],
  "accepts": ["image/png"],
  "image_transport": ["base64"],
  "max_lines_per_request": 32,
  "preprocessing_supported": ["line_height", "grayscale"],
  "params_accepted": ["beam_width"],
  "reports": ["timing_ms", "confidence", "warnings"]
}
```

## Model metadata

`GET /v1/models/{model_id}` returns one model. `GET /v1/models` returns
the same engine name plus a `models` array. Each model's `engine` must
match the list, and `model_id` values must be unique.

`licence` and `source` are required strings (a URL or a local note).
`scripts` and `languages` are lists and may be empty. `alphabet_note`
may be empty. `experimental` is a boolean. `input` is a preprocessing
object describing the geometry this model expects.

```json
{
  "api_version": "1",
  "engine": "example",
  "model_id": "example-model",
  "model_version": "1",
  "display_name": "Example",
  "licence": "unknown",
  "source": "local:example",
  "scripts": ["Latn"],
  "languages": ["en"],
  "input": {
    "line_height": 128,
    "grayscale": true
  },
  "alphabet_note": "",
  "experimental": false
}
```

List response:

```json
{
  "api_version": "1",
  "engine": "example",
  "models": []
}
```

The array entries are model objects like the one above.

## Recognize

The request carries one page's lines. `lines` is non-empty, and
`line_id` values are unique. `image` is standard base64 of a PNG, with
no `data:` prefix and no whitespace. The example below uses the dummy
string `AAAA`. A real crop is longer. `baseline` (at least two `[x, y]`
points) and `mask` (at least three) may be omitted.

Send only preprocessing the engine listed. A key that is present is a
request to apply that setting, including `false`. Omit a setting the
engine does not support. Do not send `deslant: false` to an engine that
did not list `deslant`.

```json
{
  "api_version": "1",
  "job_id": "job-1",
  "document_id": 123,
  "part_id": 456,
  "engine": "example",
  "model_id": "example-model",
  "preprocessing": {
    "line_height": 128,
    "grayscale": true,
    "params": {"beam_width": 4}
  },
  "lines": [
    {
      "line_id": 1,
      "image": "AAAA",
      "baseline": [[10, 20], [120, 22]]
    }
  ]
}
```

The response repeats `job_id`, `engine`, and `model_id` from the
request. Every requested `line_id` appears once. An extra id is
`invalid_request`. A missing id is `line_failed` for that line.
`text` may be empty. `warnings` is a list of short strings and may be
empty. `timing_ms` is a non-negative integer on each line and on the
job. `0` means the engine did not measure that interval.

`confidence` is `null` or a number from 0 to 1. It describes that
engine's own score. Do not compare confidence across engines.
Compare engines by error against reviewed text, not by this field.

`provenance` repeats `engine`, `model_id`, `model_version`, and
`api_version` from the response. Those four values are how a caller
can record which engine and model produced the line. `resources` may
be `{}`. The only defined key is `device`, a short string such as
`cpu`.

```json
{
  "api_version": "1",
  "job_id": "job-1",
  "engine": "example",
  "model_id": "example-model",
  "model_version": "1",
  "results": [
    {
      "line_id": "1",
      "text": "transcribed line",
      "confidence": 0.91,
      "timing_ms": 34,
      "warnings": []
    }
  ],
  "provenance": {
    "engine": "example",
    "model_id": "example-model",
    "model_version": "1",
    "api_version": "1"
  },
  "timing_ms": 120,
  "resources": {"device": "cpu"}
}
```

## Preprocessing

Shared fields, all optional:

| Field | Value |
|-------|--------|
| `line_height` | integer ≥ 1 |
| `deslant` | boolean |
| `grayscale` | boolean |
| `preserve_aspect` | boolean |

Anything else goes in `params`. A param value is a string, a finite
number, a boolean, or a list of those. Null and nested objects are
rejected. Declare the keys in `params_accepted`. `["*"]` accepts any
param key, and that wildcard must be the only entry.

`preprocessing_supported` lists shared fields only. An unknown shared
name there is `unsupported_preprocessing`. A request that uses an
undeclared shared field or param key is the same code.

## Errors

Failed calls return one object, not a recognize response:

```json
{
  "api_version": "1",
  "error": {
    "code": "model_not_found",
    "message": "example-model is not loaded",
    "retryable": false,
    "line_id": null
  }
}
```

`line_id` is an id or `null`. `message` is plain text and must not
include secrets.

| Code | HTTP | When |
|------|------|------|
| `invalid_request` | 400 | Malformed body, unknown field, bad id, or a result line that was not requested |
| `unsupported_preprocessing` | 400 | Shared field or param key this engine did not declare |
| `model_not_found` | 404 | Unknown `model_id` |
| `line_failed` | 422 | A requested line is missing from `results` |
| `busy` | 429 | Retry later |
| `unavailable` | 503 | Engine process cannot run |
| `internal` | 500 | Unexpected failure |

`busy` and `unavailable` are the retryable codes. The others set
`retryable` to `false`.

## Provenance

A successful response is not usable unless `provenance` matches the
response header:

- `provenance.engine` = `engine`
- `provenance.model_id` = `model_id`
- `provenance.model_version` = `model_version`
- `provenance.api_version` = `"1"`

The wrapper fills those from the model it actually ran. It does not
invent a second record of the line. This contract does not write a
transcription layer.
