"""Document API for the external HTR action.

The view calls these helpers. They do not change the Transcribe action.
A hosted tier is refused before any engine call. Model lists omit the
endpoint, stored metadata, and model source paths.
"""
from __future__ import annotations

from rest_framework import status
from rest_framework.response import Response

from ai.external_htr_dispatch import is_htrflow_page_config
from ai.external_htr_menu import engine_menu
from ai.external_htr_policy import review_document_endpoint
from ai.external_htr_run import (
    CODE_CONFIG,
    CODE_DISABLED,
    CODE_INTERNAL,
    CODE_MODEL,
    CODE_NOTHING,
    CODE_PERMISSION,
    CODE_RESPONSE,
    CODE_TIER,
    CODE_LINE_HEIGHT,
    CODE_UNAVAILABLE,
    MESSAGE_DISABLED,
    MESSAGE_LINE_HEIGHT,
    MESSAGE_MODEL,
    MESSAGE_PERMISSION,
    MESSAGE_RESPONSE,
    MESSAGE_TIER,
    MESSAGE_UNAVAILABLE,
    _message,
    _public,
)
from ai.htr_engine_client import EngineClientError
from ai.htr_engine_contract import ContractError

CODE_INVALID = "invalid_request"
CODE_BEAM = "beam_width_refused"
MESSAGE_BEAM = "model beam width was not accepted"
MESSAGE_PAGES = "pages were not accepted"
MESSAGE_TOO_MANY = "too many pages"
MESSAGE_MODEL_ID = "model was not accepted"
MESSAGE_NONE = "no pages were selected"
MESSAGE_QUEUE = "recognition job was not queued"
_PAGE_CAP = 100
_LINE_KEYS = ("line_id", "status", "code", "empty", "text_length")
_PAGE_KEYS = (
    "part_id", "status", "code", "requested", "skipped", "results",
    "empty_text", "warnings", "job_id", "attempts", "lines",
)
_IMPORT_OK = frozenset({"writeback_imported", "already_imported"})


def list_engines(document, user):
    """Return engine names an authorized user may consider. Does not call them."""
    denied = _denied(document, user)
    if denied is not None:
        return denied
    from ai.models import ExternalHTREngineConfig

    configs = list(ExternalHTREngineConfig.objects.order_by("name"))
    engines = []
    for config in configs:
        enabled = config.enabled is True
        engines.append({
            "id": config.pk,
            "name": config.name,
            "tier": config.tier,
            "experimental": config.experimental is True,
            "enabled": enabled,
            "available": enabled and config.tier != "api",
        })
    return Response({"engines": engine_menu(configs, engines)})


def list_models(document, user, config_id):
    """Return models from one enabled engine, after the document policy check."""
    denied = _denied(document, user)
    if denied is not None:
        return denied
    config = _config(config_id)
    if config is None:
        return _error(CODE_CONFIG, "engine was not found")
    refused = _refused(document, config)
    if refused is not None:
        return refused
    if is_htrflow_page_config(config):
        return _page_models(config)
    try:
        from ai.htr_engine_client import fetch_capabilities, fetch_models
        capabilities = fetch_capabilities(config)
        listed = fetch_models(config)
    except (EngineClientError, ContractError) as exc:
        code = _public(getattr(exc, "code", None))
        return _error(code, _message(code))
    except Exception:
        return _error(CODE_RESPONSE, MESSAGE_RESPONSE)
    engine = getattr(capabilities, "engine", None)
    if not isinstance(engine, str) or not engine or engine != getattr(listed, "engine", None):
        return _error(CODE_RESPONSE, MESSAGE_RESPONSE)
    models = []
    for model in getattr(listed, "models", ()) or ():
        model_id = getattr(model, "model_id", None)
        if not isinstance(model_id, str) or not model_id:
            continue
        display = getattr(model, "display_name", "")
        licence = getattr(model, "licence", "")
        models.append({
            "model_id": model_id,
            "display_name": display if isinstance(display, str) and display else model_id,
            "licence": licence if isinstance(licence, str) else "",
            "experimental": getattr(model, "experimental", None) is True,
            "line_height": _reported_line_height(model),
        })
    return Response({"engine": engine, "models": models})


