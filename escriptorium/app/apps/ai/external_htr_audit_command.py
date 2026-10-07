"""Run one external HTR audit for a document part.

The operator command calls this. It encodes line images in memory, calls
one configured engine, and stores audit rows. It does not write a
transcription. A failure stores a fixed code and sentence only.
"""
from __future__ import annotations

from ai.external_htr_apply_plan import (
    MESSAGE_READY as APPLY_READY,
    ExternalHtrApplyPlan,
)
from ai.external_htr_audit import (
    STATUS_COMPLETED,
    STATUS_PLANNED,
    ExternalHtrAuditError,
    record_external_htr_apply,
    record_external_htr_plan,
)
from ai.external_htr_image import encode_line_image
from ai.external_htr_live import (
    MESSAGE_APPLY,
    MESSAGE_CLIENT,
    MESSAGE_CONTRACT,
    MESSAGE_DISABLED,
    run_external_htr_live,
)
from ai.external_htr_plan import (
    MESSAGE_INVALID as PLAN_INVALID,
    MESSAGE_NOTHING,
    MESSAGE_READY as PLAN_READY,
    ExternalHtrPlan,
    plan_external_htr,
)

CODE_CONFIG = "config_not_found"
CODE_PART = "part_not_found"
CODE_DISABLED = "disabled"
CODE_IMAGE = "image_unavailable"
CODE_NOTHING = "nothing_to_send"
CODE_INVALID = "invalid_request"
CODE_INTERNAL = "internal"

_FAILED = "FAILED: external HTR audit "

_FAILURE_MESSAGES = frozenset({
    MESSAGE_DISABLED,
    MESSAGE_CLIENT,
    MESSAGE_CONTRACT,
    MESSAGE_APPLY,
    PLAN_INVALID,
    MESSAGE_NOTHING,
})

# Printed and stored failure codes. Anything else, including a payload, becomes internal.
_PUBLIC_CODES = frozenset({
    CODE_CONFIG,
    CODE_PART,
    CODE_DISABLED,
    CODE_IMAGE,
    CODE_NOTHING,
    CODE_INVALID,
    CODE_INTERNAL,
    "timeout",
    "unavailable",
    "invalid_response",
    "http_error",
    "unsupported_preprocessing",
    "model_not_found",
    "line_failed",
    "busy",
    "already_finalized",
    "invalid_plan",
})

_SKIP_CODES = frozenset({
    "no_mask",
    "no_baseline",
    "bad_line_id",
    "duplicate_line_id",
    "no_image",
    "bad_image",
    "line_cap",
    "payload_cap",
})


class ConfigNotFound(Exception):
    """The stored engine row does not exist."""


class PartNotFound(Exception):
    """The document part does not exist."""


def execute_external_htr_audit(
    config_id,
    part_id,
    *,
    model_id,
    engine,
    fetch_config,
    fetch_part,
    open_image,
    plan=plan_external_htr,
    record_plan=record_external_htr_plan,
    record_apply=record_external_htr_apply,
    run=run_external_htr_live,
) -> tuple[int, str]:
    """Audit one document part. A disabled config returns before the page opens.

    Image failure returns before any audit row is written. A live failure
    marks the planned job failed and does not copy the response.
    """
    try:
        config = fetch_config(config_id)
    except ConfigNotFound:
        return _fail(CODE_CONFIG)
    except Exception:
        return _fail(CODE_INTERNAL)
    try:
        loaded = fetch_part(part_id)
    except PartNotFound:
        return _fail(CODE_PART)
    except Exception:
        return _fail(CODE_INTERNAL)
    part, lines, document = _loaded(loaded)
    if config is None or part is None or document is None or lines is None:
        return _fail(CODE_INVALID)
    document_id = _pk(document)
    part_pk = _pk(part)
    if document_id is None or part_pk is None:
        return _fail(CODE_INVALID)
    if getattr(config, "enabled", None) is not True:
        return _fail(CODE_DISABLED)
    page = _opened_page(open_image, part)
    if not _usable_page(page):
        _close(page)
        return _fail(CODE_IMAGE)
    try:
        try:
            built = plan(
                config,
                lines,
                document_id=document_id,
                part_id=part_pk,
                engine=engine,
                model_id=model_id,
                encode_line=_encode_loaded_page(page),
            )
        except Exception:
            return _fail(CODE_INTERNAL)
    finally:
        _close(page)
    if not isinstance(built, ExternalHtrPlan):
        return _fail(CODE_INTERNAL)
    built = _boring_plan(built)
    try:
        job = record_plan(
            built,
            document=document,
            config=config,
            part=part,
            created_by=None,
        )
    except ExternalHtrAuditError as exc:
        return _fail(_public_code(getattr(exc, "code", None), CODE_INTERNAL))
    except Exception:
        return _fail(CODE_INTERNAL)
    if job is None:
        return _fail(CODE_INTERNAL)
    request = getattr(built, "request", None)
    if not _sendable(built, request):
        if getattr(job, "status", None) == STATUS_PLANNED:
            _store_failure(job, CODE_INVALID, PLAN_INVALID, record_apply)
        return _fail(_public_code(getattr(built, "code", None), CODE_NOTHING))
    try:
        result = run(config, request)
    except Exception:
        _store_failure(job, CODE_INTERNAL, None, record_apply)
        return _fail(CODE_INTERNAL)
    apply_plan = getattr(result, "apply_plan", None)
    if not _accepted(result, apply_plan):
        _store_failure(
            job,
            _public_code(getattr(result, "code", None), CODE_INTERNAL),
            getattr(result, "message", None),
            record_apply,
        )
        return _fail(_public_code(getattr(result, "code", None), CODE_INTERNAL))
    try:
        record_apply(job, apply_plan)
    except ExternalHtrAuditError as exc:
        code = _public_code(getattr(exc, "code", None), CODE_INTERNAL)
        _store_failure(job, code, None, record_apply)
        return _fail(code)
    except Exception:
        _store_failure(job, CODE_INTERNAL, None, record_apply)
        return _fail(CODE_INTERNAL)
    return _ok(job)


