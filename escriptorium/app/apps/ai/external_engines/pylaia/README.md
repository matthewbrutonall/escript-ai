# PyLaia wrapper skeleton

This is the external-engine shape for a future PyLaia service. It is not a recognizer and not for production. PyLaia is not installed and this package does not import it. Transcription jobs do not use it. Do not deploy it as HTR.

`backend.py` is the seam a real recognizer will implement: `capabilities`, `list_models`, `get_model`, and `recognize`. The default `UnavailablePyLaiaBackend` does not load a model. The HTTP routes call that object and do not contain recognition code.

`tier` is `research`. `GET /v1/models` returns an empty list because no recognition backend is installed. `GET /v1/models/{model_id}` returns `model_not_found`. A valid `POST /v1/recognize` returns `unavailable` with the fixed message "recognition backend is not installed". It does not return text. An invalid request returns a contract error and does not echo the image or the line text.

`python -m ai.external_engines.pylaia.server` serves that handler on `127.0.0.1` port `8766` for local checks. It refuses any non-loopback host. Docker and transcription do not start it.
