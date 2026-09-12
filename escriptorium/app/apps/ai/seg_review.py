"""AI segmentation review (ARCHITECTURE.md §6).

Judgements on existing kraken lines: spurious, missed, order, typology.
Never overwrites Line.mask / baseline.
"""
from __future__ import annotations

import json
import re

REVIEW_KEY = "review"

# Bbox IoU at or above this → smaller box is a likely duplicate (newspaper
# fat polygons sat around 0.15–0.53). Geometry flags these; VLM confirms extras.
SPURIOUS_IOU = 0.20

PROMPT = (
    "You are reviewing kraken line segmentation. Numbered boxes are EXISTING "
    "lines (1-based). Do NOT draw polygons. Do NOT return pixel masks. "
    "FIRST report boxes that should not exist and writing that has no box. "
    "Typology is optional and LAST. Return ONLY JSON:\n"
    "{"
    '"spurious": [{"n": 12, "reason": "duplicate of box 11"}], '
    '"missed": [{"after_n": 8, "where": "left margin", "reason": "unboxed text"}], '
    '"order": [{"n": 5, "should_follow_n": 3, "reason": "column jump"}], '
    '"typology": [{"n": 1, "type": "heading|body|margin|page_number", "reason": "..."}]'
    "}\n"
    "spurious = empty, junk, a picture, or a near-duplicate of another box. "
    "missed = visible text with no box. "
    "order = reading order is wrong. "
    "typology = heading vs body vs margin vs page_number. "
    "Prefer spurious and missed. Empty typology is fine. Empty lists are fine."
)


def _bbox(mask):
    xs = [p[0] for p in mask]
    ys = [p[1] for p in mask]
    return min(xs), min(ys), max(xs), max(ys)


def _area(box):
    return max(0, box[2] - box[0]) * max(0, box[3] - box[1])


def _iou(a, b):
    x0 = max(a[0], b[0])
    y0 = max(a[1], b[1])
    x1 = min(a[2], b[2])
    y1 = min(a[3], b[3])
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    if not inter:
        return 0.0
    union = _area(a) + _area(b) - inter
    return inter / union if union else 0.0


def overlap_spurious_items(masks, threshold=SPURIOUS_IOU):
    """Flag the smaller box of each overlapping pair. 1-based n. No mask writes."""
    scored = []
    for i, mask in enumerate(masks):
        if not mask or len(mask) < 3:
            continue
        box = _bbox(mask)
        scored.append((i, box, _area(box)))
    scored.sort(key=lambda row: -row[2])
    kept = []
    flagged = []
    for i, box, _area_i in scored:
        n = i + 1
        best_iou = 0.0
        dup_of = None
        for j, other, _area_j in kept:
            iou = _iou(box, other)
            if iou >= threshold and iou > best_iou:
                best_iou = iou
                dup_of = j + 1
        if dup_of is not None:
            flagged.append({
                "n": n,
                "reason": f"overlaps box {dup_of} (IoU {best_iou:.2f})",
                "source": "geometry",
            })
        else:
            kept.append((i, box, _area_i))
    flagged.sort(key=lambda item: item["n"])
    return flagged


def merge_geom_spurious(geom_items, parsed):
    """Geometry flags first; VLM may add more. Drop typology on flagged ns."""
    parsed = {
        "spurious": list(parsed.get("spurious") or []),
        "missed": list(parsed.get("missed") or []),
        "order": list(parsed.get("order") or []),
        "typology": list(parsed.get("typology") or []),
    }
    seen = {item["n"] for item in geom_items if "n" in item}
    extra = [
        item for item in parsed["spurious"]
        if item.get("n") not in seen
    ]
    parsed["spurious"] = list(geom_items) + extra
    parsed["typology"] = [
        item for item in parsed["typology"]
        if item.get("n") not in seen
    ]
    return parsed


def parse_review_json(raw: str, n_lines: int) -> dict:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return {"spurious": [], "missed": [], "order": [], "typology": []}
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            return {"spurious": [], "missed": [], "order": [], "typology": []}
    if not isinstance(obj, dict):
        return {"spurious": [], "missed": [], "order": [], "typology": []}

    def _n(val):
        try:
            n = int(val)
        except (TypeError, ValueError):
            return None
        if n < 1 or n > n_lines:
            return None
        return n

    spurious = []
    for item in obj.get("spurious") or []:
        if not isinstance(item, dict):
            continue
        n = _n(item.get("n"))
        if n is None:
            continue
        spurious.append({"n": n, "reason": str(item.get("reason") or "")[:500]})

    missed = []
    for item in obj.get("missed") or []:
        if not isinstance(item, dict):
            continue
        missed.append({
            "after_n": _n(item.get("after_n")),
            "where": str(item.get("where") or "")[:200],
            "reason": str(item.get("reason") or "")[:500],
        })

    order = []
    for item in obj.get("order") or []:
        if not isinstance(item, dict):
            continue
        n = _n(item.get("n"))
        follow = _n(item.get("should_follow_n"))
        if n is None:
            continue
        order.append({
            "n": n,
            "should_follow_n": follow,
            "reason": str(item.get("reason") or "")[:500],
        })

    typology = []
    allowed = {"heading", "body", "margin", "page_number"}
    for item in obj.get("typology") or []:
        if not isinstance(item, dict):
            continue
        n = _n(item.get("n"))
        kind = str(item.get("type") or "").strip().lower().replace(" ", "_")
        if n is None or kind not in allowed:
            continue
        typology.append({
            "n": n,
            "type": kind,
            "reason": str(item.get("reason") or "")[:500],
        })

    return {
        "spurious": spurious,
        "missed": missed,
        "order": order,
        "typology": typology,
    }


def suggestions_from_review(lines, parsed) -> list:
    """Map 1-based box numbers onto Line objects. No mask writes."""
    by_n = {i + 1: ln for i, ln in enumerate(lines)}
    out = []
    for item in parsed.get("spurious") or []:
        ln = by_n.get(item["n"])
        if ln is None:
            continue
        out.append({
            "kind": "spurious",
            "line": ln,
            "payload": item,
        })
    for item in parsed.get("missed") or []:
        after = by_n.get(item.get("after_n")) if item.get("after_n") else None
        out.append({
            "kind": "missed",
            "line": after,
            "payload": item,
        })
    for item in parsed.get("order") or []:
        ln = by_n.get(item["n"])
        if ln is None:
            continue
        out.append({
            "kind": "order",
            "line": ln,
            "payload": item,
        })
    for item in parsed.get("typology") or []:
        ln = by_n.get(item["n"])
        if ln is None:
            continue
        out.append({
            "kind": "typology",
            "line": ln,
            "payload": item,
        })
    return out
