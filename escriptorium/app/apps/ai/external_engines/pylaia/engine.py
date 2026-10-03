"""Skeleton PyLaia routes.

Routes call a backend object. The default backend does not recognize text.
PyLaia is not imported.
"""
from __future__ import annotations

import json

from ai.external_engines.pylaia.backend import (
    ENGINE_NAME,
    BackendFailure,
    UnavailablePyLaiaBackend,
)
from ai.htr_engine_contract import (
    ERROR_HTTP_STATUS,
    PATH_CAPABILITIES,
    PATH_MODELS,
    PATH_RECOGNIZE,
    ContractError,
    check_preprocessing,
    parse_capabilities,
    parse_error,
    parse_model,
    parse_model_list,
    parse_recognize_request,
    parse_recognize_response,
)

_MAX_BODY = 1_048_576

_MESSAGES = {
    "invalid_request": "request was not valid",
    "unsupported_preprocessing": "preprocessing is not supported",
    "model_not_found": "model was not found",
    "line_failed": "line was not recognized",
    "busy": "engine is busy",
    "unavailable": "recognition backend is not installed",
    "internal": "pylaia wrapper failed",
}


def handle(method: str, path: str, body: bytes | None = None, backend=None) -> tuple[int, dict]:
    """Return ``(http_status, json_object)`` for one contract call.

    ``backend`` defaults to ``UnavailablePyLaiaBackend``, which has no models
    and does not return text.
    """
    backend = UnavailablePyLaiaBackend() if backend is None else backend
    if not isinstance(method, str) or not isinstance(path, str):
        return _error("invalid_request")
    if "?" in path or "#" in path:
        return _error("invalid_request")
    route = path.rstrip("/") or "/"
    verb = method.upper()
    try:
        if verb == "GET" and route == PATH_CAPABILITIES:
            return _payload(parse_capabilities, _call(backend.capabilities))
        if verb == "GET" and route == PATH_MODELS:
            return _payload(parse_model_list, _call(backend.list_models))
        if verb == "GET" and route.startswith(PATH_MODELS + "/"):
            model_id = route[len(PATH_MODELS) + 1:]
            return _payload(parse_model, _call(backend.get_model, model_id))
        if verb == "POST" and route == PATH_RECOGNIZE:
            return _recognize(body, backend)
    except ContractError as exc:
        return _error(exc.code)
    return _error("invalid_request")


def _recognize(body: bytes | None, backend):
    if body is None:
        body = b""
    if not isinstance(body, bytes) or len(body) > _MAX_BODY:
        return _error("invalid_request")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _error("invalid_request")
    request = parse_recognize_request(payload)
    if request.engine != ENGINE_NAME:
        return _error("model_not_found")
    caps_payload = _call(backend.capabilities)
    if not isinstance(caps_payload, dict):
        return _error("internal")
    caps = parse_capabilities(caps_payload)
    check_preprocessing(request.preprocessing, caps)
    outcome = _call(backend.recognize, request)
    if isinstance(outcome, BackendFailure):
        return _error(_safe_code(outcome.code))
    if not isinstance(outcome, dict):
        return _error("internal")
    parse_recognize_response(outcome, request)
    return 200, outcome


def _call(method, *args):
    try:
        return method(*args)
    except ContractError:
        raise
    except Exception:
        return BackendFailure("internal")


def _payload(parser, outcome) -> tuple[int, dict]:
    if isinstance(outcome, BackendFailure):
        return _error(_safe_code(outcome.code))
    if not isinstance(outcome, dict):
        return _error("internal")
    parser(outcome)
    return 200, outcome


def _safe_code(code: str) -> str:
    if code in _MESSAGES:
        return code
    return "internal"


def _error(code: str) -> tuple[int, dict]:
    body = {
        "api_version": "1",
        "error": {
            "code": code,
            "message": _MESSAGES[code],
            "retryable": code in {"busy", "unavailable"},
            "line_id": None,
        },
    }
    parse_error(body)
    return ERROR_HTTP_STATUS[code], body
