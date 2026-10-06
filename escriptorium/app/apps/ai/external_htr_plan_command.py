"""Plan one external HTR dry run and store its audit row.

The operator command calls this. It does not call an engine or write a
transcription layer. ``no_line_image`` supplies no page image, so the
command checks line geometry and the audit row but does not build a
sendable image payload.
"""
from __future__ import annotations

from ai.external_htr_service import run_external_htr_dry_run

CODE_LIVE = "live_refused"
CODE_CONFIG = "config_not_found"
CODE_PART = "part_not_found"
CODE_INVALID = "invalid_plan"
CODE_INTERNAL = "internal"

_FAILED = "FAILED: external HTR dry run "


class ConfigNotFound(Exception):
    """The stored engine row does not exist."""


class PartNotFound(Exception):
    """The document part does not exist."""


def no_line_image(line):
    """No page image is read. The line stays unsendable."""
    return None


def execute_external_htr_dry_run(
    config_id,
    part_id,
    *,
    model_id,
    engine,
    fetch_config,
    fetch_part,
    run=run_external_htr_dry_run,
    encode_line=None,
    dry_run=True,
    job_model=None,
    line_model=None,
    atomic=None,
) -> tuple[int, str]:
    """Load one config and part, plan a dry run, and return one line.

    ``dry_run`` must stay true. A false value returns before either loader
    is called. The line is a fixed sentence. It does not include stored
    options, document names, images, or exception text.
    """
    if dry_run is not True:
        return 1, _FAILED + CODE_LIVE
    try:
        config = fetch_config(config_id)
    except ConfigNotFound:
        return 1, _FAILED + CODE_CONFIG
    except Exception:
        return 1, _FAILED + CODE_INTERNAL
    try:
        loaded = fetch_part(part_id)
    except PartNotFound:
        return 1, _FAILED + CODE_PART
    except Exception:
        return 1, _FAILED + CODE_INTERNAL
    part, lines, document = _loaded(loaded)
    if config is None or part is None or document is None or lines is None:
        return 1, _FAILED + CODE_INVALID
    try:
        result = run(
            config,
            lines,
            document=document,
            part=part,
            engine=engine,
            model_id=model_id,
            encode_line=no_line_image if encode_line is None else encode_line,
            dry_run=True,
            job_model=job_model,
            line_model=line_model,
            atomic=atomic,
        )
    except Exception:
        return 1, _FAILED + CODE_INTERNAL
    return _summary(result)


def _loaded(value):
    if not isinstance(value, tuple) or len(value) != 2:
        return None, None, None
    part, lines = value
    if not isinstance(lines, (list, tuple)):
        return None, None, None
    document = getattr(part, "document", None)
    return part, lines, document


def _summary(result) -> tuple[int, str]:
    code = getattr(result, "code", None)
    status = getattr(result, "status", None)
    job_id = _job_id(getattr(result, "job_id", None))
    included = _count(getattr(result, "requested_line_count", None))
    skipped = _count(getattr(result, "skipped_line_count", None))
    if (
        status == "planned"
        and code in ("", None)
        and job_id is not None
        and included is not None
        and skipped is not None
    ):
        return 0, (
            f"OK: external HTR dry run planned job {job_id} "
            f"(planned, included={included}, skipped={skipped})"
        )
    return 1, _FAILED + _public_code(code)


def _job_id(value):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _count(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _public_code(value) -> str:
    if (
        isinstance(value, str)
        and value
        and len(value) <= 64
        and value == value.lower()
        and value.replace("_", "").isalnum()
    ):
        return value
    return CODE_INVALID
