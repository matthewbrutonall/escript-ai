"""Calamari checkpoint layout and predict command.

This module does not import Calamari or TensorFlow. It does not download a
model. A directory is accepted only when it already contains a checkpoint
description and a weights file or SavedModel directory.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

_MAX_JSON = 2 * 1024 * 1024
_MAX_LINES = 32
_PNG = b"\x89PNG\r\n\x1a\n"
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_LICENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_-]{0,63}$")
_CPU_ENV = {
    "CUDA_VISIBLE_DEVICES": "-1",
    "HIP_VISIBLE_DEVICES": "-1",
    "NVIDIA_VISIBLE_DEVICES": "void",
}


class LayoutError(Exception):
    """The model directory cannot be listed. The code is a fixed token."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class PredictRunError(Exception):
    """A predict process failed. The code carries no program output."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Checkpoint:
    """One real checkpoint pair. The paths stay inside the model directory."""

    model_id: str
    checkpoint_path: Path
    line_height: int | None
    model_version: str
    licence: str


@dataclass(frozen=True)
class LayoutReport:
    """Checkpoints that passed the file check, plus a code when none did."""

    checkpoints: tuple[Checkpoint, ...]
    code: str


@dataclass(frozen=True)
class LineImage:
    line_id: str
    png: bytes


@dataclass(frozen=True)
class Prepared:
    model_id: str
    argv: tuple[str, ...]
    outputs: tuple[tuple[str, Path], ...]


@dataclass(frozen=True)
class PredictedLine:
    line_id: str
    text: str


@dataclass(frozen=True)
class PredictRunResult:
    model_id: str
    lines: tuple[PredictedLine, ...]


def inspect_model_dir(model_dir: Path) -> LayoutReport:
    """Return checkpoint pairs in one directory. Does not load weights."""
    root = _directory(model_dir)
    if root is None:
        raise LayoutError("unsafe_path")
    licence = _licence(root)
    found: list[Checkpoint] = []
    reasons: list[str] = []
    for path in sorted(root.iterdir(), key=lambda item: item.name):
        if not path.name.endswith(".ckpt.json"):
            continue
        checkpoint, reason = _checkpoint(root, path, licence)
        if checkpoint is None:
            reasons.append(reason or "missing_checkpoint")
            continue
        found.append(checkpoint)
    if found:
        return LayoutReport(tuple(found), "ok")
    return LayoutReport((), _failure_code(reasons))


def declared_line_height(payload: dict) -> int | None:
    """Return ``scenario.data.line_height`` when the checkpoint declares one.

    A processor height is not the network input. A missing key is not the
    historical training default.
    """
    if not isinstance(payload, dict):
        return None
    scenario = payload.get("scenario")
    source = scenario if isinstance(scenario, dict) else payload
    data = source.get("data") if isinstance(source, dict) else None
    if not isinstance(data, dict) or "line_height" not in data:
        return None
    value = data.get("line_height")
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 1024:
        return None
    return value


def prepare_predict(
    *,
    checkpoint: Checkpoint,
    work_dir: Path,
    lines: tuple[LineImage, ...],
) -> Prepared:
    """Write line PNGs and the ``calamari-predict`` argument list."""
    if not isinstance(work_dir, Path) or not work_dir.is_dir():
        raise LayoutError("work_dir")
    if not lines:
        raise LayoutError("lines")
    if len(lines) > _MAX_LINES:
        raise LayoutError("lines")
    images: list[str] = []
    outputs: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for index, line in enumerate(lines, start=1):
        if not isinstance(line.line_id, str) or not line.line_id or line.line_id in seen:
            raise LayoutError("line_id")
        if not isinstance(line.png, bytes) or not line.png.startswith(_PNG):
            raise LayoutError("image")
        seen.add(line.line_id)
        stem = f"line{index:04d}"
        image_path = work_dir / f"{stem}.png"
        image_path.write_bytes(line.png)
        images.append(str(image_path))
        outputs.append((line.line_id, work_dir / f"{stem}.pred.txt"))
    argv = [
        "calamari-predict",
        "--checkpoint",
        str(checkpoint.checkpoint_path),
        "--data.images",
        *images,
    ]
    if checkpoint.line_height is not None:
        argv.extend(["--data.line_height", str(checkpoint.line_height)])
    return Prepared(checkpoint.model_id, tuple(argv), tuple(outputs))


class PredictRunner:
    """Run one prepared ``calamari-predict`` command. The child is CPU-only."""

    def __init__(self, timeout_seconds: int = 30) -> None:
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int):
            raise LayoutError("timeout")
        if not 0 < timeout_seconds <= 3600:
            raise LayoutError("timeout")
        self._timeout = timeout_seconds

    def run(self, prepared: Prepared) -> PredictRunResult:
        env = os.environ.copy()
        env.update(_CPU_ENV)
        try:
            completed = subprocess.run(
                list(prepared.argv),
                cwd=str(prepared.outputs[0][1].parent),
                env=env,
                shell=False,
                capture_output=True,
                timeout=self._timeout,
                check=False,
            )
        except FileNotFoundError:
            raise PredictRunError("missing_program") from None
        except subprocess.TimeoutExpired:
            raise PredictRunError("timeout") from None
        except OSError:
            raise PredictRunError("exit") from None
        if completed.returncode != 0:
            raise PredictRunError("exit")
        lines: list[PredictedLine] = []
        for line_id, path in prepared.outputs:
            if not path.is_file():
                raise PredictRunError("missing_output")
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                raise PredictRunError("missing_output") from None
            if text.endswith("\n"):
                text = text[:-1]
            if text.endswith("\r"):
                text = text[:-1]
            lines.append(PredictedLine(line_id, text))
        return PredictRunResult(prepared.model_id, tuple(lines))


def _checkpoint(root: Path, json_path: Path, licence: str) -> tuple[Checkpoint | None, str | None]:
    if not _contained(root, json_path) or json_path.is_symlink() or not json_path.is_file():
        return None, "unsafe_path"
    model_id = json_path.name[: -len(".json")]
    if not _MODEL_ID.fullmatch(model_id) or ".." in model_id:
        return None, "model_id"
    try:
        raw = json_path.read_bytes()
    except OSError:
        return None, "invalid_json"
    if len(raw) > _MAX_JSON or not raw:
        return None, "invalid_json"
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return None, "invalid_json"
    if not isinstance(payload, dict):
        return None, "invalid_json"
    checkpoint_path = json_path.with_suffix("")
    if not _weights_present(root, checkpoint_path):
        return None, "missing_weights"
    version = payload.get("version")
    model_version = str(version) if isinstance(version, int) and not isinstance(version, bool) and version >= 0 else "0"
    return Checkpoint(
        model_id=model_id,
        checkpoint_path=checkpoint_path,
        line_height=declared_line_height(payload),
        model_version=model_version,
        licence=licence,
    ), None


def _weights_present(root: Path, checkpoint_path: Path) -> bool:
    if not _contained(root, checkpoint_path):
        return False
    if checkpoint_path.is_symlink():
        return False
    if checkpoint_path.is_file():
        try:
            return checkpoint_path.stat().st_size > 0
        except OSError:
            return False
    saved = checkpoint_path / "saved_model.pb"
    if checkpoint_path.is_dir() and saved.is_file() and not saved.is_symlink():
        try:
            return _contained(root, saved) and saved.stat().st_size > 0
        except OSError:
            return False
    h5 = Path(str(checkpoint_path) + ".h5")
    if h5.is_file() and not h5.is_symlink() and _contained(root, h5):
        try:
            return h5.stat().st_size > 0
        except OSError:
            return False
    return False


def _licence(root: Path) -> str:
    path = root / "licence.txt"
    if not path.is_file() or path.is_symlink() or not _contained(root, path):
        return "unspecified"
    try:
        raw = path.read_bytes()
    except OSError:
        return "unspecified"
    if len(raw) > 256:
        return "unspecified"
    try:
        line = raw.decode("utf-8").splitlines()[0].strip()
    except (UnicodeError, IndexError):
        return "unspecified"
    if not _LICENCE.fullmatch(line):
        return "unspecified"
    return line


def _failure_code(reasons: list[str]) -> str:
    for code in ("unsafe_path", "invalid_json", "missing_weights", "model_id"):
        if code in reasons:
            return code
    return "missing_checkpoint"


def _directory(value: Path) -> Path | None:
    if not isinstance(value, Path) or not value.is_absolute() or ".." in value.parts:
        return None
    try:
        resolved = value.resolve()
    except OSError:
        return None
    if not resolved.is_dir():
        return None
    return resolved


def _contained(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True
