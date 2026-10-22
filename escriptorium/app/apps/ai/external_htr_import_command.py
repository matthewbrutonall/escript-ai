"""Preview or apply one external HTR audit import.

Preview only reads the planner. ``--apply`` calls the injected writer,
which checks the audit again. This module does not call an engine, open
an image, or print recognized text.
"""
from __future__ import annotations

from ai.external_htr_writeback_plan import (
    CODE_CANCELLED,
    CODE_DUPLICATE,
    CODE_FAILED,
    CODE_INCOMPLETE,
    CODE_LINE,
    CODE_MALFORMED,
    CODE_PART,
    CODE_PLANNED,
    CODE_SENT,
    CODE_TEXT,
)

CODE_INTERNAL = "import_internal"
CODE_ALREADY = "already_imported"
CODE_COLLISION = "layer_name_taken"
CODE_WRITE_FAILED = "writeback_failed"
CODE_SAMPLE = "review_sample_empty"

_PUBLIC_CODES = frozenset({
    CODE_ALREADY,
    CODE_COLLISION,
    CODE_WRITE_FAILED,
    CODE_SAMPLE,
    CODE_INTERNAL,
    CODE_PLANNED,
    CODE_SENT,
    CODE_FAILED,
    CODE_CANCELLED,
    CODE_INCOMPLETE,
    CODE_PART,
    CODE_DUPLICATE,
    CODE_MALFORMED,
    CODE_LINE,
    CODE_TEXT,
})


def execute_external_htr_import(
    job_id,
    *,
    apply,
    load_plan,
    import_writeback,
    fetch_job,
    count_lines,
):
    """Return ``(exit_code, line)`` for one preview or apply.

    ``apply`` must be exactly ``True`` or ``False``. The preview result is
    not passed to the writer.
    """
    parsed = _positive_job_id(job_id)
    if parsed is None:
        return _fail(CODE_INCOMPLETE)
    try:
        if apply is True:
            return _apply(parsed, import_writeback, count_lines)
        if apply is not False:
            return _fail(CODE_INTERNAL)
        return _preview(parsed, load_plan, fetch_job)
    except Exception:
        return _fail(CODE_INTERNAL)


def _preview(job_id, load_plan, fetch_job):
    job = fetch_job(job_id)
    if job is not None and (
        getattr(job, "imported", False) or getattr(job, "imported_transcription_id", None)
    ):
        return _fail(CODE_ALREADY)
    plan = load_plan(job_id)
    if not getattr(plan, "allowed", False):
        return _fail(getattr(plan, "code", None))
    lines = getattr(plan, "lines", None)
    if not isinstance(lines, tuple) or getattr(plan, "job_id", None) != job_id:
        return _fail(CODE_INTERNAL)
    return 0, "OK: external HTR import preview job=%d lines=%d" % (job_id, len(lines))


def _apply(job_id, import_writeback, count_lines):
    result = import_writeback(job_id)
    if not getattr(result, "imported", False):
        return _fail(getattr(result, "code", None))
    layer_id = getattr(result, "transcription_id", None)
    if type(layer_id) is not int or layer_id < 1 or getattr(result, "job_id", None) != job_id:
        return _fail(CODE_INTERNAL)
    count = count_lines(layer_id)
    if type(count) is not int or count < 1:
        return _fail(CODE_INTERNAL)
    return 0, "OK: external HTR import applied job=%d layer=%d lines=%d" % (
        job_id, layer_id, count,
    )


def _positive_job_id(value):
    if type(value) is int:
        number = value
    elif type(value) is str and value.isdigit():
        number = int(value)
    else:
        return None
    if number < 1:
        return None
    return number


def _fail(code):
    if type(code) is not str or code not in _PUBLIC_CODES:
        code = CODE_INTERNAL
    return 1, "FAILED: external HTR import " + code
