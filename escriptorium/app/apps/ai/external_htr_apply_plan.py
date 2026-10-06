"""Plan how an external HTR response would be stored.

The plan parses a recognize response against its request. It does not call
an engine or write a transcription. Transcription jobs do not import it.
"""
from __future__ import annotations

from dataclasses import dataclass

from ai.htr_engine_contract import (
    ContractError,
    RecognizeRequest,
    parse_recognize_request,
    parse_recognize_response,
)

# The transcription version_source field holds 128 characters. This module
# does not import the write path.
VERSION_SOURCE_LIMIT = 128
LAYER_SOURCE_PREFIX = "external-htr"

CODE_INVALID = "invalid_request"
MESSAGE_INVALID = "response could not be applied"
MESSAGE_READY = "response would be written"


@dataclass(frozen=True)
class PlannedLine:
    """One line that a later writer could store. Text may be empty."""

    line_id: str
    text: str
    confidence: float | None
    timing_ms: int
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class ExternalHtrApplyPlan:
    """What a later writer could store. ``message`` is a fixed sentence."""

    would_write: bool
    code: str | None
    message: str
    layer_source: str
    engine: str
    model_id: str
    model_version: str
    api_version: str
    results: tuple[PlannedLine, ...]
    result_count: int
    warning_count: int
    empty_text_count: int


def plan_external_htr_apply(request, response) -> ExternalHtrApplyPlan:
    """Validate ``response`` against ``request`` and describe the lines.

    ``request`` is a recognize payload or a parsed request. A contract
    failure becomes a fixed result with no lines. The message does not
    include parser text, images, or document metadata.
    """
    try:
        parsed_request = _request(request)
        parsed = parse_recognize_response(response, parsed_request)
    except ContractError as exc:
        return _rejected(exc.code)
    by_id = {item.line_id: item for item in parsed.results}
    lines = tuple(
        PlannedLine(
            line_id=item.line_id,
            text=by_id[item.line_id].text,
            confidence=by_id[item.line_id].confidence,
            timing_ms=by_id[item.line_id].timing_ms,
            warnings=by_id[item.line_id].warnings,
        )
        for item in parsed_request.lines
    )
    warning_count = sum(len(item.warnings) for item in lines)
    empty_text_count = sum(item.text == "" for item in lines)
    return ExternalHtrApplyPlan(
        would_write=True,
        code=None,
        message=MESSAGE_READY,
        layer_source=_layer_source(parsed.engine, parsed.model_id),
        engine=parsed.engine,
        model_id=parsed.model_id,
        model_version=parsed.model_version,
        api_version=parsed.api_version,
        results=lines,
        result_count=len(lines),
        warning_count=warning_count,
        empty_text_count=empty_text_count,
    )


def _request(value) -> RecognizeRequest:
    if isinstance(value, RecognizeRequest):
        return value
    return parse_recognize_request(value)


def _layer_source(engine: str, model_id: str) -> str:
    return f"{LAYER_SOURCE_PREFIX}:{engine}:{model_id}"[:VERSION_SOURCE_LIMIT]


def _rejected(code: str) -> ExternalHtrApplyPlan:
    return ExternalHtrApplyPlan(
        would_write=False,
        code=code if code else CODE_INVALID,
        message=MESSAGE_INVALID,
        layer_source="",
        engine="",
        model_id="",
        model_version="",
        api_version="",
        results=(),
        result_count=0,
        warning_count=0,
        empty_text_count=0,
    )
