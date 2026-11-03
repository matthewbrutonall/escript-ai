"""Page-level contract for an HTRflow endpoint.

The line contract cannot represent a page of regions, line geometry, and
line text. This module checks the page-model list and the beam width.
It does not open a network connection, read an image, or write a
transcription. HTRflow is not integrated into Escript AI.
"""
from __future__ import annotations

from dataclasses import dataclass

PATH_PAGE_MODELS = "/v1/page-models"
PATH_RECOGNIZE_PAGE = "/v1/recognize-page"
REQUIRED_BEAMS = 1
MAX_REQUEST_BYTES = 32 * 1024 * 1024
MAX_ALTO_BYTES = 4 * 1024 * 1024
MAX_IMAGE_BYTES = 20 * 1024 * 1024

CODE_BEAM = "beam_width_refused"
MESSAGE_BEAM = "model beam width was not accepted"
CODE_RESPONSE = "invalid_response"
MESSAGE_RESPONSE = "engine response could not be read"


class HtrflowContractError(Exception):
    """A page payload that cannot be trusted. The message is a fixed sentence."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class PageModel:
    """One pipeline configuration. ``model_id`` is not a file path."""

    model_id: str
    display_name: str
    licence: str
    experimental: bool
    num_beams: object
    beams_present: bool


@dataclass(frozen=True)
class PageModelList:
    """Models reported by ``GET /v1/page-models``."""

    engine: str
    models: tuple[PageModel, ...]


def beam_refusal(value):
    """Return a code when a reported beam width is not exactly the integer 1.

    ``None`` means the model did not report a width, which is allowed.
    A boolean is refused, including ``True``. A string is refused.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value != REQUIRED_BEAMS:
        return CODE_BEAM
    return None


def public_num_beams(model):
    """Value the images page can compare with ``1``.

    A missing width stays ``None``. A present width that is not an integer
    is reported as ``0`` so the page can refuse it before a run is queued.
    """
    if not getattr(model, "beams_present", False):
        return None
    value = getattr(model, "num_beams", None)
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value


def parse_page_model_list(payload) -> PageModelList:
    """Accept a page-model list. Does not call an endpoint."""
    if not isinstance(payload, dict):
        raise HtrflowContractError(CODE_RESPONSE, MESSAGE_RESPONSE)
    engine = payload.get("engine")
    if not isinstance(engine, str) or not engine or engine != engine.strip() or len(engine) > 256:
        raise HtrflowContractError(CODE_RESPONSE, MESSAGE_RESPONSE)
    raw_models = payload.get("models")
    if not isinstance(raw_models, list):
        raise HtrflowContractError(CODE_RESPONSE, MESSAGE_RESPONSE)
    models = []
    seen = set()
    for item in raw_models:
        if not isinstance(item, dict):
            raise HtrflowContractError(CODE_RESPONSE, MESSAGE_RESPONSE)
        model_id = item.get("model_id")
        if (
            not isinstance(model_id, str)
            or not model_id
            or model_id != model_id.strip()
            or len(model_id) > 256
            or model_id in seen
        ):
            raise HtrflowContractError(CODE_RESPONSE, MESSAGE_RESPONSE)
        seen.add(model_id)
        display = item.get("display_name", model_id)
        if not isinstance(display, str) or not display.strip():
            display = model_id
        licence = item.get("licence", "")
        if not isinstance(licence, str):
            licence = ""
        present = "num_beams" in item and item.get("num_beams") is not None
        models.append(PageModel(
            model_id=model_id,
            display_name=display.strip(),
            licence=licence,
            experimental=item.get("experimental", False) is True,
            num_beams=item.get("num_beams") if present else None,
            beams_present=present,
        ))
    return PageModelList(engine, tuple(models))