def list_runs(document, user):
    denied = _denied(document, user)
    if denied is not None:
        return denied
    from ai.models import ExternalHTRRun

    runs = []
    for run in ExternalHTRRun.objects.filter(document_id=document.pk).order_by("-pk")[:20]:
        runs.append({
            "id": run.pk,
            "status": run.status,
            "code": run.code,
            "engine": run.engine,
            "model_id": run.model_id,
            "imported": run.imported is True,
            "transcription_id": run.imported_transcription_id,
        })
    return Response({"runs": runs})


def start_run(document, user, payload, queue=None):
    """Queue recognition for pages of this document. Does not call the engine here."""
    denied = _denied(document, user)
    if denied is not None:
        return denied
    if not isinstance(payload, dict):
        return _error(CODE_INVALID, MESSAGE_PAGES)
    config = _config(payload.get("config_id"))
    if config is None:
        return _error(CODE_CONFIG, "engine was not found")
    refused = _refused(document, config)
    if refused is not None:
        return refused
    model_id = payload.get("model_id")
    if not isinstance(model_id, str) or not model_id.strip() or model_id != model_id.strip():
        return _error(CODE_INVALID, MESSAGE_MODEL_ID)
    if len(model_id) > 256:
        return _error(CODE_INVALID, MESSAGE_MODEL_ID)
    if is_htrflow_page_config(config):
        blocked_model = _htrflow_beams(config, model_id)
    else:
        blocked_model = _pylaia_line_height(config, model_id)
    if blocked_model is not None:
        return blocked_model
    part_ids, part_error = _part_ids(document, payload.get("part_ids"))
    if part_error is not None:
        return part_error
    from ai.models import ExternalHTRRun

    run = ExternalHTRRun.objects.create(
        document=document,
        config=config,
        created_by=user,
        model_id=model_id,
        engine="",
        status=ExternalHTRRun.STATUS_QUEUED,
        part_ids=part_ids,
        pages=[],
    )
    if queue is None:
        queue = _default_queue
    try:
        queue(run.pk)
    except Exception:
        run.status = ExternalHTRRun.STATUS_FAILED
        run.code = CODE_UNAVAILABLE
        run.message = MESSAGE_QUEUE
        run.save(update_fields=["status", "code", "message", "updated_at"])
        return Response(
            {"id": run.pk, "status": run.status, "code": run.code, "message": run.message},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return Response(
        {"id": run.pk, "status": ExternalHTRRun.STATUS_QUEUED},
        status=status.HTTP_202_ACCEPTED,
    )


def get_run(document, user, run_id):
    denied = _denied(document, user)
    if denied is not None:
        return denied
    run = _run(document, run_id)
    if run is None:
        return Response({"code": CODE_INTERNAL, "message": "recognition was not found"}, status=404)
    return Response(_detail(run))


def import_run(document, user, run_id):
    """Import a finished run. Does not call an engine or acknowledge the sample."""
    denied = _denied(document, user)
    if denied is not None:
        return denied
    run = _run(document, run_id)
    if run is None:
        return Response({"code": CODE_INTERNAL, "message": "recognition was not found"}, status=404)
    if run.engine == "htrflow":
        from ai.htrflow_run_import import import_htrflow_run

        result = import_htrflow_run(run.pk)
    else:
        from ai.external_htr_run_import import import_external_htr_run

        result = import_external_htr_run(run.pk)
    body = {
        "code": result.code,
        "message": result.message,
        "transcription_id": result.transcription_id,
        "lines": result.lines,
    }
    http = status.HTTP_200_OK if result.code in _IMPORT_OK else status.HTTP_400_BAD_REQUEST
    return Response(body, status=http)


def _default_queue(run_id):
    from ai.external_htr_tasks import external_htr_recognize

    external_htr_recognize.delay(run_id)


def _denied(document, user):
    from core.models import Document

    if document is None or user is None or not getattr(user, "is_authenticated", False):
        return _error(CODE_PERMISSION, MESSAGE_PERMISSION, status.HTTP_403_FORBIDDEN)
    pk = getattr(document, "pk", None)
    if isinstance(pk, bool) or not isinstance(pk, int):
        return _error(CODE_PERMISSION, MESSAGE_PERMISSION, status.HTTP_403_FORBIDDEN)
    if not Document.objects.for_user(user).filter(pk=pk).exists():
        return _error(CODE_PERMISSION, MESSAGE_PERMISSION, status.HTTP_403_FORBIDDEN)
    return None


def _refused(document, config):
    if getattr(config, "enabled", None) is not True:
        return _error(CODE_DISABLED, MESSAGE_DISABLED)
    if getattr(config, "tier", None) == "api":
        return _error(CODE_TIER, MESSAGE_TIER)
    try:
        verdict = review_document_endpoint(document, config)
    except Exception:
        verdict = "policy_unreadable"
    if verdict is not None:
        code = _public(verdict)
        return _error(code, _message(code))
    return None


def _config(config_id):
    from ai.models import ExternalHTREngineConfig

    if isinstance(config_id, bool) or not isinstance(config_id, int) or config_id < 1:
        if isinstance(config_id, str) and config_id.isdecimal():
            config_id = int(config_id)
        else:
            return None
    return ExternalHTREngineConfig.objects.filter(pk=config_id).first()


def _part_ids(document, raw):
    if not isinstance(raw, list) or not raw:
        return None, _error(CODE_NOTHING, MESSAGE_NONE)
    if len(raw) > _PAGE_CAP:
        return None, _error(CODE_INVALID, MESSAGE_TOO_MANY)
    collected = []
    seen = set()
    for item in raw:
        if isinstance(item, str) and item.isdecimal():
            item = int(item)
        if isinstance(item, bool) or not isinstance(item, int) or item < 1:
            return None, _error(CODE_INVALID, MESSAGE_PAGES)
        if item in seen:
            continue
        seen.add(item)
        collected.append(item)
    from core.models import DocumentPart

    found = set(
        DocumentPart.objects.filter(document_id=document.pk, pk__in=collected)
        .values_list("pk", flat=True)
    )
    if found != set(collected):
        return None, _error("part_not_found", "page was not found")
    return collected, None


def _run(document, run_id):
    from ai.models import ExternalHTRRun

    if isinstance(run_id, str) and run_id.isdecimal():
        run_id = int(run_id)
    if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id < 1:
        return None
    return ExternalHTRRun.objects.filter(pk=run_id, document_id=document.pk).first()


def _detail(run):
    layer_name = ""
    gate_id = None
    gate_state = None
    if run.imported_transcription_id:
        from ai.models import AILayerGate
        from core.models import Transcription

        layer_name = Transcription.objects.filter(pk=run.imported_transcription_id).values_list(
            "name", flat=True,
        ).first() or ""
        gate = AILayerGate.objects.filter(transcription_id=run.imported_transcription_id).only(
            "pk", "state",
        ).first()
        if gate is not None:
            gate_id = gate.pk
            gate_state = gate.state
    return {
        "id": run.pk,
        "status": run.status,
        "code": run.code,
        "message": run.message,
        "engine": run.engine,
        "model_id": run.model_id,
        "imported": run.imported is True,
        "transcription_id": run.imported_transcription_id,
        "layer_name": layer_name,
        "gate_id": gate_id,
        "gate_state": gate_state,
        "pages": [_public_page(page) for page in run.pages] if isinstance(run.pages, list) else [],
    }


def _public_page(page):
    if not isinstance(page, dict):
        return {}
    cleaned = {key: page.get(key) for key in _PAGE_KEYS if key != "lines"}
    lines = page.get("lines")
    cleaned["lines"] = []
    if isinstance(lines, list):
        for line in lines:
            if not isinstance(line, dict):
                continue
            cleaned["lines"].append({key: line.get(key) for key in _LINE_KEYS})
    return cleaned


def _reported_line_height(model):
    value = getattr(getattr(model, "input", None), "line_height", None)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 1024:
        return None
    return value


_PAGE_MESSAGES = {
    "disabled": MESSAGE_DISABLED,
    "timeout": MESSAGE_UNAVAILABLE,
    "unavailable": MESSAGE_UNAVAILABLE,
    "invalid_response": MESSAGE_RESPONSE,
    "http_error": MESSAGE_UNAVAILABLE,
    "invalid_request": "recognition request was not accepted",
    CODE_BEAM: MESSAGE_BEAM,
}


def _page_models(config):
    """List page pipelines. Does not open a page image."""
    try:
        from ai.htrflow_client import HtrflowClientError, fetch_page_models

        listed = fetch_page_models(config)
    except HtrflowClientError as exc:
        code = exc.code if isinstance(getattr(exc, "code", None), str) else CODE_RESPONSE
        safe = code if code in _PAGE_MESSAGES else CODE_RESPONSE
        return _error(safe, _PAGE_MESSAGES.get(safe, MESSAGE_RESPONSE))
    except Exception:
        return _error(CODE_RESPONSE, MESSAGE_RESPONSE)
    if getattr(listed, "engine", None) != "htrflow" or not isinstance(getattr(listed, "models", None), tuple):
        return _error(CODE_RESPONSE, MESSAGE_RESPONSE)
    from ai.htrflow_contract import public_num_beams

    models = []
    for model in listed.models:
        models.append({
            "model_id": model.model_id,
            "display_name": model.display_name or model.model_id,
            "licence": model.licence if isinstance(model.licence, str) else "",
            "experimental": model.experimental is True,
            "num_beams": public_num_beams(model),
            "task": "recognize_page",
        })
    return Response({"engine": "htrflow", "models": models})


def _htrflow_beams(config, model_id):
    """Refuse a reported beam width other than 1 before a run is queued.

    A failed model-list call does not block the queue. The worker records
    that failure. This check does not open a page image.
    """
    try:
        from ai.htrflow_client import fetch_page_models

        listed = fetch_page_models(config)
    except Exception:
        return None
    if getattr(listed, "engine", None) != "htrflow" or not isinstance(getattr(listed, "models", None), tuple):
        return _error(CODE_RESPONSE, MESSAGE_RESPONSE)
    chosen = None
    for model in listed.models:
        if getattr(model, "model_id", None) == model_id:
            chosen = model
            break
    if chosen is None:
        return _error(CODE_MODEL, MESSAGE_MODEL)
    if getattr(chosen, "beams_present", False):
        from ai.htrflow_contract import beam_refusal

        if beam_refusal(getattr(chosen, "num_beams", None)):
            return _error(CODE_BEAM, MESSAGE_BEAM)
    return None


def _pylaia_line_height(config, model_id):
    """Refuse a PyLaia model that does not report a usable line height.

    A failed engine call does not block the queue. The worker records that
    failure. This check runs only after a readable model list.
    """
    try:
        from ai.htr_engine_client import fetch_capabilities, fetch_models

        capabilities = fetch_capabilities(config)
        listed = fetch_models(config)
    except Exception:
        return None
    engine = getattr(capabilities, "engine", None)
    if engine != "pylaia" or engine != getattr(listed, "engine", None):
        return None
    models = getattr(listed, "models", None)
    if not isinstance(models, tuple):
        return _error(CODE_LINE_HEIGHT, MESSAGE_LINE_HEIGHT)
    chosen = None
    for model in models:
        if getattr(model, "model_id", None) == model_id:
            chosen = model
            break
    if chosen is None:
        return _error(CODE_MODEL, MESSAGE_MODEL)
    if _reported_line_height(chosen) is None:
        return _error(CODE_LINE_HEIGHT, MESSAGE_LINE_HEIGHT)
    return None


def _error(code, message, http=status.HTTP_400_BAD_REQUEST):
    return Response({"code": code, "message": message}, status=http)
