"""Plan a new transcription layer from a completed external HTR audit.

The checks accept plain objects, so they can run without Django. The loader
reads one audit and the part's current line primary keys. Neither function
calls an engine, opens an image, changes the audit, or writes transcription
output. Transcription jobs do not import this module.
"""
from __future__ import annotations

from dataclasses import dataclass

# version_source holds 128 characters. This is the same limit as
# external_htr_apply_plan.VERSION_SOURCE_LIMIT. Transcription.name holds 512.
# A truncated stamp is not a unique job or layer id.
# The transcription content field holds 2048 characters. Django counts them with len().
VERSION_SOURCE_LIMIT = 128
LAYER_NAME_LIMIT = 512
CONTENT_LIMIT = 2048
LAYER_SOURCE_PREFIX = "external-htr"
LINE_ID_LIMIT = 256

STATUS_COMPLETED = "completed"
LINE_INCLUDED = "included"
LINE_SKIPPED = "skipped"

CODE_READY = "writeback_planned"
MESSAGE_READY = "new transcription layer would be created"

CODE_PLANNED = "audit_planned"
MESSAGE_PLANNED = "audit is still planned"
CODE_SENT = "audit_sent"
MESSAGE_SENT = "audit was sent"
CODE_FAILED = "audit_failed"
MESSAGE_FAILED = "audit failed"
CODE_CANCELLED = "audit_cancelled"
MESSAGE_CANCELLED = "audit was cancelled"
CODE_INCOMPLETE = "audit_incomplete"
MESSAGE_INCOMPLETE = "audit is incomplete"
CODE_PART = "part_mismatch"
MESSAGE_PART = "part does not belong to the document"
CODE_DUPLICATE = "duplicate_line"
MESSAGE_DUPLICATE = "included line is duplicated"
CODE_MALFORMED = "malformed_line"
MESSAGE_MALFORMED = "included line id is malformed"
CODE_LINE = "line_not_on_part"
MESSAGE_LINE = "included line is not on the part"
CODE_TEXT = "line_text_too_long"
MESSAGE_TEXT = "included line text is too long"

_STATUS_FAILURES = {
    "planned": (CODE_PLANNED, MESSAGE_PLANNED),
    "sent": (CODE_SENT, MESSAGE_SENT),
    "failed": (CODE_FAILED, MESSAGE_FAILED),
    "cancelled": (CODE_CANCELLED, MESSAGE_CANCELLED),
}


@dataclass(frozen=True)
class PlannedImportLine:
    """One included line, in stored order. Text may be empty."""

    line_id: str
    text: str


@dataclass(frozen=True)
class ExternalHtrWritebackPlan:
    """A proposal to create one new layer. ``message`` is a fixed sentence.

    ``provenance_stamp`` is the 128-character version_source value. It is
    not a layer id. ``layer_name`` is only a proposed name; this plan does
    not choose an existing layer.
    """

    allowed: bool
    code: str
    message: str
    job_id: int | None
    document_id: int | None
    part_id: int | None
    layer_name: str
    provenance_stamp: str
    engine: str
    model_id: str
    model_version: str
    api_version: str
    lines: tuple[PlannedImportLine, ...]
    included_count: int
    skipped_count: int
    empty_text_count: int


def provenance_stamp(engine: str, model_id: str) -> str:
    """Return ``external-htr:<engine>:<model_id>`` cut to 128 characters."""
    return f"{LAYER_SOURCE_PREFIX}:{engine}:{model_id}"[:VERSION_SOURCE_LIMIT]


