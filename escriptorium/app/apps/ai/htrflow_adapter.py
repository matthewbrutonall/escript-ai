"""Plan how an HTRflow page result is imported.

The page workflow is integrated. This module does not call HTRflow, open a
network connection, or write a transcription. Unknown line ids are
skipped. This planner does not create layout lines.
"""
from __future__ import annotations

from dataclasses import dataclass

HTRFLOW_INTEGRATED = True

CODE_READY = "htrflow_planned"
CODE_MISMATCH = "part_mismatch"
CODE_INVALID = "invalid_request"
CODE_LINE = "line_not_on_part"


@dataclass(frozen=True)
class HtrflowPlannedLine:
    """One trusted line. ``text`` may be empty."""

    line_id: str
    text: str
    reading_order: int | None


@dataclass(frozen=True)
class HtrflowSkippedLine:
    """A line id that is not on the trusted page."""

    line_id: str | None
    reason: str


@dataclass(frozen=True)
class HtrflowImportPlan:
    """A proposal only. ``allowed`` does not mean a layer was written."""

    allowed: bool
    code: str
    document_id: int | None
    part_id: int | None
    lines: tuple[HtrflowPlannedLine, ...]
    skipped: tuple[HtrflowSkippedLine, ...]


def plan_htrflow_import(payload, document_id, part_id, line_ids) -> HtrflowImportPlan:
    """Accept page-level regions only when the page identity matches.

    ``document_id`` and ``part_id`` are the trusted primary keys. A payload
    that names another page is refused and contributes no lines. A line id
    that is not in ``line_ids`` is skipped and is not created.
    """
    trusted_document = _positive_int(document_id)
    trusted_part = _positive_int(part_id)
    trusted_lines = _line_set(line_ids)
    if trusted_document is None or trusted_part is None or trusted_lines is None:
        return _plan(False, CODE_INVALID, None, None, (), ())
    if not isinstance(payload, dict):
        return _plan(False, CODE_INVALID, trusted_document, trusted_part, (), ())
    if (
        _positive_int(payload.get("document_id")) != trusted_document
        or _positive_int(payload.get("part_id")) != trusted_part
    ):
        return _plan(False, CODE_MISMATCH, trusted_document, trusted_part, (), ())
    regions = payload.get("regions")
    if not isinstance(regions, list):
        return _plan(False, CODE_INVALID, trusted_document, trusted_part, (), ())

    included = []
    skipped = []
    seen = set()
    for region in regions:
        if not isinstance(region, dict):
            return _plan(False, CODE_INVALID, trusted_document, trusted_part, (), ())
        lines = region.get("lines")
        if not isinstance(lines, list):
            return _plan(False, CODE_INVALID, trusted_document, trusted_part, (), ())
        for item in lines:
            if not isinstance(item, dict):
                return _plan(False, CODE_INVALID, trusted_document, trusted_part, (), ())
            line_id = _line_id(item.get("line_id"))
            if line_id is None:
                skipped.append(HtrflowSkippedLine(None, CODE_LINE))
                continue
            if line_id not in trusted_lines or line_id in seen:
                skipped.append(HtrflowSkippedLine(line_id, CODE_LINE))
                continue
            text = item.get("text")
            if not isinstance(text, str):
                return _plan(False, CODE_INVALID, trusted_document, trusted_part, (), ())
            seen.add(line_id)
            included.append(HtrflowPlannedLine(line_id, text, _order(item.get("reading_order"))))
    included.sort(key=lambda line: ((line.reading_order is None), line.reading_order or 0, line.line_id))
    return _plan(True, CODE_READY, trusted_document, trusted_part, tuple(included), tuple(skipped))


def _plan(allowed, code, document_id, part_id, lines, skipped):
    return HtrflowImportPlan(allowed, code, document_id, part_id, lines, skipped)


def _positive_int(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _line_id(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return str(value)
    if isinstance(value, str) and value.strip() and value == value.strip():
        return value
    return None


def _line_set(line_ids):
    if isinstance(line_ids, (str, bytes)) or line_ids is None:
        return None
    try:
        values = list(line_ids)
    except TypeError:
        return None
    collected = set()
    for value in values:
        line_id = _line_id(value)
        if line_id is None:
            return None
        collected.add(line_id)
    return collected


def _order(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value
