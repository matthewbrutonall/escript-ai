"""Skeleton PyLaia engine.

This speaks the external HTR routes and does not recognize text. PyLaia is
not imported. There is no model list because no recognition backend is
installed. A valid recognize call returns ``unavailable``.
"""
from __future__ import annotations

import json

from ai.htr_engine_contract import (
    ERROR_HTTP_STATUS,
    PATH_CAPABILITIES,
    PATH_MODELS,
    PATH_RECOGNIZE,
    ContractError,
    check_preprocessing,
    parse_capabilities,
    parse_error,
    parse_model_list,
    parse_recognize_request,
)

ENGINE_NAME = "pylaia"
_MAX_BODY = 1_048_576

_CAPABILITIES = {
    "api_version": "1",
    "engine": ENGINE_NAME,
    "tier": "research",
    "tasks": ["recognize_lines"],
    "accepts": ["image/png"],
    "image_transport": ["base64"],
    "max_lines_per_request": 32,
    "preprocessing_supported": [],
    "params_accepted": [],
    "reports": ["timing_ms", "confidence", "warnings"],
}

_MODELS = {
    "api_version": "1",
    "engine": ENGINE_NAME,
    "models": [],
}

_MESSAGES = {
    "invalid_request": "request was not valid",
    "unsupported_preprocessing": "preprocessing is not supported",
    "model_not_found": "model was not found",
    "line_failed": "line was not recognized",
    "busy": "engine is busy",
    "unavailable": "recognition backend is not installed",
    "internal": "pylaia wrapper failed",
}


def handle(method: str, path: str, body: bytes | None = None) -> tuple[int, dict]:
    """Return ``(http_status, json_object)`` for one contract call.

    This never returns recognized text.
    """
    if not isinstance(method, str) or not isinstance(path, str):
        return _error("invalid_request")
    if "?" in path or "#" in path:
        return _error("invalid_request")
    route = path.rstrip("/") or "/"
    verb = method.upper()
    try:
        if verb == "GET" and route == PATH_CAPABILITIES:
            return _ok(parse_capabilities, _CAPABILITIES)
        if verb == "GET" and route == PATH_MODELS:
            return _ok(parse_model_list, _MODELS)
        if verb == "GET" and route.startswith(PATH_MODELS + "/"):
            return _error("model_not_found")
        if verb == "POST" and route == PATH_RECOGNIZE:
            return _recognize(body)
    except ContractError as exc:
        return _error(exc.code)
    return _error("invalid_request")


def _recognize(body: bytes | None):
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
    check_preprocessing(request.preprocessing, parse_capabilities(_CAPABILITIES))
    return _error("unavailable")


def _ok(parser, payload: dict) -> tuple[int, dict]:
    parser(payload)
    return 200, payload


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
