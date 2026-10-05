# PyLaia wrapper skeleton

This is the external-engine shape for a future PyLaia service. It is not a recognizer and not for production. PyLaia is not installed and this package does not import it. Transcription jobs do not use it. Do not deploy it as HTR.

`backend.py` is the seam a real recognizer will implement: `capabilities`, `list_models`, `get_model`, and `recognize`. The default `UnavailablePyLaiaBackend` does not load a model. The HTTP routes call that object and do not contain recognition code.

`DecodePyLaiaBackend` is not the default. A caller can pass that object to `handle`, and the server constructs it only when started with `--backend decode`. It keeps one configured model id and model directory, writes a temporary work directory, calls `prepare_decode`, then `DecodeRunner.run`, and builds a contract recognize response. The model id is a label, not a path. A layout or runner failure becomes a contract error code and does not include the image, the recognized text, stdout, stderr, or a path. The temporary directory is removed after the call. Listing the configured model does not claim accuracy, and it does not prove the model files are present; `recognize` checks those files.

`tier` is `research`. With the default backend, `GET /v1/models` returns an empty list because no recognition backend is installed. `GET /v1/models/{model_id}` returns `model_not_found`. A valid `POST /v1/recognize` returns `unavailable` with the fixed message "recognition backend is not installed". It does not return text. An invalid request returns a contract error and does not echo the image or the line text.

`python -m ai.external_engines.pylaia.server` serves that handler on `127.0.0.1` port `8766` for local checks. Omitting `--backend` selects `UnavailablePyLaiaBackend`. It refuses any non-loopback host unless `--allow-container-bind` is set, and that flag allows only `0.0.0.0`. The default Compose project and transcription jobs do not start it.

## Runtime

These notes describe the current Teklia package. This repository does not install it, does not depend on it, and does not recognize text.

The PyPI package is `pylaia`. The import name is `laia`. Current `pylaia` 1.1.2 requires Python `>=3.9,<3.11`. It pins `torch>=1.13,<1.14`, with `torchvision` and `torchaudio`. It also pins `pytorch-lightning==1.4.2`. That old Lightning stack is why Python 3.11/3.12 and PyTorch 2.x support should be treated as a major upstream or runtime change, not a simple version bump. The Escript AI app image is Python 3.12, so PyLaia must run in its own external engine container, not inside the main Escript AI Python environment. Escript AI should plan as if PyLaia requires a separate Python 3.10 / torch 1.13 engine container unless upstream changes. The main Escript AI image does not include that stack. A prototype Dockerfile for a separate image is described below. Nothing in this repository builds it.

PyLaia has no HTTP API. The decode entrypoint is `pylaia-htr-decode-ctc`. It reads image files plus `img_list.txt`, `syms.txt`, a model architecture pickle, and a checkpoint (`*.ckpt`). Hugging Face bundles such as `Teklia/pylaia-norhand-v1` contain `model`, `weights.ckpt`, and `syms.txt`. `Teklia/pylaia-huginmunin` redirects to that repository. CPU decode uses `--trainer.gpus 0`. Line-image height and colour must match the trained model. Known bundles are often 128 pixels high.

`python -m ai.external_engines.pylaia.check_model MODEL_DIR` checks that directory for `model`, `syms.txt`, and a checkpoint. It prints `OK` or `FAILED:` plus a fixed code. It does not print the path, does not import PyLaia, and does not run decode. `weights.ckpt` is preferred when it is present. Otherwise exactly one regular top-level `*.ckpt` file inside the directory is accepted. A symlink for `model`, `syms.txt`, or `weights.ckpt` that resolves outside the directory is rejected. Compose and the HTTP server do not start this command.

`decoder.py` checks the model directory, writes temporary PNG line images and `img_list.txt`, and builds the argument list for `pylaia-htr-decode-ctc`. `DecodeRunner` can run that list with `shell=False`. With `--decode.include_img_ids true`, each stdout line is `{image file name} {text}`, for example `0001.png hello`. Text may contain spaces. `0001.png ` (the file name, one space, and nothing after it) is a successful empty transcription. `0001.png` with no space is malformed and rejected. The runner maps those ids back to contract line order. A mismatch, a nonzero exit, or a timeout is a fixed error and does not include the text or stderr. The HTTP server does not call the runner directly. It constructs `DecodePyLaiaBackend` only for `--backend decode`, after `check_model_layout` accepts the directory.

