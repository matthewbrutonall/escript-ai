"""Backend seam for the Calamari line wrapper.

The default backend has no models and does not recognize text.
``PredictCalamariBackend`` calls ``calamari-predict`` only when that object
is used. This module does not import Calamari.
"""
from __future__ import annotations

import base64
import copy
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ai.external_engines.calamari.predictor import (
    Checkpoint,
    LayoutError,
    LineImage,
    PredictRunError,
    PredictRunResult,
    PredictRunner,
    _MAX_LINES,
    inspect_model_dir,
    prepare_predict,
)

ENGINE_NAME = "calamari"
_MODEL_VERSION = "0"

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


@dataclass(frozen=True)
class BackendFailure:
    """A contract error code. It carries no request data."""

    code: str


class UnavailableCalamariBackend:
    """Contract answers when no recognition backend is selected."""

    def capabilities(self) -> dict:
        return copy.deepcopy(_CAPABILITIES)

    def list_models(self) -> dict:
        return {"api_version": "1", "engine": ENGINE_NAME, "models": []}

    def get_model(self, model_id: str) -> BackendFailure:
        return BackendFailure("model_not_found")

    def recognize(self, request) -> BackendFailure:
        return BackendFailure("unavailable")


class PredictCalamariBackend:
    """Recognize one configured directory of real Calamari checkpoints.

    The HTTP server constructs this class only for ``--backend predict``.
    Model ids come from the checkpoint file names. They are not paths.
    """

    def __init__(
        self,
        *,
        model_dir: Path,
        work_root: Path,
        timeout_seconds: int = 30,
        runner: PredictRunner | None = None,
    ) -> None:
        if not isinstance(model_dir, Path) or not isinstance(work_root, Path):
            raise LayoutError("unsafe_path")
        report = inspect_model_dir(model_dir)
        if not report.checkpoints:
            raise LayoutError(report.code)
        self._checkpoints = {item.model_id: item for item in report.checkpoints}
        self._work_root = work_root
        self._runner = runner or PredictRunner(timeout_seconds=timeout_seconds)

    def capabilities(self) -> dict:
        return copy.deepcopy(_CAPABILITIES)

    def list_models(self) -> dict:
        return {
            "api_version": "1",
            "engine": ENGINE_NAME,
            "models": [self._model_record(item) for item in self._ordered()],
        }

    def get_model(self, model_id: str) -> dict | BackendFailure:
        checkpoint = self._checkpoints.get(model_id)
        if checkpoint is None:
            return BackendFailure("model_not_found")
        return self._model_record(checkpoint)

    def recognize(self, request) -> dict | BackendFailure:
        if getattr(request, "engine", None) != ENGINE_NAME:
            return BackendFailure("model_not_found")
        checkpoint = self._checkpoints.get(getattr(request, "model_id", None))
        if checkpoint is None:
            return BackendFailure("model_not_found")
        try:
            images = _line_images(request)
        except LayoutError as exc:
            return BackendFailure(_mapped(exc.code))
        if not images:
            return BackendFailure("invalid_request")
        try:
            work_dir = Path(tempfile.mkdtemp(prefix="calamari-", dir=self._work_root))
        except OSError:
            return BackendFailure("internal")
        try:
            merged: list = []
            for index, start in enumerate(range(0, len(images), _MAX_LINES)):
                batch_dir = work_dir / f"batch-{index}"
                batch_dir.mkdir()
                prepared = prepare_predict(
                    checkpoint=checkpoint,
                    work_dir=batch_dir,
                    lines=tuple(images[start:start + _MAX_LINES]),
                )
                result = self._runner.run(prepared)
                if getattr(result, "model_id", None) != checkpoint.model_id:
                    return BackendFailure("internal")
                merged.extend(getattr(result, "lines", ()))
            return _recognize_response(
                request,
                checkpoint,
                PredictRunResult(model_id=checkpoint.model_id, lines=tuple(merged)),
            )
        except LayoutError as exc:
            return BackendFailure(_mapped(exc.code))
        except PredictRunError as exc:
            return BackendFailure(_run_code(exc.code))
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def _ordered(self) -> tuple[Checkpoint, ...]:
        return tuple(self._checkpoints[key] for key in sorted(self._checkpoints))

    def _model_record(self, checkpoint: Checkpoint) -> dict:
        return {
            "api_version": "1",
            "engine": ENGINE_NAME,
            "model_id": checkpoint.model_id,
            "model_version": checkpoint.model_version or _MODEL_VERSION,
            "display_name": checkpoint.model_id,
            "licence": checkpoint.licence,
            "source": "configured",
            "scripts": [],
            "languages": [],
            "input": (
                {"line_height": checkpoint.line_height}
                if checkpoint.line_height is not None
                else {}
            ),
            "alphabet_note": "",
            "experimental": True,
        }


def _line_images(request) -> tuple[LineImage, ...]:
    lines = getattr(request, "lines", None)
    if isinstance(lines, (str, bytes)) or not isinstance(lines, (tuple, list)):
        raise LayoutError("lines")
    images = []
    for line in lines:
        line_id = getattr(line, "line_id", None)
        raw = getattr(line, "image", None)
        if not isinstance(line_id, str) or not isinstance(raw, str):
            raise LayoutError("lines")
        try:
            png = base64.b64decode(raw, validate=True)
        except ValueError:
            raise LayoutError("image") from None
        images.append(LineImage(line_id, png))
    return tuple(images)


def _recognize_response(request, checkpoint: Checkpoint, result: PredictRunResult) -> dict | BackendFailure:
    if result.model_id != request.model_id:
        return BackendFailure("internal")
    found = {}
    for decoded in result.lines:
        if not isinstance(decoded.line_id, str) or not isinstance(decoded.text, str) or decoded.line_id in found:
            return BackendFailure("internal")
        found[decoded.line_id] = decoded.text
    expected = [line.line_id for line in request.lines]
    if set(found) != set(expected) or len(found) != len(expected):
        return BackendFailure("line_failed")
    version = checkpoint.model_version or _MODEL_VERSION
    return {
        "api_version": "1",
        "job_id": request.job_id,
        "engine": ENGINE_NAME,
        "model_id": request.model_id,
        "model_version": version,
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
            "model_version": version,
            "api_version": "1",
        },
        "timing_ms": 0,
        "resources": {"device": "cpu"},
    }


def _mapped(code: str) -> str:
    if code in {"lines", "line_id", "image"}:
        return "invalid_request"
    if code in {"missing_checkpoint", "missing_weights", "model_id", "unsafe_path"}:
        return "model_not_found"
    return "internal"


def _run_code(code: str) -> str:
    if code in {"timeout", "missing_program"}:
        return "unavailable"
    if code == "missing_output":
        return "line_failed"
    return "internal"
