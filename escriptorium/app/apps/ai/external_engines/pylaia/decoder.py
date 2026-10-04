"""Decode-adapter scaffold for PyLaia.

This module plans the file layout and the argument list for
``pylaia-htr-decode-ctc``. It does not call that program, does not load a
model, and is not used by the HTTP server.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

_PNG = b"\x89PNG\r\n\x1a\n"
_MAX_LINES = 32
_MAX_ID = 256
_MODEL_NAME = "model"
_SYMS_NAME = "syms.txt"
_PREFERRED_CHECKPOINT = "weights.ckpt"
_IMG_LIST_NAME = "img_list.txt"


class DecodeLayoutError(Exception):
    """A fixed reason the layout was rejected. The message has no request data."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("decode layout was rejected")


@dataclass(frozen=True)
class LineImage:
    """One contract line, already decoded to PNG bytes."""

    line_id: str
    png: bytes


@dataclass(frozen=True)
class PreparedDecode:
    """Files written under the work directory, plus the command that would run later."""

    model_id: str
    command: tuple[str, ...]
    image_ids: tuple[str, ...]
    line_ids: tuple[str, ...]


class DecodeRunner:
    """Placeholder. The server does not call this, and it does not start a process."""

    def run(self, prepared: PreparedDecode) -> None:
        if not isinstance(prepared, PreparedDecode):
            raise DecodeLayoutError("not_implemented")
        raise DecodeLayoutError("not_implemented")


def prepare_decode(
    *,
    model_dir: Path,
    work_dir: Path,
    lines: Sequence[LineImage],
    model_id: str,
) -> PreparedDecode:
    """Write line images and ``img_list.txt``, and return the decode argument list."""
    model_root = _resolved_dir(model_dir, "model_dir")
    work_root = _resolved_dir(work_dir, "work_dir")
    _separate(model_root, work_root)
    checked_id = _plain_id(model_id, "model_id")
    syms = _file_inside(model_root, _SYMS_NAME, "syms")
    _file_inside(model_root, _MODEL_NAME, "model_file")
    checkpoint = _checkpoint(model_root)
    image_ids, line_ids, payloads = _checked_lines(lines)
    _write_layout(work_root, image_ids, payloads)
    command = build_decode_command(
        model_dir=model_root,
        work_dir=work_root,
        checkpoint=checkpoint,
        syms=syms,
        img_list=work_root / _IMG_LIST_NAME,
    )
    return PreparedDecode(
        model_id=checked_id,
        command=command,
        image_ids=tuple(image_ids),
        line_ids=tuple(line_ids),
    )


def build_decode_command(
    *,
    model_dir: Path,
    work_dir: Path,
    checkpoint: Path,
    syms: Path,
    img_list: Path,
) -> tuple[str, ...]:
    """Return argv for a later CPU decode. This does not start a process."""
    model_root = _resolved_dir(model_dir, "model_dir")
    work_root = _resolved_dir(work_dir, "work_dir")
    _separate(model_root, work_root)
    _file_inside(model_root, _MODEL_NAME, "model_file")
    checkpoint_path = _checked_file(model_root, checkpoint, "checkpoint")
    syms_path = _checked_file(model_root, syms, "syms")
    list_path = _checked_file(work_root, img_list, "lines")
    return (
        "pylaia-htr-decode-ctc",
        "--trainer.gpus",
        "0",
        "--common.train_path",
        str(model_root),
        "--common.model_filename",
        _MODEL_NAME,
        "--common.checkpoint",
        str(checkpoint_path),
        "--decode.include_img_ids",
        "true",
        "--img_dirs",
        # PyLaia types this option as a list. A bare path is rejected.
        json.dumps([str(work_root)]),
        str(syms_path),
        str(list_path),
    )


def _plain_id(value, code: str) -> str:
    if not isinstance(value, str) or value == "" or len(value) > _MAX_ID:
        raise DecodeLayoutError(code)
    return value


