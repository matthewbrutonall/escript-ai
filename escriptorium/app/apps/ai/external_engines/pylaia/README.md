# PyLaia wrapper skeleton

This is the external-engine shape for a future PyLaia service. It is not a recognizer and not for production. PyLaia is not installed and this package does not import it. Transcription jobs do not use it. Do not deploy it as HTR.

`backend.py` is the seam a real recognizer will implement: `capabilities`, `list_models`, `get_model`, and `recognize`. The default `UnavailablePyLaiaBackend` does not load a model. The HTTP routes call that object and do not contain recognition code.

`tier` is `research`. `GET /v1/models` returns an empty list because no recognition backend is installed. `GET /v1/models/{model_id}` returns `model_not_found`. A valid `POST /v1/recognize` returns `unavailable` with the fixed message "recognition backend is not installed". It does not return text. An invalid request returns a contract error and does not echo the image or the line text.

`python -m ai.external_engines.pylaia.server` serves that handler on `127.0.0.1` port `8766` for local checks. It refuses any non-loopback host. Docker and transcription do not start it.

## Runtime

These notes describe the current Teklia package. This repository does not install it, does not depend on it, and does not recognize text.

The PyPI package is `pylaia`. The import name is `laia`. Current `pylaia` 1.1.2 requires Python `>=3.9,<3.11`. It pins `torch>=1.13,<1.14`, with `torchvision` and `torchaudio`. It also pins `pytorch-lightning==1.4.2`. That old Lightning stack is why Python 3.11/3.12 and PyTorch 2.x support should be treated as a major upstream or runtime change, not a simple version bump. The Escript AI app image is Python 3.12, so PyLaia must run in its own external engine container, not inside the main Escript AI Python environment. Escript AI should plan as if PyLaia requires a separate Python 3.10 / torch 1.13 engine container unless upstream changes. This repository does not add that container.

PyLaia has no HTTP API. The decode entrypoint is `pylaia-htr-decode-ctc`. It reads image files plus `img_list.txt`, `syms.txt`, a model architecture pickle, and a checkpoint (`*.ckpt`). Hugging Face bundles such as `Teklia/pylaia-huginmunin` contain `model`, `weights.ckpt`, and `syms.txt`. CPU decode uses `--trainer.gpus 0`. Line-image height and colour must match the trained model. Known bundles are often 128 pixels high.

A future adapter would write temporary line images and invoke or hold a decoder process. That adapter is not implemented.
