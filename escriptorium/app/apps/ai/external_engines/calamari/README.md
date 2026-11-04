# Calamari line service

This is the external-engine shape for a Calamari line service. The default command is not a recognizer. The Escript AI app image does not install Calamari or TensorFlow, and this package does not import them. Transcription jobs do not start the service. `CALAMARI_INTEGRATED` is true. The menu can list a configured row. The optional service below is what recognizes text.

`backend.py` answers `capabilities`, `list_models`, `get_model`, and `recognize`. The default `UnavailableCalamariBackend` does not read a directory. The HTTP routes call that object and do not contain recognition code.

`PredictCalamariBackend` is not the default. The server constructs it only when started with `--backend predict`, and only after the model directory contains at least one real checkpoint pair. The model id is the checkpoint file name without `.json`. It is not a path. A missing program, a timeout, or a bad exit becomes a contract error and does not include the image, the recognized text, stdout, stderr, or a path. The temporary directory is removed after the call.

The task is `recognize_lines`. This package does not serve a page contract and does not call another engine.

`python -m ai.external_engines.calamari.server` serves the handler on `127.0.0.1` port `8768` for local checks. Omitting `--backend` selects `UnavailableCalamariBackend`. It refuses any non-loopback host unless `--allow-container-bind` is set, and that flag allows only `0.0.0.0`. The default `docker-compose.yml` does not start it. Transcription jobs do not start it.

## Runtime

These notes describe the current Calamari package. This repository does not install it, does not depend on it, and does not recognize text.

The PyPI package is `calamari-ocr` 2.3.1. Its wheel sha256 is `f742740424cdfde6abb5bfc7084a690ea9bdbe04e2f648ba82390b519218b605`. It requires Python `>=3.7` and `tensorflow>=2.4`. The tested runtime is Python 3.10 and TensorFlow 2.15.1, which is the newest TensorFlow the 2.3.1 release notes say the bundled `ocrd-fork-tfaip` 1.2.7 supports. The package licence is GPL-3.0. That software licence is not a model licence. The Escript AI app image is Python 3.12 and does not install this package. Run it in the separate image below.

Calamari has no HTTP API. The prediction entrypoint is `calamari-predict`. A checkpoint argument is the path without `.json`, for example a file or SavedModel directory named `0.ckpt` beside `0.ckpt.json`. Checkpoint version 6, the SavedModel form, needs Calamari 2.1 or newer. An older 1.x install cannot load those files.

The default child process is kept off the GPU. The wrapper sets `CUDA_VISIBLE_DEVICES` and `HIP_VISIBLE_DEVICES` to empty strings, and `NVIDIA_VISIBLE_DEVICES=void`, for that process only. Calamari 2.3.1 treats each non-empty `CUDA_VISIBLE_DEVICES` entry as a GPU, so `-1` asks for a device and then fails when none is present. It does not stop another job. Do not start the GPU overlay while another process is using the GPU.

## Image size and preprocessing

The network input height is `scenario.data.line_height` in the checkpoint JSON. The wrapper reports `input.line_height` only when that value is an integer from 1 to 1024. It does not invent the historical training default of 48, and it does not invent the height of 64 documented for an `htr+` network. A processor `line_height` is ignored.

When the model record includes that height, the existing external HTR line worker resizes each line crop to it before the request. The wrapper does not resize again and does not pass `--data.line_height`. Calamari 2.3.1 predict rejects that flag. The checkpoint already stores the height on its processors, and Calamari applies its own scale-to-height step. It may also center-normalize. Those steps stay inside Calamari.

The checked public data parameter `input_channels` is 1. The published antiqua checkpoints were trained on binary line images. A grey photograph of the same line decodes to an empty sentence, so the wrapper writes each line as black text on a white ground before `calamari-predict`. A light-on-dark crop is inverted when its border is dark. A tight crop whose ink covers most of the box stays dark when the border is light. Bytes that are not a readable image are written unchanged. It does not send a contract preprocessing field. `preprocessing_supported` is empty. A request that includes `line_height`, `deslant`, `grayscale`, `preserve_aspect`, or a param key is rejected as unsupported.

