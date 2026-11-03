# Calamari line wrapper

This is the external-engine shape for a Calamari line service. The default command is not a recognizer. Calamari is not installed in the Escript AI app image, and this package does not import it. Transcription jobs do not start it. `CALAMARI_INTEGRATED` is true. A real checkpoint and a manual workflow were verified.

`backend.py` answers `capabilities`, `list_models`, `get_model`, and `recognize`. The default `UnavailableCalamariBackend` does not read a directory. The HTTP routes call that object and do not contain recognition code.

`PredictCalamariBackend` is not the default. The server constructs it only when started with `--backend predict`, and only after the model directory contains at least one real checkpoint pair. The model id is the checkpoint file name without `.json`. It is not a path. A missing program, a timeout, or a bad exit becomes a contract error and does not include the image, the recognized text, stdout, stderr, or a path. The temporary directory is removed after the call.

The task is `recognize_lines`. This package does not serve a page contract and does not call another engine.

`python -m ai.external_engines.calamari.server` serves the handler on `127.0.0.1` port `8768` for local checks. Omitting `--backend` selects `UnavailableCalamariBackend`. It refuses any non-loopback host unless `--allow-container-bind` is set, and that flag allows only `0.0.0.0`. Compose and transcription jobs do not start it.

## Runtime

These notes describe the current Calamari package. This repository does not install it, does not depend on it, and does not recognize text.

The PyPI package is `calamari-ocr`. The current release is 2.3.1. It requires Python `>=3.7` and `tensorflow>=2.4`. Its software licence is GPL-3.0. That software licence is not a model licence. The Escript AI app image is Python 3.12 and does not install this package. Run it in a separate environment.

Calamari has no HTTP API. The prediction entrypoint is `calamari-predict`. A checkpoint argument is the path without `.json`, for example a file or SavedModel directory named `0.ckpt` beside `0.ckpt.json`. Checkpoint version 6, the SavedModel form, needs Calamari 2.1 or newer. An older 1.x install cannot load those files.

The child process is kept off the GPU. The wrapper sets `CUDA_VISIBLE_DEVICES=-1`, `HIP_VISIBLE_DEVICES=-1`, and `NVIDIA_VISIBLE_DEVICES=void` for that process only. It does not stop another job. Do not start a real prediction while another process is using the GPU.

## Image size and preprocessing

The network input height is `scenario.data.line_height` in the checkpoint JSON. The wrapper reports `input.line_height` only when that value is an integer from 1 to 1024. It does not invent the historical training default of 48, and it does not invent the height of 64 documented for an `htr+` network. A processor `line_height` is ignored.

When the model record includes that height, the existing external HTR line worker resizes each line crop to it before the request. The wrapper does not resize again. It passes the same height as `--data.line_height` so the prediction pipeline uses the declared value. Calamari still applies its own scale-to-height step, and it may center-normalize. Those steps stay inside Calamari.

The checked public data parameter `input_channels` is 1. This wrapper writes the PNG bytes it was given and does not recolour them. It does not send a contract preprocessing field. `preprocessing_supported` is empty. A request that includes `line_height`, `deslant`, `grayscale`, `preserve_aspect`, or a param key is rejected as unsupported.

## Model files

This repository does not include a Calamari checkpoint. Keep `*.ckpt.json`, SavedModel directories, and `*.h5` weights outside git. Do not commit them, and do not download a model archive from this package.

A directory the checker can accept has, at its top level, one or more pairs:

- `<name>.ckpt.json`, a JSON object small enough to read as text
- weights at `<name>.ckpt` (a non-empty file, or a directory that contains `saved_model.pb`) or a non-empty `<name>.ckpt.h5`

`<name>.ckpt` is the model id. Nested family directories are not searched. An archive in the same directory is not a model. A symlink that resolves outside the directory is rejected.

`python -m ai.external_engines.calamari.check_model MODEL_DIR` prints `OK` or `FAILED:` plus a fixed code. It does not print the path, does not import Calamari, and does not predict.

Put the directory outside this repository, for example `/opt/escript-ai/models/calamari/<family>`. From `escriptorium/app/apps`:

```bash
python -m ai.external_engines.calamari.check_model /opt/escript-ai/models/calamari/<family>
```

The model licence is `unspecified` unless that directory contains `licence.txt` whose first line is one short label, such as `CC-BY-4.0`. A paragraph, a software `LICENSE` file, and the GPL-3.0 package licence are not copied onto the model.

Pretrained weights are published separately from the program, in the `calamari_models` and `calamari_models_experimental` repositories. This note does not download them. Check the model licence before any hosted or client use.

## What is still required for a real prediction

The default server lists no models. Prediction mode needs all of the following, and this repository does not supply them:

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

Mount the model directory read-only. Mount the work root read-write, outside the model directory. The app image does not contain this process, and Compose does not start it.
