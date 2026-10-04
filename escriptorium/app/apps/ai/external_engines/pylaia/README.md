# PyLaia wrapper skeleton

This is the external-engine shape for a future PyLaia service. It is not a recognizer and not for production. PyLaia is not installed and this package does not import it. Transcription jobs do not use it. Do not deploy it as HTR.

`backend.py` is the seam a real recognizer will implement: `capabilities`, `list_models`, `get_model`, and `recognize`. The default `UnavailablePyLaiaBackend` does not load a model. The HTTP routes call that object and do not contain recognition code.

`DecodePyLaiaBackend` is not the default. A caller can pass that object to `handle`. It keeps one configured model id and model directory, writes a temporary work directory, calls `prepare_decode`, then `DecodeRunner.run`, and builds a contract recognize response. The server does not construct it. The model id is a label, not a path. A layout or runner failure becomes a contract error code and does not include the image, the recognized text, stdout, stderr, or a path. The temporary directory is removed after the call. Listing the configured model does not claim accuracy, and it does not prove the model files are present; `recognize` checks those files.

`tier` is `research`. `GET /v1/models` returns an empty list because no recognition backend is installed. `GET /v1/models/{model_id}` returns `model_not_found`. A valid `POST /v1/recognize` returns `unavailable` with the fixed message "recognition backend is not installed". It does not return text. An invalid request returns a contract error and does not echo the image or the line text.

`python -m ai.external_engines.pylaia.server` serves that handler on `127.0.0.1` port `8766` for local checks. It refuses any non-loopback host unless `--allow-container-bind` is set, and that flag allows only `0.0.0.0`. Compose and transcription do not start it.

## Runtime

These notes describe the current Teklia package. This repository does not install it, does not depend on it, and does not recognize text.

The PyPI package is `pylaia`. The import name is `laia`. Current `pylaia` 1.1.2 requires Python `>=3.9,<3.11`. It pins `torch>=1.13,<1.14`, with `torchvision` and `torchaudio`. It also pins `pytorch-lightning==1.4.2`. That old Lightning stack is why Python 3.11/3.12 and PyTorch 2.x support should be treated as a major upstream or runtime change, not a simple version bump. The Escript AI app image is Python 3.12, so PyLaia must run in its own external engine container, not inside the main Escript AI Python environment. Escript AI should plan as if PyLaia requires a separate Python 3.10 / torch 1.13 engine container unless upstream changes. The main Escript AI image does not include that stack. A prototype Dockerfile for a separate image is described below. Nothing in this repository builds it.

PyLaia has no HTTP API. The decode entrypoint is `pylaia-htr-decode-ctc`. It reads image files plus `img_list.txt`, `syms.txt`, a model architecture pickle, and a checkpoint (`*.ckpt`). Hugging Face bundles such as `Teklia/pylaia-huginmunin` contain `model`, `weights.ckpt`, and `syms.txt`. CPU decode uses `--trainer.gpus 0`. Line-image height and colour must match the trained model. Known bundles are often 128 pixels high.

`decoder.py` checks the model directory, writes temporary PNG line images and `img_list.txt`, and builds the argument list for `pylaia-htr-decode-ctc`. `DecodeRunner` can run that list with `shell=False`. With `--decode.include_img_ids true`, each stdout line is `{image file name} {text}`, for example `0001.png hello`. Text may contain spaces. `0001.png ` (the file name, one space, and nothing after it) is a successful empty transcription. `0001.png` with no space is malformed and rejected. The runner maps those ids back to contract line order. A mismatch, a nonzero exit, or a timeout is a fixed error and does not include the text or stderr. The HTTP server does not construct `DecodePyLaiaBackend` and does not call the runner.

## Planned container

This is the planned shape of a separate engine container. Compose does not start it, and the main Escript AI image does not install PyLaia.

The container uses Python 3.10 and installs `pylaia==1.1.2`. That keeps the torch 1.13 / Lightning 1.4 stack isolated from the Escript AI app image.

The container entrypoint is an HTTP wrapper process. The wrapper receives contract JSON over HTTP. The intended decode flow is: contract request, temporary PNG line images, `img_list.txt`, `pylaia-htr-decode-ctc`, stdout, then a contract response. `decoder.py` can prepare the images, the list, and the argument list. The server still returns `unavailable` and does not call the program.

The model directory is mounted read-only. It should contain at minimum `model`, `weights.ckpt` or an equivalent checkpoint, and `syms.txt`. Optional language-model files may be supported later. They are not required for the first pass.

The first mode is CPU decode with `--trainer.gpus 0`. GPU mode can be added after the CPU wrapper works.

No API keys or secrets belong in this container. Model metadata and licence belong in Escript AI configuration or documentation.

A prototype Dockerfile is `escriptorium/app/apps/ai/external_engines/pylaia/Dockerfile`. Compose does not reference it, and it is not built by default. Do not treat that image as HTR. The skeleton inside it still returns `unavailable` and does not import PyLaia.

The intended later build context is `escriptorium/app/apps/ai`:

```bash
docker build -f external_engines/pylaia/Dockerfile -t escript-ai-pylaia-skeleton .
```

The image uses Python 3.10 and installs `pylaia==1.1.2`. It also installs `git`, because importing `laia` probes `git` and raises `TypeError` when `git` is absent. The skeleton server still does not import `laia`. It copies only the contract module and this skeleton package. It does not copy model files. A read-only model mount is future work, and the image does not read one yet.

The prototype image command is `python -m ai.external_engines.pylaia.server --host 0.0.0.0 --port 8766 --allow-container-bind`. That exposes the fake skeleton on the Docker network only when the container is run. The unflagged default remains `127.0.0.1`. The flag allows only `0.0.0.0`. It is still not a recognizer, and Compose does not start it.
