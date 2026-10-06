"""Build one external HTR recognize request from line geometry.

The caller supplies already-segmented lines and an image encoder. This
module does not open files, call an engine, read an engine config, or
write a transcription. Transcription jobs do not import it.
"""
from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from ai.htr_engine_contract import (
    API_VERSION,
    ContractError,
    parse_preprocessing,
    parse_recognize_request,
)

# Compact JSON, matching the recognition client's request cap without
# importing that client.
MAX_PAYLOAD_BYTES = 32 * 1024 * 1024

SKIP_NO_MASK = "no_mask"
SKIP_NO_BASELINE = "no_baseline"
SKIP_BAD_LINE_ID = "bad_line_id"
SKIP_DUPLICATE = "duplicate_line_id"
SKIP_NO_IMAGE = "no_image"
SKIP_BAD_IMAGE = "bad_image"
SKIP_LINE_CAP = "line_cap"
SKIP_PAYLOAD_CAP = "payload_cap"

_B64 = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")


@dataclass(frozen=True)
class SkippedLine:
    """One line left out of the request. ``reason`` is a fixed code."""

    line_id: str | None
    reason: str


@dataclass(frozen=True)
class BuiltRecognizeRequest:
    """A contract payload, or ``None`` when every line was skipped."""

    request: dict | None
    skipped: tuple[SkippedLine, ...]


def build_recognize_request(
    lines: Iterable,
    *,
    job_id,
    document_id,
    part_id,
    engine: str,
    model_id: str,
    encode_line: Callable,
    preprocessing: dict | None = None,
    max_lines: int | None = None,
    max_payload_bytes: int = MAX_PAYLOAD_BYTES,
) -> BuiltRecognizeRequest:
    """Return a ``/v1/recognize`` body and the lines that were not included.

    ``document_id`` and ``part_id`` are contract identities. Document
    titles, paths, and other metadata are not read and are not copied.
    ``encode_line(line)`` returns standard base64, or ``None`` when the
    line cannot be cropped. It is not called for a line that is already
    skipped.
    """
    if isinstance(lines, (str, bytes)) or not isinstance(lines, Iterable):
        raise ContractError("invalid_request", "lines must be a sequence")
    if not callable(encode_line):
        raise ContractError("invalid_request", "encode_line is required")
    if max_lines is not None:
        _positive_limit(max_lines, "max_lines")
    _positive_limit(max_payload_bytes, "max_payload_bytes")
    prepared = _preprocessing_dict(parse_preprocessing(
        {} if preprocessing is None else preprocessing,
        "recognize request.preprocessing",
    ))
    _validate_identity(job_id, document_id, part_id, engine, model_id, prepared)

    included: list[dict] = []
    skipped: list[SkippedLine] = []
    seen: set[str] = set()
    payload_blocked = False
    for line in lines:
        line_id = _line_id(line)
        if line_id is None:
            skipped.append(SkippedLine(None, SKIP_BAD_LINE_ID))
            continue
        if line_id in seen:
            skipped.append(SkippedLine(line_id, SKIP_DUPLICATE))
            continue
        mask = _points(getattr(line, "mask", None), minimum=3, require_area=True)
        if mask is None:
            skipped.append(SkippedLine(line_id, SKIP_NO_MASK))
            continue
        baseline = _points(getattr(line, "baseline", None), minimum=2, require_area=False)
        if baseline is None:
            skipped.append(SkippedLine(line_id, SKIP_NO_BASELINE))
            continue
        if payload_blocked:
            skipped.append(SkippedLine(line_id, SKIP_PAYLOAD_CAP))
            continue
        if max_lines is not None and len(included) >= max_lines:
            skipped.append(SkippedLine(line_id, SKIP_LINE_CAP))
            continue
        image, image_reason = _encoded_image(encode_line, line)
        if image_reason is not None:
            skipped.append(SkippedLine(line_id, image_reason))
            continue
        entry = {
            "line_id": line_id,
            "image": image,
            "baseline": baseline,
            "mask": mask,
        }
        trial = _payload(
            job_id, document_id, part_id, engine, model_id, prepared, included + [entry],
        )
        if _payload_size(trial) > max_payload_bytes:
            payload_blocked = True
            skipped.append(SkippedLine(line_id, SKIP_PAYLOAD_CAP))
            continue
        included.append(entry)
        seen.add(line_id)

    if not included:
        return BuiltRecognizeRequest(None, tuple(skipped))
    request = _payload(
        job_id, document_id, part_id, engine, model_id, prepared, included,
    )
    parse_recognize_request(request)
    return BuiltRecognizeRequest(request, tuple(skipped))


def _validate_identity(job_id, document_id, part_id, engine, model_id, preprocessing):
    probe = _payload(
        job_id, document_id, part_id, engine, model_id, preprocessing,
        [{"line_id": "0", "image": "AAAA"}],
    )
    parse_recognize_request(probe)


def _payload(job_id, document_id, part_id, engine, model_id, preprocessing, lines) -> dict:
    return {
        "api_version": API_VERSION,
        "job_id": job_id,
        "document_id": document_id,
        "part_id": part_id,
        "engine": engine,
        "model_id": model_id,
        "preprocessing": preprocessing,
        "lines": lines,
    }


def _preprocessing_dict(parsed) -> dict:
    body = {}
    for name in ("line_height", "deslant", "grayscale", "preserve_aspect"):
        value = getattr(parsed, name)
        if value is not None:
            body[name] = value
    if parsed.params:
        body["params"] = {
            key: list(value) if isinstance(value, tuple) else value
            for key, value in parsed.params
        }
    return body


def _positive_limit(value, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ContractError("invalid_request", f"{label} must be a positive integer")
    return value


def _line_id(line) -> str | None:
    if hasattr(line, "pk"):
        raw = getattr(line, "pk")
    elif hasattr(line, "line_id"):
        raw = getattr(line, "line_id")
    elif hasattr(line, "id"):
        raw = getattr(line, "id")
    else:
        return None
    if isinstance(raw, bool) or not isinstance(raw, (str, int)):
        return None
    if isinstance(raw, int):
        if raw < 0:
            return None
        return str(raw)
    if not raw.strip() or raw != raw.strip():
        return None
    return raw


def _points(value, *, minimum: int, require_area: bool):
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        return None
    points = []
    for point in value:
        if isinstance(point, (str, bytes)) or not isinstance(point, Iterable):
            return None
        pair = list(point)
        if len(pair) != 2:
            return None
        coords = [_coord(item) for item in pair]
        if any(item is None for item in coords):
            return None
        points.append(coords)
    if len(points) < minimum:
        return None
    if require_area:
        xs = [item[0] for item in points]
        ys = [item[1] for item in points]
        if min(xs) >= max(xs) or min(ys) >= max(ys):
            return None
    return points


def _coord(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _encoded_image(encode_line, line):
    try:
        image = encode_line(line)
    except Exception:
        return None, SKIP_NO_IMAGE
    if image is None:
        return None, SKIP_NO_IMAGE
    if not isinstance(image, str) or not _standard_base64(image):
        return None, SKIP_BAD_IMAGE
    return image, None


def _standard_base64(value: str) -> bool:
    if value.startswith("data:") or len(value) % 4 != 0:
        return False
    return _B64.fullmatch(value) is not None


def _payload_size(payload: dict) -> int:
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return len(body)
