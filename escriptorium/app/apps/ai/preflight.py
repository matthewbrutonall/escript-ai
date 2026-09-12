"""
Pre-flight guards (ARCHITECTURE.md §5.A) — run BEFORE spending a VLM call.

Empirical failure modes from phase0-spike:
  - overlapping masks (HTRflow ALTO, fat newspaper polygons) → colour keys
    don't map to lines. Do not send the *group overlay*. Pipeline may still
    crop each remaining line on its own (no sibling colours).
  - degenerate / fragment lines → VLM hallucinates on garbage (1866 last crop:
    a short leftover next to a full-width line). Drop *that line*, send the rest.

Height/aspect alone do not catch the 1866 hallucination crop (those masks are
56×208 and 65×1711). Relative width vs the widest line in the crop does.
"""
from __future__ import annotations

from typing import NamedTuple

from shapely.geometry import Polygon

OVERLAP_THRESHOLD = 0.10
MIN_LINE_HEIGHT_PX = 8
MAX_ASPECT = 60.0
# In a multi-line crop, a mask this narrow vs the widest sibling is a leftover
# fragment (1866 crop 2: 208/1711 ≈ 0.12). Single-line crops are not filtered.
FRAGMENT_WIDTH_RATIO = 0.15


class CropDecision(NamedTuple):
    send: bool                 # False → do not call the VLM on this crop
    reason: str
    keep: list                 # indices to send
    drop: list                 # [(index, reason), ...]


def _bbox(mask):
    xs = [x for x, _ in mask]; ys = [y for _, y in mask]
    return min(xs), min(ys), max(xs), max(ys)


def _poly(mask):
    poly = Polygon(mask)
    if not poly.is_valid:
        poly = poly.buffer(0)
    return poly


def max_overlap(masks) -> float:
    """Max pairwise polygon-overlap fraction.

    Do not use bbox area here: dense, parallel handwriting can have stacked
    bounding boxes even when the actual kraken line masks do not touch.
    """
    worst = 0.0
    polys = [_poly(m) for m in masks]
    for i in range(len(polys)):
        a = polys[i]
        aa = max(1.0, a.area)
        for j in range(i + 1, len(polys)):
            b = polys[j]
            bb = max(1.0, b.area)
            inter = a.intersection(b).area
            worst = max(worst, inter / min(aa, bb))
    return worst


def is_degenerate(mask) -> bool:
    x0, y0, x1, y1 = _bbox(mask)
    h = y1 - y0
    if h < MIN_LINE_HEIGHT_PX:
        return True
    return (x1 - x0) / max(1, h) > MAX_ASPECT


def evaluate_crop(masks) -> CropDecision:
    """Decide what, if anything, of this crop may be sent to a VLM.

    Drop fragments/degenerates *before* the overlap test. A 9px junk mask
    sitting on a real line must not skip the other five lines in the crop
    (modern Hindi demo, part 8 crop 2). Overlap among remaining full lines
    still blocks the crop (HTRflow shattered boxes).
    """
    n = len(masks)
    if n == 0:
        return CropDecision(False, "empty crop", [], [])

    widths = [_bbox(m)[2] - _bbox(m)[0] for m in masks]
    max_w = max(widths)
    keep, drop = [], []
    for i, m in enumerate(masks):
        if is_degenerate(m):
            drop.append((i, "degenerate"))
        elif n > 1 and widths[i] < FRAGMENT_WIDTH_RATIO * max_w:
            drop.append((i, "fragment"))
        else:
            keep.append(i)
    if not keep:
        return CropDecision(False, "all lines dropped as degenerate/fragment", [], drop)

    kept_masks = [masks[i] for i in keep]
    ov = max_overlap(kept_masks)
    if ov > OVERLAP_THRESHOLD:
        return CropDecision(
            False,
            f"mask overlap {ov:.0%} > {OVERLAP_THRESHOLD:.0%} — re-segment",
            [],
            drop + [(i, "overlap") for i in keep],
        )
    return CropDecision(True, "ok", keep, drop)


def check_crop(masks) -> tuple[bool, str]:
    """Back-compat: can *any* of this crop be sent?"""
    d = evaluate_crop(masks)
    return d.send, d.reason
