"""Recognize the pages of one external HTR run on the selected engine.

A failure stays on that engine. This module does not import a transcription
layer and does not call another provider. Logs contain the run id and a
fixed code only.
"""
from __future__ import annotations

import logging
import time

from django.db import transaction

from ai.external_htr_audit_command import (
    ConfigNotFound,
    PartNotFound,
    execute_external_htr_audit,
)
from ai.external_htr_policy import (
    CODE_OFFSITE,
    CODE_POLICY,
    MESSAGE_OFFSITE,
    MESSAGE_POLICY,
    review_document_endpoint,
)
from ai.htr_engine_client import EngineClientError
from ai.htr_engine_contract import ContractError

logger = logging.getLogger(__name__)

CODE_PERMISSION = "permission_denied"
CODE_DISABLED = "disabled"
CODE_TIER = "tier_refused"
CODE_MODEL = "model_not_found"
CODE_RESPONSE = "invalid_response"
CODE_UNAVAILABLE = "unavailable"
CODE_TIMEOUT = "timeout"
CODE_CONFIG = "config_not_found"
CODE_PART = "part_not_found"
CODE_IMAGE = "image_unavailable"
CODE_INTERNAL = "internal"
CODE_NOTHING = "nothing_to_send"
CODE_PARTIAL = "partial"
CODE_LINE_HEIGHT = "model_line_height"

MESSAGE_PERMISSION = "document permission was denied"
MESSAGE_DISABLED = "engine config is disabled"
MESSAGE_TIER = "hosted engines are not used for this action"
MESSAGE_MODEL = "engine did not report that model"
MESSAGE_RESPONSE = "engine response could not be read"
MESSAGE_UNAVAILABLE = "recognition request was not completed"
MESSAGE_FINISHED = "recognition finished"
MESSAGE_PARTIAL = "recognition finished with page failures"
MESSAGE_FAILED = "recognition failed"
MESSAGE_INTERNAL = "recognition could not be completed"
MESSAGE_LINE_HEIGHT = "model did not report a usable line height"

_RETRY = frozenset({"busy", "unavailable"})
_TERMINAL = frozenset({"completed", "partial", "failed"})
_LINE_STATUS = frozenset({"included", "skipped", "failed"})
_PUBLIC = frozenset({
    CODE_PERMISSION,
    CODE_DISABLED,
    CODE_TIER,
    CODE_MODEL,
    CODE_RESPONSE,
    CODE_UNAVAILABLE,
    CODE_TIMEOUT,
    CODE_OFFSITE,
    CODE_POLICY,
    CODE_CONFIG,
    CODE_PART,
    CODE_IMAGE,
    CODE_INTERNAL,
    CODE_NOTHING,
    CODE_PARTIAL,
    CODE_LINE_HEIGHT,
    "busy",
    "http_error",
    "invalid_request",
    "line_failed",
    "unsupported_preprocessing",
    "already_finalized",
    "invalid_plan",
    "completed",
    "partial",
    "failed",
    "queued",
    "running",
})
_MESSAGES = {
    CODE_PERMISSION: MESSAGE_PERMISSION,
    CODE_DISABLED: MESSAGE_DISABLED,
    CODE_TIER: MESSAGE_TIER,
    CODE_MODEL: MESSAGE_MODEL,
    CODE_RESPONSE: MESSAGE_RESPONSE,
    CODE_UNAVAILABLE: MESSAGE_UNAVAILABLE,
    CODE_TIMEOUT: MESSAGE_UNAVAILABLE,
    CODE_OFFSITE: MESSAGE_OFFSITE,
    CODE_POLICY: MESSAGE_POLICY,
    CODE_CONFIG: "engine was not found",
    CODE_PART: "page was not found",
    CODE_IMAGE: "page image could not be read",
    CODE_INTERNAL: MESSAGE_INTERNAL,
    CODE_NOTHING: "page has no lines to send",
    "busy": MESSAGE_UNAVAILABLE,
    "http_error": MESSAGE_UNAVAILABLE,
    "invalid_request": "recognition request was not accepted",
    "line_failed": MESSAGE_FAILED,
    "unsupported_preprocessing": "recognition request was not accepted",
    "already_finalized": MESSAGE_INTERNAL,
    "invalid_plan": MESSAGE_INTERNAL,
    CODE_LINE_HEIGHT: MESSAGE_LINE_HEIGHT,
}


