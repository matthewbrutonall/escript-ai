"""Client for an external HTR engine.

Calls the capabilities, model, and recognition routes. Transcription jobs
do not import this module. Nothing here retries a request.
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
    PATH_RECOGNIZE,
    ContractError,
    parse_capabilities,
    parse_error,
    parse_model,
    parse_model_list,
    parse_recognize_request,
    parse_recognize_response,
)

_MAX_BODY = 1_048_576
# Line images travel in the request. The response is text and stays at _MAX_BODY.
_MAX_REQUEST = 32 * 1024 * 1024


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


def probe_configs(configs, *, fetch_capabilities=None, fetch_models=None):
    """Check enabled engines and return admin message pairs.

    Each item is ``("success"|"warning"|"error", text)``. The text uses the
    config name, a status code, and on success the tier and model count.
    It does not include the endpoint, stored options, or a response body.
    Disabled rows are not fetched.
    """
    if fetch_capabilities is None:
        fetch_capabilities = globals()["fetch_capabilities"]
    if fetch_models is None:
        fetch_models = globals()["fetch_models"]
    return [
        _probe_one(config, fetch_capabilities, fetch_models)
        for config in configs
    ]


def _probe_one(config, fetch_capabilities, fetch_models):
    label = _probe_label(config)
    if getattr(config, "enabled", False) is not True:
        return ("warning", f"{label} is disabled and was not called")
    try:
        capabilities = fetch_capabilities(config)
        listing = fetch_models(config)
    except (EngineClientError, ContractError) as exc:
        code = getattr(exc, "code", None)
        if isinstance(code, str) and code.strip():
            return ("error", f"{label} was not checked ({code.strip()})")
        return ("error", f"{label} was not checked")
    except Exception:
        return ("error", f"{label} was not checked")
    tier = getattr(capabilities, "tier", None)
    models = getattr(listing, "models", None)
    if not isinstance(tier, str) or not isinstance(models, tuple):
        return ("error", f"{label} was not checked")
    return ("success", f"{label} responded ({tier}, {len(models)} models)")


def _probe_label(config) -> str:
    name = getattr(config, "name", None)
    if isinstance(name, str) and name.strip():
        return name.strip()
    return "engine"


def recognize_lines(config, payload):
    """POST one recognition request and return the parsed response.

    A disabled config is rejected before the body is encoded or sent.
    Error text does not include the image, the endpoint, or the response body.
    """
    _ensure_enabled(config)
    parsed_request = parse_recognize_request(payload)
    body = _encode_request(payload)
    url = join_engine_url(_endpoint(config), PATH_RECOGNIZE)
    status, raw = _request(url, _timeout(config), method="POST", data=body)
    if len(raw) > _MAX_BODY:
        raise EngineClientError("invalid_response", "engine response is too large")
    if status != 200:
        parsed = _error_payload(raw)
        if parsed is not None:
            raise ContractError(
                parsed.code,
                "engine rejected the recognition request",
                line_id=parsed.line_id,
            )
        raise EngineClientError("http_error", f"engine returned HTTP {status}", status=status)
    return parse_recognize_response(_json_body(raw), parsed_request)


def _encode_request(payload) -> bytes:
    try:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError):
        raise EngineClientError("invalid_request", "recognition request could not be encoded") from None
    if len(body) > _MAX_REQUEST:
        raise EngineClientError("invalid_request", "recognition request is too large")
    return body


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


def _request(url: str, timeout: int, *, method: str = "GET", data: bytes | None = None) -> tuple[int, bytes]:
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
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
