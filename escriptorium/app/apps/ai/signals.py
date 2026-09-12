"""Capture same-document corrections as few-shot text (ARCHITECTURE.md §10.1)."""
from django.db.models.signals import post_save
from django.dispatch import receiver

from core.models import LineTranscription

from .models import AIExample, AILayerGate

_AI_PROVIDERS = ("gemini", "anthropic", "openai", "local")


def _is_ai_stamp(version_source):
    src = (version_source or "").split(":", 1)[0]
    return src in _AI_PROVIDERS


@receiver(post_save, sender=LineTranscription)
def capture_correction(sender, instance, created, **kwargs):
    if created or not (instance.content or "").strip():
        return
    if _is_ai_stamp(instance.version_source):
        return
    if not AILayerGate.objects.filter(transcription_id=instance.transcription_id).exists():
        return
    line = instance.line
    AIExample.objects.update_or_create(
        document_id=line.document_part.document_id,
        line=line,
        defaults={"text": instance.content[:2048]},
    )