def _accepted(result, apply_plan) -> bool:
    return (
        getattr(result, "ok", None) is True
        and isinstance(apply_plan, ExternalHtrApplyPlan)
        and apply_plan.would_write is True
        and apply_plan.message == APPLY_READY
    )


class _Skip:
    """A skipped line with a fixed reason. The reason is not caller text."""

    def __init__(self, line_id, reason):
        self.line_id = line_id
        self.reason = reason


def _boring_plan(plan):
    """Keep stored status text on the fixed sentences this helper already uses."""
    if plan.would_send is True:
        message = PLAN_READY
        code = None
    else:
        message = plan.message if plan.message in _FAILURE_MESSAGES else MESSAGE_NOTHING
        code = _public_code(plan.code, CODE_NOTHING)
    skipped = _fixed_skips(plan.skipped)
    if message == plan.message and code == plan.code and skipped is plan.skipped:
        return plan
    included = _count(plan.included_count)
    return ExternalHtrPlan(
        would_send=plan.would_send is True,
        request=plan.request,
        included_count=0 if included is None else included,
        skipped=skipped,
        engine=plan.engine if isinstance(plan.engine, str) else "",
        config_name=plan.config_name if isinstance(plan.config_name, str) else "",
        model_id=plan.model_id if isinstance(plan.model_id, str) else "",
        code=code,
        message=message,
    )


def _fixed_skips(skipped):
    """Drop a skip reason that is not one of the fixed codes."""
    if not isinstance(skipped, tuple):
        return skipped
    if all(getattr(item, "reason", None) in _SKIP_CODES for item in skipped):
        return skipped
    cleaned = []
    for item in skipped:
        reason = getattr(item, "reason", None)
        if reason not in _SKIP_CODES:
            reason = "no_image"
        line_id = getattr(item, "line_id", None)
        if not isinstance(line_id, str):
            line_id = None
        cleaned.append(_Skip(line_id, reason))
    return tuple(cleaned)


def _store_failure(job, code, message, record_apply):
    """Mark a planned job failed. The plan has no line text and no response."""
    if getattr(job, "status", None) != STATUS_PLANNED:
        return
    failure = ExternalHtrApplyPlan(
        would_write=False,
        code=_public_code(code, CODE_INTERNAL),
        message=_fixed_message(message),
        layer_source="",
        engine="",
        model_id="",
        model_version="",
        api_version="",
        results=(),
        result_count=0,
        warning_count=0,
        empty_text_count=0,
    )
    try:
        record_apply(job, failure)
    except Exception:
        return


def _fixed_message(message) -> str:
    if message in _FAILURE_MESSAGES:
        return message
    return MESSAGE_CLIENT


def _sendable(built, request) -> bool:
    if getattr(built, "would_send", None) is not True:
        return False
    if not isinstance(request, dict):
        return False
    lines = request.get("lines")
    return isinstance(lines, list) and bool(lines)


def _ok(job):
    job_id = _job_id(job)
    results = _count(getattr(job, "result_count", None))
    skipped = _count(getattr(job, "skipped_line_count", None))
    if (
        getattr(job, "status", None) != STATUS_COMPLETED
        or job_id is None
        or results is None
        or skipped is None
    ):
        return _fail(CODE_INTERNAL)
    return 0, (
        f"OK: external HTR audit job {job_id} completed "
        f"results={results} skipped={skipped}"
    )


def _opened_page(open_image, part):
    if not callable(open_image):
        return None
    try:
        return open_image(part)
    except Exception:
        return None


def _usable_page(page) -> bool:
    width = getattr(page, "width", None)
    height = getattr(page, "height", None)
    if isinstance(width, bool) or not isinstance(width, int) or width < 1:
        return False
    if isinstance(height, bool) or not isinstance(height, int) or height < 1:
        return False
    return callable(getattr(page, "crop", None))


def _encode_loaded_page(page):
    def encode(line):
        try:
            result = encode_line_image(page, line)
        except Exception:
            return None
        image = result.image
        if result.code is not None or not isinstance(image, str) or not image:
            return None
        if image.startswith("data:"):
            return None
        return image

    return encode


def _close(page):
    close = getattr(page, "close", None)
    if not callable(close):
        return
    try:
        close()
    except Exception:
        return


def _loaded(value):
    if not isinstance(value, tuple) or len(value) != 2:
        return None, None, None
    part, lines = value
    if not isinstance(lines, (list, tuple)):
        return None, None, None
    return part, lines, getattr(part, "document", None)


def _pk(value):
    pk = getattr(value, "pk", None)
    if isinstance(pk, bool):
        return None
    if isinstance(pk, int):
        return pk
    if isinstance(pk, str) and pk.strip() and pk == pk.strip():
        return pk
    return None


def _job_id(job):
    pk = getattr(job, "pk", None)
    if isinstance(pk, bool) or not isinstance(pk, int) or pk < 1:
        return None
    return pk


def _count(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _public_code(value, fallback: str) -> str:
    if isinstance(value, str) and value in _PUBLIC_CODES:
        return value
    return fallback


def _fail(code: str) -> tuple[int, str]:
    return 1, _FAILED + code
