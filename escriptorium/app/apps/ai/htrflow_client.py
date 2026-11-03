"""HTTP client for the HTRflow page contract.

Calls ``GET /v1/page-models`` and ``POST /v1/recognize-page``. It does not
retry, does not follow a redirect, and does not read ``metadata``. Tests
replace ``urllib.request.urlopen``. HTRflow is not integrated into Escript AI.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from ai.htr_engine_client import join_engine_url
from ai.htrflow_contract import (
    MAX_ALTO_BYTES,
    MAX_IMAGE_BYTES,
    MAX_REQUEST_BYTES,
    PATH_PAGE_MODELS,
    PATH_RECOGNIZE_PAGE,
    REQUIRED_BEAMS,
    HtrflowContractError,
    parse_page_model_list,
)


class HtrflowClientError(Exception):
    """A page call that did not return ALTO. The message includes no body or URL."""

    def __init__(self, code: str, message: str, *, status: int | None = None):
        self.code = code
        self.status = status
        super().__init__(message)


_RETRY_CODES = frozenset({"busy", "unavailable"})
_TRANSIENT_STATUS = frozenset({408, 429})


def retryable_failure(exc) -> bool:
    """True when the page worker should try the same call again.

    Named codes match the line worker: ``busy`` and ``unavailable``.
    An HTTP error is transient only when its status is 408, 429, or
    500–599. Other 4xx statuses are permanent. A missing status is not
    retried. This function does not wait or send a request.
    """
    code = getattr(exc, "code", None)
    if code in _RETRY_CODES:
        return True
    if code != "http_error":
        return False
    status = getattr(exc, "status", None)
    if isinstance(status, bool) or not isinstance(status, int):
        return False
    if status in _TRANSIENT_STATUS:
        return True
    return 500 <= status <= 599


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HtrflowClientError("invalid_response", "engine redirect was not followed")


def fetch_page_models(config):
    """Return the page-model list. A disabled config is not called."""
    _ensure_enabled(config)
    status, raw = _request(_url(config, PATH_PAGE_MODELS), _timeout(config))
    if status != 200:
        _raise_status(status)
    try:
        return parse_page_model_list(_json_object(raw))
    except HtrflowContractError:
        raise HtrflowClientError("invalid_response", "engine response could not be read") from None


def recognize_page(config, *, model_id, image, num_beams=REQUIRED_BEAMS):
    """POST one page and return the ALTO text.

    ``num_beams`` must be the integer 1. Any other value is refused before
    the image is encoded.
    """
    _ensure_enabled(config)
    if isinstance(num_beams, bool) or num_beams != REQUIRED_BEAMS:
        raise HtrflowClientError("beam_width_refused", "model beam width was not accepted")
    if not isinstance(model_id, str) or not model_id or model_id != model_id.strip() or len(model_id) > 256:
        raise HtrflowClientError("invalid_request", "model_id is required")
    if not isinstance(image, (bytes, bytearray)) or not image or len(image) > MAX_IMAGE_BYTES:
        raise HtrflowClientError("invalid_request", "page image was not accepted")
    body = _encode(model_id, bytes(image))
    status, raw = _request(
        _url(config, PATH_RECOGNIZE_PAGE), _timeout(config), data=body,
    )
    if status != 200:
        _raise_status(status)
    return _alto_text(raw)


def _encode(model_id, image) -> bytes:
    import base64

    payload = {
        "model_id": model_id,
        "num_beams": REQUIRED_BEAMS,
        "image": base64.standard_b64encode(image).decode("ascii"),
    }
    try:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError):
        raise HtrflowClientError("invalid_request", "recognition request could not be encoded") from None
    if len(body) > MAX_REQUEST_BYTES:
        raise HtrflowClientError("invalid_request", "recognition request is too large")
    return body


def _alto_text(raw: bytes) -> str:
    if len(raw) > MAX_ALTO_BYTES:
        raise HtrflowClientError("invalid_response", "engine response is too large")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HtrflowClientError("invalid_response", "engine response could not be read") from None
    stripped = text.lstrip("\ufeff \t\r\n")
    if stripped.startswith("<"):
        return stripped
    if stripped.startswith("{"):
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            raise HtrflowClientError("invalid_response", "engine response could not be read") from None
        alto = payload.get("alto") if isinstance(payload, dict) else None
        if isinstance(alto, str) and alto.lstrip().startswith("<"):
            if len(alto.encode("utf-8")) > MAX_ALTO_BYTES:
                raise HtrflowClientError("invalid_response", "engine response is too large")
            return alto
    raise HtrflowClientError("invalid_response", "engine response could not be read")


def _json_object(raw: bytes):
    if len(raw) > MAX_ALTO_BYTES:
        raise HtrflowClientError("invalid_response", "engine response is too large")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HtrflowClientError("invalid_response", "engine response could not be read") from None
    if not isinstance(payload, dict):
        raise HtrflowClientError("invalid_response", "engine response could not be read")
    return payload


def _raise_status(status):
    if isinstance(status, int) and 300 <= status < 400:
        raise HtrflowClientError("invalid_response", "engine redirect was not followed", status=status)
    label = status if isinstance(status, int) else 0
    raise HtrflowClientError("http_error", "engine returned HTTP %s" % label, status=label)


def _request(url, timeout, *, data=None):
    headers = {"Accept": "application/json, application/xml, text/xml"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET", headers=headers)
    try:
        with _open(request, timeout) as response:
            final = response.geturl() if hasattr(response, "geturl") else request.full_url
            if final != request.full_url:
                raise HtrflowClientError("invalid_response", "engine redirect was not followed")
            status = getattr(response, "status", None)
            if status is None:
                status = getattr(response, "code", 200)
            return status, _read_limited(response)
    except HtrflowClientError:
        raise
    except urllib.error.HTTPError as exc:
        status = exc.code
        if isinstance(status, int) and 300 <= status < 400:
            raise HtrflowClientError("invalid_response", "engine redirect was not followed") from None
        raise HtrflowClientError(
            "http_error", "engine returned HTTP %s" % status, status=status,
        ) from None
    except TimeoutError:
        raise HtrflowClientError("timeout", "engine request timed out") from None
    except urllib.error.URLError as exc:
        if isinstance(getattr(exc, "reason", None), TimeoutError):
            raise HtrflowClientError("timeout", "engine request timed out") from None
        raise HtrflowClientError("unavailable", "engine request failed") from None


def _open(request, timeout):
    """Open one request. A patched ``urlopen`` is used as-is."""
    urlopen = urllib.request.urlopen
    if getattr(urlopen, "__module__", "") != "urllib.request":
        return urlopen(request, timeout=timeout)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    return opener.open(request, timeout=timeout)


def _read_limited(response) -> bytes:
    body = response.read(MAX_ALTO_BYTES + 1)
    if len(body) > MAX_ALTO_BYTES:
        raise HtrflowClientError("invalid_response", "engine response is too large")
    return body


def _url(config, path):
    try:
        return join_engine_url(_endpoint(config), path)
    except Exception:
        raise HtrflowClientError("invalid_request", "endpoint_url must be an http(s) base URL") from None


def _endpoint(config) -> str:
    value = getattr(config, "endpoint_url", None)
    if not isinstance(value, str) or not value.strip():
        raise HtrflowClientError("invalid_request", "endpoint_url must be an http(s) base URL")
    return value


def _timeout(config) -> int:
    value = getattr(config, "timeout_seconds", None)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise HtrflowClientError("invalid_request", "timeout_seconds must be a positive integer")
    return value


def _ensure_enabled(config):
    if getattr(config, "enabled", None) is not True:
        raise HtrflowClientError("disabled", "engine config is disabled")
