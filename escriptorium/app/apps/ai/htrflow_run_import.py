"""Import one HTRflow page run into a new transcription layer.

The background task does not call this. A person asks for the import
after recognition. Only completed pages are written. The layer starts
behind the raw review gate and is not marked training-eligible. This
module does not call an engine.
"""
from __future__ import annotations

from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from ai.external_htr_writeback_plan import (
    CODE_EMPTY,
    CODE_INCOMPLETE,
    CONTENT_LIMIT,
    MESSAGE_EMPTY,
    MESSAGE_INCOMPLETE,
    VERSION_SOURCE_LIMIT,
)
from ai.gate import build_gate_sample
from ai.htrflow_adapter import plan_htrflow_import

CODE_IMPORTED = "writeback_imported"
MESSAGE_IMPORTED = "new transcription layer was created"
CODE_ALREADY = "already_imported"
MESSAGE_ALREADY = "audit was already imported"
CODE_COLLISION = "layer_name_taken"
MESSAGE_COLLISION = "transcription layer name is taken"
CODE_FAILED = "writeback_failed"
MESSAGE_FAILED = "transcription layer was not created"
CODE_SAMPLE = "review_sample_empty"
MESSAGE_SAMPLE = "review sample was not created"
LAYER_NAME_LIMIT = 512
_EXTERNAL_ID_LIMIT = 128


class _NoReviewSample(Exception):
    """Raised inside the import transaction when the sample has no lines."""


class _Refuse(Exception):
    """Raised inside the import transaction when the page cannot be written."""


@dataclass(frozen=True)
class HtrflowRunImportResult:
    """Outcome of one page-run import. ``message`` is a fixed sentence."""

    imported: bool
    code: str
    message: str
    run_id: int | None
    transcription_id: int | None
    lines: int


def import_htrflow_run(run_id):
    """Create one layer for the completed pages, or refuse with a fixed code."""
    if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id < 1:
        return _result(False, CODE_INCOMPLETE, MESSAGE_INCOMPLETE, None, None, 0)
    try:
        with transaction.atomic():
            return _import_locked(run_id)
    except IntegrityError:
        return _integrity_result(run_id)
    except ValidationError:
        return _result(False, CODE_FAILED, MESSAGE_FAILED, run_id, None, 0)
    except _NoReviewSample:
        return _result(False, CODE_SAMPLE, MESSAGE_SAMPLE, run_id, None, 0)
    except _Refuse:
        return _result(False, CODE_INCOMPLETE, MESSAGE_INCOMPLETE, run_id, None, 0)


def _import_locked(run_id):
    from ai.models import AILayerGate, ExternalHTRJob, ExternalHTRRun
    from core.models import Block, CascadeUpdate, Line, LineTranscription, Transcription

    try:
        run = ExternalHTRRun.objects.select_for_update().get(pk=run_id)
    except ExternalHTRRun.DoesNotExist:
        return _result(False, CODE_INCOMPLETE, MESSAGE_INCOMPLETE, None, None, 0)
    if run.engine != "htrflow":
        return _result(False, CODE_INCOMPLETE, MESSAGE_INCOMPLETE, run.pk, None, 0)
    if run.status not in {ExternalHTRRun.STATUS_COMPLETED, ExternalHTRRun.STATUS_PARTIAL}:
        return _result(False, CODE_INCOMPLETE, MESSAGE_INCOMPLETE, run.pk, None, 0)
    jobs = list(
        ExternalHTRJob.objects.select_for_update().filter(run_id=run.pk).order_by("pk")
    )
    completed = [job for job in jobs if job.status == ExternalHTRJob.STATUS_COMPLETED]
    if run.imported or run.imported_transcription_id or any(
        job.imported or job.imported_transcription_id for job in completed
    ):
        return _already(run, completed)
    chosen = []
    for job in completed:
        if job.document_id != run.document_id or job.part_id is None:
            raise _Refuse()
        page = _page_work(job)
        if page is None:
            raise _Refuse()
        if not page["writes"]:
            continue
        chosen.append((job, page))
    if not chosen:
        return _result(False, CODE_EMPTY, MESSAGE_EMPTY, run.pk, None, 0)
    stamps = {page["stamp"] for _job, page in chosen}
    if len(stamps) != 1 or not next(iter(stamps)):
        raise _Refuse()

    stamp = next(iter(stamps))
    layer = Transcription(
        document_id=run.document_id,
        name=_layer_name(run.pk, "htrflow", run.model_id),
    )
    layer.save()
    author = _author(run)
    written_pks = []
    with CascadeUpdate.bypass():
        for job, page in chosen:
            part_id = job.part_id
            if page["mode"] == "create":
                blocks = _create_blocks(Block, part_id, page["regions"])
                for item in page["writes"]:
                    line = Line(
                        document_part_id=part_id,
                        block=blocks.get(item["region_id"]),
                        external_id=item["external_id"],
                        mask=item["polygon"],
                        baseline=item["baseline"],
                        order=len(written_pks),
                    )
                    line.full_clean()
                    line.save()
                    row = LineTranscription.objects.create(
                        transcription=layer,
                        line=line,
                        content=item["text"],
                        version_source=stamp,
                        version_author=author,
                    )
                    written_pks.append(row.line_id)
            else:
                for item in page["writes"]:
                    row = LineTranscription.objects.create(
                        transcription=layer,
                        line_id=item["line_pk"],
                        content=item["text"],
                        version_source=stamp,
                        version_author=author,
                    )
                    written_pks.append(row.line_id)
    summary = build_gate_sample([], written_pks=written_pks, comparison=None)
    if not summary["sample_line_pks"]:
        raise _NoReviewSample()
    AILayerGate.objects.create(
        transcription=layer,
        state=AILayerGate.STATE_RAW,
        sample_line_pks=summary["sample_line_pks"],
        mean_cer=summary["mean_cer"],
    )
    for job, _page in chosen:
        job.imported = True
        job.imported_transcription = layer
        job.save(update_fields=["imported", "imported_transcription", "updated_at"])
    run.imported = True
    run.imported_transcription = layer
    run.save(update_fields=["imported", "imported_transcription", "updated_at"])
    return _result(True, CODE_IMPORTED, MESSAGE_IMPORTED, run.pk, layer.pk, len(written_pks))