## Model files

This repository does not include a Calamari checkpoint. Keep `*.ckpt.json`, SavedModel directories, and `*.h5` weights outside git. Do not commit them. The provision script downloads only the pinned archive below.

A directory the checker can accept has, at its top level, one or more pairs:

- `<name>.ckpt.json`, a JSON object small enough to read as text
- weights at `<name>.ckpt` (a non-empty file, or a directory that contains `saved_model.pb`) or a non-empty `<name>.ckpt.h5`

`<name>.ckpt` is the model id. Nested family directories are not searched. An archive in the same directory is not a model. A symlink that resolves outside the directory is rejected.

`python -m ai.external_engines.calamari.check_model MODEL_DIR` prints `OK` or `FAILED:` plus a fixed code. It does not print the path, does not import Calamari, and does not predict.

Put the directory outside this repository, for example `/opt/escript-ai/models/calamari/<family>`. From `escriptorium/app/apps`:

```bash
python -m ai.external_engines.calamari.check_model /opt/escript-ai/models/calamari/<family>
```

The model licence is `unspecified` unless that directory contains `licence.txt` whose first line is one short label, such as `MIT`. A paragraph, a software `LICENSE` file, and the GPL-3.0 package licence are not copied onto the model. The optional service is started with `--require-licence`, so it refuses to listen while any checkpoint is `unspecified`.

## Pinned model

