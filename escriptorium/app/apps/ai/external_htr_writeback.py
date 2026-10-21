"""Create one transcription layer from a completed external HTR audit.

Callable from code and tests. No command, task, or view imports this module.
The audit status stays completed. ``imported`` is the durable marker, and
``imported_transcription`` is the layer link. The marker is set only after
the line rows, the raw gate, and its review sample have been created, in
the same transaction.
"""
from __future__ import annotations

from dataclasses import dataclass

from django.db import IntegrityError, transaction

from ai.external_htr_writeback_plan import (
    CODE_INCOMPLETE,
    MESSAGE_INCOMPLETE,
    load_external_htr_writeback_plan,
)
from ai.gate import build_gate_sample

CODE_IMPORTED = "writeback_imported"
MESSAGE_IMPORTED = "new transcription layer was created"
CODE_ALREADY = "already_imported"
MESSAGE_ALREADY = "audit was already imported"
CODE_COLLISION = "layer_name_taken"
MESSAGE_COLLISION = "transcription layer name is taken"
CODE_FAILED = "writeback_failed"
MESSAGE_FAILED = "transcription layer was not created"
CODE_SAMPLE = "review_sample_empty"
MESSAGE_SAMPLE = "review sample was not created"


class _NoReviewSample(Exception):
    """Raised inside the import transaction when the sample has no lines."""


@dataclass(frozen=True)
class ExternalHtrWritebackResult:
    """Outcome of one import attempt. ``message`` is a fixed sentence."""

    imported: bool
    code: str
    message: str
    job_id: int | None
    transcription_id: int | None


def import_external_htr_writeback(job_id):
    """Create a new layer for one completed audit, or refuse with a fixed code.

    The job row is locked inside ``transaction.atomic`` and the planner runs
    again against the current part lines. An integrity error, and an empty
    review sample, are handled outside that block so the layer, its line
    rows, the gate, and the import marker roll back together.
    """
    if isinstance(job_id, bool) or not isinstance(job_id, int) or job_id < 1:
        return _result(False, CODE_INCOMPLETE, MESSAGE_INCOMPLETE, None, None)
    try:
        with transaction.atomic():
            return _import_locked(job_id)
    except IntegrityError:
        return _integrity_result(job_id)
    except _NoReviewSample:
        return _result(False, CODE_SAMPLE, MESSAGE_SAMPLE, job_id, None)


def _import_locked(job_id):
    from ai.models import AILayerGate, ExternalHTRJob
    from core.models import CascadeUpdate, LineTranscription, Transcription

    try:
        job = ExternalHTRJob.objects.select_for_update().get(pk=job_id)
    except ExternalHTRJob.DoesNotExist:
        return _result(False, CODE_INCOMPLETE, MESSAGE_INCOMPLETE, None, None)
    if job.imported or job.imported_transcription_id:
        return _result(False, CODE_ALREADY, MESSAGE_ALREADY, job.pk, None)

    plan = load_external_htr_writeback_plan(job.pk)
    if not plan.allowed:
        return _result(False, plan.code, plan.message, plan.job_id, None)

    author = _author(job)
    layer = Transcription(document_id=plan.document_id, name=plan.layer_name)
    layer.save()
    # LineTranscription.save updates the line and then the part. This import
    # only adds rows on the new layer.
    written_pks = []
    with CascadeUpdate.bypass():
        for item in plan.lines:
            row = LineTranscription.objects.create(
                transcription=layer,
                line_id=int(item.line_id),
                content=item.text,
                version_source=plan.provenance_stamp,
                version_author=author,
            )
            written_pks.append(row.line_id)
    # No comparison layer: build_gate_sample keeps the default size rule
    # and does not change gate state. Acknowledgement sets sampled.
    summary = build_gate_sample([], written_pks=written_pks, comparison=None)
    if not summary["sample_line_pks"]:
        raise _NoReviewSample()
    # job stays null: AILayerGate.job is an AIJob, not this audit.
    AILayerGate.objects.create(
        transcription=layer,
        state=AILayerGate.STATE_RAW,
        sample_line_pks=summary["sample_line_pks"],
        mean_cer=summary["mean_cer"],
    )
    job.imported = True
    job.imported_transcription = layer
    job.save(update_fields=["imported", "imported_transcription", "updated_at"])
    return _result(True, CODE_IMPORTED, MESSAGE_IMPORTED, job.pk, layer.pk)


def _integrity_result(job_id):
    from ai.models import ExternalHTRJob
    from core.models import Transcription

    job = ExternalHTRJob.objects.filter(pk=job_id).only(
        "pk", "imported", "imported_transcription_id",
    ).first()
    if job is not None and (job.imported or job.imported_transcription_id):
        return _result(False, CODE_ALREADY, MESSAGE_ALREADY, job.pk, None)
    plan = load_external_htr_writeback_plan(job_id)
    if plan.allowed and Transcription.objects.filter(
        document_id=plan.document_id, name=plan.layer_name,
    ).exists():
        return _result(False, CODE_COLLISION, MESSAGE_COLLISION, job_id, None)
    if not plan.allowed:
        return _result(False, plan.code, plan.message, plan.job_id, None)
    return _result(False, CODE_FAILED, MESSAGE_FAILED, job_id, None)


def _author(job):
    from django.contrib.auth import get_user_model
    from core.models import LineTranscription

    user_id = getattr(job, "created_by_id", None)
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id < 1:
        return ""
    username = get_user_model().objects.filter(pk=user_id).values_list(
        "username", flat=True,
    ).first()
    limit = LineTranscription._meta.get_field("version_author").max_length
    if not isinstance(username, str) or username == "" or len(username) > limit:
        return ""
    return username


def _result(imported, code, message, job_id, transcription_id):
    return ExternalHtrWritebackResult(
        imported=imported,
        code=code,
        message=message,
        job_id=job_id,
        transcription_id=transcription_id,
    )