def _page_work(job):
    """Decide whether this page creates lines or writes onto matches."""
    from core.models import Line

    layout = job.layout if isinstance(job.layout, dict) else None
    if layout is None:
        return None
    if layout.get("document_id") != job.document_id or layout.get("part_id") != job.part_id:
        return None
    bounds = _page_bounds(layout)
    if bounds is None:
        return {"writes": [], "stamp": _stamp(job.model_id), "mode": "create", "regions": []}
    width, height = bounds
    geometry = {}
    regions = []
    for region in layout.get("regions") or []:
        if not isinstance(region, dict):
            return None
        region_id = region.get("region_id")
        polygon = region.get("polygon")
        if isinstance(region_id, str) and region_id and _inside(polygon, width, height, 3):
            regions.append({"region_id": region_id[:_EXTERNAL_ID_LIMIT], "polygon": polygon})
    for line in layout.get("lines") or []:
        if not isinstance(line, dict):
            return None
        line_id = line.get("line_id")
        if not isinstance(line_id, str) or not line_id:
            continue
        polygon = line.get("polygon")
        baseline = line.get("baseline")
        if _inside(polygon, width, height, 3) and _inside(baseline, width, height, 2):
            geometry[line_id] = {
                "region_id": line.get("region_id") if isinstance(line.get("region_id"), str) else "",
                "polygon": polygon,
                "baseline": baseline,
            }
    included = []
    for row in job.lines.order_by("position", "pk"):
        if row.status != "included" or not isinstance(row.text, str):
            continue
        if len(row.text) > CONTENT_LIMIT:
            return None
        included.append(row)
    if not included:
        return {"writes": [], "stamp": _stamp(job.model_id), "mode": "create", "regions": []}
    existing = list(Line.objects.filter(document_part_id=job.part_id))
    by_key = {}
    for line in existing:
        by_key[str(line.pk)] = line
        if isinstance(line.external_id, str) and line.external_id:
            by_key.setdefault(line.external_id, line)
    matched = []
    unmatched = []
    for row in included:
        found = by_key.get(row.line_id) or by_key.get(row.line_id[:_EXTERNAL_ID_LIMIT])
        if found is None:
            unmatched.append(row)
        else:
            matched.append((found, row))
    stamp = _stamp(job.model_id)
    if matched:
        trusted = []
        for line, row in matched:
            trusted.append(row.line_id)
            trusted.append(str(line.pk))
            if isinstance(line.external_id, str) and line.external_id:
                trusted.append(line.external_id)
        payload = {
            "document_id": job.document_id,
            "part_id": job.part_id,
            "regions": [{
                "lines": [
                    {"line_id": row.line_id, "text": row.text, "reading_order": row.position}
                    for _line, row in matched
                ],
            }],
        }
        plan = plan_htrflow_import(payload, job.document_id, job.part_id, trusted)
        if not plan.allowed:
            return None
        allowed = {item.line_id: item.text for item in plan.lines}
        writes = []
        seen_pk = set()
        for line, row in matched:
            if line.pk in seen_pk or row.line_id not in allowed:
                continue
            seen_pk.add(line.pk)
            writes.append({"line_pk": line.pk, "text": allowed[row.line_id]})
        return {"writes": writes, "stamp": stamp, "mode": "match", "regions": []}
    writes = []
    seen = set()
    for row in unmatched:
        shape = geometry.get(row.line_id)
        if shape is None:
            continue
        external_id = row.line_id[:_EXTERNAL_ID_LIMIT]
        if external_id in seen or external_id in by_key:
            continue
        seen.add(external_id)
        writes.append({
            "external_id": external_id,
            "text": row.text,
            "region_id": shape["region_id"][:_EXTERNAL_ID_LIMIT],
            "polygon": shape["polygon"],
            "baseline": shape["baseline"],
        })
    return {"writes": writes, "stamp": stamp, "mode": "create", "regions": regions}