def plan_external_htr_writeback(job, results, part_line_ids, *, part_document_id):
    """Describe a new layer for one completed audit.

    ``part_line_ids`` are current line primary keys on the audit part.
    Included ``line_id`` values must be those primary keys as decimal
    strings. ``external_id`` is not read. ``results`` are used in the order
    given. The loader passes the stored ``position``, ``pk`` order.

    ``requested_line_count`` and ``skipped_line_count`` stay as stored when
    the request was recorded. A successful apply rewrites skipped rows in
    place and does not change those two totals, so they are not compared.
    """
    job_id, document_id, part_id = _job_ids(job)
    if job is None:
        return _reject(CODE_INCOMPLETE, MESSAGE_INCOMPLETE, None, None, None)
    status = getattr(job, "status", None)
    if status in _STATUS_FAILURES:
        code, message = _STATUS_FAILURES[status]
        return _reject(code, message, job_id, document_id, part_id)
    if (
        status != STATUS_COMPLETED
        or job_id is None
        or document_id is None
        or part_id is None
    ):
        return _reject(CODE_INCOMPLETE, MESSAGE_INCOMPLETE, job_id, document_id, part_id)
    if _positive_int(part_document_id) != document_id:
        return _reject(CODE_PART, MESSAGE_PART, job_id, document_id, part_id)

    engine = _required_text(getattr(job, "engine", None))
    model_id = _required_text(getattr(job, "model_id", None))
    model_version = _required_text(getattr(job, "model_version", None))
    api_version = _required_text(getattr(job, "api_version", None))
    stamp = None if engine is None or model_id is None else provenance_stamp(engine, model_id)
    if (
        engine is None
        or model_id is None
        or model_version is None
        or api_version is None
        or getattr(job, "layer_source", None) != stamp
        or getattr(job, "code", None) not in ("", None)
    ):
        return _reject(CODE_INCOMPLETE, MESSAGE_INCOMPLETE, job_id, document_id, part_id)

    rows = _rows(results)
    allowed_ids = _allowed_ids(part_line_ids)
    if rows is None or allowed_ids is None:
        return _reject(CODE_INCOMPLETE, MESSAGE_INCOMPLETE, job_id, document_id, part_id)
    included, skipped_count, intact = rows
    if not intact:
        return _reject(CODE_INCOMPLETE, MESSAGE_INCOMPLETE, job_id, document_id, part_id)
    texts, empty_text_count, warning_count = _included_payload(included)
    if texts is None:
        return _reject(CODE_INCOMPLETE, MESSAGE_INCOMPLETE, job_id, document_id, part_id)
    if any(len(text) > CONTENT_LIMIT for text in texts):
        return _reject(CODE_TEXT, MESSAGE_TEXT, job_id, document_id, part_id)
    if (
        _stored_count(job, "result_count") != len(included)
        or _stored_count(job, "empty_text_count") != empty_text_count
        or _stored_count(job, "warning_count") != warning_count
    ):
        return _reject(CODE_INCOMPLETE, MESSAGE_INCOMPLETE, job_id, document_id, part_id)

    lines, problem = _import_lines(included, texts, allowed_ids)
    if problem is not None:
        code, message = problem
        return _reject(code, message, job_id, document_id, part_id)
    return ExternalHtrWritebackPlan(
        allowed=True,
        code=CODE_READY,
        message=MESSAGE_READY,
        job_id=job_id,
        document_id=document_id,
        part_id=part_id,
        layer_name=_layer_name(job_id, engine, model_id),
        provenance_stamp=stamp,
        engine=engine,
        model_id=model_id,
        model_version=model_version,
        api_version=api_version,
        lines=lines,
        included_count=len(lines),
        skipped_count=skipped_count,
        empty_text_count=empty_text_count,
    )


def load_external_htr_writeback_plan(job_id):
    """Load one audit and plan a new layer from its current part lines.

    The part image and each line's ``external_id`` are not read.
    """
    if isinstance(job_id, bool) or not isinstance(job_id, int) or job_id < 1:
        return _reject(CODE_INCOMPLETE, MESSAGE_INCOMPLETE, None, None, None)
    from ai.models import ExternalHTRJob, ExternalHTRLineResult
    from core.models import Line

    try:
        job = ExternalHTRJob.objects.select_related("part").get(pk=job_id)
    except ExternalHTRJob.DoesNotExist:
        return _reject(CODE_INCOMPLETE, MESSAGE_INCOMPLETE, None, None, None)
    results = list(
        ExternalHTRLineResult.objects.filter(job_id=job.pk).order_by("position", "pk")
    )
    part = job.part
    if part is None:
        part_document_id = None
        part_line_ids = ()
    else:
        part_document_id = part.document_id
        part_line_ids = list(
            Line.objects.filter(document_part_id=part.pk).values_list("pk", flat=True)
        )
    return plan_external_htr_writeback(
        job,
        results,
        part_line_ids,
        part_document_id=part_document_id,
    )


