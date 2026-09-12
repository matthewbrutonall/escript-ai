"""Layer state machine helpers (ARCHITECTURE.md §9).

No Django required for the pure helpers; ORM wrappers take model classes so
unit tests can inject fakes.
"""
from __future__ import annotations

from .triage import pick_random_sample, pick_sample


class LayerNotEligible(ValueError):
    """Raised when a caller tries to train on a raw/sampled AI layer."""


def find_comparison_transcription(document, ai_transcription):
    """Prefer a kraken-stamped sibling layer; else any other layer with rows."""
    qs = document.transcriptions.exclude(pk=ai_transcription.pk)
    kraken = None
    fallback = None
    for tr in qs:
        lts = tr.linetranscription_set.all()[:1]
        if not lts:
            continue
        src = (getattr(lts[0], 'version_source', '') or '')
        if src.startswith('kraken:'):
            kraken = tr
            break
        if fallback is None:
            fallback = tr
    return kraken or fallback


def comparison_text_for_line(line, comparison):
    if comparison is None:
        return ""
    lt = line.linetranscription_set.filter(transcription=comparison).first()
    if lt is None:
        return ""
    return lt.content or ""


def assert_training_eligible(transcription, gate=None):
    """Block training on gated AI layers that are not training-eligible.

    Layers with no AILayerGate (manual / kraken) are allowed.
    """
    if gate is None:
        gate = getattr(transcription, 'ai_gate', None)
    if gate is None:
        return
    if gate.state != 'training-eligible':
        raise LayerNotEligible(
            f"AI layer is {gate.state}, not training-eligible. "
            "Review the sample first.")


def finalise_gate(gate, rows, *, min_n=50, pct=0.02, rng=None):
    """rows: list of dict(line_pk, cer, ai_text, comparison_text)."""
    pks = pick_sample(rows, min_n=min_n, pct=pct, rng=rng)
    sample = set(pks)
    mean = None
    if rows:
        mean = sum(float(r['cer']) for r in rows) / len(rows)
    return {
        'sample_line_pks': pks,
        'mean_cer': mean,
        'in_sample': sample,
        'write_disagreements': True,
    }


def build_gate_sample(rows, written_pks=None, comparison=None,
                      min_n=50, pct=0.02, rng=None):
    """Disagreement sample if a comparison layer produced rows; else random AI lines."""
    if comparison and rows:
        return finalise_gate(None, rows, min_n=min_n, pct=pct, rng=rng)
    sample = pick_random_sample(
        written_pks or [], min_n=min_n, pct=pct, rng=rng)
    return {
        'sample_line_pks': sample,
        'mean_cer': None,
        'in_sample': set(sample),
        'write_disagreements': False,
    }


ACK_PHRASE = "I reviewed the sample"


def acknowledge_sample(gate, user=None, phrase="", now=None):
    if (phrase or "").strip() != ACK_PHRASE:
        raise ValueError(
            f'Type exactly: {ACK_PHRASE}')
    if not gate.sample_line_pks:
        raise ValueError("No sample to acknowledge.")
    gate.state = 'sampled'
    gate.acknowledged_by = user
    if now is not None:
        gate.acknowledged_at = now
    return gate


def assemble_sample_lines(sample_pks, disagreements_by_line=None, ai_text_by_line=None):
    """Build the review table. Disagreement rows win; else AI text only."""
    disagreements_by_line = disagreements_by_line or {}
    ai_text_by_line = ai_text_by_line or {}
    out = []
    for pk in sample_pks or []:
        d = disagreements_by_line.get(pk)
        if d:
            out.append({
                'line_pk': pk,
                'ai_text': d.get('ai_text') or '',
                'comparison_text': d.get('comparison_text') or '',
                'cer': d.get('cer'),
            })
        else:
            out.append({
                'line_pk': pk,
                'ai_text': ai_text_by_line.get(pk) or '',
                'comparison_text': '',
                'cer': None,
            })
    return out


def mark_training_eligible(gate):
    if gate.state != 'sampled':
        raise LayerNotEligible(
            "Acknowledge the sample before marking "
            "the layer training-eligible.")
    gate.state = 'training-eligible'
    return gate