def _resolved_dir(path, code: str) -> Path:
    if not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts:
        raise DecodeLayoutError(code)
    try:
        resolved = path.resolve()
    except OSError:
        raise DecodeLayoutError(code) from None
    if not resolved.is_dir():
        raise DecodeLayoutError(code)
    return resolved


def _separate(model_root: Path, work_root: Path) -> None:
    if _is_inside(model_root, work_root) or _is_inside(work_root, model_root):
        raise DecodeLayoutError("work_dir")


def _is_inside(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _file_inside(root: Path, name: str, code: str) -> Path:
    if name == "" or "/" in name or name in (".", ".."):
        raise DecodeLayoutError(code)
    return _checked_file(root, root / name, code)


def _checked_file(root: Path, path, code: str) -> Path:
    if not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts:
        raise DecodeLayoutError(code)
    try:
        resolved = path.resolve()
    except OSError:
        raise DecodeLayoutError(code) from None
    if not _is_inside(root, resolved) or not resolved.is_file():
        raise DecodeLayoutError(code)
    return resolved


def _checkpoint(root: Path) -> Path:
    preferred = root / _PREFERRED_CHECKPOINT
    try:
        preferred_exists = preferred.exists() or preferred.is_symlink()
    except OSError:
        raise DecodeLayoutError("checkpoint") from None
    if preferred_exists:
        return _file_inside(root, _PREFERRED_CHECKPOINT, "checkpoint")
    found = []
    try:
        children = list(root.iterdir())
    except OSError:
        raise DecodeLayoutError("checkpoint") from None
    for child in children:
        if not child.name.endswith(".ckpt"):
            continue
        try:
            resolved = child.resolve()
        except OSError:
            raise DecodeLayoutError("checkpoint") from None
        if _is_inside(root, resolved) and resolved.is_file():
            found.append(resolved)
    if len(found) != 1:
        raise DecodeLayoutError("checkpoint")
    return found[0]


def _checked_lines(lines):
    if isinstance(lines, (str, bytes)) or not isinstance(lines, Sequence):
        raise DecodeLayoutError("lines")
    if len(lines) == 0 or len(lines) > _MAX_LINES:
        raise DecodeLayoutError("lines")
    image_ids = []
    line_ids = []
    payloads = []
    for index, line in enumerate(lines, start=1):
        if not isinstance(line, LineImage):
            raise DecodeLayoutError("lines")
        line_ids.append(_plain_id(line.line_id, "line_id"))
        if not isinstance(line.png, bytes) or not line.png.startswith(_PNG):
            raise DecodeLayoutError("image")
        image_ids.append(f"{index:04d}")
        payloads.append(line.png)
    return image_ids, line_ids, payloads


def _write_layout(work_root: Path, image_ids, payloads) -> None:
    destinations = []
    for image_id, payload in zip(image_ids, payloads):
        destination = (work_root / f"{image_id}.png").resolve()
        if destination.parent != work_root or not _is_inside(work_root, destination):
            raise DecodeLayoutError("escape")
        destinations.append((destination, payload))
    list_path = (work_root / _IMG_LIST_NAME).resolve()
    if list_path.parent != work_root or not _is_inside(work_root, list_path):
        raise DecodeLayoutError("escape")
    for destination, _payload in destinations:
        if destination.is_symlink():
            raise DecodeLayoutError("escape")
    if list_path.is_symlink():
        raise DecodeLayoutError("escape")
    for destination, payload in destinations:
        _write_new(destination, payload)
    text = "".join(f"{image_id}.png\n" for image_id in image_ids)
    _write_new(list_path, text.encode("utf-8"))


def _write_new(path: Path, payload: bytes) -> None:
    try:
        fd = os.open(
            path,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
            0o600,
        )
    except OSError:
        raise DecodeLayoutError("write") from None
    try:
        os.write(fd, payload)
    except OSError:
        raise DecodeLayoutError("write") from None
    finally:
        os.close(fd)
