"""AI segmentation review (ARCHITECTURE.md §6).

Judgements on existing kraken lines: spurious, missed, order, typology.
Never overwrites Line.mask / baseline.
"""
from __future__ import annotations

import json
import re

REVIEW_KEY = "review"

PROMPT = (
    "You are reviewing line segmentation on a manuscript or printed page. "
    "Each existing kraken line is a numbered box. Numbers start at 1. "
    "Do NOT draw new polygons. Do NOT return pixel masks. "
    "Return ONLY JSON of the form:\n"
    "{"
    '"spurious": [{"n": 12, "reason": "duplicate of box 11"}], '
    '"missed": [{"after_n": 8, "where": "left margin", "reason": "unboxed text"}], '
    '"order": [{"n": 5, "should_follow_n": 3, "reason": "column jump"}], '
    '"typology": [{"n": 1, "type": "heading|body|margin|page_number", "reason": "..."}]'
    "}\n"
    "spurious = box is junk, empty, or a near-duplicate of another box. "
    "missed = visible text with no box. "
    "order = reading order is wrong. "
    "typology = heading vs body vs margin vs page_number. "
    "Only report real problems. Empty lists are fine."
)


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
