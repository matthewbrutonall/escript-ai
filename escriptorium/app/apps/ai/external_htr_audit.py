"""Store external HTR audit rows from a request plan or an apply plan.

The caller passes an ``ExternalHtrPlan`` or an ``ExternalHtrApplyPlan``.
This module does not call an engine, encode an image, or write a
transcription layer. Recording an apply plan twice is rejected.
"""
from __future__ import annotations

from ai.external_htr_apply_plan import ExternalHtrApplyPlan
from ai.external_htr_plan import ExternalHtrPlan

STATUS_PLANNED = "planned"
STATUS_FAILED = "failed"
STATUS_COMPLETED = "completed"
LINE_INCLUDED = "included"
LINE_SKIPPED = "skipped"

CODE_ALREADY = "already_finalized"
CODE_INVALID = "invalid_plan"

_ERRORS = {
    CODE_ALREADY: "audit job is already finalized",
    CODE_INVALID: "audit plan could not be stored",
}

_MODEL_ID_LIMIT = 256
_ENGINE_LIMIT = 256
_VERSION_LIMIT = 256
_API_LIMIT = 16
_CODE_LIMIT = 64
_MESSAGE_LIMIT = 256
_LINE_ID_LIMIT = 256
_LAYER_LIMIT = 128
_COUNT_LIMIT = 2147483647

# updated_at is auto_now. Django 4.2 refreshes it only when the name is listed.
_LINE_FIELDS = (
    "position", "text", "confidence", "timing_ms", "warnings", "status", "code",
    "updated_at",
)
_APPLY_FIELDS = (
    "status", "code", "message", "result_count", "warning_count", "empty_text_count",
    "updated_at",
)
_SUCCESS_FIELDS = _APPLY_FIELDS + (
    "layer_source", "engine", "model_id", "model_version", "api_version",
)


class ExternalHtrAuditError(Exception):
    """A fixed failure while storing an audit row.

    The message is chosen from the code. It does not include plan text.
    """

    def __init__(self, code: str):
        self.code = code
        super().__init__(_ERRORS[code])


def record_external_htr_plan(
    plan,
    *,
    document,
    config,
    part=None,
    created_by=None,
    job_model=None,
    line_model=None,
    atomic=None,
):
    """Create one audit job and one row for each skipped line id.

    ``document`` and ``config`` are stored as references. Their titles,
    paths, endpoints, and metadata are not copied. A line with no id, or
    a repeated id, is counted and does not add another row.
    """
    if (
        not isinstance(plan, ExternalHtrPlan)
        or document is None
        or config is None
        or not isinstance(plan.skipped, tuple)
        or plan.would_send not in (True, False)
    ):
        raise ExternalHtrAuditError(CODE_INVALID)
    job_model, line_model = _models(job_model, line_model)
    with _atomic(atomic):
        job = job_model.objects.create(
            document=document,
            part=part,
            config=config,
            created_by=created_by,
            model_id=_clip(plan.model_id, _MODEL_ID_LIMIT),
            status=STATUS_PLANNED if plan.would_send else STATUS_FAILED,
            layer_source=None,
            engine=_clip(plan.engine, _ENGINE_LIMIT),
            model_version="",
            api_version="",
            requested_line_count=_count(plan.included_count),
            skipped_line_count=_count(len(plan.skipped)),
            result_count=0,
            warning_count=0,
            empty_text_count=0,
            code=_clip("" if plan.code is None else plan.code, _CODE_LIMIT),
            message=_clip(plan.message, _MESSAGE_LIMIT),
        )
        seen = set()
        for position, skipped in enumerate(plan.skipped):
            line_id = skipped.line_id
            if not _line_id(line_id) or line_id in seen:
                continue
            seen.add(line_id)
            line_model.objects.create(
                job=job,
                line_id=line_id,
                position=position,
                text="",
                confidence=None,
                timing_ms=0,
                warnings=list(),
                status=LINE_SKIPPED,
                code=_clip(skipped.reason, _CODE_LIMIT),
            )
    return job


