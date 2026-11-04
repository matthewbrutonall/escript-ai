"""Backend seam for the Loghi-HTR line wrapper.

The default backend has no models and does not recognize text.
``PredictLoghiBackend`` calls the local Loghi API only when that object is
used. This module does not import Loghi or TensorFlow.
"""
from __future__ import annotations

import base64
import copy
import threading

from ai.external_engines.loghi.client import ClientFailure, LineJob, LoghiClient
from ai.external_engines.loghi.layout import (
    CHANNELS,
    DISPLAY_NAME,
    LICENCE,
    LINE_HEIGHT,
    MODEL_ID,
    MODEL_VERSION,
    LayoutError,
    inspect_model_dir,
)

ENGINE_NAME = "loghi"
_MAX_LINES = 32

_CAPABILITIES = {
    "api_version": "1",
    "engine": ENGINE_NAME,
    "tier": "research",
    "tasks": ["recognize_lines"],
    "accepts": ["image/png"],
    "image_transport": ["base64"],
    "max_lines_per_request": _MAX_LINES,
    "preprocessing_supported": [],
    "params_accepted": [],
    "reports": ["timing_ms", "confidence", "warnings"],
}


class BackendFailure:
    """A contract error code. It carries no request data."""

    def __init__(self, code: str) -> None:
        self.code = code


class UnavailableLoghiBackend:
    """Contract answers when no recognition backend is selected."""

    def capabilities(self) -> dict:
        return copy.deepcopy(_CAPABILITIES)

    def list_models(self) -> dict:
        return {"api_version": "1", "engine": ENGINE_NAME, "models": []}

    def get_model(self, model_id: str) -> BackendFailure:
        return BackendFailure("model_not_found")

    def recognize(self, request) -> BackendFailure:
        return BackendFailure("unavailable")


class PredictLoghiBackend:
    """Recognize with the one model loaded by the Loghi process at startup.

    A second recognize call while the first is running returns ``busy``.
    The client is not asked to switch models.
    """

    def __init__(self, *, model_dir, work_root, client: LoghiClient, record=None) -> None:
        del work_root
        if record is None:
            record = inspect_model_dir(model_dir)
        if record.model_id != MODEL_ID or record.channels != CHANNELS:
            raise LayoutError("model_id")
        self._record = record
        self._client = client
        self._lock = threading.Lock()

    def capabilities(self) -> dict:
        return copy.deepcopy(_CAPABILITIES)

    def list_models(self) -> dict:
        return {"api_version": "1", "engine": ENGINE_NAME, "models": [self._model_record()]}

    def get_model(self, model_id: str) -> dict | BackendFailure:
        if model_id != self._record.model_id:
            return BackendFailure("model_not_found")
        return self._model_record()

    def recognize(self, request) -> dict | BackendFailure:
        if getattr(request, "engine", None) != ENGINE_NAME:
            return BackendFailure("model_not_found")
        if getattr(request, "model_id", None) != self._record.model_id:
            return BackendFailure("model_not_found")
        if not self._lock.acquire(blocking=False):
            return BackendFailure("busy")
        try:
            try:
                jobs = _jobs(request)
            except LayoutError:
                return BackendFailure("invalid_request")
            if not jobs:
                return BackendFailure("invalid_request")
            try:
                merged = []
                for start in range(0, len(jobs), _MAX_LINES):
                    merged.extend(self._client.recognize(tuple(jobs[start:start + _MAX_LINES])))
            except ClientFailure as exc:
                return BackendFailure(_client_code(exc.code))
            return _response(request, tuple(merged))
        finally:
            self._lock.release()

    def _model_record(self) -> dict:
        return {
            "api_version": "1",
            "engine": ENGINE_NAME,
            "model_id": self._record.model_id,
            "model_version": self._record.model_version or MODEL_VERSION,
            "display_name": self._record.display_name or DISPLAY_NAME,
            "licence": self._record.licence or LICENCE,
            "source": "configured",
            "scripts": [],
            "languages": [],
            "input": {"line_height": self._record.line_height or LINE_HEIGHT},
            "alphabet_note": "",
            "experimental": True,
        }


def _jobs(request) -> tuple[LineJob, ...]:
    lines = getattr(request, "lines", None)
    if isinstance(lines, (str, bytes)) or not isinstance(lines, (tuple, list)):
        raise LayoutError("lines")
    jobs = []
    for line in lines:
        line_id = getattr(line, "line_id", None)
        raw = getattr(line, "image", None)
        if not isinstance(line_id, str) or not isinstance(raw, str):
            raise LayoutError("lines")
        try:
            png = base64.b64decode(raw, validate=True)
        except ValueError:
            raise LayoutError("image") from None
        if not png:
            raise LayoutError("image")
        jobs.append(LineJob(line_id, png))
    return tuple(jobs)


def _response(request, outcomes) -> dict | BackendFailure:
    if not isinstance(outcomes, tuple):
        return BackendFailure("internal")
    found = {}
    for item in outcomes:
        line_id = getattr(item, "line_id", None)
        text = getattr(item, "text", None)
        warnings = getattr(item, "warnings", None)
        confidence = getattr(item, "confidence", None)
        if not isinstance(line_id, str) or not isinstance(text, str) or line_id in found:
            return BackendFailure("internal")
        if not isinstance(warnings, tuple) or any(not isinstance(note, str) for note in warnings):
            return BackendFailure("internal")
        if confidence is not None and (
            isinstance(confidence, bool) or not isinstance(confidence, (int, float))
        ):
            return BackendFailure("internal")
        found[line_id] = item
    expected = [line.line_id for line in request.lines]
    if [line_id for line_id in found] != expected and set(found) != set(expected):
        return BackendFailure("line_failed")
    if set(found) != set(expected) or len(found) != len(expected):
        return BackendFailure("line_failed")
    version = MODEL_VERSION
    return {
        "api_version": "1",
        "job_id": request.job_id,
        "engine": ENGINE_NAME,
        "model_id": request.model_id,
        "model_version": version,
        "results": [
            {
                "line_id": line_id,
                "text": found[line_id].text,
                "confidence": found[line_id].confidence,
                "timing_ms": 0,
                "warnings": list(found[line_id].warnings),
            }
            for line_id in expected
        ],
        "provenance": {
            "engine": ENGINE_NAME,
            "model_id": request.model_id,
            "model_version": version,
            "api_version": "1",
        },
        "timing_ms": 0,
        "resources": {"device": "cpu"},
    }


def _client_code(code: str) -> str:
    if code in {"busy", "unavailable"}:
        return code
    return "internal"
