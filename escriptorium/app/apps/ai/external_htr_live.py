"""Call an external HTR engine with an already-built recognize request.

The caller supplies the request. This module does not crop an image,
store an audit row, or write a transcription. The result does not
include the response body.
"""
from __future__ import annotations

from dataclasses import dataclass

from ai.external_htr_apply_plan import ExternalHtrApplyPlan, plan_external_htr_apply
from ai.htr_engine_client import EngineClientError, recognize_lines
from ai.htr_engine_contract import ERROR_CODES, ContractError, RecognizeResponse

CODE_DISABLED = "disabled"
CODE_CLIENT = "invalid_response"
CODE_CONTRACT = "invalid_request"

MESSAGE_DISABLED = "engine config is disabled"
MESSAGE_CLIENT = "recognition request was not completed"
MESSAGE_CONTRACT = "recognition request was not accepted"
MESSAGE_APPLY = "response could not be applied"
MESSAGE_READY = "recognition response is ready"

_CLIENT_CODES = frozenset({
    "disabled",
    "timeout",
    "unavailable",
    "invalid_request",
    "invalid_response",
    "http_error",
})


@dataclass(frozen=True)
class ExternalHtrLiveResult:
    """Outcome of one recognition call. ``message`` is a fixed sentence."""

    ok: bool
    code: str | None
    message: str
    apply_plan: ExternalHtrApplyPlan | None


def run_external_htr_live(
    config,
    request,
    *,
    recognize_lines=recognize_lines,
    plan_apply=plan_external_htr_apply,
) -> ExternalHtrLiveResult:
    """Recognize one built request and describe how it would be stored.

    A config that is not exactly enabled is rejected before the client
    runs. Client and contract failures use a fixed sentence. A rejected
    apply plan is not returned.
    """
    if getattr(config, "enabled", None) is not True:
        return _fail(CODE_DISABLED, MESSAGE_DISABLED)
    try:
        response = recognize_lines(config, request)
    except EngineClientError as exc:
        return _fail(_known(getattr(exc, "code", None), _CLIENT_CODES, CODE_CLIENT), MESSAGE_CLIENT)
    except ContractError as exc:
        return _fail(_known(getattr(exc, "code", None), ERROR_CODES, CODE_CONTRACT), MESSAGE_CONTRACT)
    try:
        plan = plan_apply(request, _response_body(response))
    except ContractError as exc:
        return _fail(_known(getattr(exc, "code", None), ERROR_CODES, CODE_CONTRACT), MESSAGE_APPLY)
    if not isinstance(plan, ExternalHtrApplyPlan) or plan.would_write is not True:
        return _fail(_known(getattr(plan, "code", None), ERROR_CODES, CODE_CONTRACT), MESSAGE_APPLY)
    return ExternalHtrLiveResult(True, None, MESSAGE_READY, plan)


def _response_body(response):
    """The client returns a parsed response. Apply planning reads a payload."""
    if isinstance(response, dict) or not isinstance(response, RecognizeResponse):
        return response
    resources = {}
    device = getattr(response.resources, "device", None)
    if isinstance(device, str):
        resources["device"] = device
    return {
        "api_version": response.api_version,
        "job_id": response.job_id,
        "engine": response.engine,
        "model_id": response.model_id,
        "model_version": response.model_version,
        "results": [
            {
                "line_id": item.line_id,
                "text": item.text,
                "confidence": item.confidence,
                "timing_ms": item.timing_ms,
                "warnings": list(item.warnings),
            }
            for item in response.results
        ],
        "provenance": {
            "engine": response.provenance.engine,
            "model_id": response.provenance.model_id,
            "model_version": response.provenance.model_version,
            "api_version": response.provenance.api_version,
        },
        "timing_ms": response.timing_ms,
        "resources": resources,
    }


def _known(code, allowed, fallback: str) -> str:
    if isinstance(code, str) and code in allowed:
        return code
    return fallback


def _fail(code: str, message: str) -> ExternalHtrLiveResult:
    return ExternalHtrLiveResult(False, code, message, None)
