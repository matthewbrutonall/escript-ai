"""Plan one external HTR recognize call without sending it.

The plan uses ``build_recognize_request``. It does not call an engine,
read an endpoint, or write a transcription. Transcription jobs do not
import this module.
"""
from __future__ import annotations

from dataclasses import dataclass

from ai.external_htr_request import SkippedLine, build_recognize_request
from ai.htr_engine_contract import ContractError

CODE_DISABLED = "disabled"
CODE_INVALID = "invalid_request"
CODE_NOTHING = "nothing_to_send"

MESSAGE_DISABLED = "engine config is disabled"
MESSAGE_INVALID = "request could not be built"
MESSAGE_NOTHING = "nothing to send"
MESSAGE_READY = "request would be sent"

_PLAN_JOB = "plan"


@dataclass(frozen=True)
class ExternalHtrPlan:
    """What a later caller could send. ``message`` is a fixed sentence."""

    would_send: bool
    request: dict | None
    included_count: int
    skipped: tuple[SkippedLine, ...]
    engine: str
    config_name: str
    model_id: str
    code: str | None
    message: str


def plan_external_htr(
    config,
    lines,
    *,
    document_id,
    part_id,
    engine: str,
    model_id: str,
    encode_line,
    preprocessing: dict | None = None,
    max_lines: int | None = None,
    job_id: str = _PLAN_JOB,
) -> ExternalHtrPlan:
    """Describe a recognize request. A disabled config is not encoded.

    ``engine`` is the contract engine name. ``config.name`` is the stored
    row name. Document titles, paths, metadata, and the endpoint are not
    read. ``job_id`` defaults to a placeholder because this helper does
    not create a job.
    """
    config_name = _label(getattr(config, "name", None))
    safe_engine = _label(engine)
    safe_model = _label(model_id)
    if getattr(config, "enabled", False) is not True:
        return _result(
            False, None, (), safe_engine, config_name, safe_model,
            CODE_DISABLED, MESSAGE_DISABLED,
        )
    try:
        built = build_recognize_request(
            lines,
            job_id=job_id,
            document_id=document_id,
            part_id=part_id,
            engine=engine,
            model_id=model_id,
            encode_line=encode_line,
            preprocessing=preprocessing,
            max_lines=max_lines,
        )
    except ContractError:
        return _result(
            False, None, (), safe_engine, config_name, safe_model,
            CODE_INVALID, MESSAGE_INVALID,
        )
    if built.request is None:
        return _result(
            False, None, built.skipped, safe_engine, config_name, safe_model,
            CODE_NOTHING, MESSAGE_NOTHING,
        )
    included = built.request["lines"]
    return _result(
        True, built.request, built.skipped, engine, config_name, model_id,
        None, MESSAGE_READY, included_count=len(included),
    )


def _result(
    would_send, request, skipped, engine, config_name, model_id, code, message,
    included_count=0,
) -> ExternalHtrPlan:
    return ExternalHtrPlan(
        would_send=would_send,
        request=request,
        included_count=included_count,
        skipped=tuple(skipped),
        engine=engine,
        config_name=config_name,
        model_id=model_id,
        code=code,
        message=message,
    )


def _label(value) -> str:
    if isinstance(value, str) and value.strip() and value == value.strip():
        return value
    return ""
