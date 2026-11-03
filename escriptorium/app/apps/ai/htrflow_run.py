"""Recognize selected pages with one HTRflow page endpoint.

A failure stays on HTRflow. This module does not call a line recognizer,
does not crop a line, and does not import a transcription layer. Logs
contain the run id and a fixed code only.
"""
from __future__ import annotations

import logging
import time

from django.db import transaction

from ai.external_htr_policy import CODE_OFFSITE, CODE_POLICY, MESSAGE_OFFSITE, MESSAGE_POLICY
from ai.external_htr_run import (
    CODE_CONFIG,
    CODE_DISABLED,
    CODE_IMAGE,
    CODE_INTERNAL,
    CODE_MODEL,
    CODE_NOTHING,
    CODE_PARTIAL,
    CODE_PART,
    CODE_PERMISSION,
    CODE_RESPONSE,
    CODE_TIER,
    CODE_TIMEOUT,
    CODE_UNAVAILABLE,
    MESSAGE_DISABLED,
    MESSAGE_FAILED,
    MESSAGE_FINISHED,
    MESSAGE_INTERNAL,
    MESSAGE_MODEL,
    MESSAGE_PARTIAL,
    MESSAGE_PERMISSION,
    MESSAGE_RESPONSE,
    MESSAGE_TIER,
    MESSAGE_UNAVAILABLE,
    _allowed,
    _attempts,
    _claim,
    _mark_internal,
    _part_ids,
    _policy,
    _previous,
)
from ai.htrflow_alto import parse_htrflow_alto
from ai.htrflow_contract import (
    MAX_IMAGE_BYTES,
    CODE_BEAM,
    MESSAGE_BEAM,
    beam_refusal,
)
from ai.htrflow_client import HtrflowClientError, retryable_failure

logger = logging.getLogger(__name__)

CODE_DIMENSIONS = "page_dimensions"
CODE_BOUNDS = "geometry_out_of_bounds"
MESSAGE_DIMENSIONS = "page dimensions were not accepted"
MESSAGE_BOUNDS = "line geometry was outside the page"
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
    CODE_BEAM,
    "http_error",
    "invalid_request",
    "no_geometry",
    "line_id_missing",
    "line_text_too_long",
    CODE_DIMENSIONS,
    CODE_BOUNDS,
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
    CODE_BEAM: MESSAGE_BEAM,
    "http_error": MESSAGE_UNAVAILABLE,
    "invalid_request": "recognition request was not accepted",
    "no_geometry": "line geometry was not usable",
    "line_id_missing": "line id was not accepted",
    "line_text_too_long": "line text was too long",
    CODE_DIMENSIONS: MESSAGE_DIMENSIONS,
    CODE_BOUNDS: MESSAGE_BOUNDS,
}
_PAGE_MARKER_CODE = "invalid_request"


def execute_htrflow_run(
    run_id,
    *,
    task_id="",
    recognize=None,
    fetch_models=None,
    pause=None,
    attempts=3,
    open_image=None,
    fetch_policy=None,
):
    """Run one queued page recognition. A terminal run returns without new calls."""
    if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id < 1:
        return None
    try:
        return _execute(
            run_id,
            task_id=task_id if isinstance(task_id, str) else "",
            recognize=recognize,
            fetch_models=fetch_models,
            pause=pause if pause is not None else _sleep,
            attempts=_attempts(attempts),
            open_image=open_image,
            fetch_policy=fetch_policy,
        )
    except Exception:
        _note(run_id, CODE_INTERNAL)
        _mark_internal(run_id)
        return None


