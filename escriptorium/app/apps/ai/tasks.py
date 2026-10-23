"""
Celery entry point for AI transcription.

Mirrors the contract of core.tasks.transcribe (tasks.py:555): takes part pks +
a transcription layer + user, loops parts, emits workflow events. Adds: backend
resolution, a cost pre-flight/budget gate (§11), and ledger accounting. Kept in
this app so core stays untouched (§2.3).

Budget holds, per-call reservations, job claims, and why these tasks do not
auto-retry: docs/budget-reservations.md.
"""
import logging

from django.apps import apps
from django.contrib.auth import get_user_model
from django.utils.translation import gettext as _

from escriptorium.celery import app
from users.consumers import send_event

from .backends import get_backend
from .billing import BillingStop
from .billing_store import JobNotClaimed, claim_job, open_job_meter
from .dispatch import (
    CrossDocumentError,
    RemoteAIForbidden,
    assert_dispatch_allowed,
    assert_parts_belong,
    load_parts_for_transcription,
    resolve_api_key,
)
from .compare import (
    disagreement_rows_from_layers, ensure_comparison_layer,
    find_cheap_recognizer,
)
from .fewshot import load_examples_for_document
from .gate import build_gate_sample, find_comparison_transcription
from .ketos_hook import reserve_held_out_parts
from .models import AILayerGate, AILineDisagreement
from .notify import notify_user
from .pipeline import transcribe_part
from .workflow import client_process
from .triage import normalised_cer

logger = logging.getLogger(__name__)
User = get_user_model()


def _fail_job(job, exc):
    """Status and message only: a full save() would overwrite per-call cost."""
    AIJob = apps.get_model('ai', 'AIJob')
    AIJob.objects.filter(pk=job.pk).update(
        status=AIJob.STATUS_ERROR, error=str(exc)[:2000])


def _require_job(AIJob, job_pk, name):
    """A delivery is de-duplicated through its AIJob, so one is required.

    Without job_pk a redelivered message would create a second job and run
    again. Refuse it before any lookup, key resolution, or provider call.
    """
    job = AIJob.objects.filter(pk=job_pk).first() if job_pk else None
    if job is None:
        raise ValueError(
            f"{name} needs the job_pk of an existing AIJob; it is refused "
            "without one so a duplicate delivery cannot run twice.")
    return job


def _claim_or_skip(job, task_id):
    try:
        return claim_job(job.pk, task_id)
    except JobNotClaimed as e:
        logger.warning("ai: job %s not run by task %s: %s", job.pk, task_id, e)
        return None


# No autoretry: a retry would re-run every part and bill it again.
@app.task(bind=True)
def ai_transcribe(self, instance_pks, ai_config_pk=None, transcription_pk=None,
                  user_pk=None, job_pk=None, per_crop=6, **kwargs):
    Transcription = apps.get_model('core', 'Transcription')
    AIBackendConfig = apps.get_model('ai', 'AIBackendConfig')
    AIJob = apps.get_model('ai', 'AIJob')

    job = _require_job(AIJob, job_pk, 'ai_transcribe')
    config = AIBackendConfig.objects.get(pk=ai_config_pk)
    transcription = Transcription.objects.get(pk=transcription_pk)
    user = User.objects.get(pk=user_pk) if user_pk else None

    claim = _claim_or_skip(job, self.request.id)
    if claim is None:
        return {"cost": 0.0, "skipped": True}
    job.refresh_from_db()

    # Validate the whole pk list *before* resolving a key or spending.
    try:
        parts = load_parts_for_transcription(instance_pks, transcription)
        assert_dispatch_allowed(transcription.document, config, user=user,
                                est_cost=job.est_cost)
        api_key = resolve_api_key(config, user=user)
        backend = get_backend(config, api_key=api_key)
        meter = open_job_meter(job, claim, config, user, transcription.document)
    except (CrossDocumentError, RemoteAIForbidden, ValueError, RuntimeError,
            BillingStop) as e:
        logger.exception(e)
        _fail_job(job, e)
        raise

    AIJob.objects.filter(pk=job.pk).update(parts_count=len(parts))

    total_cost = 0.0
    written_pks = []
    examples = load_examples_for_document(transcription.document)
    process = client_process("ai.tasks.ai_transcribe")
    try:
        for part in parts:
            send_event("document", part.document.pk, "part:workflow", {
                "id": part.pk, "process": process, "status": "ongoing",
                "task_id": self.request.id})
            try:
                # Charges are recorded per call as they happen, so a part that
                # fails half way keeps what it already spent.
                res = transcribe_part(part, config, transcription, backend,
                                      user=user, per_crop=per_crop, job=job,
                                      examples=examples, meter=meter)
                written_pks.extend(res.get('written_line_pks') or [])
                total_cost += res['cost']
                send_event("document", part.document.pk, "part:workflow", {
                    "id": part.pk, "process": process, "status": "done",
                    "task_id": self.request.id})
            except Exception as e:
                logger.exception(e)
                _fail_job(job, e)
                notify_user(user, _("Something went wrong during AI transcription!"),
                            id="ai-transcription-error", level='danger')
                send_event("document", part.document.pk, "part:workflow", {
                    "id": part.pk, "process": process, "status": "canceled",
                    "task_id": self.request.id})
                raise
    finally:
        meter.close()

    # Only from running: reconciliation may already have stopped this job.
    AIJob.objects.filter(pk=job.pk, claim=claim, status=AIJob.STATUS_RUNNING).update(
        status=AIJob.STATUS_DONE)
    job.refresh_from_db()
    comparison = _ensure_kraken_comparison(
        transcription.document, transcription, parts, user)
    rows = _disagreement_rows(transcription, comparison, written_pks)
    _write_layer_gate(job, transcription, comparison, rows, written_pks)
    notify_user(user, _("AI transcription done!"),
                id="ai-transcription-success", level='success')
    return {"cost": total_cost}


