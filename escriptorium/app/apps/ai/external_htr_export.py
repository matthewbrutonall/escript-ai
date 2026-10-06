"""Export one external HTR recognize request as JSON.

The operator command calls this. It encodes line images and writes the
request file. It does not call an engine, store an audit row, or write a
transcription. The summary line does not include the output path.
"""
from __future__ import annotations

import json
import os
import tempfile

from ai.external_htr_image import encode_line_image
from ai.external_htr_plan import plan_external_htr

CODE_CONFIG = "config_not_found"
CODE_PART = "part_not_found"
CODE_DISABLED = "disabled"
CODE_IMAGE = "image_unavailable"
CODE_NOTHING = "nothing_to_send"
CODE_INVALID = "invalid_request"
CODE_EXISTS = "output_exists"
CODE_OUTPUT = "output_unavailable"
CODE_INTERNAL = "internal"

_FAILED = "FAILED: external HTR request export "


class ConfigNotFound(Exception):
    """The stored engine row does not exist."""


class PartNotFound(Exception):
    """The document part does not exist."""


class OutputExists(Exception):
    """The output file already exists."""


class OutputUnavailable(Exception):
    """The output file could not be written."""


def write_request_json(path, payload, *, force=False):
    """Write compact JSON. The destination changes only after the bytes are complete."""
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    if force is not True and _output_exists(path):
        raise OutputExists
    try:
        temporary = _write_temporary(os.path.dirname(path) or ".", raw)
    except OSError:
        raise OutputUnavailable from None
    try:
        _publish(temporary, path, force=force is True)
    except OutputExists:
        raise
    except OSError:
        raise OutputUnavailable from None
    finally:
        # The temporary name is not the published destination.
        _discard(temporary)


def _output_exists(path):
    try:
        return os.path.lexists(path)
    except OSError:
        raise OutputUnavailable from None


def _write_temporary(directory, raw):
    try:
        fd, temporary = tempfile.mkstemp(prefix=".htrreq-", dir=directory)
    except OSError:
        raise OutputUnavailable from None
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        _discard(temporary)
        raise
    return temporary


def _publish(temporary, path, *, force):
    """Put a finished file in place. Link refuses a name that already exists."""
    if force:
        os.replace(temporary, path)
        return
    try:
        os.link(temporary, path)
    except FileExistsError:
        raise OutputExists from None


def _discard(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def execute_external_htr_export(
    config_id,
    part_id,
    *,
    model_id,
    engine,
    output_path,
    fetch_config,
    fetch_part,
    open_image,
    write_output=write_request_json,
    plan=plan_external_htr,
    force=False,
) -> tuple[int, str]:
    """Build one recognize request and write it when it can be sent.

    A disabled config returns before the page is opened. A request that
    cannot be sent does not create the output file. An existing file is
    left in place unless ``force`` is exactly true.
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
    request = getattr(built, "request", None)
    included = _count(getattr(built, "included_count", None))
    skipped = _skipped_count(getattr(built, "skipped", None))
    if (
        getattr(built, "would_send", None) is not True
        or not isinstance(request, dict)
        or not request.get("lines")
        or included is None
        or skipped is None
    ):
        return _fail(_public_code(getattr(built, "code", None), CODE_NOTHING))
    try:
        write_output(output_path, request, force=force is True)
    except OutputExists:
        return _fail(CODE_EXISTS)
    except OutputUnavailable:
        return _fail(CODE_OUTPUT)
    except FileExistsError:
        return _fail(CODE_EXISTS)
    except OSError:
        return _fail(CODE_OUTPUT)
    except Exception:
        return _fail(CODE_INTERNAL)
    return 0, (
        f"OK: external HTR request exported lines={included} skipped={skipped}"
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


def _count(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _skipped_count(value):
    if not isinstance(value, tuple):
        return None
    return _count(len(value))


def _public_code(value, fallback: str) -> str:
    if (
        isinstance(value, str)
        and value
        and len(value) <= 64
        and value == value.lower()
        and value.replace("_", "").isalnum()
    ):
        return value
    return fallback


def _fail(code: str) -> tuple[int, str]:
    return 1, _FAILED + code
