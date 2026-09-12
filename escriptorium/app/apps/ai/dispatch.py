"""
Dispatch guards for ai_transcribe (ARCHITECTURE.md §13).

Kept off tasks.py so they can be unit-tested without importing Celery.
Optional callables (`get_part`, `get_policy`, `get_api_key`) are production
defaults that hit Django; tests inject fakes.
"""
import logging
import os

logger = logging.getLogger(__name__)


class RemoteAIForbidden(Exception):
    """Document policy forbids sending images to a hosted backend (§13)."""


class CrossDocumentError(ValueError):
    """A part pk does not belong to the transcription's document.

    Raw ai_transcribe.delay() must not send document B's images under
    document A's policy (or write B's lines onto A's layer).
    """


def resolve_api_key(config):
    """Resolve the provider key from a secret store, never stored in cleartext
    on the config (§7/§13). Local backends need no key."""
    from django.conf import settings
    if getattr(config, 'is_local', False) or config.provider == 'local':
        return None
    if config.key_ref:
        val = os.environ.get(config.key_ref)
        if val:
            return val
    return getattr(settings, 'AI_DEFAULT_API_KEY', None)


def check_budget(user, est_cost):
    """§11 budget gate. Placeholder mirroring users.has_free_cpu_minutes."""
    from django.conf import settings
    if getattr(settings, 'DISABLE_QUOTAS', True):
        return True
    fn = getattr(user, 'has_free_ai_budget', None)
    return fn(est_cost) if callable(fn) else True


def assert_dispatch_allowed(document, config, user=None, est_cost=0.0, *,
                            get_policy=None, get_api_key=None, budget_ok=None):
    """Enforce policy at the task, not the UI.

    A background job or raw .delay() must not bypass never-send-off-site,
    a missing hosted key, or a failed budget check.
    """
    is_local = getattr(config, 'is_local', False) or config.provider == 'local'
    if not is_local:
        if get_policy is None:
            from django.apps import apps
            Policy = apps.get_model('ai', 'AIDocumentPolicy')

            def get_policy(doc):
                return Policy.objects.filter(document=doc).first()
        policy = get_policy(document)
        if policy and getattr(policy, 'never_send_offsite', False):
            raise RemoteAIForbidden(
                f"Document {getattr(document, 'pk', document)} forbids remote AI; "
                f"backend {config.provider}:{config.model_id} is not local.")
        key_fn = get_api_key or resolve_api_key
        if not key_fn(config):
            raise RuntimeError(
                f"No API key resolved for {config.provider}:{config.model_id} "
                f"(key_ref={getattr(config, 'key_ref', None)!r}).")
    if user is not None:
        ok = budget_ok if budget_ok is not None else check_budget(user, est_cost)
        if not ok:
            raise RuntimeError(
                f"User {getattr(user, 'pk', user)} has no remaining AI budget.")


def assert_parts_belong(document, parts):
    """Serializer-level twin of load_parts_for_transcription.

    `document` is the transcription's document. Raise if any part is foreign.
    """
    doc_id = getattr(document, 'pk', document)
    for part in parts:
        if part.document_id != doc_id:
            raise CrossDocumentError(
                f"DocumentPart {part.pk} belongs to document {part.document_id}, "
                f"not document {doc_id}.")


def load_parts_for_transcription(instance_pks, transcription, *,
                                 get_part=None, missing=None):
    """Resolve part pks and refuse any that belong to another document.

    Missing parts are skipped (same as core.tasks.transcribe). A part on a
    different document is a contract violation: raise before any VLM call.
    """
    if get_part is None:
        from django.apps import apps
        DocumentPart = apps.get_model('core', 'DocumentPart')
        get_part = DocumentPart.objects.get
        missing = DocumentPart.DoesNotExist
    if missing is None:
        missing = LookupError
    parts = []
    for pk in instance_pks:
        try:
            part = get_part(pk=pk)
        except missing:
            logger.error('ai_transcribe: missing DocumentPart %s', pk)
            continue
        if part.document_id != transcription.document_id:
            raise CrossDocumentError(
                f"DocumentPart {part.pk} belongs to document "
                f"{part.document_id}, not transcription {transcription.pk}'s "
                f"document {transcription.document_id}.")
        parts.append(part)
    return parts
