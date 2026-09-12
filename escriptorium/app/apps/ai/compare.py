"""Cheap kraken comparison pass when no sibling layer exists (ARCHITECTURE.md §9).

Failure is non-fatal: the AI job still completes and the gate falls back to a
random sample.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

COMPARE_LAYER_NAME = "kraken-compare"


def find_cheap_recognizer(ocr_model_qs, job_recognize=2):
    """Prefer a public recognizer; else any recognizer with a file."""
    qs = ocr_model_qs.filter(job=job_recognize)
    public = qs.filter(public=True).first()
    if public is not None:
        return public
    return qs.first()


def ensure_comparison_layer(document, transcription_model, name=COMPARE_LAYER_NAME):
    layer, _created = transcription_model.objects.get_or_create(
        document=document,
        name=name,
        defaults={"comments": "Cheap kraken pass for AI disagreement triage."},
    )
    return layer


def disagreement_rows_from_layers(line_pks, ai_text_by_line, kraken_text_by_line,
                                  cer_fn):
    rows = []
    for pk in line_pks:
        ai = (ai_text_by_line.get(pk) or "").strip()
        kr = (kraken_text_by_line.get(pk) or "").strip()
        if not ai:
            continue
        rows.append({
            "line_pk": pk,
            "ai_text": ai,
            "comparison_text": kr,
            "cer": cer_fn(ai, kr),
        })
    return rows
