"""
Celery entry point for AI transcription.

Mirrors the contract of core.tasks.transcribe (tasks.py:555): takes part pks +
a transcription layer + user, loops parts, emits workflow events. Adds: backend
resolution, a cost pre-flight/budget gate (§11), and ledger accounting. Kept in
this app so core stays untouched (§2.3).
"""
import logging

from django.apps import apps
from django.contrib.auth import get_user_model
from django.utils.translation import gettext as _

from escriptorium.celery import app
from users.consumers import send_event

from .backends import get_backend
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


@app.task(bind=True, autoretry_for=(MemoryError,), default_retry_delay=600)
def ai_transcribe(self, instance_pks, ai_config_pk=None, transcription_pk=None,
                  user_pk=None, job_pk=None, per_crop=6, **kwargs):
    Transcription = apps.get_model('core', 'Transcription')
    AIBackendConfig = apps.get_model('ai', 'AIBackendConfig')
    AIJob = apps.get_model('ai', 'AIJob')
    AIUsageLedger = apps.get_model('ai', 'AIUsageLedger')

    config = AIBackendConfig.objects.get(pk=ai_config_pk)
    transcription = Transcription.objects.get(pk=transcription_pk)
    user = User.objects.get(pk=user_pk) if user_pk else None
    job = AIJob.objects.filter(pk=job_pk).first() if job_pk else None

    # Validate the whole pk list *before* resolving a key or spending.
    try:
        parts = load_parts_for_transcription(instance_pks, transcription)
        assert_dispatch_allowed(transcription.document, config, user=user,
                                est_cost=job.est_cost if job else 0.0)
        api_key = resolve_api_key(config, user=user)
        backend = get_backend(config, api_key=api_key)
    except (CrossDocumentError, RemoteAIForbidden, ValueError, RuntimeError) as e:
        logger.exception(e)
        if job:
            job.status = job.STATUS_ERROR
            job.error = str(e)[:2000]
            job.save()
        raise

    if job:
        job.status = job.STATUS_RUNNING
        job.task_id = self.request.id
        job.parts_count = len(parts)
        job.save()

    total_cost = 0.0
    written_pks = []
    examples = load_examples_for_document(transcription.document)
    process = client_process("ai.tasks.ai_transcribe")
    for part in parts:
        send_event("document", part.document.pk, "part:workflow", {
            "id": part.pk, "process": process, "status": "ongoing",
            "task_id": self.request.id})
        try:
            res = transcribe_part(part, config, transcription, backend,
                                  user=user, per_crop=per_crop, job=job,
                                  examples=examples)
            written_pks.extend(res.get('written_line_pks') or [])
            total_cost += res['cost']
            AIUsageLedger.objects.create(
                job=job, provider=config.provider, model_id=config.model_id,
                tokens_in=res['tokens_in'], tokens_out=res['tokens_out'],
                actual_cost=res['cost'], user=user, document=part.document)
            send_event("document", part.document.pk, "part:workflow", {
                "id": part.pk, "process": process, "status": "done",
                "task_id": self.request.id})
        except Exception as e:
            logger.exception(e)
            if job:
                job.status = job.STATUS_ERROR
                job.error = str(e)[:2000]
                job.save()
            notify_user(user, _("Something went wrong during AI transcription!"),
                        id="ai-transcription-error", level='danger')
            send_event("document", part.document.pk, "part:workflow", {
                "id": part.pk, "process": process, "status": "canceled",
                "task_id": self.request.id})
            raise

    if job:
        job.status = job.STATUS_DONE
        job.save()
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


@app.task(bind=True, autoretry_for=(MemoryError,), default_retry_delay=600)
def ai_seg_review(self, instance_pks, ai_config_pk=None, user_pk=None,
                  job_pk=None, **kwargs):
    """Flag segmentation problems. Does not write Line.mask."""
    DocumentPart = apps.get_model('core', 'DocumentPart')
    AIBackendConfig = apps.get_model('ai', 'AIBackendConfig')
    AIJob = apps.get_model('ai', 'AIJob')
    AIUsageLedger = apps.get_model('ai', 'AIUsageLedger')
    AISegSuggestion = apps.get_model('ai', 'AISegSuggestion')

    parts = list(DocumentPart.objects.filter(pk__in=instance_pks).order_by('order'))
    if not parts:
        return {"cost": 0}
    document = parts[0].document
    config = AIBackendConfig.objects.get(pk=ai_config_pk)
    user = User.objects.filter(pk=user_pk).first() if user_pk else None
    job = AIJob.objects.filter(pk=job_pk).first() if job_pk else None
    try:
        # Same contract as ai_transcribe: mixed pks must not inherit the
        # first part's document for policy, budget, events, or suggestions.
        assert_parts_belong(document, parts)
        assert_dispatch_allowed(
            document, config, user=user,
            est_cost=job.est_cost if job else 0.0)
    except (CrossDocumentError, RemoteAIForbidden, RuntimeError) as e:
        if job:
            job.status = job.STATUS_ERROR
            job.error = str(e)[:2000]
            job.save()
        raise

    api_key = resolve_api_key(config, user=user)
    backend = get_backend(config, api_key=api_key)
    if job:
        job.status = job.STATUS_RUNNING
        job.task_id = self.request.id
        job.backend = config
        job.document = document
        job.parts_count = len(parts)
        job.save()

    from .pipeline import review_part_segmentation
    process = client_process("ai.tasks.ai_seg_review")
    total_cost = 0.0
    n_sugg = 0
    part_errors = []
    for part in parts:
        send_event("document", document.pk, "part:workflow", {
            "id": part.pk, "process": process, "status": "ongoing",
            "task_id": self.request.id})
        try:
            res = review_part_segmentation(part, backend)
        except Exception as e:
            logger.exception("ai: seg review failed on part %s", part.pk)
            part_errors.append(f"part {part.pk}: {e}")
            send_event("document", document.pk, "part:workflow", {
                "id": part.pk, "process": process, "status": "canceled",
                "task_id": self.request.id})
            continue
        total_cost += res['cost']
        AIUsageLedger.objects.create(
            job=job, provider=config.provider, model_id=config.model_id,
            tokens_in=res['tokens_in'], tokens_out=res['tokens_out'],
            actual_cost=res['cost'], user=user, document=document)
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
        if job:
            job.actual_cost = total_cost
            job.tokens_in = (job.tokens_in or 0) + res['tokens_in']
            job.tokens_out = (job.tokens_out or 0) + res['tokens_out']
            job.lines_flagged = n_sugg
            job.save()

    if job:
        if part_errors and n_sugg == 0:
            job.status = job.STATUS_ERROR
            job.error = "; ".join(part_errors)[:2000]
            notify_user(user, _("Something went wrong during AI segmentation review!"),
                        id="ai-seg-review-error", level='danger')
        else:
            job.status = job.STATUS_DONE
            if part_errors:
                job.error = "; ".join(part_errors)[:2000]
            notify_user(user, _("AI segmentation review done!"),
                        id="ai-seg-review-success", level='success')
        job.save()
    elif n_sugg:
        notify_user(user, _("AI segmentation review done!"),
                    id="ai-seg-review-success", level='success')
    return {"cost": total_cost, "suggestions": n_sugg, "errors": part_errors}
