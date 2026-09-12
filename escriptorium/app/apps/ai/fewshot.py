"""Same-document few-shot text for the diplomatic prompt (ARCHITECTURE.md §10.1).

Image few-shot and cross-document retrieval are not in this slice. Cap is
hard: 4–8 examples, pinned first, then recency.
"""
from __future__ import annotations

FEWSHOT_CAP = 8
FEWSHOT_MIN = 0


def select_examples(examples, cap=FEWSHOT_CAP):
    """examples: iterable of dict(text, pinned, created_at, line_pk, document_pk).

    Never mix documents: caller must already have filtered to one document.
    """
    items = [e for e in examples if (e.get("text") or "").strip()]
    pinned = [e for e in items if e.get("pinned")]
    rest = [e for e in items if not e.get("pinned")]
    rest.sort(key=lambda e: e.get("created_at") or 0, reverse=True)
    picked = (pinned + rest)[:cap]
    return picked


def fewshot_prompt_block(examples):
    picked = select_examples(examples)
    if not picked:
        return ""
    lines = ["Corrected examples from this document (match this style):"]
    for i, ex in enumerate(picked, 1):
        lines.append(f"{i}. {ex['text'].strip()}")
    return "\n".join(lines)


def load_examples_for_document(document, example_model=None, cap=FEWSHOT_CAP):
    """ORM loader. example_model injectable for tests."""
    if example_model is None:
        from .models import AIExample
        example_model = AIExample
    qs = example_model.objects.filter(document=document).order_by(
        "-pinned", "-created_at")[:cap * 2]
    return [
        {
            "text": row.text,
            "pinned": row.pinned,
            "created_at": row.created_at,
            "line_pk": row.line_id,
            "document_pk": row.document_id,
        }
        for row in qs
    ]