def _execute(
    run_id, *, task_id, recognize, fetch_models, pause, attempts, open_image, fetch_policy,
):
    from ai.external_htr_dispatch import is_htrflow_page_config
    from ai.external_htr_policy import review_document_endpoint
    from ai.models import ExternalHTRRun

    if not _claim(run_id, task_id[:64]):
        return ExternalHTRRun.objects.filter(pk=run_id).first()
    run = ExternalHTRRun.objects.select_related("document", "config", "created_by").get(pk=run_id)
    if fetch_policy is None:
        fetch_policy = review_document_endpoint
    if open_image is None:
        open_image = _open_part_bytes
    if recognize is None or fetch_models is None:
        from ai.htrflow_client import fetch_page_models, recognize_page
        if recognize is None:
            recognize = recognize_page
        if fetch_models is None:
            fetch_models = fetch_page_models

    user = run.created_by
    document = run.document
    config = run.config
    if not _allowed(user, document.pk):
        return _fail_run(run, CODE_PERMISSION)
    if getattr(config, "enabled", None) is not True:
        return _fail_run(run, CODE_DISABLED)
    if getattr(config, "tier", None) == "api":
        return _fail_run(run, CODE_TIER)
    if not is_htrflow_page_config(config):
        return _fail_run(run, _PAGE_MARKER_CODE)
    blocked = _policy(fetch_policy, document, config)
    if blocked:
        return _fail_run(run, blocked)
    try:
        listed = fetch_models(config)
    except HtrflowClientError as exc:
        return _fail_run(run, getattr(exc, "code", None))
    except Exception:
        return _fail_run(run, CODE_RESPONSE)
    if getattr(listed, "engine", None) != "htrflow" or not isinstance(getattr(listed, "models", None), tuple):
        return _fail_run(run, CODE_RESPONSE)
    chosen = None
    for model in listed.models:
        if getattr(model, "model_id", None) == run.model_id:
            chosen = model
            break
    if chosen is None:
        return _fail_run(run, CODE_MODEL)
    if getattr(chosen, "beams_present", False) and beam_refusal(getattr(chosen, "num_beams", None)):
        return _fail_run(run, CODE_BEAM)
    run.engine = "htrflow"
    run.save(update_fields=["engine", "updated_at"])

    previous = _previous(run.pages)
    pages = []
    for part_id in _part_ids(run.part_ids):
        existing = _existing_job(run.pk, part_id)
        if existing is not None:
            prior = previous.get(part_id) or {}
            used = prior.get("attempts") if isinstance(prior.get("attempts"), int) else 0
            pages.append(_page_from_job(existing, used))
            continue
        if not _allowed(user, document.pk):
            pages.append(_failed_page(part_id, CODE_PERMISSION, 0))
            _note(run.pk, CODE_PERMISSION)
            _save_pages(run, pages)
            continue
        part = _load_part(document.pk, part_id)
        if part is None:
            pages.append(_failed_page(part_id, CODE_PART, 0))
            _note(run.pk, CODE_PART)
            _save_pages(run, pages)
            continue
        image = _read_image(open_image, part)
        if image is None:
            pages.append(_failed_page(part_id, CODE_IMAGE, 0))
            _note(run.pk, CODE_IMAGE)
            _save_pages(run, pages)
            continue
        used, alto, code = _recognize(recognize, config, run.model_id, image, attempts, pause)
        if alto is None:
            pages.append(_failed_page(part_id, code, used))
            _note(run.pk, _public(code))
            _save_pages(run, pages)
            continue
        parsed = parse_htrflow_alto(alto, document.pk, part.pk)
        if not parsed.ok or parsed.document_id != document.pk or parsed.part_id != part.pk:
            pages.append(_failed_page(part_id, parsed.code or CODE_RESPONSE, used))
            _note(run.pk, _public(parsed.code or CODE_RESPONSE))
            _save_pages(run, pages)
            continue
        job = _store_page(run, part, user, parsed)
        pages.append(_page_from_job(job, used))
        _save_pages(run, pages)
    return _finish(run, pages)


def _recognize(recognize, config, model_id, image, attempts, pause):
    last = CODE_UNAVAILABLE
    for number in range(1, attempts + 1):
        try:
            alto = recognize(config, model_id=model_id, image=image)
        except HtrflowClientError as exc:
            last = getattr(exc, "code", None) or CODE_RESPONSE
            if not retryable_failure(exc) or number == attempts:
                return number, None, last
            pause()
            continue
        except Exception:
            return number, None, CODE_RESPONSE
        if not isinstance(alto, str) or not alto.strip():
            return number, None, CODE_RESPONSE
        return number, alto, ""
    return attempts, None, last


