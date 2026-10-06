"""Encode one line crop as a PNG for an external HTR request.

The caller supplies the page image and the line geometry. This module
does not open a document path, call an engine, or write a transcription.
Unusable geometry is rejected. The crop is not replaced with the page.
"""
from __future__ import annotations

import base64
import io
import math
from collections.abc import Iterable
from dataclasses import dataclass

from PIL import Image

CODE_NO_MASK = "no_mask"
CODE_NO_BASELINE = "no_baseline"
CODE_BAD_GEOMETRY = "bad_geometry"
CODE_BAD_IMAGE = "bad_image"
CODE_TOO_LARGE = "too_large"
CODE_BAD_PADDING = "bad_padding"
CODE_BAD_MODE = "bad_mode"
CODE_BAD_LIMIT = "bad_limit"

MAX_PAD = 32
MAX_WIDTH = 4096
MAX_HEIGHT = 1024
_MODES = frozenset({"L", "RGB"})


@dataclass(frozen=True)
class LineImage:
    """A raw base64 PNG, or a fixed failure code and no image."""

    image: str | None
    code: str | None


def encode_line_image(
    image,
    line,
    *,
    pad: int = 0,
    mode: str = "RGB",
    max_width: int | None = None,
    max_height: int | None = None,
) -> LineImage:
    """Crop ``line`` from ``image`` and return standard base64 PNG.

    ``pad`` is pixels around the mask box, from 0 to 32. ``mode`` is
    ``RGB`` or ``L``. A limit cannot exceed the built-in ceiling. The
    result code is a fixed token and does not include caller data.
    """
    page = _page(image)
    if page is None:
        return _fail(CODE_BAD_IMAGE)
    padding = _pad(pad)
    if padding is None:
        return _fail(CODE_BAD_PADDING)
    if mode not in _MODES:
        return _fail(CODE_BAD_MODE)
    width_limit = _limit(max_width, MAX_WIDTH)
    height_limit = _limit(max_height, MAX_HEIGHT)
    if width_limit is None or height_limit is None:
        return _fail(CODE_BAD_LIMIT)
    mask, mask_code = _points(getattr(line, "mask", None), 3, CODE_NO_MASK)
    if mask_code is not None:
        return _fail(mask_code)
    if _zero_area(mask):
        return _fail(CODE_NO_MASK)
    baseline_code = _points(
        getattr(line, "baseline", None), 2, CODE_NO_BASELINE,
    )[1]
    if baseline_code is not None:
        return _fail(baseline_code)
    box = _box(mask, page.width, page.height, padding)
    if box is None:
        return _fail(CODE_BAD_GEOMETRY)
    left, top, right, bottom = box
    if right - left > width_limit or bottom - top > height_limit:
        return _fail(CODE_TOO_LARGE)
    return _png(page, box, mode)


def _page(image):
    if isinstance(image, (str, bytes, bytearray)) or not isinstance(image, Image.Image):
        return None
    width = getattr(image, "width", None)
    height = getattr(image, "height", None)
    if _size(width) is None or _size(height) is None:
        return None
    return image


def _pad(value):
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 0 or value > MAX_PAD:
        return None
    return value


def _limit(value, ceiling: int):
    if value is None:
        return ceiling
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 1 or value > ceiling:
        return None
    return value


def _size(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _points(value, minimum: int, missing: str):
    if value is None or isinstance(value, (str, bytes, bytearray)):
        return None, missing
    if not isinstance(value, Iterable):
        return None, missing
    points = []
    for point in value:
        if isinstance(point, (str, bytes, bytearray)) or not isinstance(point, Iterable):
            return None, CODE_BAD_GEOMETRY
        pair = list(point)
        if len(pair) != 2:
            return None, CODE_BAD_GEOMETRY
        coords = []
        for item in pair:
            coord = _coord(item)
            if coord is None:
                return None, CODE_BAD_GEOMETRY
            coords.append(coord)
        points.append(coords)
    if len(points) < minimum:
        return None, missing
    return points, None


def _coord(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _zero_area(points) -> bool:
    xs = [item[0] for item in points]
    ys = [item[1] for item in points]
    return min(xs) >= max(xs) or min(ys) >= max(ys)


def _box(points, width: int, height: int, pad: int):
    xs = [item[0] for item in points]
    ys = [item[1] for item in points]
    left = math.floor(min(xs)) - pad
    top = math.floor(min(ys)) - pad
    right = math.ceil(max(xs)) + pad
    bottom = math.ceil(max(ys)) + pad
    left = max(0, left)
    top = max(0, top)
    right = min(width, right)
    bottom = min(height, bottom)
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _png(image, box, mode: str) -> LineImage:
    try:
        cropped = image.crop(box).convert(mode)
        buffer = io.BytesIO()
        cropped.save(buffer, format="PNG")
        payload = base64.standard_b64encode(buffer.getvalue()).decode("ascii")
    except Exception:
        return _fail(CODE_BAD_IMAGE)
    if not payload or payload.startswith("data:") or len(payload) % 4 != 0:
        return _fail(CODE_BAD_IMAGE)
    return LineImage(payload, None)


def _fail(code: str) -> LineImage:
    return LineImage(None, code)