The fresh-clone model is `antiqua_historical` from [Calamari-OCR/calamari_models release 2.2](https://github.com/Calamari-OCR/calamari_models/releases/tag/2.2), commit `308697f2fe2f0143d687107e6157688ea4116157`. The archive URL is `https://github.com/Calamari-OCR/calamari_models/releases/download/2.2/antiqua_historical.tar.gz`. It is 30633782 bytes and its sha256 is `d3326df94559fda7b40dc6cbcbb5834ce2ec409346e405c103f6dadca3f090b7`.

The repository `LICENSE` file at that tag is the MIT licence. That is the model licence. It is not the GPL-3.0 licence of the `calamari-ocr` package. The archive itself contains no `licence.txt`. The installer writes `MIT` into `licence.txt` only after the size and sha256 match. A mismatch writes nothing.

The family is five SavedModel voters, `0.ckpt` through `4.ckpt`. Each JSON file is version 6, `scenario.data.line_height` 48, `input_channels` 1, and `ensemble` 0. The service lists each voter as its own model id. Calamari can vote by receiving all five checkpoints in one `calamari-predict` command. This service does not do that. Selecting one id recognizes with that voter only. The published README for the family says the voters were trained on binarized lines from the antiqua portion of GT4HistOCR.

The weights are not committed. Do not copy them into this repository.

From `escriptorium/app/apps`:

```bash
sh ai/external_engines/calamari/provision-model.sh \
  /var/lib/escript-ai/models \
  /var/lib/escript-ai/archives
python -m ai.external_engines.calamari.check_model /var/lib/escript-ai/models/antiqua_historical
```

The first command downloads the pinned archive when it is not already in the archive directory, checks it, and extracts it. The checker then prints `OK`.

## Optional service

The image is built only from `docker-compose.calamari.yml`. `docker compose up` does not read that file, and `docker-compose.yml` does not start Calamari. The base image is `python:3.10-slim-bookworm` digest `sha256:5be5aaecb962a41567bc8041f1e9e3432cb085b037fe057f9a562630b2e4b370`. The image installs Debian `libgomp1` `12.2.0-14+deb12u1` and the dependency lock `requirements.lock` beside the Dockerfile. That lock was resolved from `calamari-ocr` 2.3.1, TensorFlow 2.15.1, `ocrd-fork-tfaip` 1.2.7, and Pillow 10.4.0. TensorFlow stays in that image.

From `escriptorium/`:

```bash
export CALAMARI_MODEL_DIR=/var/lib/escript-ai/models/antiqua_historical
docker compose -f docker-compose.calamari.yml build
docker compose -f docker-compose.calamari.yml up -d
docker compose -f docker-compose.calamari.yml down
```

`CALAMARI_MODEL_DIR` is required, including for `down`, because Compose interpolates it on every command. The model mount is read-only. The work directory is the named volume `calamari-work`. There is no host port. The network `escript-ai-calamari` is internal. Attach the Escript AI `web` and `celery-main` services to that network, and set the engine endpoint to `http://calamari:8768`. Leave the engine metadata empty. The task is the line contract, not the HTRflow page contract. A `never_send_offsite` document accepts this endpoint only when it is literal loopback. A private Docker name is not loopback, so that document policy must be false for this service, or the endpoint must be a loopback publish that this compose file does not create.

The CPU command sets `--device cpu`. To use one GPU, and only when that GPU is free:

```bash
export CALAMARI_GPU_DEVICE=0
docker compose -f docker-compose.calamari.yml -f docker-compose.calamari.gpu.yml up -d
```

`CALAMARI_GPU_DEVICE` is one host index, not `all`. The overlay reserves that device and sets `CUDA_VISIBLE_DEVICES` to `0`, because the container sees the reserved GPU as device 0. The overlay is not part of the default command. Do not start it while another process is using the GPU.

The tested CPU image reported `calamari-ocr` 2.3.1 and TensorFlow 2.15.1, with no GPU visible to TensorFlow. One synthetic printed line was recognized twice with voter `0.ckpt`: once as black text on white, and once as the same line inverted. Both calls returned the same five-character reading. That is process evidence for this runtime and the pinned model. It is not a character-error rate. The service log stayed empty, and the work directory was empty after each call.

## Configure the row

Create one enabled research engine row named Calamari. Set the endpoint to the service address, timeout 180 or higher, experimental true, and metadata `{}`. The model list then shows `0.ckpt` through `4.ckpt` with licence `MIT` and line height 48. Select pages, start External HTR, and import into a new layer. The layer stamp is `external-htr:calamari:<model-id>`. Import leaves the review gate raw. The layer stays training-ineligible until the existing review control is used. A second import does not write another layer.

## What is still required for a real prediction

The default server lists no models. Prediction mode needs all of the following. The pinned installer and the optional image supply them when the steps above are followed:

- an executable `calamari-predict` from `calamari-ocr` 2.1 or newer (2.3.1 is the current PyPI release), in an environment the service account can run
- one family directory of real checkpoint pairs, mounted read-only
- a writable work directory outside that model directory
- a checkpoint JSON that declares `scenario.data.line_height` when the app should resize line crops before sending them
- a `licence.txt` label when the model licence should be shown as anything other than `unspecified`

A layout check does not prove that the weights load. An empty or stand-in file is not a model.

## Server modes

The default command uses `UnavailableCalamariBackend`. No model directory is read. `GET /v1/models` is empty, and a valid recognize call returns `unavailable`.

```bash
python -m ai.external_engines.calamari.server
```

`--backend unavailable` is the same default. Model flags with that mode are rejected. Nothing in the process environment selects predict mode.

`--backend predict` constructs `PredictCalamariBackend`. It requires `--model-dir` and `--work-root`. `--timeout` defaults to 30 seconds. Startup checks the directory and exits before listening when no checkpoint pair is accepted. Failure text is a fixed message or `FAILED:` plus a fixed code. It does not include the model path.

```bash
python -m ai.external_engines.calamari.server \
  --backend predict \
  --model-dir /models/<family> \
  --work-root /tmp/calamari-work \
  --timeout 180
```

Mount the model directory read-only. Mount the work root read-write, outside the model directory. The app image does not contain this process. The default Compose file does not start it. `docker-compose.calamari.yml` is the optional service, and it adds `--require-licence`.