## Model files

This repository does not include a PyLaia model bundle. Keep `model`, `syms.txt`, and checkpoint files outside git. Do not commit them.

A directory the checker can accept has these top-level names:

- `model`
- `weights.ckpt`, or exactly one other `*.ckpt` when `weights.ckpt` is absent
- `syms.txt`

Put the directory outside this repository, for example `/opt/escript-ai/models/pylaia/<model-name>` or `~/.cache/escript-ai/pylaia/<model-name>`. From `escriptorium/app/apps`, check it with:

```bash
python -m ai.external_engines.pylaia.check_model /opt/escript-ai/models/pylaia/<model-name>
```

That command prints `OK` or `FAILED:` plus a fixed code. It does not print the path, does not import PyLaia, and does not run decode.

`Teklia/pylaia-huginmunin` on Hugging Face redirects to `Teklia/pylaia-norhand-v1`. The model card for `Teklia/pylaia-norhand-v1` is labelled MIT. This note does not download it. Check the model licence before any hosted or client use. A public example is not permission to serve that model.

The future engine container should mount that directory read-only. The default Compose project does not mount it. A one-off smoke can mount the same directory for a single process check. `docker-compose.pylaia-smoke.yml` can mount it when that file is named with `-f`. Neither adds the mount to `docker-compose.yml`.

## Planned container

This is the planned shape of a separate engine container. Compose does not start it, and the main Escript AI image does not install PyLaia.

The container uses Python 3.10 and installs `pylaia==1.1.2`. That keeps the torch 1.13 / Lightning 1.4 stack isolated from the Escript AI app image.

The container entrypoint is an HTTP wrapper process. The wrapper receives contract JSON over HTTP. The intended decode flow is: contract request, temporary PNG line images, `img_list.txt`, `pylaia-htr-decode-ctc`, stdout, then a contract response. `decoder.py` can prepare the images, the list, and the argument list. The server still returns `unavailable` and does not call the program.

The model directory is mounted read-only. It should contain at minimum `model`, `weights.ckpt` or an equivalent checkpoint, and `syms.txt`. Optional language-model files may be supported later. They are not required for the first pass.

The first mode is CPU decode with `--trainer.gpus 0`. GPU mode can be added after the CPU wrapper works.

No API keys or secrets belong in this container. Model metadata and licence belong in Escript AI configuration or documentation.

A prototype Dockerfile is `escriptorium/app/apps/ai/external_engines/pylaia/Dockerfile`. Compose does not reference it, and it is not built by default. Do not treat that image as HTR. The default command inside it still returns `unavailable` and does not import PyLaia. That command does not pass `--backend decode`.

The intended later build context is `escriptorium/app/apps/ai`:

```bash
docker build -f external_engines/pylaia/Dockerfile -t escript-ai-pylaia-skeleton .
```

The image uses Python 3.10 and installs `pylaia==1.1.2`. It also installs `git`, because importing `laia` probes `git` and raises `TypeError` when `git` is absent. The skeleton server still does not import `laia`. It copies only the contract module and this skeleton package. It does not copy model files, and it does not copy `check_model.py`. A read-only model mount is future work for the image command, and the image does not read one yet. The smoke below passes that mount only on an explicit `docker run` or the optional smoke Compose file.

The prototype image command is `python -m ai.external_engines.pylaia.server --host 0.0.0.0 --port 8766 --allow-container-bind`. That exposes the fake skeleton on the Docker network only when the container is run. The unflagged default remains `127.0.0.1`. The flag allows only `0.0.0.0`. It is still not a recognizer, and the default Compose project does not start it.

## Server modes

The default command uses `UnavailablePyLaiaBackend`. No model directory is read. `GET /v1/models` is empty, and a valid recognize call returns `unavailable`.

```bash
python -m ai.external_engines.pylaia.server
```