def _create_blocks(block_model, part_id, regions):
    created = {}
    seen = set()
    for region in regions:
        external_id = region["region_id"]
        if external_id in seen:
            continue
        seen.add(external_id)
        block = block_model(
            document_part_id=part_id,
            external_id=external_id,
            box=region["polygon"],
            order=len(created),
        )
        block.full_clean()
        block.save()
        created[external_id] = block
    return created


def _page_bounds(layout):
    """ALTO page width and height. Missing or non-positive sizes are refused."""
    page = layout.get("page") if isinstance(layout, dict) else None
    if not isinstance(page, dict):
        return None
    width = page.get("width")
    height = page.get("height")
    if (
        isinstance(width, bool) or isinstance(height, bool)
        or not isinstance(width, int) or not isinstance(height, int)
        or width < 1 or height < 1 or width > 20000 or height > 20000
    ):
        return None
    return width, height


def _inside(points, width, height, minimum):
    """True when the polygon lies inside the ALTO page, edges included."""
    if not _polygon(points, minimum):
        return False
    return all(0 <= point[0] <= width and 0 <= point[1] <= height for point in points)


def _polygon(value, minimum):
    if not isinstance(value, list) or len(value) < minimum:
        return False
    for point in value:
        if (
            not isinstance(point, list)
            or len(point) != 2
            or isinstance(point[0], bool)
            or isinstance(point[1], bool)
            or not isinstance(point[0], int)
            or not isinstance(point[1], int)
        ):
            return False
    return True


def _stamp(model_id):
    source = "external-htr:htrflow:%s" % (model_id if isinstance(model_id, str) else "")
    return source[:VERSION_SOURCE_LIMIT]


def _already(run, completed):
    transcription_id = run.imported_transcription_id
    if transcription_id is None:
        for job in completed:
            if job.imported_transcription_id:
                transcription_id = job.imported_transcription_id
                break
    return _result(False, CODE_ALREADY, MESSAGE_ALREADY, run.pk, transcription_id, 0)


def _integrity_result(run_id):
    from ai.models import ExternalHTRRun
    from core.models import Transcription

    run = ExternalHTRRun.objects.filter(pk=run_id).first()
    if run is None:
        return _result(False, CODE_INCOMPLETE, MESSAGE_INCOMPLETE, None, None, 0)
    if run.imported or run.imported_transcription_id:
        return _result(
            False, CODE_ALREADY, MESSAGE_ALREADY, run.pk, run.imported_transcription_id, 0,
        )
    name = _layer_name(run.pk, run.engine, run.model_id)
    if Transcription.objects.filter(document_id=run.document_id, name=name).exists():
        return _result(False, CODE_COLLISION, MESSAGE_COLLISION, run.pk, None, 0)
    return _result(False, CODE_FAILED, MESSAGE_FAILED, run.pk, None, 0)


def _layer_name(run_id, engine, model_id) -> str:
    suffix = ":run-%s" % run_id
    prefix = "external-htr:%s:%s" % (engine, model_id)
    if len(suffix) >= LAYER_NAME_LIMIT:
        return suffix[-LAYER_NAME_LIMIT:]
    room = LAYER_NAME_LIMIT - len(suffix)
    if len(prefix) > room:
        prefix = prefix[:room]
    return prefix + suffix


def _author(run):
    from django.contrib.auth import get_user_model
    from core.models import LineTranscription

    user_id = getattr(run, "created_by_id", None)
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id < 1:
        return ""
    username = get_user_model().objects.filter(pk=user_id).values_list(
        "username", flat=True,
    ).first()
    limit = LineTranscription._meta.get_field("version_author").max_length
    if not isinstance(username, str) or username == "" or len(username) > limit:
        return ""
    return username


def _result(imported, code, message, run_id, transcription_id, lines):
    return HtrflowRunImportResult(
        imported=imported,
        code=code,
        message=message,
        run_id=run_id,
        transcription_id=transcription_id,
        lines=lines,
    )
