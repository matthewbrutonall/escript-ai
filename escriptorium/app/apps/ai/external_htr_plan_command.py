"""Plan one external HTR dry run and store its audit row.

The operator command calls this. It does not call an engine or write a
transcription layer. The default encoder is ``no_line_image``, which does
not read a page. ``encode_images=True`` crops with ``encode_line_image``
after the caller has opened the page. An unusable crop is skipped.
"""
from __future__ import annotations

from ai.external_htr_image import encode_line_image
from ai.external_htr_service import run_external_htr_dry_run

CODE_LIVE = "live_refused"
CODE_CONFIG = "config_not_found"
CODE_PART = "part_not_found"
CODE_INVALID = "invalid_plan"
CODE_INTERNAL = "internal"
CODE_IMAGE = "image_unavailable"

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
    encode_images=False,
    open_image=None,
    dry_run=True,
    job_model=None,
    line_model=None,
    atomic=None,
) -> tuple[int, str]:
    """Load one config and part, plan a dry run, and return one line.

    ``dry_run`` must stay true. A false value returns before either loader
    is called. ``encode_images`` must be true before a page is opened.
    The line is a fixed sentence. It does not include stored options,
    document names, images, or exception text.
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
    encoder = no_line_image if encode_line is None else encode_line
    page = None
    if encode_images is True and getattr(config, "enabled", None) is True:
        page = _opened_page(open_image, part)
        if not _usable_page(page):
            _close(page)
            return 1, _FAILED + CODE_IMAGE
        encoder = _encode_loaded_page(page)
    failed = False
    try:
        result = run(
            config,
            lines,
            document=document,
            part=part,
            engine=engine,
            model_id=model_id,
            encode_line=encoder,
            dry_run=True,
            job_model=job_model,
            line_model=line_model,
            atomic=atomic,
        )
    except Exception:
        failed = True
    finally:
        _close(page)
    if failed:
        return 1, _FAILED + CODE_INTERNAL
    return _summary(result)


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