def _store_page(run, part, user, parsed):
    from ai.models import ExternalHTRJob, ExternalHTRLineResult

    version = ""
    for item in parsed.models:
        if item.role == "recognition" and item.model_version:
            version = item.model_version
            break
    included = [line for line in parsed.lines if line.status == "included"]
    skipped = [line for line in parsed.lines if line.status == "skipped"]
    failed = [line for line in parsed.lines if line.status == "failed"]
    with transaction.atomic():
        existing = _existing_job(run.pk, part.pk)
        if existing is not None:
            return existing
        job = ExternalHTRJob.objects.create(
            document_id=run.document_id,
            part=part,
            config_id=run.config_id,
            model_id=run.model_id[:256],
            status=ExternalHTRJob.STATUS_COMPLETED,
            layer_source=_stamp(run.model_id),
            engine="htrflow",
            model_version=version[:256],
            api_version="alto",
            requested_line_count=len(parsed.lines),
            skipped_line_count=len(skipped),
            result_count=len(included),
            warning_count=0,
            empty_text_count=sum(1 for line in included if line.text == ""),
            code="",
            message="",
            run=run,
            created_by=user if getattr(user, "pk", None) else None,
            layout=parsed.layout(),
        )
        for line in parsed.lines:
            ExternalHTRLineResult.objects.create(
                job=job,
                line_id=line.line_id[:256],
                position=line.reading_order,
                text=line.text if line.status == "included" else "",
                confidence=line.confidence,
                timing_ms=0,
                warnings=[],
                status=line.status if line.status in _LINE_STATUS else "failed",
                code=line.code if line.code in _PUBLIC else "",
            )
    return job


def _stamp(model_id):
    source = "external-htr:htrflow:%s" % (model_id if isinstance(model_id, str) else "")
    return source[:128]


def _existing_job(run_id, part_id):
    from ai.models import ExternalHTRJob

    return (
        ExternalHTRJob.objects.filter(run_id=run_id, part_id=part_id).order_by("pk").first()
    )


def _load_part(document_id, part_id):
    from core.models import DocumentPart

    try:
        return DocumentPart.objects.get(pk=part_id, document_id=document_id)
    except (DocumentPart.DoesNotExist, ValueError, TypeError):
        return None


def _read_image(open_image, part):
    try:
        data = open_image(part)
    except Exception:
        return None
    if isinstance(data, memoryview):
        data = data.tobytes()
    if not isinstance(data, (bytes, bytearray)) or not data or len(data) > MAX_IMAGE_BYTES:
        return None
    return bytes(data)


def _open_part_bytes(part):
    image = getattr(part, "image", None)
    path = getattr(image, "path", None)
    if not isinstance(path, str) or not path:
        raise OSError("page image could not be read")
    with open(path, "rb") as handle:
        data = handle.read(MAX_IMAGE_BYTES + 1)
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise OSError("page image could not be read")
    return data


def _page_from_job(job, attempts):
    lines = []
    for row in job.lines.order_by("position", "pk"):
        text = row.text if isinstance(row.text, str) else ""
        status = row.status if row.status in _LINE_STATUS else "failed"
        lines.append({
            "line_id": row.line_id if isinstance(row.line_id, str) else "",
            "status": status,
            "code": row.code if isinstance(row.code, str) and row.code in _PUBLIC else "",
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


def _save_pages(run, pages):
    run.pages = pages
    run.save(update_fields=["pages", "updated_at"])


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


def _fail_run(run, code):
    safe = _public(code)
    run.status = "failed"
    run.code = safe
    run.message = _message(safe)[:256]
    run.save(update_fields=["status", "code", "message", "updated_at"])
    _note(run.pk, safe)
    return run


def _public(code):
    if isinstance(code, str) and code in _PUBLIC:
        return code
    return CODE_INTERNAL


def _message(code):
    return _MESSAGES.get(code, MESSAGE_INTERNAL)


def _note(run_id, code):
    logger.info("htrflow run %s %s", run_id, _public(code))


def _sleep():
    time.sleep(1)