`--backend unavailable` is the same default. Model flags with that mode are rejected. Nothing in the process environment selects decode mode.

`--backend decode` constructs `DecodePyLaiaBackend`. It requires `--model-dir`, `--model-id`, and `--work-root`. `--timeout` defaults to 30 seconds. Startup calls `check_model_layout` and exits before listening when the directory is not accepted. Failure text is a fixed message or `FAILED:` plus a fixed code. It does not include the model path. `--model-id` is a label, not a path.

Mount the model directory read-only. Mount the work root read-write, outside the model directory. The paths, model id, and image tag below are examples.

```bash
python -m ai.external_engines.pylaia.server \
  --backend decode \
  --model-dir /models/<model-name> \
  --model-id <model-name> \
  --work-root /tmp/pylaia-work \
  --timeout 180
```

A container keeps the unavailable image command unless this run replaces it. The model mount stays read-only. Paths, the model id, and the image tag are examples:

```bash
docker run --rm --network none \
  -v "/path/to/model:/models/<model-name>:ro" \
  -v "/tmp/pylaia-work:/work" \
  --entrypoint python \
  escript-ai-pylaia-skeleton:local \
  -m ai.external_engines.pylaia.server \
  --host 0.0.0.0 \
  --port 8766 \
  --allow-container-bind \
  --backend decode \
  --model-dir /models/<model-name> \
  --model-id <model-name> \
  --work-root /work \
  --timeout 180
```

That example does not publish a host port. The live HTTP smoke below does. If the image was built before this server, also mount the current `ai` package read-only at `/opt/engine/ai`, as in the one-line smoke section. The prototype Dockerfile copies `decoder.py`, which provides `check_model_layout`, and it does not copy `check_model.py`.

`--allow-container-bind` still allows only `0.0.0.0`. The default Compose project does not start this process. Escript AI transcription jobs do not call PyLaia.

## Live HTTP decode smoke

This is an optional local check that the skeleton server can answer HTTP when started with `--backend decode`. The result is process evidence that the service ran. It is not an accuracy result. This run replaces the image command for one container. The image command itself stays the unavailable server.

Keep the model bundle outside this repository. Do not commit `model`, `syms.txt`, or a checkpoint. The check reads a directory mounted from outside the image. The image does not contain that bundle.

If `escript-ai-pylaia-skeleton:local` was built before `--backend decode` existed, rebuild it from current main. From `escriptorium/app/apps/ai`:

```bash
docker build -f external_engines/pylaia/Dockerfile -t escript-ai-pylaia-skeleton:local .
```

The image tag, container name, and every host path below are examples. Publish the host port on `127.0.0.1` only. Mount the model directory read-only. Mount an empty temporary work directory read-write, outside the model directory. The process inside the container listens on `0.0.0.0` with `--allow-container-bind` so Docker can forward that published port. This smoke uses the default Docker network so the host can reach the port.

```bash
docker run -d --name pylaia-http-smoke \
  -p 127.0.0.1:8766:8766 \
  -v "/path/to/model:/models/<model-name>:ro" \
  -v "/tmp/pylaia-http-work:/work" \
  --entrypoint python \
  escript-ai-pylaia-skeleton:local \
  -m ai.external_engines.pylaia.server \
  --host 0.0.0.0 \
  --port 8766 \
  --allow-container-bind \
  --backend decode \
  --model-dir /models/<model-name> \
  --model-id <model-name> \
  --work-root /work \
  --timeout 180
```

`/path/to/model` is the bundle outside the repository. `/tmp/pylaia-http-work` is the empty temporary work directory. `<model-name>` is the `--model-id` label, not a file in this repository. `--timeout 180` is only for this smoke. The runner default remains 30 seconds. Startup still checks the model layout and exits before listening when the directory is not accepted.

From the host, call:

```bash
curl -sS http://127.0.0.1:8766/v1/capabilities
curl -sS http://127.0.0.1:8766/v1/models
curl -sS http://127.0.0.1:8766/v1/models/<model-name>
```

Then `POST /v1/recognize` with one synthetic grayscale line image. Do not use a user document. A successful response has one line. The text is process evidence that the HTTP path ran. It is not an accuracy result, so this note does not record it.

