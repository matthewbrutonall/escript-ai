# Reference engine

This is a fake engine for contract tests. It is not a recognizer and not for production. Transcription jobs do not use it. Do not deploy it as HTR.

`handle(method, path, body)` is in-process. It does not open a socket, write files, or read the line image. `POST /v1/recognize` answers `LINE {line_id}` for the model `reference-line`. That text is a placeholder.

`python -m ai.reference_engine.server` serves that same handler on `127.0.0.1` port `8765` for local contract tests. It refuses any other host. Docker and transcription do not start it. Do not expose it beyond loopback.