def _write_layer_gate(job, transcription, comparison, rows, written_pks=None):
    gate, _ = AILayerGate.objects.get_or_create(
        transcription=transcription, defaults={'job': job})
    gate.job = job
    gate.comparison = comparison
    gate.state = AILayerGate.STATE_RAW
    summary = build_gate_sample(
        rows, written_pks=written_pks, comparison=comparison)
    gate.sample_line_pks = summary['sample_line_pks']
    gate.mean_cer = summary['mean_cer']
    gate.save()
    AILineDisagreement.objects.filter(gate=gate).delete()
    Line = apps.get_model('core', 'Line')
    sample_pks = summary['sample_line_pks']
    part_ids = list(
        Line.objects.filter(pk__in=sample_pks).values_list(
            'document_part_id', flat=True))
    gate.held_out_part_pks = reserve_held_out_parts(part_ids)
    gate.save(update_fields=['held_out_part_pks'])
    if not summary.get('write_disagreements'):
        return
    sample = summary['in_sample']
    for row in rows:
        if not Line.objects.filter(pk=row['line_pk']).exists():
            continue
        AILineDisagreement.objects.create(
            gate=gate,
            line_id=row['line_pk'],
            ai_text=row.get('ai_text', '')[:2048],
            comparison_text=row.get('comparison_text', '')[:2048],
            cer=float(row['cer']),
            in_sample=row['line_pk'] in sample,
        )


def _ensure_kraken_comparison(document, ai_transcription, parts, user):
    comparison = find_comparison_transcription(document, ai_transcription)
    if comparison is not None:
        return comparison
    OcrModel = apps.get_model('core', 'OcrModel')
    Transcription = apps.get_model('core', 'Transcription')
    model = find_cheap_recognizer(OcrModel.objects.all())
    if model is None:
        logger.info("ai: no kraken recognizer on the instance; skip comparison pass")
        return None
    layer = ensure_comparison_layer(document, Transcription)
    try:
        for part in parts:
            part.transcribe(model, layer, user=user)
    except Exception:
        logger.exception("ai: kraken comparison pass failed")
        return None
    return layer


def _disagreement_rows(ai_transcription, comparison, line_pks):
    if comparison is None or not line_pks:
        return []
    LineTranscription = apps.get_model('core', 'LineTranscription')
    ai_map = {
        lt.line_id: lt.content or ''
        for lt in LineTranscription.objects.filter(
            transcription=ai_transcription, line_id__in=line_pks)
    }
    kr_map = {
        lt.line_id: lt.content or ''
        for lt in LineTranscription.objects.filter(
            transcription=comparison, line_id__in=line_pks)
    }
    return disagreement_rows_from_layers(
        line_pks, ai_map, kr_map, normalised_cer)