def execute_external_htr_run(
    run_id,
    *,
    task_id="",
    live=None,
    fetch_capabilities=None,
    fetch_models=None,
    pause=None,
    attempts=3,
    open_image=None,
    fetch_policy=None,
):
    """Run one queued recognition. A terminal run returns without new calls."""
    if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id < 1:
        return None
    try:
        return _execute(
            run_id,
            task_id=task_id if isinstance(task_id, str) else "",
            live=live,
            fetch_capabilities=fetch_capabilities,
            fetch_models=fetch_models,
            pause=pause,
            attempts=attempts,
            open_image=open_image,
            fetch_policy=fetch_policy,
        )
    except Exception:
        _note(run_id, CODE_INTERNAL)
        _mark_internal(run_id)
        return None


def _execute(
    run_id,
    *,
    task_id,
    live,
    fetch_capabilities,
    fetch_models,
    pause,
    attempts,
    open_image,
    fetch_policy,
):
    from ai.models import ExternalHTRRun

    if not _claim(run_id, task_id[:64]):
        return ExternalHTRRun.objects.filter(pk=run_id).first()
    run = ExternalHTRRun.objects.select_related("document", "config", "created_by").get(pk=run_id)
    if fetch_policy is None:
        fetch_policy = review_document_endpoint
    if open_image is None:
        open_image = _open_part_image
    if pause is None:
        pause = _sleep
    attempts = _attempts(attempts)
    if live is None or fetch_capabilities is None or fetch_models is None:
        from ai.external_htr_live import run_external_htr_live
        from ai.htr_engine_client import fetch_capabilities as default_capabilities
        from ai.htr_engine_client import fetch_models as default_models
        if live is None:
            live = run_external_htr_live
        if fetch_capabilities is None:
            fetch_capabilities = default_capabilities
        if fetch_models is None:
            fetch_models = default_models

    user = run.created_by
    document = run.document
    config = run.config
    if not _allowed(user, document.pk):
        return _fail_run(run, CODE_PERMISSION)
    if getattr(config, "enabled", None) is not True:
        return _fail_run(run, CODE_DISABLED)
    if getattr(config, "tier", None) == "api":
        return _fail_run(run, CODE_TIER)
    blocked = _policy(fetch_policy, document, config)
    if blocked:
        return _fail_run(run, blocked)
    try:
        capabilities = fetch_capabilities(config)
        listed = fetch_models(config)
    except EngineClientError as exc:
        return _fail_run(run, _public(getattr(exc, "code", None)))
    except ContractError as exc:
        return _fail_run(run, _public(getattr(exc, "code", None)))
    except Exception:
        return _fail_run(run, CODE_RESPONSE)
    cap_engine = getattr(capabilities, "engine", None)
    list_engine = getattr(listed, "engine", None)
    if not isinstance(cap_engine, str) or not cap_engine or cap_engine != list_engine:
        return _fail_run(run, CODE_RESPONSE)
    models = getattr(listed, "models", None)
    chosen = _chosen_model(models, run.model_id)
    if chosen is None:
        return _fail_run(run, CODE_MODEL)
    line_height = _line_height(chosen)
    if cap_engine == "pylaia" and line_height is None:
        return _fail_run(run, CODE_LINE_HEIGHT)
    run.engine = cap_engine[:256]
    run.save(update_fields=["engine", "updated_at"])

    part_ids = _part_ids(run.part_ids)
    previous = _previous(run.pages)
    pages = []
    for part_id in part_ids:
        existing = _existing_job(run.pk, part_id)
        if existing is not None:
            prior = previous.get(part_id) or {}
            used = prior.get("attempts") if isinstance(prior.get("attempts"), int) else 0
            pages.append(_page_from_job(existing, used))
            continue
        if not _allowed(user, document.pk):
            pages.append(_failed_page(part_id, CODE_PERMISSION, 0))
            _note(run.pk, CODE_PERMISSION)
            run.pages = pages
            run.save(update_fields=["pages", "updated_at"])
            continue
        box = {"n": 0}
        outcome = execute_external_htr_audit(
            config.pk,
            part_id,
            model_id=run.model_id,
            engine=cap_engine,
            fetch_config=_fetch_config(config.pk),
            fetch_part=_fetch_part(document.pk),
            open_image=open_image,
            run=_retrying(live, attempts, pause, box),
            fetch_policy=fetch_policy,
            created_by=user,
            audit_run=run,
            line_height=line_height,
        )
        job = _existing_job(run.pk, part_id)
        if job is not None:
            pages.append(_page_from_job(job, box["n"]))
        else:
            pages.append(_failed_page(part_id, _code_from_outcome(outcome), box["n"]))
        page_code = pages[-1]["code"] or pages[-1]["status"]
        if pages[-1]["status"] != "completed":
            _note(run.pk, page_code)
        run.pages = pages
        run.save(update_fields=["pages", "updated_at"])
    return _finish(run, pages)


