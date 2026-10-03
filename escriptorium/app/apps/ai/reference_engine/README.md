# Reference engine

This is a fake in-process engine for contract tests. Call `handle(method, path, body)` and it returns an HTTP status and a JSON object. It does not open a socket, write files, or read the line image.

`POST /v1/recognize` answers `LINE {line_id}` for the model `reference-line`. That text is a placeholder. This is not a recognizer and not for production. Transcription jobs do not use it. Do not deploy it as HTR.
