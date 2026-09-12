"""
Overlay -> VLM -> write-a-layer pipeline (ARCHITECTURE.md §4, §5.A).

Writes via the ORM so it can set the real `version_source` — the capability
the stock eScriptorium REST API lacks (`version_source` is editable=False, so
the API stamps 'eScriptorium'; verified 2026-09-05).

Mirrors the contract of core.models.DocumentPart.transcribe() (models.py:1592):
per-line get_or_create(LineTranscription) + new_version + version_source, but
sourced from a VLM over colour-keyed region crops instead of a kraken recognizer.
"""
from __future__ import annotations

import logging

from PIL import Image

from .conventions import conventions_prompt
from .fewshot import fewshot_prompt_block
from .gate import comparison_text_for_line
from .overlay import render_crop, key_for_index
from .preflight import evaluate_crop
from .triage import normalised_cer

logger = logging.getLogger(__name__)

DEFAULT_PROMPT = (
    "This is a crop of a manuscript page. Each text line is highlighted with a "
    "distinct translucent colour and labelled by colour name in the left margin. "
    "Transcribe each coloured line diplomatically: keep original spelling, do NOT "
    "expand abbreviations, do NOT modernise, do NOT add or remove punctuation. "
    "Transcribe ONLY ink that sits inside a coloured band. Do NOT invent dates, "
    "filenames, UI labels, or any metadata that is not written on the page. "
    "Return ONLY a JSON object mapping each colour name to that line's text."
)


def build_prompt(config, keys, examples=None) -> str:
    base = (config.prompt_template or DEFAULT_PROMPT).strip()
    conv = conventions_prompt(getattr(config, 'conventions', None))
    shots = fewshot_prompt_block(examples or [])
    parts = [base, conv]
    if shots:
        parts.append(shots)
    parts.append(f'The colour keys on this crop are: {", ".join(keys)}.')
    return "\n".join(parts)


def _line_masks(lines):
    """Prefer mask polygon; fall back to a thin band around the baseline."""
    out = []
    for ln in lines:
        if ln.mask:
            out.append([tuple(p) for p in ln.mask])
        elif ln.baseline:
            xs = [p[0] for p in ln.baseline]; ys = [p[1] for p in ln.baseline]
            top = min(ys) - 20
            out.append([(min(xs), top), (max(xs), top),
                        (max(xs), max(ys)), (min(xs), max(ys))])
        else:
            out.append(None)
    return out


def stamp_line_transcription(line, transcription, text, version_source, author,
                             *, lt_model=None, no_change=None):
    """Write one LineTranscription via the ORM so version_source sticks.

    lt_model/no_change are injectable for tests (production uses core + Versioned).
    """
    if lt_model is None:
        from core.models import LineTranscription as lt_model
        from versioning.models import NoChangeException as no_change
    lt, created = lt_model.objects.get_or_create(
        line=line, transcription=transcription)
    if not created:
        try:
            lt.new_version(author=author, source=version_source)
        except no_change:
            pass
    lt.content = text
    lt.version_author = author
    lt.version_source = version_source
    lt.save()
    return lt


def transcribe_part(part, config, transcription, backend, *, user=None,
                    per_crop=6, job=None, comparison=None, examples=None):
    """Transcribe one DocumentPart into `transcription` via colour-keyed crops.
    Returns dict including optional disagreement rows vs `comparison`."""
    lines = list(part.lines.all().order_by('order'))
    masks = _line_masks(lines)
    version_source = config.version_source
    author = (user.username if user else '')[:128]

    written = flagged = tok_in = tok_out = 0
    cost = 0.0
    disagreements = []
    written_line_pks = []

    with Image.open(part.image.path) as im:
        for start in range(0, len(lines), per_crop):
            group = lines[start:start + per_crop]
            group_masks = masks[start:start + per_crop]
            if any(m is None for m in group_masks):
                flagged += sum(1 for m in group_masks if m is None)
                group = [g for g, m in zip(group, group_masks) if m]
                group_masks = [m for m in group_masks if m]
                if not group:
                    continue

            # PRE-FLIGHT (§5.A): overlap → skip whole crop; fragments → drop
            # that line and still send the rest.
            decision = evaluate_crop(group_masks)
            for i, why in decision.drop:
                logger.warning("ai: dropping line %s (%s) on part %s",
                               group[i].pk, why, part.pk)
                flagged += 1
            if not decision.send:
                logger.warning("ai: skipping crop (%s) on part %s",
                               decision.reason, part.pk)
                continue
            group = [group[i] for i in decision.keep]
            group_masks = [group_masks[i] for i in decision.keep]

            crop_img, key_to_idx = render_crop(im, group_masks)
            keys = list(key_to_idx)
            result = backend.transcribe_region(
                crop_img, keys, build_prompt(config, keys, examples=examples))
            tok_in += result.tokens_in
            tok_out += result.tokens_out
            cost += backend.cost(result.tokens_in, result.tokens_out)
            if result.unknown_keys:
                logger.warning("ai: unexpected keys %s on part %s (parser, not "
                               "dropped lines)", result.unknown_keys, part.pk)

            for key, local_idx in key_to_idx.items():
                text = result.text_by_key.get(key, "").strip()
                line = group[local_idx]
                if not text:
                    flagged += 1
                    continue
                stamp_line_transcription(
                    line, transcription, text, version_source, author)
                written += 1
                written_line_pks.append(line.pk)
                if comparison is not None:
                    other = comparison_text_for_line(line, comparison)
                    disagreements.append({
                        'line_pk': line.pk,
                        'cer': normalised_cer(text, other),
                        'ai_text': text,
                        'comparison_text': other,
                    })

    if job:
        job.lines_written += written
        job.lines_flagged += flagged
        job.tokens_in += tok_in
        job.tokens_out += tok_out
        job.actual_cost += cost
        job.save()

    return dict(lines_written=written, lines_flagged=flagged,
                tokens_in=tok_in, tokens_out=tok_out, cost=cost,
                disagreements=disagreements,
                written_line_pks=written_line_pks)
