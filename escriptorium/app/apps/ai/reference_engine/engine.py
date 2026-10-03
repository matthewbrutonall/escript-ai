"""In-process fake of the external HTR routes.

This is a contract test double. It returns ``LINE {line_id}`` and does not
read images. It is not a recognizer, it does not listen on a socket, and
transcription jobs do not import it.
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
    parse_model,
    parse_model_list,
    parse_recognize_request,
    parse_recognize_response,
)

ENGINE_NAME = "reference"
MODEL_ID = "reference-line"
MODEL_VERSION = "1"
MAX_LINES = 32
_MAX_BODY = 1_048_576

_CAPABILITIES = {
    "api_version": "1",
    "engine": ENGINE_NAME,
    "tier": "research",
    "tasks": ["recognize_lines"],
    "accepts": ["image/png"],
    "image_transport": ["base64"],
    "max_lines_per_request": MAX_LINES,
    "preprocessing_supported": [
        "line_height",
        "deslant",
        "grayscale",
        "preserve_aspect",
    ],
    "params_accepted": [],
    "reports": ["timing_ms", "confidence", "warnings"],
}

_MODEL = {
    "api_version": "1",
    "engine": ENGINE_NAME,
    "model_id": MODEL_ID,
    "model_version": MODEL_VERSION,
    "display_name": "Reference",
    "licence": "test-only",
    "source": "local:reference",
    "scripts": [],
    "languages": [],
    "input": {"line_height": 64},
    "alphabet_note": "Not a recognizer. Returns LINE {line_id}.",
    "experimental": True,
}

_MESSAGES = {
    "invalid_request": "request was not valid",
    "unsupported_preprocessing": "preprocessing is not supported",
    "model_not_found": "model was not found",
    "line_failed": "line was not recognized",
    "busy": "engine is busy",
    "unavailable": "engine is unavailable",
    "internal": "reference engine failed",
}


def handle(method: str, path: str, body: bytes | None = None) -> tuple[int, dict]:
    """Return ``(http_status, json_object)`` for one contract call."""
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
            payload = {"api_version": "1", "engine": ENGINE_NAME, "models": [_MODEL]}
            return _ok(parse_model_list, payload)
        if verb == "GET" and route.startswith(PATH_MODELS + "/"):
            return _model_detail(route[len(PATH_MODELS) + 1:])
        if verb == "POST" and route == PATH_RECOGNIZE:
            return _recognize(body)
    except ContractError as exc:
        return _error(exc.code)
    if verb not in {"GET", "POST"} or route in {PATH_CAPABILITIES, PATH_MODELS, PATH_RECOGNIZE}:
        return _error("invalid_request")
    return _error("invalid_request")


def _model_detail(model_id: str):
    if model_id != MODEL_ID:
        return _error("model_not_found")
    return _ok(parse_model, _MODEL)


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
    if request.engine != ENGINE_NAME or request.model_id != MODEL_ID:
        return _error("model_not_found")
    if len(request.lines) > MAX_LINES:
        return _error("invalid_request")
    check_preprocessing(request.preprocessing, parse_capabilities(_CAPABILITIES))
    response = {
        "api_version": "1",
        "job_id": request.job_id,
        "engine": request.engine,
        "model_id": request.model_id,
        "model_version": MODEL_VERSION,
        "results": [
            {
                "line_id": line.line_id,
                "text": f"LINE {line.line_id}",
                "confidence": None,
                "timing_ms": 0,
                "warnings": ["reference engine ignored the image"],
            }
            for line in request.lines
        ],
        "provenance": {
            "engine": request.engine,
            "model_id": request.model_id,
            "model_version": MODEL_VERSION,
            "api_version": "1",
        },
        "timing_ms": 0,
        "resources": {"device": "cpu"},
    }
    parse_recognize_response(response, request)
    return 200, response


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
