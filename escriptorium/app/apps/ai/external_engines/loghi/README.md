# Loghi-HTR line service

This is the external-engine shape for Loghi-HTR line recognition. The default command is not a recognizer. The Escript AI app image does not install Loghi or TensorFlow, and this package does not import them. Transcription jobs do not start the service. `LOGHI_INTEGRATED` is true. The menu can list a configured row. The optional service below is what recognizes text.

Laypa is the Loghi layout component. It is not included. Line masks and baselines stay on the existing eScriptorium segmentation.

`backend.py` answers `capabilities`, `list_models`, `get_model`, and `recognize`. The default `UnavailableLoghiBackend` does not read a directory. The HTTP routes call that object and do not contain recognition code.

`PredictLoghiBackend` is not the default. The server constructs it only when started with `--backend predict`, and only after the model directory matches the pinned GLOBALISE files. One recognize at a time is accepted. A second call returns `busy`. The client posts each line to the Loghi API on `127.0.0.1` and does not send a model field, so the process keeps the model loaded at startup.

## Software and model

Loghi-HTR source is commit `c24c745e9aeb287bedeb6b4edee670ae5c81a3cd` from `knaw-huc/loghi-htr`. The repository software licence is MIT. That is not the model licence.

The model id is `globalise-2023-08`. The source files are the GLOBALISE SavedModel at [doi:10.34894/HHM4TE](https://doi.org/10.34894/HHM4TE). DataverseNL names the licence CC-BY-4.0. The training description is handwritten Dutch from the VOC corpus. It is a disposable-test model. It is not evidence that the model suits Boyne Archives material.

The pinned Loghi source cannot load that SavedModel under Keras 3. The runtime directory is a separate conversion. `utils/convert-v2-to-v3` at the same source commit, with TensorFlow 2.15.0 and Keras 2.15.0, writes a Keras 2 `model.keras`. The service image then rewrites that file to Keras 3. The mounted directory has `model.keras`, `tokenizer.json`, `config.json`, `charlist.txt`, `metadata.json`, and `licence.txt`. It must not contain a `.pb` file. The Dataverse directory stays unchanged and is not mounted. `provision_model` only downloads the Dataverse files. `check_model` checks the converted directory.

`config.json` has no top-level `channels` key. `args.channels` is 4 and `args.height` is 64. `args.use_mask` is true. `tokenizer.json` is already present, so the decoder does not write into the read-only mount. The service reports line height 64. The existing line worker resizes crops to that height before the request. The Loghi process resizes to height 64 again. The shim sends an 8-bit RGBA PNG so the fourth channel is present. Opaque alpha means the whole crop is the line.

`licence.txt` in the model directory is the single line `CC-BY-4.0`. The provision script writes it only after every Dataverse SHA-1 matches. Weights stay outside git and outside the image.

The upstream API is `POST /predict` with multipart fields `image`, `group_id`, and `identifier`. It answers 202. Text is read from `GET /status/{identifier}/`. A duplicate identifier would overwrite that status, so each line in a request gets a new identifier. The group directory under the output path is removed after the request. A line that finishes is returned with its text. A line that errors is empty text with warning `unrecognized`. A line that is still unfinished at the deadline is empty text with warning `timeout`. Confidence outside 0 to 1 is stored as null. That number is not a character-error rate.

Upstream API security defaults off. This service does not publish the Loghi port. The Loghi process binds `127.0.0.1:5000` inside the container. The contract server is the only listener on `0.0.0.0:8769`, and Compose gives it no host port. Do not put credentials in logs. The Loghi process log is `/work/loghi-api.log` and the entrypoint does not print it.

`LOGHI_GPUS=-1` selects CPU in the current Loghi source. The container also sets `CUDA_VISIBLE_DEVICES` empty. Do not start a GPU copy while another job is using the GPU.

## Run

```bash
python -m ai.external_engines.loghi.provision_model /absolute/path/globalise-2023-08
python -m ai.external_engines.loghi.check_model /absolute/path/globalise-2023-08-runtime
```

`check_model` prints `OK` or `FAILED:` plus a fixed code. It does not print the path, does not import TensorFlow, and does not predict.

From `escriptorium/`:

```bash
export LOGHI_MODEL_DIR=/absolute/path/globalise-2023-08-runtime
docker compose -f docker-compose.loghi.yml build
docker compose -f docker-compose.loghi.yml up -d
```

`docker compose up` does not read that file. The base image is `python:3.11-slim-bookworm` digest `sha256:94a8fee35252d06ab75856f15f12c7bae67b10e8a6da93c7fded82b2610926cd`. Loghi's README requires Python above 3.9 and at most 3.11 for its TensorFlow pin. This image installs CPU `tensorflow==2.19.0`, not `tensorflow[and-cuda]`. Dependency hashes are in `requirements.lock`.

Create one enabled research engine row named Loghi. Set the endpoint to `http://loghi.local:8769`, timeout 1600, experimental true, and metadata `{}`. The model list then shows `globalise-2023-08` with licence `CC-BY-4.0` and line height 64. Select pages that already have line masks, start External HTR, and import into a new layer. The layer stamp is `external-htr:loghi:globalise-2023-08`. Import leaves the review gate raw. The layer stays training-ineligible until the existing review control is used. A second import does not write another layer. A failure stays on Loghi. There is no fallback.

The images page already lists a saved numeric row. A fresh checkout shows a disabled Loghi placeholder when no row exists. That placeholder does not request models.

## Rollback

Stop and remove only the Loghi container that this deploy created. Do not stop Kraken, PyLaia, HTRflow, SATRN, Calamari, the database, Redis, nginx, or the original eScriptorium stack. Deleting the Loghi engine row hides the menu entry. Leave the row if it already has runs.
