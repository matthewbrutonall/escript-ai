"""Create one transcription layer from a completed external HTR audit.

Callable from code and tests. No command, task, or view imports this module.
The audit status stays completed. ``imported`` is the durable marker, and
``imported_transcription`` is the layer link. The marker is set only after
every line row has been created, in the same transaction.
"""
from __future__ import annotations

from dataclasses import dataclass

from django.db import IntegrityError, transaction

from ai.external_htr_writeback_plan import (
    CODE_INCOMPLETE,
    MESSAGE_INCOMPLETE,
    load_external_htr_writeback_plan,
)

CODE_IMPORTED = "writeback_imported"
MESSAGE_IMPORTED = "new transcription layer was created"
CODE_ALREADY = "already_imported"
MESSAGE_ALREADY = "audit was already imported"
CODE_COLLISION = "layer_name_taken"
MESSAGE_COLLISION = "transcription layer name is taken"
CODE_FAILED = "writeback_failed"
MESSAGE_FAILED = "transcription layer was not created"


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
    again against the current part lines. An integrity error is handled
    outside that block so a failed insert rolls back the layer, its line
    rows, and the import marker.
    """
    if isinstance(job_id, bool) or not isinstance(job_id, int) or job_id < 1:
        return _result(False, CODE_INCOMPLETE, MESSAGE_INCOMPLETE, None, None)
    try:
        with transaction.atomic():
            return _import_locked(job_id)
    except IntegrityError:
        return _integrity_result(job_id)


def _import_locked(job_id):
    from ai.models import ExternalHTRJob
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
    with CascadeUpdate.bypass():
        for item in plan.lines:
            LineTranscription.objects.create(
                transcription=layer,
                line_id=int(item.line_id),
                content=item.text,
                version_source=plan.provenance_stamp,
                version_author=author,
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