def _claim(run_id, task_id):
    from ai.models import ExternalHTRRun

    with transaction.atomic():
        try:
            run = ExternalHTRRun.objects.select_for_update().get(pk=run_id)
        except ExternalHTRRun.DoesNotExist:
            return False
        if run.status in _TERMINAL:
            return False
        if (
            run.status == ExternalHTRRun.STATUS_RUNNING
            and task_id
            and run.task_id
            and run.task_id != task_id
        ):
            return False
        run.status = ExternalHTRRun.STATUS_RUNNING
        fields = ["status", "updated_at"]
        if task_id and not run.task_id:
            run.task_id = task_id
            fields.append("task_id")
        run.save(update_fields=fields)
    return True


def _chosen_model(models, model_id):
    if not isinstance(models, tuple):
        return None
    for model in models:
        if getattr(model, "model_id", None) == model_id:
            return model
    return None


def _line_height(model):
    value = getattr(getattr(model, "input", None), "line_height", None)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 1024:
        return None
    return value


def _fail_run(run, code):
    safe = _public(code)
    run.status = "failed"
    run.code = safe
    run.message = _message(safe)[:256]
    run.save(update_fields=["status", "code", "message", "updated_at"])
    _note(run.pk, safe)
    return run


def _finish(run, pages):
    done = [page for page in pages if page["status"] == "completed"]
    failed = [page for page in pages if page["status"] != "completed"]
    if done and not failed:
        run.status = "completed"
        run.code = ""
        run.message = MESSAGE_FINISHED
    elif done and failed:
        run.status = "partial"
        run.code = CODE_PARTIAL
        run.message = MESSAGE_PARTIAL
    else:
        run.status = "failed"
        run.code = _public(failed[0]["code"] if failed else CODE_INTERNAL)
        run.message = _message(run.code)
    run.pages = pages
    run.save(update_fields=["status", "code", "message", "pages", "updated_at"])
    _note(run.pk, run.code or run.status)
    return run


def _mark_internal(run_id):
    from ai.models import ExternalHTRRun

    run = ExternalHTRRun.objects.filter(pk=run_id).first()
    if run is None or run.status in _TERMINAL:
        return
    pages = run.pages if isinstance(run.pages, list) else []
    done = any(isinstance(page, dict) and page.get("status") == "completed" for page in pages)
    run.status = "partial" if done else "failed"
    run.code = CODE_INTERNAL
    run.message = MESSAGE_INTERNAL
    run.save(update_fields=["status", "code", "message", "updated_at"])


def _retrying(live, attempts, pause, box):
    def run(config, request):
        last = None
        for number in range(1, attempts + 1):
            box["n"] = number
            last = live(config, request)
            if getattr(last, "ok", None) is True:
                return last
            if getattr(last, "code", None) not in _RETRY:
                return last
            if number < attempts:
                pause()
        return last

    return run


