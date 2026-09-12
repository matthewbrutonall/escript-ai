"""Apply accepted segmentation suggestions. Never invents masks.

Spurious → delete the Line (user accepted).
Typology → set Line.typology (document LineType).
Missed / order → acknowledge only (human still draws/reorders).
"""
from __future__ import annotations

LINE_TYPE_NAMES = {
    "heading": "heading",
    "body": "body",
    "margin": "margin",
    "page_number": "page_number",
}


def planned_action(kind, payload, has_line):
    if kind == "spurious":
        return "delete_line" if has_line else "acknowledge"
    if kind == "typology":
        name = LINE_TYPE_NAMES.get((payload or {}).get("type"))
        if name and has_line:
            return ("set_type", name)
        return "acknowledge"
    return "acknowledge"


def apply_suggestion(sugg, *, accept=True):
    """Mutates the suggestion and maybe the Line. Returns an action label."""
    from core.models import LineType, get_or_create_doc_type

    if not accept:
        sugg.status = sugg.STATUS_DISMISSED
        sugg.save(update_fields=["status", "updated_at"])
        return "dismissed"

    action = planned_action(sugg.kind, sugg.payload, sugg.line_id is not None)
    if action == "delete_line":
        line = sugg.line
        sugg.status = sugg.STATUS_ACCEPTED
        sugg.line = None
        sugg.save(update_fields=["status", "line", "updated_at"])
        if line is not None:
            line.delete()
        return "deleted"
    if isinstance(action, tuple) and action[0] == "set_type":
        typo, _created = get_or_create_doc_type(
            sugg.document, LineType, action[1])
        line = sugg.line
        if line is not None:
            line.typology = typo
            line.save(update_fields=["typology"])
        sugg.status = sugg.STATUS_ACCEPTED
        sugg.save(update_fields=["status", "updated_at"])
        return "typed"
    sugg.status = sugg.STATUS_ACCEPTED
    sugg.save(update_fields=["status", "updated_at"])
    return "acknowledged"


def apply_pending(document, status):
    from .models import AISegSuggestion

    qs = (
        AISegSuggestion.objects
        .filter(document=document, status=AISegSuggestion.STATUS_PENDING)
        .select_related("line", "part", "document")
    )
    if status == AISegSuggestion.STATUS_DISMISSED:
        n = qs.update(status=AISegSuggestion.STATUS_DISMISSED)
        return {"updated": n, "deleted": 0, "typed": 0, "acknowledged": 0}

    items = list(qs)
    # Types before deletes so a line that is both typed and spurious is deleted last.
    rank = {"typology": 0, "order": 1, "missed": 1, "spurious": 2}
    items.sort(key=lambda s: rank.get(s.kind, 1))
    counts = {"updated": 0, "deleted": 0, "typed": 0, "acknowledged": 0}
    for sugg in items:
        label = apply_suggestion(sugg, accept=True)
        counts["updated"] += 1
        if label == "deleted":
            counts["deleted"] += 1
        elif label == "typed":
            counts["typed"] += 1
        else:
            counts["acknowledged"] += 1
    return counts