The server removes its own child directory under the work mount. Stop the container and remove it when the check is done:

```bash
docker stop pylaia-http-smoke
docker rm pylaia-http-smoke
```

The default Compose project is not wired to this container. Escript AI transcription jobs are not wired to PyLaia. The default server remains `UnavailablePyLaiaBackend` and is still not a recognizer.

## Optional Compose smoke

`escriptorium/docker-compose.pylaia-smoke.yml` repeats the live HTTP smoke. It is optional and local-only, and it is not loaded by default. `docker compose up` does not read it. Pass `-f docker-compose.pylaia-smoke.yml`. Its project name is `escript-ai-pylaia-smoke`, so it does not join the Escript AI services. Escript AI transcription jobs do not call it.

The service uses the image `escript-ai-pylaia-skeleton:local`. This file does not build that image. Build it from `escriptorium/app/apps/ai` when the local tag is missing, as in the section above. The image command stays the unavailable server. This file replaces that command for the smoke service only.

`PYLAIA_MODEL_DIR` is required. Point it at a model directory outside this repository. The file mounts that directory read-only at `/models/current`. Do not commit `model`, `syms.txt`, or a checkpoint. `PYLAIA_WORK_DIR` defaults to `/tmp/escript-ai-pylaia-work` and is mounted read-write at `/work`. Keep that directory outside the model directory. `PYLAIA_MODEL_ID` defaults to `huginmunin` and is only a label. `PYLAIA_TIMEOUT` defaults to `180` for this smoke. The runner default remains 30 seconds. The host port is `127.0.0.1:8766` only.

Compose interpolates `PYLAIA_MODEL_DIR` on every command, including `down`. Export it for the session and leave it set until the container is removed. From `escriptorium/`:

```bash
export PYLAIA_MODEL_DIR=/path/to/model
docker compose -f docker-compose.pylaia-smoke.yml up -d
```

`/path/to/model` is the bundle outside the repository. `-d` leaves the shell free for the same calls as the live HTTP smoke: `GET /v1/capabilities`, `GET /v1/models`, `GET /v1/models/<model-id>`, and `POST /v1/recognize` with one synthetic grayscale line. The text is process evidence that the HTTP path ran. It is not an accuracy result, so this note does not record it.

Stop and remove the smoke container when the check is done. `down` without `PYLAIA_MODEL_DIR` exits before it can remove the container:

```bash
docker compose -f docker-compose.pylaia-smoke.yml down
```

This optional path has been exercised with a model bundle kept outside the repository. `GET /v1/capabilities`, `GET /v1/models`, `GET /v1/models/<model-id>`, and `POST /v1/recognize` each returned a successful HTTP response. The recognize call used one synthetic grayscale line. Its text is process evidence only, not an accuracy result, so this note does not record it. `docker compose -f docker-compose.pylaia-smoke.yml down` removed the service while `PYLAIA_MODEL_DIR` stayed set. The default Compose project and Escript AI transcription jobs remain unwired. `/tmp/escript-ai-pylaia-work` may remain as an empty host bind directory. It is not a leftover decode directory, and it can be removed manually.

## Manual admin check

An admin can store a pointer to a smoke service that is already running and reachable. In Django admin, under **AI transcription**, open **External HTR engines** at `/admin/ai/externalhtrengineconfig/`. Saving the row does not call the service. It does not add a Transcribe option. Transcription jobs, views, and the UI do not read this table.

Suggested fields for a local test:

- `name`: `PyLaia smoke`
- `endpoint_url`: `http://127.0.0.1:8766`
- `tier`: `research`
- `experimental`: true
- `enabled`: true only while testing
- `timeout_seconds`: `180`
- `metadata`: `{}`

`endpoint_url` is the base URL only. Do not add a route, a query, or user info. `http://127.0.0.1:8766` is the host port published by the optional smoke file. Use it when `manage.py` runs on the host and can open that loopback port. The smoke service name `pylaia-smoke` is visible only to containers on a network that includes that service. The optional file is its own project, `escript-ai-pylaia-smoke`, and it does not join the app stack, so app containers cannot use that name today. The admin form also cannot save a single-label host such as `http://pylaia-smoke:8766`. Do not put that name in `endpoint_url`.