def _fetch_config(config_id):
    def fetch(_config_id):
        from ai.models import ExternalHTREngineConfig

        try:
            return ExternalHTREngineConfig.objects.get(pk=config_id)
        except ExternalHTREngineConfig.DoesNotExist:
            raise ConfigNotFound()

    return fetch


def _fetch_part(document_id):
    def fetch(part_id):
        from core.models import DocumentPart

        try:
            part = DocumentPart.objects.select_related("document").get(
                pk=part_id, document_id=document_id,
            )
        except (DocumentPart.DoesNotExist, ValueError, TypeError):
            raise PartNotFound()
        return part, list(part.lines.order_by("order", "pk"))

    return fetch


def _open_part_image(part):
    from PIL import Image

    return Image.open(part.image.path)


def _existing_job(run_id, part_id):
    from ai.models import ExternalHTRJob

    return (
        ExternalHTRJob.objects.filter(run_id=run_id, part_id=part_id)
        .order_by("pk")
        .first()
    )


def _page_from_job(job, attempts):
    lines = []
    for row in job.lines.order_by("position", "pk"):
        text = row.text if isinstance(row.text, str) else ""
        status = row.status if row.status in _LINE_STATUS else "failed"
        lines.append({
            "line_id": row.line_id if isinstance(row.line_id, str) else "",
            "status": status,
            "code": row.code if isinstance(row.code, str) else "",
            "empty": status == "included" and text == "",
            "text_length": len(text) if status == "included" else 0,
        })
    completed = job.status == "completed"
    return {
        "part_id": job.part_id,
        "status": "completed" if completed else "failed",
        "code": "" if completed else _public(job.code),
        "requested": job.requested_line_count,
        "skipped": job.skipped_line_count,
        "results": job.result_count,
        "empty_text": job.empty_text_count,
        "warnings": job.warning_count,
        "job_id": job.pk,
        "attempts": attempts if isinstance(attempts, int) and not isinstance(attempts, bool) else 0,
        "lines": lines,
    }


def _failed_page(part_id, code, attempts):
    return {
        "part_id": part_id,
        "status": "failed",
        "code": _public(code),
        "requested": 0,
        "skipped": 0,
        "results": 0,
        "empty_text": 0,
        "warnings": 0,
        "job_id": None,
        "attempts": attempts if isinstance(attempts, int) and not isinstance(attempts, bool) else 0,
        "lines": [],
    }


def _code_from_outcome(outcome):
    if not isinstance(outcome, tuple) or len(outcome) != 2:
        return CODE_INTERNAL
    exit_code, line = outcome
    if exit_code == 0:
        return ""
    prefix = "FAILED: external HTR audit "
    if isinstance(line, str) and line.startswith(prefix):
        return _public(line[len(prefix):].strip())
    return CODE_INTERNAL


def _allowed(user, document_id):
    from core.models import Document

    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if isinstance(document_id, bool) or not isinstance(document_id, int):
        return False
    return Document.objects.for_user(user).filter(pk=document_id).exists()


def _policy(fetch_policy, document, config):
    if not callable(fetch_policy):
        return CODE_POLICY
    try:
        verdict = fetch_policy(document, config)
    except Exception:
        return CODE_POLICY
    if verdict is None:
        return None
    return _public(verdict)


def _part_ids(value):
    if not isinstance(value, list):
        return []
    collected = []
    seen = set()
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or item < 1 or item in seen:
            continue
        seen.add(item)
        collected.append(item)
    return collected


def _previous(pages):
    found = {}
    if not isinstance(pages, list):
        return found
    for page in pages:
        if isinstance(page, dict) and isinstance(page.get("part_id"), int):
            found[page["part_id"]] = page
    return found


def _attempts(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return 3
    return 3 if value > 3 else value


def _public(code):
    if isinstance(code, str) and code in _PUBLIC:
        return code
    return CODE_INTERNAL


def _message(code):
    return _MESSAGES.get(code, MESSAGE_INTERNAL)


def _note(run_id, code):
    logger.info("external htr run %s %s", run_id, _public(code))


def _sleep():
    time.sleep(1)
