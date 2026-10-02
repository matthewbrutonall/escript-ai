"""Read-only client for an external HTR engine.

Calls the capabilities and model routes. Transcription jobs do not import
this module. The recognition route is not called.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from ai.htr_engine_contract import (
    PATH_CAPABILITIES,
    PATH_MODEL,
    PATH_MODELS,
    ContractError,
    parse_capabilities,
    parse_error,
    parse_model,
    parse_model_list,
)

_MAX_BODY = 1_048_576


class EngineClientError(Exception):
    """A failure before a contract payload could be trusted.

    ``code`` is ``disabled``, ``timeout``, ``unavailable``,
    ``invalid_request``, ``invalid_response``, or ``http_error``.
    The message never includes a response body or a URL query.
    """

    def __init__(self, code: str, message: str, *, status: int | None = None):
        self.code = code
        self.status = status
        super().__init__(message)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise EngineClientError(
            "invalid_response",
            "engine redirect was not followed",
            status=code,
        )


def join_engine_url(endpoint_url: str, path: str) -> str:
    """Join a base URL with a contract path.

    A trailing slash on the base is ignored. Query, fragment, and userinfo
    are rejected so a key cannot ride along in the URL.
    """
    if not isinstance(endpoint_url, str) or not isinstance(path, str):
        raise EngineClientError("invalid_request", "endpoint_url must be an http(s) base URL")
    parts = urllib.parse.urlsplit(endpoint_url.strip())
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or not path.startswith("/")
    ):
        raise EngineClientError(
            "invalid_request",
            "endpoint_url must be an http(s) base URL without userinfo, query, or fragment",
        )
    base_path = parts.path.rstrip("/")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, base_path + path, "", ""))


def fetch_capabilities(config):
    return parse_capabilities(_get_json(config, PATH_CAPABILITIES))


def fetch_models(config):
    return parse_model_list(_get_json(config, PATH_MODELS))


def fetch_model(config, model_id: str):
    _ensure_enabled(config)
    if not isinstance(model_id, str) or not model_id or model_id != model_id.strip():
        raise EngineClientError("invalid_request", "model_id is required")
    path = PATH_MODEL.format(model_id=urllib.parse.quote(model_id, safe=""))
    parsed = parse_model(_get_json(config, path))
    if parsed.model_id != model_id:
        raise EngineClientError("invalid_response", "engine returned a different model_id")
    return parsed


def _get_json(config, path: str):
    _ensure_enabled(config)
    url = join_engine_url(_endpoint(config), path)
    status, body = _request(url, _timeout(config))
    if status == 200:
        return _json_body(body)
    parsed = _error_payload(body)
    if parsed is not None:
        raise ContractError(parsed.code, parsed.message, line_id=parsed.line_id)
    raise EngineClientError("http_error", f"engine returned HTTP {status}", status=status)


def _ensure_enabled(config):
    if getattr(config, "enabled", False) is not True:
        raise EngineClientError("disabled", "engine is disabled")


def _endpoint(config) -> str:
    value = getattr(config, "endpoint_url", None)
    if not isinstance(value, str) or not value.strip():
        raise EngineClientError("invalid_request", "endpoint_url is required")
    return value


def _timeout(config) -> int:
    value = getattr(config, "timeout_seconds", None)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise EngineClientError("invalid_request", "timeout_seconds must be a positive integer")
    return value


def _json_body(body: bytes):
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise EngineClientError("invalid_response", "engine response was not UTF-8 JSON") from None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise EngineClientError("invalid_response", "engine response was not JSON") from None


def _error_payload(body: bytes):
    if not body:
        return None
    try:
        payload = _json_body(body)
    except EngineClientError:
        return None
    try:
        return parse_error(payload)
    except ContractError:
        return None


def _request(url: str, timeout: int) -> tuple[int, bytes]:
    request = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirect(),
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, _read_limited(response)
    except EngineClientError:
        raise
    except urllib.error.HTTPError as exc:
        return exc.code, _read_limited(exc)
    except TimeoutError:
        raise EngineClientError("timeout", "engine request timed out") from None
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise EngineClientError("timeout", "engine request timed out") from None
        raise EngineClientError("unavailable", "engine request failed") from None


def _read_limited(response) -> bytes:
    body = response.read(_MAX_BODY + 1)
    if len(body) > _MAX_BODY:
        raise EngineClientError("invalid_response", "engine response is too large")
    return body
