"""Run an external HTR plan and store the audit row.

The default call is a dry run. It does not call an engine or write a
transcription layer. Live execution is refused. An optional response is
applied to the audit row only when the caller already has one.

Off-site document policy is not checked here. No local helper applies
that policy to an external engine config, and this dry run does not
open a connection.
"""
from __future__ import annotations

from dataclasses import dataclass

from ai.external_htr_apply_plan import plan_external_htr_apply
from ai.external_htr_audit import record_external_htr_apply, record_external_htr_plan
from ai.external_htr_plan import plan_external_htr

CODE_LIVE = "live_refused"
MESSAGE_LIVE = "live execution is not available"

_ERRORS = {
    CODE_LIVE: MESSAGE_LIVE,
}


class ExternalHtrServiceError(Exception):
    """A fixed failure before any plan or audit write.

    The message is chosen from the code. It does not include caller data.
    """

    def __init__(self, code: str):
        self.code = code
        super().__init__(_ERRORS[code])


@dataclass(frozen=True)
class ExternalHtrDryRun:
    """Audit outcome of one dry run. It does not carry a request body."""

    job_id: int | None
    status: str
    code: str
    message: str
    requested_line_count: int
    skipped_line_count: int
    result_count: int
    warning_count: int
    empty_text_count: int
    skips: tuple[tuple[str | None, str], ...]


def run_external_htr_dry_run(
    config,
    lines,
    *,
    document,
    engine: str,
    model_id: str,
    encode_line,
    part=None,
    preprocessing: dict | None = None,
    max_lines: int | None = None,
    created_by=None,
    response=None,
    dry_run: bool = True,
    job_model=None,
    line_model=None,
    atomic=None,
) -> ExternalHtrDryRun:
    """Plan a recognize call and store the audit job.

    ``engine`` is the contract engine name. ``config`` is stored as a
    reference. ``response``, when given, is an already-built recognize
    body used only to finalize that audit job. ``dry_run`` must stay true.
    """
    if dry_run is not True:
        raise ExternalHtrServiceError(CODE_LIVE)
    plan = plan_external_htr(
        config,
        lines,
        document_id=_pk(document),
        part_id=None if part is None else _pk(part),
        engine=engine,
        model_id=model_id,
        encode_line=encode_line,
        preprocessing=preprocessing,
        max_lines=max_lines,
    )
    job = record_external_htr_plan(
        plan,
        document=document,
        config=config,
        part=part,
        created_by=created_by,
        job_model=job_model,
        line_model=line_model,
        atomic=atomic,
    )
    if response is not None and plan.would_send and plan.request is not None:
        applied = plan_external_htr_apply(plan.request, response)
        job = record_external_htr_apply(
            job, applied, line_model=line_model, atomic=atomic,
        )
    return ExternalHtrDryRun(
        job_id=getattr(job, "pk", None),
        status=job.status,
        code=job.code,
        message=job.message,
        requested_line_count=job.requested_line_count,
        skipped_line_count=job.skipped_line_count,
        result_count=job.result_count,
        warning_count=job.warning_count,
        empty_text_count=job.empty_text_count,
        skips=tuple((item.line_id, item.reason) for item in plan.skipped),
    )


def _pk(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return getattr(value, "pk", None)
    return value