# No autoretry: a retry would re-run every part and bill it again.
@app.task(bind=True)
def ai_seg_review(self, instance_pks, ai_config_pk=None, user_pk=None,
                  job_pk=None, **kwargs):
    """Flag segmentation problems. Does not write Line.mask."""
    DocumentPart = apps.get_model('core', 'DocumentPart')
    AIBackendConfig = apps.get_model('ai', 'AIBackendConfig')
    AIJob = apps.get_model('ai', 'AIJob')
    AISegSuggestion = apps.get_model('ai', 'AISegSuggestion')

    job = _require_job(AIJob, job_pk, 'ai_seg_review')
    parts = list(DocumentPart.objects.filter(pk__in=instance_pks).order_by('order'))
    if not parts:
        return {"cost": 0}
    document = parts[0].document
    config = AIBackendConfig.objects.get(pk=ai_config_pk)
    user = User.objects.filter(pk=user_pk).first() if user_pk else None

    claim = _claim_or_skip(job, self.request.id)
    if claim is None:
        return {"cost": 0.0, "suggestions": 0, "errors": [], "skipped": True}
    job.refresh_from_db()
    try:
        # Same contract as ai_transcribe: mixed pks must not inherit the
        # first part's document for policy, budget, events, or suggestions.
        assert_parts_belong(document, parts)
        assert_dispatch_allowed(
            document, config, user=user, est_cost=job.est_cost)
        api_key = resolve_api_key(config, user=user)
        backend = get_backend(config, api_key=api_key)
        meter = open_job_meter(job, claim, config, user, document)
    except (CrossDocumentError, RemoteAIForbidden, ValueError, RuntimeError,
            BillingStop) as e:
        _fail_job(job, e)
        raise

    AIJob.objects.filter(pk=job.pk).update(
        backend=config, document=document, parts_count=len(parts))

    from .pipeline import review_part_segmentation
    process = client_process("ai.tasks.ai_seg_review")
    total_cost = 0.0
    n_sugg = 0
    part_errors = []
    try:
        for part in parts:
            send_event("document", document.pk, "part:workflow", {
                "id": part.pk, "process": process, "status": "ongoing",
                "task_id": self.request.id})
            try:
                res = review_part_segmentation(part, backend, meter=meter)
            except BillingStop as e:
                _fail_job(job, e)
                notify_user(user, _("Something went wrong during AI segmentation review!"),
                            id="ai-seg-review-error", level='danger')
                send_event("document", document.pk, "part:workflow", {
                    "id": part.pk, "process": process, "status": "canceled",
                    "task_id": self.request.id})
                raise
            except Exception as e:
                logger.exception("ai: seg review failed on part %s", part.pk)
                part_errors.append(f"part {part.pk}: {e}")
                send_event("document", document.pk, "part:workflow", {
                    "id": part.pk, "process": process, "status": "canceled",
                    "task_id": self.request.id})
                continue
            total_cost += res['cost']
            AISegSuggestion.objects.filter(
                part=part, status=AISegSuggestion.STATUS_PENDING).delete()
            for item in res['suggestions']:
                AISegSuggestion.objects.create(
                    document=document, part=part, line=item.get('line'),
                    job=job, kind=item['kind'], payload=item.get('payload') or {})
                n_sugg += 1
            send_event("document", document.pk, "part:workflow", {
                "id": part.pk, "process": process, "status": "done",
                "task_id": self.request.id})
            AIJob.objects.filter(pk=job.pk).update(lines_flagged=n_sugg)
    finally:
        meter.close()

    if part_errors and n_sugg == 0:
        AIJob.objects.filter(pk=job.pk).update(
            status=AIJob.STATUS_ERROR, error="; ".join(part_errors)[:2000])
        notify_user(user, _("Something went wrong during AI segmentation review!"),
                    id="ai-seg-review-error", level='danger')
    else:
        AIJob.objects.filter(pk=job.pk, claim=claim, status=AIJob.STATUS_RUNNING).update(
            status=AIJob.STATUS_DONE,
            error="; ".join(part_errors)[:2000] if part_errors else "")
        notify_user(user, _("AI segmentation review done!"),
                    id="ai-seg-review-success", level='success')
    return {"cost": total_cost, "suggestions": n_sugg, "errors": part_errors}
