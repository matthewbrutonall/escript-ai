"""Import one external HTR run into a single new transcription layer.

The background task does not call this. A person asks for the import
after recognition. Failed pages stay unimported. The layer starts behind
the raw review gate and is not marked training-eligible.
"""
from __future__ import annotations

from dataclasses import dataclass

from django.db import IntegrityError, transaction

from ai.external_htr_writeback_plan import (
    CODE_EMPTY,
    CODE_INCOMPLETE,
    MESSAGE_EMPTY,
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
LAYER_NAME_LIMIT = 512


class _NoReviewSample(Exception):
    """Raised inside the import transaction when the sample has no lines."""


@dataclass(frozen=True)
class ExternalHtrRunImportResult:
    """Outcome of one run import. ``message`` is a fixed sentence."""

    imported: bool
    code: str
    message: str
    run_id: int | None
    transcription_id: int | None
    lines: int


def import_external_htr_run(run_id):
    """Create one layer for the completed pages, or refuse with a fixed code."""
    if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id < 1:
        return _result(False, CODE_INCOMPLETE, MESSAGE_INCOMPLETE, None, None, 0)
    try:
        with transaction.atomic():
            return _import_locked(run_id)
    except IntegrityError:
        return _integrity_result(run_id)
    except _NoReviewSample:
        return _result(False, CODE_SAMPLE, MESSAGE_SAMPLE, run_id, None, 0)


def _import_locked(run_id):
    from ai.models import AILayerGate, ExternalHTRJob, ExternalHTRRun
    from core.models import CascadeUpdate, LineTranscription, Transcription

    try:
        run = ExternalHTRRun.objects.select_for_update().get(pk=run_id)
    except ExternalHTRRun.DoesNotExist:
        return _result(False, CODE_INCOMPLETE, MESSAGE_INCOMPLETE, None, None, 0)
    if run.status not in {ExternalHTRRun.STATUS_COMPLETED, ExternalHTRRun.STATUS_PARTIAL}:
        return _result(False, CODE_INCOMPLETE, MESSAGE_INCOMPLETE, run.pk, None, 0)
    jobs = list(
        ExternalHTRJob.objects.select_for_update().filter(run_id=run.pk).order_by("pk")
    )
    completed = [job for job in jobs if job.status == ExternalHTRJob.STATUS_COMPLETED]
    if run.imported or run.imported_transcription_id or any(
        job.imported or job.imported_transcription_id for job in completed
    ):
        return _already(run, completed)

    chosen = []
    for job in completed:
        plan = load_external_htr_writeback_plan(job.pk)
        if plan.code == CODE_EMPTY:
            continue
        if not plan.allowed or plan.document_id != run.document_id:
            return _result(False, CODE_INCOMPLETE, MESSAGE_INCOMPLETE, run.pk, None, 0)
        chosen.append((job, plan))
    if not chosen:
        return _result(False, CODE_EMPTY, MESSAGE_EMPTY, run.pk, None, 0)
    stamps = {plan.provenance_stamp for _job, plan in chosen}
    if len(stamps) != 1 or not next(iter(stamps)):
        return _result(False, CODE_INCOMPLETE, MESSAGE_INCOMPLETE, run.pk, None, 0)

    stamp = next(iter(stamps))
    first = chosen[0][1]
    layer = Transcription(
        document_id=run.document_id,
        name=_layer_name(run.pk, first.engine, first.model_id),
    )
    layer.save()
    author = _author(run)
    written_pks = []
    with CascadeUpdate.bypass():
        for _job, plan in chosen:
            for item in plan.lines:
                row = LineTranscription.objects.create(
                    transcription=layer,
                    line_id=int(item.line_id),
                    content=item.text,
                    version_source=stamp,
                    version_author=author,
                )
                written_pks.append(row.line_id)
    summary = build_gate_sample([], written_pks=written_pks, comparison=None)
    if not summary["sample_line_pks"]:
        raise _NoReviewSample()
    AILayerGate.objects.create(
        transcription=layer,
        state=AILayerGate.STATE_RAW,
        sample_line_pks=summary["sample_line_pks"],
        mean_cer=summary["mean_cer"],
    )
    for job, _plan in chosen:
        job.imported = True
        job.imported_transcription = layer
        job.save(update_fields=["imported", "imported_transcription", "updated_at"])
    run.imported = True
    run.imported_transcription = layer
    run.save(update_fields=["imported", "imported_transcription", "updated_at"])
    return _result(True, CODE_IMPORTED, MESSAGE_IMPORTED, run.pk, layer.pk, len(written_pks))


def _already(run, completed):
    transcription_id = run.imported_transcription_id
    if transcription_id is None:
        for job in completed:
            if job.imported_transcription_id:
                transcription_id = job.imported_transcription_id
                break
    return _result(False, CODE_ALREADY, MESSAGE_ALREADY, run.pk, transcription_id, 0)


def _integrity_result(run_id):
    from ai.models import ExternalHTRRun
    from core.models import Transcription

    run = ExternalHTRRun.objects.filter(pk=run_id).first()
    if run is None:
        return _result(False, CODE_INCOMPLETE, MESSAGE_INCOMPLETE, None, None, 0)
    if run.imported or run.imported_transcription_id:
        return _result(
            False, CODE_ALREADY, MESSAGE_ALREADY, run.pk, run.imported_transcription_id, 0,
        )
    name = _layer_name(run.pk, run.engine, run.model_id)
    if Transcription.objects.filter(document_id=run.document_id, name=name).exists():
        return _result(False, CODE_COLLISION, MESSAGE_COLLISION, run.pk, None, 0)
    return _result(False, CODE_FAILED, MESSAGE_FAILED, run.pk, None, 0)


def _layer_name(run_id, engine, model_id) -> str:
    suffix = f":run-{run_id}"
    prefix = f"external-htr:{engine}:{model_id}"
    if len(suffix) >= LAYER_NAME_LIMIT:
        return suffix[-LAYER_NAME_LIMIT:]
    room = LAYER_NAME_LIMIT - len(suffix)
    if len(prefix) > room:
        prefix = prefix[:room]
    return prefix + suffix


def _author(run):
    from django.contrib.auth import get_user_model
    from core.models import LineTranscription

    user_id = getattr(run, "created_by_id", None)
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id < 1:
        return ""
    username = get_user_model().objects.filter(pk=user_id).values_list(
        "username", flat=True,
    ).first()
    limit = LineTranscription._meta.get_field("version_author").max_length
    if not isinstance(username, str) or username == "" or len(username) > limit:
        return ""
    return username


def _result(imported, code, message, run_id, transcription_id, lines):
    return ExternalHtrRunImportResult(
        imported=imported,
        code=code,
        message=message,
        run_id=run_id,
        transcription_id=transcription_id,
        lines=lines,
    )
