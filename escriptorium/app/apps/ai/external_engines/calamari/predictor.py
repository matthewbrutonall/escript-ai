"""Calamari checkpoint layout and predict command.

This module does not import Calamari or TensorFlow. It does not download a
model. A directory is accepted only when it already contains a checkpoint
description and a weights file or SavedModel directory.
"""
from __future__ import annotations

import io
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
# Calamari 2.3.1 counts every non-empty CUDA_VISIBLE_DEVICES entry as a GPU.
# "-1" asks for device 0 and then fails when the container has none. An empty
# value leaves the child on CPU. The same applies to HIP. GPU mode does not
# use this map: the child keeps the single device the container was given.
_CPU_ENV = {
    "CUDA_VISIBLE_DEVICES": "",
    "HIP_VISIBLE_DEVICES": "",
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
        # These checkpoints were trained on binary line images. A grey
        # photograph of the same crop decodes to an empty sentence.
        image_path.write_bytes(_binarize_line_png(line.png))
        images.append(str(image_path))
        outputs.append((line.line_id, work_dir / f"{stem}.pred.txt"))
    # FileDataParams on calamari-predict 2.3.1 has no line_height. The height
    # is already stored on the checkpoint processors. Passing the flag makes
    # the process exit before it writes a prediction.
    argv = [
        "calamari-predict",
        "--checkpoint",
        str(checkpoint.checkpoint_path),
        "--data.images",
        *images,
    ]
    return Prepared(checkpoint.model_id, tuple(argv), tuple(outputs))


class PredictRunner:
    """Run one prepared ``calamari-predict`` command.

    ``device="cpu"`` hides every GPU from that child. ``device="gpu"`` leaves
    ``CUDA_VISIBLE_DEVICES`` as the parent set it. The server accepts GPU
    mode only for one numbered device.
    """

    def __init__(self, timeout_seconds: int = 30, device: str = "cpu") -> None:
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int):
            raise LayoutError("timeout")
        if not 0 < timeout_seconds <= 3600:
            raise LayoutError("timeout")
        if device not in {"cpu", "gpu"}:
            raise LayoutError("device")
        self._timeout = timeout_seconds
        self._device = device

    def run(self, prepared: Prepared) -> PredictRunResult:
        env = os.environ.copy()
        if self._device == "cpu":
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


def _binarize_line_png(payload: bytes) -> bytes:
    """Return black text on white. Unreadable bytes are left unchanged.

    Dark ink on a light ground stays dark. Light ink on a dark ground is
    inverted when the border is dark, including a tight crop whose ink is
    most of the box.
    """
    try:
        from PIL import Image
    except ImportError:
        return payload
    try:
        with Image.open(io.BytesIO(payload)) as image:
            gray = image.convert("L")
            gray.load()
            low, high = gray.getextrema()
            if low == high:
                ink = 0 if low < 128 else 255
                binary = Image.new("L", gray.size, ink)
            else:
                binary = gray.point(_binary_table(_otsu_threshold(gray.histogram())))
            if _border_is_dark(binary):
                binary = Image.eval(binary, lambda value: 255 - value)
            buffer = io.BytesIO()
            binary.save(buffer, format="PNG")
    except OSError:
        return payload
    return buffer.getvalue()


def _border_is_dark(image) -> bool:
    """True when the outer pixels are mostly black."""
    width, height = image.size
    if width <= 0 or height <= 0:
        return False
    pixels = image.load()
    dark = 0
    total = 0
    for x in range(width):
        for y in (0, height - 1):
            total += 1
            if pixels[x, y] == 0:
                dark += 1
    if height > 2:
        for y in range(1, height - 1):
            for x in (0, width - 1):
                total += 1
                if pixels[x, y] == 0:
                    dark += 1
    return dark > total / 2


def _otsu_threshold(histogram: list[int]) -> int:
    total = sum(histogram)
    if total <= 0:
        return 0
    sum_all = sum(index * count for index, count in enumerate(histogram))
    sum_background = 0
    weight_background = 0
    best = -1.0
    threshold = 0
    for index, count in enumerate(histogram):
        weight_background += count
        if weight_background == 0:
            continue
        weight_foreground = total - weight_background
        if weight_foreground == 0:
            break
        sum_background += index * count
        mean_background = sum_background / weight_background
        mean_foreground = (sum_all - sum_background) / weight_foreground
        score = weight_background * weight_foreground * (mean_background - mean_foreground) ** 2
        if score > best:
            best = score
            threshold = index
    return threshold


def _binary_table(threshold: int) -> list[int]:
    return [0 if value <= threshold else 255 for value in range(256)]


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
