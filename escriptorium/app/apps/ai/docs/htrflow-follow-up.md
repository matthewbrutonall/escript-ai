# HTRflow follow-up

HTRflow page recognition is integrated into Escript AI. It is not deployed to the public site. This note does not state a character-error rate.

HTRflow is a page-level layout and recognition pipeline. It is not a line-only recognizer. The external line contract (`recognize_lines`, `POST /v1/recognize`) cannot safely represent what this installation returns: page segmentation, region polygons, line polygons, line text, and the model and version metadata for each step. That output must not be disguised as line-only recognition. `htr_engine_contract.py` stays limited to `recognize_lines`.

## Page contract

A caller that wants this output uses a separate page contract:

- `GET /v1/page-models` lists pipeline configurations. A `model_id` is a pipeline name, not a file path and not a single recognition-model id.
- `POST /v1/recognize-page` sends JSON with `model_id`, `num_beams` set to `1`, and the page image as base64. The response is ALTO XML, or JSON `{"alto": "<alto...>"}`.
- The page worker retries `busy` and `unavailable`, the same codes as the line worker, at most three times. It waits one second between attempts. An HTTP error is retried only when its status is 408, 429, or 500–599. Other 4xx responses are permanent and are not retried. A timeout, a redirect, and an unreadable body are not retried. The client itself does not retry.
- Recognized text is not part of the public job status. Geometry stored for import is polygons, region and line ids, page size, software version, and model metadata. It does not include the image, the text, a filename, or the endpoint.

This installation's recognition step uses `num_beams` 1. A model that reports any other beam width is refused before a page image is opened. The client always sends `num_beams` 1. It does not send a larger width.

Escript AI does not shell out to a command-line pipeline. An operator supplies the page endpoint. The app calls it only when the engine row's metadata is exactly `{"task": "recognize_page", "engine": "htrflow"}`. Any other metadata, including a URL alone, stays on the line-recognition path. Hosted and paid APIs stay disabled. A checked call returned ALTO whose page width and height were the source image's pixel size. The line polygons sat on the ink. One printed line had two overlapping polygons. That is the recognizer's layout, not a rescaling error.

## What the ALTO parser keeps

The parser reads ALTO, including the v4 namespace and documents with no namespace. It stamps `document_id` and `part_id` from the page Escript AI already trusts. ALTO does not supply those ids. A payload whose page identity does not match that page is refused and contributes no lines.

For each processing step whose settings contain `model`, the role is assigned in order: regions, then lines, then recognition. Settings are space-separated `key: value` pairs. The parser does not keep `fileName`.

A line with a polygon, or a usable bounding box, keeps its text. Empty text, and a line with geometry but no `String`, stays included. A line with no usable geometry is skipped with `no_geometry` and its text is not kept. A mask needs at least three points. A baseline needs two; when ALTO does not give one, the baseline is the bottom edge of the bounding box.

A page whose `WIDTH` or `HEIGHT` is missing, not a number, or not positive is refused. No lines from that page are stored. A region or line whose polygon or baseline has a point outside that ALTO rectangle, edges included, is not stored and is not imported. Its text is not kept.

Those checks use the ALTO page rectangle. The checked page coordinates matched the source image, so the parser does not rescale them. On that page the four image corners and a blank point away from the text fall outside every polygon. Each polygon contains ink, and no polygon point falls outside the image. A future response that uses another coordinate space is not rescaled by Escript AI. A point can then lie inside the ALTO page and still fall outside the stored image.

`plan_htrflow_import` still does not create a line, open a network connection, or write a transcription. A line id that is not already on the page is skipped. The page importer is separate from the line-only external HTR import. When no engine line id matches a line on that page, it creates the regions and lines in one transaction and stores the engine line id on `Line.external_id`. When some ids match, it writes text only onto those lines and does not create a second set. The new layer starts behind the raw review gate and is not marked training-eligible.

`HTRFLOW_INTEGRATED` is true. The checked browser run selected the engine, recognised one page, imported a new layer, and left that layer raw and training-ineligible. This is not a character-error rate, and it is not a deployment to the public site.

Calamari is listed as a line-recognition choice and is integrated. Loghi, TrOCR, DRetHTR, and Laypa remain future candidates without adapters. The HTRflow page contract is unchanged.

## SATRN page pipeline

`satrn-riksarkivet` is a separate HTRflow page pipeline. It uses the `Satrn` adapter in HTRflow 0.2.6 and the model repository `Riksarkivet/satrn_htr` at revision `a40c7093232eaa47a83ce6469fc4abd033486bdc`. The weights at that revision are MIT. It is not a line adapter, and it does not replace `handwriting-english-large` or the other TrOCR page pipelines.

The images page lists it only when `GET /v1/page-models` returns that `model_id`. A missing service, a missing model file, or any reported beam width other than the integer 1 leaves it unavailable. The worker calls only the selected id. It does not fall back to TrOCR. A successful import stamps `external-htr:htrflow:satrn-riksarkivet`, leaves the new layer raw, and does not mark it training-eligible. This note does not state a character-error rate. The operator page service is not part of this repository.