def record_external_htr_apply(job, plan, *, line_model=None, atomic=None):
    """Record an apply plan on a planned audit job.

    A successful plan stores provenance and one included row per line, in
    plan order. An existing skipped row with the same line id is updated
    instead of inserted again. A failed plan stores the fixed code,
    message, and counts, and does not add included rows. A job that is
    not still planned is left unchanged.
    """
    if job is None or not isinstance(plan, ExternalHtrApplyPlan):
        raise ExternalHtrAuditError(CODE_INVALID)
    if not isinstance(plan.results, tuple) or plan.would_write not in (True, False):
        raise ExternalHtrAuditError(CODE_INVALID)
    if line_model is None:
        line_model = _models(None, None)[1]
    if getattr(job, "status", None) != STATUS_PLANNED:
        raise ExternalHtrAuditError(CODE_ALREADY)
    with _atomic(atomic):
        if plan.would_write:
            _store_results(job, plan, line_model)
            job.layer_source = _layer(plan.layer_source)
            job.engine = _clip(plan.engine, _ENGINE_LIMIT)
            job.model_id = _clip(plan.model_id, _MODEL_ID_LIMIT)
            job.model_version = _clip(plan.model_version, _VERSION_LIMIT)
            job.api_version = _clip(plan.api_version, _API_LIMIT)
            job.status = STATUS_COMPLETED
            job.code = ""
            fields = _SUCCESS_FIELDS
        else:
            job.status = STATUS_FAILED
            job.code = _clip("" if plan.code is None else plan.code, _CODE_LIMIT)
            fields = _APPLY_FIELDS
        job.message = _clip(plan.message, _MESSAGE_LIMIT)
        job.result_count = _count(plan.result_count)
        job.warning_count = _count(plan.warning_count)
        job.empty_text_count = _count(plan.empty_text_count)
        job.save(update_fields=list(fields))
    return job


def _store_results(job, plan, line_model):
    prepared = []
    seen = set()
    for position, line in enumerate(plan.results):
        line_id = line.line_id
        if not _line_id(line_id) or line_id in seen:
            raise ExternalHtrAuditError(CODE_INVALID)
        seen.add(line_id)
        prepared.append((position, line_id, _result_values(position, line)))
    existing = {
        row.line_id: row for row in line_model.objects.filter(job=job)
    }
    for position, line_id, values in prepared:
        current = existing.get(line_id)
        if current is None:
            line_model.objects.create(job=job, line_id=line_id, **values)
            continue
        for name, value in values.items():
            setattr(current, name, value)
        current.save(update_fields=list(_LINE_FIELDS))


def _result_values(position, line):
    text = line.text if isinstance(line.text, str) else ""
    return {
        "position": position,
        "text": text,
        "confidence": _confidence(line.confidence),
        "timing_ms": _timing(line.timing_ms),
        "warnings": _warnings(line.warnings),
        "status": LINE_INCLUDED,
        "code": "",
    }


def _models(job_model, line_model):
    if job_model is None or line_model is None:
        from ai.models import ExternalHTRJob, ExternalHTRLineResult
        job_model = ExternalHTRJob if job_model is None else job_model
        line_model = ExternalHTRLineResult if line_model is None else line_model
    return job_model, line_model


def _atomic(atomic):
    if atomic is not None:
        return atomic()
    from django.db import transaction
    return transaction.atomic()


def _line_id(value):
    return isinstance(value, str) and 0 < len(value) <= _LINE_ID_LIMIT


def _warnings(value):
    if isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
        return list(value)
    return []


def _confidence(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _timing(value):
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    if value < 0 or value > _COUNT_LIMIT:
        return 0
    return value


def _count(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    if value > _COUNT_LIMIT:
        return _COUNT_LIMIT
    return value


def _clip(value, limit):
    if not isinstance(value, str):
        return ""
    return value[:limit]


def _layer(value):
    if not isinstance(value, str) or not value:
        return None
    return value[:_LAYER_LIMIT]