def _job_ids(job):
    if job is None:
        return None, None, None
    part_raw = getattr(job, "part_id", None)
    part_id = None if part_raw is None else _positive_int(part_raw)
    return (
        _positive_int(getattr(job, "pk", None)),
        _positive_int(getattr(job, "document_id", None)),
        part_id,
    )


def _reject(code, message, job_id, document_id, part_id):
    return ExternalHtrWritebackPlan(
        allowed=False,
        code=code,
        message=message,
        job_id=job_id,
        document_id=document_id,
        part_id=part_id,
        layer_name="",
        provenance_stamp="",
        engine="",
        model_id="",
        model_version="",
        api_version="",
        lines=(),
        included_count=0,
        skipped_count=0,
        empty_text_count=0,
    )


def _layer_name(job_id, engine, model_id) -> str:
    suffix = f":job-{job_id}"
    prefix = f"{LAYER_SOURCE_PREFIX}:{engine}:{model_id}"
    if len(suffix) >= LAYER_NAME_LIMIT:
        return suffix[-LAYER_NAME_LIMIT:]
    room = LAYER_NAME_LIMIT - len(suffix)
    if len(prefix) > room:
        prefix = prefix[:room]
    return prefix + suffix


def _rows(results):
    if isinstance(results, (str, bytes)) or results is None:
        return None
    try:
        rows = list(results)
    except TypeError:
        return None
    included = []
    skipped_count = 0
    for row in rows:
        status = getattr(row, "status", None)
        if status == LINE_INCLUDED:
            included.append(row)
        elif status == LINE_SKIPPED:
            skipped_count += 1
        else:
            return included, skipped_count, False
    return included, skipped_count, True


def _included_payload(included):
    texts = []
    empty_text_count = 0
    warning_count = 0
    for row in included:
        text = getattr(row, "text", None)
        warnings = getattr(row, "warnings", None)
        if not isinstance(text, str):
            return None, 0, 0
        if (
            not isinstance(warnings, (list, tuple))
            or any(not isinstance(item, str) for item in warnings)
        ):
            return None, 0, 0
        texts.append(text)
        if text == "":
            empty_text_count += 1
        warning_count += len(warnings)
    return texts, empty_text_count, warning_count


def _import_lines(included, texts, allowed_ids):
    seen = set()
    lines = []
    for row, text in zip(included, texts):
        line_id = getattr(row, "line_id", None)
        if not _canonical_line_id(line_id):
            return None, (CODE_MALFORMED, MESSAGE_MALFORMED)
        if line_id in seen:
            return None, (CODE_DUPLICATE, MESSAGE_DUPLICATE)
        if line_id not in allowed_ids:
            return None, (CODE_LINE, MESSAGE_LINE)
        seen.add(line_id)
        lines.append(PlannedImportLine(line_id=line_id, text=text))
    return tuple(lines), None


def _allowed_ids(part_line_ids):
    if isinstance(part_line_ids, (str, bytes)) or part_line_ids is None:
        return None
    try:
        items = list(part_line_ids)
    except TypeError:
        return None
    allowed = set()
    for item in items:
        pk = _pk_str(item)
        if pk is not None:
            allowed.add(pk)
    return allowed


def _pk_str(value):
    candidate = value
    if not isinstance(value, (bool, int, str)) and value is not None:
        candidate = getattr(value, "pk", None)
    if isinstance(candidate, str):
        return candidate if _canonical_line_id(candidate) else None
    if isinstance(candidate, bool) or not isinstance(candidate, int) or candidate < 1:
        return None
    return str(candidate)


def _canonical_line_id(value) -> bool:
    if not isinstance(value, str) or not value or len(value) > LINE_ID_LIMIT:
        return False
    if value[0] == "0":
        return False
    return all(character in "0123456789" for character in value)


def _required_text(value):
    if not isinstance(value, str) or value == "":
        return None
    return value


def _positive_int(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _stored_count(job, name):
    value = getattr(job, name, None)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value
