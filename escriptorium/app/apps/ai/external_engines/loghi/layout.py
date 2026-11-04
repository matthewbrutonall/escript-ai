"""Runtime directory check for the one Loghi-HTR model.

The mounted files are a Keras 3 rewrite of the GLOBALISE SavedModel.
SOURCE_CHECKSUMS records the Dataverse files and is not the mount set.
This module does not import TensorFlow, does not load weights, and does not
predict. A directory that does not match is refused.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

MODEL_ID = "globalise-2023-08"
MODEL_VERSION = "2023-08"
DISPLAY_NAME = "GLOBALISE 2023 (Dutch VOC handwriting)"
LICENCE = "CC-BY-4.0"
LINE_HEIGHT = 64
CHANNELS = 4
SOURCE_DOI = "10.34894/HHM4TE"

_WEIGHT_FILES = (
    "charlist.txt",
    "config.json",
    "licence.txt",
    "metadata.json",
    "model.keras",
    "tokenizer.json",
)
_PINNED = object()

# sha256 of the DataverseNL SavedModel for doi:10.34894/HHM4TE. Not mounted.
SOURCE_CHECKSUMS: dict[str, str] = {
    "charlist.txt": "f1877392e09591cf40517c5ef636d6011a1c2b61e4a240b508167d0c94ff6754",
    "config.json": "faef1031298f813ab0b6352631a0397c45aa386769809a929cc8b4d8507d6c59",
    "fingerprint.pb": "780eac6e25b0c5ddbe4f8acf46f0a7da6d2077f4b2407ce24673c2a5221b6ee1",
    "keras_metadata.pb": "e4594c646cbb88f252b9d1749efc0c1c43ce2a9233e4a39bb09aac22d9e73170",
    "metadata.json": "9a1da7cf25bc7bab92fa66291b3cc6483e500d5f60a4a9b4918e590df5ef06a2",
    "saved_model.pb": "d5f4093672f51117afd2ad436c8db1573a8a301ab9b7ccd958a392d01d9a2797",
    "variables/variables.data-00000-of-00001": "fc0f3fe0420681e89935da79aa688623e537762dc250cfb1a951485972365366",
    "variables/variables.index": "50efba2af3af321e935713f1e3e83872d1df4903184097ead458f8cfc9aab397",
}

# Keras 2 intermediate file. Not mounted.
KERAS2_MODEL_SHA256 = "d542cbf1a98986202fb475b97687e0ccd9be05516e74780c57da99ce8b6c4662"

# Mounted runtime files. Not the Dataverse set and not the Keras 2 file.
CHECKSUMS: dict[str, str] = {
    "charlist.txt": "f1877392e09591cf40517c5ef636d6011a1c2b61e4a240b508167d0c94ff6754",
    "config.json": "faef1031298f813ab0b6352631a0397c45aa386769809a929cc8b4d8507d6c59",
    "licence.txt": "80f1acb2ff59a196f582b27cdd695cbcf24ddb6ceecb872fe2d5f3557c8591c8",
    "metadata.json": "9a1da7cf25bc7bab92fa66291b3cc6483e500d5f60a4a9b4918e590df5ef06a2",
    "model.keras": "d2ed09cfb8a81051d517586f9c6dddb64846be4123f3bb002c61b15f9c1df6ff",
    "tokenizer.json": "40273d7f224d61db59e4c31eabb0ec8d47596b0fa158b5e16d561b4d6be53065",
}


class LayoutError(Exception):
    """A fixed layout code. The message is the code and nothing else."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ModelRecord:
    """The one model this service is allowed to list."""

    model_id: str
    model_version: str
    display_name: str
    licence: str
    line_height: int
    channels: int


def inspect_model_dir(directory: Path, *, checksums=_PINNED) -> ModelRecord:
    """Return the model record, or raise ``LayoutError`` with a fixed code."""
    root = _directory(directory)
    for name in _WEIGHT_FILES:
        if name == "licence.txt":
            continue
        path = root / name
        if not path.is_file():
            raise LayoutError("missing_file")
    if any(path.is_file() and path.suffix == ".pb" for path in root.rglob("*")):
        raise LayoutError("savedmodel_refused")
    if (root / "charlist.txt").stat().st_size < 1:
        raise LayoutError("missing_file")
    channels = _channels(root / "config.json")
    if channels != CHANNELS:
        raise LayoutError("channel_mismatch")
    if _height(root / "config.json") != LINE_HEIGHT:
        raise LayoutError("invalid_config")
    licence = _licence(root / "licence.txt")
    if licence != LICENCE:
        raise LayoutError("missing_licence")
    expected = CHECKSUMS if checksums is _PINNED else checksums
    if expected is not None:
        _verify_checksums(root, expected)
    return ModelRecord(
        model_id=MODEL_ID,
        model_version=MODEL_VERSION,
        display_name=DISPLAY_NAME,
        licence=LICENCE,
        line_height=LINE_HEIGHT,
        channels=CHANNELS,
    )


def _directory(directory: Path) -> Path:
    if not isinstance(directory, Path) or not directory.is_absolute() or ".." in directory.parts:
        raise LayoutError("unsafe_path")
    try:
        root = directory.resolve()
    except OSError:
        raise LayoutError("unsafe_path") from None
    if not root.is_dir():
        raise LayoutError("missing_file")
    return root


def _channels(path: Path) -> int:
    config = _config(path)
    if "model_channels" in config:
        value = config.get("model_channels")
    else:
        args = config.get("args")
        value = args.get("channels") if isinstance(args, dict) else None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise LayoutError("invalid_config")
    return value


def _height(path: Path) -> int:
    args = _config(path).get("args")
    value = args.get("height") if isinstance(args, dict) else None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise LayoutError("invalid_config")
    return value


def _config(path: Path) -> dict:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise LayoutError("invalid_config") from None
    if not isinstance(loaded, dict):
        raise LayoutError("invalid_config")
    return loaded


def _licence(path: Path) -> str:
    if not path.is_file():
        raise LayoutError("missing_licence")
    try:
        text = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        raise LayoutError("missing_licence") from None
    return text


def _verify_checksums(root: Path, expected) -> None:
    if not isinstance(expected, dict) or not expected:
        raise LayoutError("checksum_mismatch")
    if set(expected) != set(_WEIGHT_FILES):
        raise LayoutError("checksum_mismatch")
    for name, digest in expected.items():
        if not isinstance(digest, str) or len(digest) != 64:
            raise LayoutError("checksum_mismatch")
        if _sha256(root / name) != digest:
            raise LayoutError("checksum_mismatch")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        raise LayoutError("checksum_mismatch") from None
    return digest.hexdigest()
