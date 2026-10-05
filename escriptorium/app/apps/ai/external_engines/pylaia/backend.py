"""Backend seam for the PyLaia skeleton.

The default backend has no models and does not recognize text.
``DecodePyLaiaBackend`` calls the decode runner only when that object is
used. This module does not import PyLaia.
"""
from __future__ import annotations

import base64
import copy
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ai.external_engines.pylaia.decoder import (
    DecodeLayoutError,
    DecodeRunError,
    DecodeRunner,
    LineImage,
    prepare_decode,
)

ENGINE_NAME = "pylaia"
_MODEL_VERSION = "0"

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


@dataclass(frozen=True)
class BackendFailure:
    """A contract error code. It carries no request data."""

    code: str


class UnavailablePyLaiaBackend:
    """Contract answers for a service with no recognition backend installed."""

    def capabilities(self) -> dict:
        return copy.deepcopy(_CAPABILITIES)

    def list_models(self) -> dict:
        return copy.deepcopy(_MODELS)

    def get_model(self, model_id: str) -> BackendFailure:
        return BackendFailure("model_not_found")

    def recognize(self, request) -> BackendFailure:
        return BackendFailure("unavailable")


class DecodePyLaiaBackend:
    """Recognize through ``DecodeRunner`` when this object is selected.

    The HTTP server constructs this class only for ``--backend decode``.
    ``model_id`` selects the configured model and is not joined onto ``model_dir``.
    """

    def __init__(
        self,
        *,
        model_id: str,
        model_dir: Path,
        work_root: Path,
        timeout_seconds: int = 30,
    ) -> None:
        self._model_id = _configured_model_id(model_id)
        if not isinstance(model_dir, Path) or not isinstance(work_root, Path):
            raise DecodeLayoutError("model_dir")
        self._model_dir = model_dir
        self._work_root = work_root
        self._runner = DecodeRunner(timeout_seconds=timeout_seconds)

    def capabilities(self) -> dict:
        return copy.deepcopy(_CAPABILITIES)

    def list_models(self) -> dict:
        return {
            "api_version": "1",
            "engine": ENGINE_NAME,
            "models": [self._model_record()],
        }

    def get_model(self, model_id: str) -> dict | BackendFailure:
        if model_id != self._model_id:
            return BackendFailure("model_not_found")
        return self._model_record()

    def recognize(self, request) -> dict | BackendFailure:
        if getattr(request, "engine", None) != ENGINE_NAME:
            return BackendFailure("model_not_found")
        if getattr(request, "model_id", None) != self._model_id:
            return BackendFailure("model_not_found")
        try:
            images = _line_images(request)
        except DecodeLayoutError as exc:
            return BackendFailure(_mapped(exc.code, _LAYOUT_CODES))
        try:
            work_dir = Path(tempfile.mkdtemp(prefix="pylaia-", dir=self._work_root))
        except OSError:
            return BackendFailure("internal")
        try:
            prepared = prepare_decode(
                model_dir=self._model_dir,
                work_dir=work_dir,
                lines=images,
                model_id=self._model_id,
            )
            result = self._runner.run(prepared)
            return _recognize_response(request, result)
        except DecodeLayoutError as exc:
            return BackendFailure(_mapped(exc.code, _LAYOUT_CODES))
        except DecodeRunError as exc:
            return BackendFailure(_mapped(exc.code, _RUN_CODES))
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def _model_record(self) -> dict:
        return {
            "api_version": "1",
            "engine": ENGINE_NAME,
            "model_id": self._model_id,
            "model_version": _MODEL_VERSION,
            "display_name": self._model_id,
            "licence": "unspecified",
            "source": "configured",
            "scripts": [],
            "languages": [],
            "input": {},
            "alphabet_note": "",
            "experimental": True,
        }


_LAYOUT_CODES = {
    "model_dir": "model_not_found",
    "model_file": "model_not_found",
    "syms": "model_not_found",
    "checkpoint": "model_not_found",
    "model_id": "model_not_found",
    "line_id": "invalid_request",
    "lines": "invalid_request",
    "image": "invalid_request",
    "work_dir": "internal",
    "escape": "internal",
    "write": "internal",
}

_RUN_CODES = {
    "timeout": "busy",
    "exit": "internal",
    "output": "internal",
}


def _configured_model_id(value: str) -> str:
    if (
        not isinstance(value, str)
        or value == ""
        or value != value.strip()
        or len(value) > 256
        or "/" in value
        or "\\" in value
        or value in {".", ".."}
        or ".." in Path(value).parts
    ):
        raise DecodeLayoutError("model_id")
    return value


def _line_images(request) -> tuple[LineImage, ...]:
    lines = getattr(request, "lines", None)
    if isinstance(lines, (str, bytes)) or not isinstance(lines, (tuple, list)):
        raise DecodeLayoutError("lines")
    images = []
    for line in lines:
        line_id = getattr(line, "line_id", None)
        raw = getattr(line, "image", None)
        if not isinstance(line_id, str) or not isinstance(raw, str):
            raise DecodeLayoutError("lines")
        try:
            png = base64.b64decode(raw, validate=True)
        except ValueError:
            raise DecodeLayoutError("image") from None
        images.append(LineImage(line_id, png))
    return tuple(images)


def _recognize_response(request, result) -> dict | BackendFailure:
    if getattr(result, "model_id", None) != request.model_id:
        return BackendFailure("internal")
    found = {}
    for decoded in getattr(result, "lines", ()):
        line_id = getattr(decoded, "line_id", None)
        text = getattr(decoded, "text", None)
        if not isinstance(line_id, str) or not isinstance(text, str) or line_id in found:
            return BackendFailure("internal")
        found[line_id] = text
    expected = [line.line_id for line in request.lines]
    if set(found) != set(expected) or len(found) != len(expected):
        return BackendFailure("line_failed")
    return {
        "api_version": "1",
        "job_id": request.job_id,
        "engine": ENGINE_NAME,
        "model_id": request.model_id,
        "model_version": _MODEL_VERSION,
        "results": [
            {
                "line_id": line_id,
                "text": found[line_id],
                "confidence": None,
                "timing_ms": 0,
                "warnings": [],
            }
            for line_id in expected
        ],
        "provenance": {
            "engine": ENGINE_NAME,
            "model_id": request.model_id,
            "model_version": _MODEL_VERSION,
            "api_version": "1",
        },
        "timing_ms": 0,
        "resources": {"device": "cpu"},
    }


def _mapped(code: str, table: dict[str, str]) -> str:
    mapped = table.get(code)
    if mapped is None:
        return "internal"
    return mapped
