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
from django.utils.translation import gettext_lazy as _

from escriptorium.celery import app
from users.consumers import send_event

from .backends import get_backend
from .dispatch import (
    CrossDocumentError,
    RemoteAIForbidden,
    assert_dispatch_allowed,
    load_parts_for_transcription,
    resolve_api_key,
)
from .pipeline import transcribe_part

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
        api_key = resolve_api_key(config)
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
    for part in parts:
        try:
            res = transcribe_part(part, config, transcription, backend,
                                  user=user, per_crop=per_crop, job=job)
            total_cost += res['cost']
            AIUsageLedger.objects.create(
                job=job, provider=config.provider, model_id=config.model_id,
                tokens_in=res['tokens_in'], tokens_out=res['tokens_out'],
                actual_cost=res['cost'], user=user, document=part.document)
            send_event("document", part.document.pk, "part:workflow", {
                "id": part.pk, "process": "ai-transcribe", "status": "done",
                "task_id": self.request.id})
        except Exception as e:
            logger.exception(e)
            if job:
                job.status = job.STATUS_ERROR
                job.error = str(e)[:2000]
                job.save()
            if user:
                user.notify(_("Something went wrong during AI transcription!"),
                            id="ai-transcription-error", level='danger')
            send_event("document", part.document.pk, "part:workflow", {
                "id": part.pk, "process": "ai-transcribe", "status": "canceled",
                "task_id": self.request.id})
            raise

    if job:
        job.status = job.STATUS_DONE
        job.save()
    if user:
        user.notify(_("AI transcription done!"),
                    id="ai-transcription-success", level='success')
    return {"cost": total_cost}
