"""Run one external HTR live check from a supplied request file.

The operator command calls this. The request is already built. This module
does not open a document image, store an audit row, or write a transcription.
The returned line is one fixed sentence.
"""
from __future__ import annotations

from ai.external_htr_live import run_external_htr_live

CODE_CONFIG = "config_not_found"
CODE_DISABLED = "disabled"
CODE_REQUEST = "request_unavailable"
CODE_INVALID = "invalid_request"
CODE_INTERNAL = "internal"

_FAILED = "FAILED: external HTR live check "
_ALNUM = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
_LABEL = _ALNUM | frozenset("._-")


class ConfigNotFound(Exception):
    """The stored engine row does not exist."""


class RequestUnavailable(Exception):
    """The request file cannot be read."""


class RequestInvalid(Exception):
    """The request file is not a JSON object."""


def execute_external_htr_live_check(
    config_id,
    request_path,
    *,
    fetch_config,
    read_request,
    run=run_external_htr_live,
) -> tuple[int, str]:
    """Load one config, read one request, and return one line.

    A missing or disabled config returns before the request is read and
    before ``run`` is called. The line does not include the service
    address, stored options, the request path, images, or exception text.
    """
    try:
        config = fetch_config(config_id)
    except ConfigNotFound:
        return _fail(CODE_CONFIG)
    except Exception:
        return _fail(CODE_INTERNAL)
    if config is None:
        return _fail(CODE_INTERNAL)
    if getattr(config, "enabled", None) is not True:
        return _fail(CODE_DISABLED)
    try:
        request = read_request(request_path)
    except RequestUnavailable:
        return _fail(CODE_REQUEST)
    except RequestInvalid:
        return _fail(CODE_INVALID)
    except Exception:
        return _fail(CODE_INTERNAL)
    if not isinstance(request, dict):
        return _fail(CODE_INVALID)
    try:
        result = run(config, request)
    except Exception:
        return _fail(CODE_INTERNAL)
    return _summary(result)


def _summary(result) -> tuple[int, str]:
    if getattr(result, "ok", None) is not True:
        return _fail(_public_code(getattr(result, "code", None)))
    plan = getattr(result, "apply_plan", None)
    if getattr(plan, "would_write", None) is not True:
        return _fail(_public_code(getattr(plan, "code", None)))
    engine = _label(getattr(plan, "engine", None))
    model_id = _label(getattr(plan, "model_id", None))
    count = _count(getattr(plan, "result_count", None))
    if engine is None or model_id is None or count is None:
        return _fail(CODE_INVALID)
    return 0, f"OK: external HTR live check {engine}:{model_id} results={count}"


def _label(value):
    if not isinstance(value, str) or not value or len(value) > 128:
        return None
    if value[0] not in _ALNUM:
        return None
    if any(char not in _LABEL for char in value):
        return None
    return value


def _count(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _public_code(value) -> str:
    if (
        isinstance(value, str)
        and value
        and len(value) <= 64
        and value == value.lower()
        and value.replace("_", "").isalnum()
    ):
        return value
    return CODE_INTERNAL


def _fail(code: str) -> tuple[int, str]:
    return 1, _FAILED + code