`enabled` must be true or the check does not call the service. The default is false. Transcription jobs do not read the flag. `timeout_seconds` is how long the client waits. `180` matches the smoke container's decode timeout. `metadata` must be a JSON object. Leave it empty. Do not store an API key or any other secret there. Keep the model bundle outside this repository. The row does not contain `model`, `syms.txt`, or a checkpoint, and it does not need the model path.

The admin action **Test connection** calls capabilities and the model list only. It does not recognize a line.

The full check is the management command. From `escriptorium/app`, with the smoke service up and `PYLAIA_MODEL_DIR` still set:

```bash
python manage.py check_external_htr_engine <config_id>
```

`<config_id>` is the numeric id of the row, not the name. The command calls `GET /v1/capabilities`, `GET /v1/models`, `GET /v1/models/<model_id>`, and one `POST /v1/recognize`. It stops at the first failure. A disabled row is not called. An empty model list stops before model detail and recognize. The default unavailable server does that. The smoke service in decode mode lists one model, so the check continues.

The recognize call sends one synthetic line. That image is not a user document and it is not a PNG. Decode mode rejects a non-PNG image, so the command can report a recognize failure while the smoke service is up. It prints one `OK:` or `FAILED:` line and does not print recognized text. This does not transcribe a document and does not add a Transcribe option. The default Compose project and Escript AI transcription jobs remain unwired.

## One-line decode smoke

This is an operator check that one synthetic line can pass through `DecodePyLaiaBackend`. The result is process evidence that the decode command ran. It is not an accuracy result. Compose does not run this check, and Escript AI transcription jobs do not call PyLaia.

Keep the model bundle outside this repository. Do not commit `model`, `syms.txt`, or a checkpoint. The bundle exercised by this check was `Teklia/pylaia-norhand-v1`. `Teklia/pylaia-huginmunin` redirects to that repository. The model card is labelled MIT. That label is not permission to serve the model.

Run the layout check on the host before the container, from `escriptorium/app/apps`. Continue only when it prints `OK`:

```bash
python -m ai.external_engines.pylaia.check_model /path/to/model
```

Use one synthetic grayscale line image. Do not use a user document. A successful run returns one parsed line. PyLaia writes that line as `{image file name} {text}`. The text is not an accuracy result, so this note does not record it.

The image tag and every path below are examples. Mount the model directory read-only. Mount an empty temporary work directory read-write. If the image was built before the current `ai` package, also mount that package read-only at `/opt/engine/ai`. The current Dockerfile copies `decoder.py` and does not copy `check_model.py`, so a layout check inside the container still needs this package mount. The host check above does not need the container. Omit the package mount only when the image already contains the same adapter files, including `check_model.py`.

The smoke replaces the image entrypoint for one command. The skeleton server still does not import `laia`. The same container can import `laia` in that one-off command, because the image installs `git`.

```bash
docker run --rm --network none \
  --entrypoint python \
  -v "/path/to/model:/models/<model-name>:ro" \
  -v "/tmp/pylaia-smoke-work:/work" \
  -v "/path/to/escriptorium/app/apps/ai:/opt/engine/ai:ro" \
  escript-ai-pylaia-skeleton:local \
  /work/smoke.py
```

`/path/to/model` is the bundle outside the repository. `/tmp/pylaia-smoke-work` is the empty temporary work directory. `/path/to/escriptorium/app/apps/ai` is the current package. `/work/smoke.py` is a one-off program placed in that work directory. This repository does not include that program. It should run `check_model` on the mounted model directory, build one synthetic line image, and call `DecodePyLaiaBackend` once. The adapter removes its own child directory under the work mount.

The runner timeout defaults to 30 seconds. Loading a checkpoint can take longer, so the one-off call may pass a longer timeout. That longer value is only for this smoke.

Compose is not wired to this container. Escript AI transcription jobs are not wired to PyLaia. The default server remains `UnavailablePyLaiaBackend` and is still not a recognizer.
