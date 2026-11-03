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

A line with a polygon, or a usable bounding box, keeps its text. One `String` is kept as its `CONTENT`. HTRflow 0.2.6 `WordLevelTrOCR` writes one `String` per piece of the decoded line split on a space, and writes an empty piece as a single space. It does not write a space element. Two or more strings are joined by reversing that split, in document order. The parser does not sort them by `HPOS` and does not concatenate their contents. Empty text, and a line with geometry but no `String`, stays included. A line with no usable geometry is skipped with `no_geometry` and its text is not kept. A mask needs at least three points. A baseline needs two; when ALTO does not give one, the baseline is the bottom edge of the bounding box.

A page whose `WIDTH` or `HEIGHT` is missing, not a number, or not positive is refused. No lines from that page are stored. A region or line whose polygon or baseline has a point outside that ALTO rectangle, edges included, is not stored and is not imported. Its text is not kept.

Those checks use the ALTO page rectangle. The checked page coordinates matched the source image, so the parser does not rescale them. On that page the four image corners and a blank point away from the text fall outside every polygon. Each polygon contains ink, and no polygon point falls outside the image. A future response that uses another coordinate space is not rescaled by Escript AI. A point can then lie inside the ALTO page and still fall outside the stored image.

`plan_htrflow_import` still does not create a line, open a network connection, or write a transcription. A line id that is not already on the page is skipped. The page importer is separate from the line-only external HTR import. When no engine line id matches a line on that page, it creates the regions and lines in one transaction and stores the engine line id on `Line.external_id`. When some ids match, it writes text only onto those lines and does not create a second set. The new layer starts behind the raw review gate and is not marked training-eligible.

`HTRFLOW_INTEGRATED` is true. The checked browser run selected the engine, recognised one page, imported a new layer, and left that layer raw and training-ineligible. This is not a character-error rate, and it is not a deployment to the public site.

Calamari is listed as a line-recognition choice and is integrated. Loghi, TrOCR, DRetHTR, and Laypa remain future candidates without adapters. The HTRflow page contract is unchanged.

## SATRN page pipeline

`satrn-riksarkivet` is a separate HTRflow page pipeline. It uses the `Satrn` adapter in HTRflow 0.2.6 and the model repository `Riksarkivet/satrn_htr` at revision `a40c7093232eaa47a83ce6469fc4abd033486bdc`. The weights at that revision are MIT. It is not a line adapter, and it does not replace `handwriting-english-large` or the other TrOCR page pipelines.

The images page lists it only when `GET /v1/page-models` returns that `model_id`. A missing service, a missing model file, or any reported beam width other than the integer 1 leaves it unavailable. The worker calls only the selected id. It does not fall back to TrOCR. A successful import stamps `external-htr:htrflow:satrn-riksarkivet`, leaves the new layer raw, and does not mark it training-eligible. This note does not state a character-error rate. The operator page service is not part of this repository.

## WordLevelTrOCR page pipeline

`wordlevel-trocr-iam` is another HTRflow 0.2.6 page pipeline on the same page contract. The recognition step uses the documented `WordLevelTrOCR` adapter. It is not a standalone engine, and it does not replace `handwriting-english-large`, the other TrOCR page pipelines, or `satrn-riksarkivet`. A standalone TrOCR line adapter is still a future candidate.

The adapter estimates word boundaries from TrOCR attention and does not support beam search. The pipeline does not pass `num_beams` or `batch_size` in that step's generation settings. It does set `attn_implementation: eager` and `max_new_tokens: 128`. The installed transformers 5 runtime names the image processor `image_processor` and decodes a one-dimensional token-id sequence as one line. The page service restores `feature_extractor` and one string per token before the adapter runs. It does not switch to another recognizer. The page request still accepts only integer `num_beams` 1. Any other width is refused before the image is opened. The worker calls only the selected id and does not fall back to line-level TrOCR or to SATRN.

The checkpoint is `microsoft/trocr-base-handwritten` at revision `eaacaf452b06415df8f10bb6fad3a4c11e609406`. The README front matter at that revision says `license: mit`. The model card describes a base TrOCR fine-tuned on the IAM dataset, which is modern English handwriting, and its intended use is recognition of a single text line. It is not a Swedish historical model and it is not evidence for other scripts or periods. `microsoft/trocr-large-handwritten` is not used here: its model card does not state a licence. `Riksarkivet/trocr-base-handwritten-hist-swe-2` is Apache-2.0 and is Swedish historical handwriting. It is not offered as this option.

Region and line segmentation stay on the existing YOLO checkpoints `Riksarkivet/yolov9-regions-1` revision `9e1a97431764db9b20e5c2afdfed2f6d36e3b456` and `Riksarkivet/yolov9-lines-within-regions-1` revision `1dda6137ce6cc4f9768c79d5625d007c24a0f8df`, both AGPL-3.0. HTRflow 0.2.6 is EUPL-1.2. The option stays unavailable when the checkpoint is missing. The images page lists it only when `GET /v1/page-models` returns `wordlevel-trocr-iam`, with the display name `WordLevelTrOCR (English handwriting, IAM)`.

ALTO keeps one `TextLine` per detected line. Each word is a `String` on that line, in decoder order, with a box in page pixels. The line transcription is the reconstructed line, not one editor line per word. A second import of the same run does not write another layer. The new layer starts behind the raw review gate and is not marked training-eligible. This note does not state a character-error rate. The operator page service is not part of this repository. The Calamari fresh-clone runtime and model packaging task is separate and is not part of this pipeline.
