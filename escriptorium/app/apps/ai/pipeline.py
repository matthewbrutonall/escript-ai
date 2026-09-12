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

from .assignment import keys_to_stamp
from .conventions import conventions_prompt
from .fewshot import fewshot_prompt_block
from .fixthis import LINE_KEY, extract_line_text, fix_prompt
from .gate import comparison_text_for_line
from .overlay import crop_line, render_crop, render_numbered_lines
from .seg_review import PROMPT as SEG_PROMPT
from .seg_review import REVIEW_KEY, parse_review_json, suggestions_from_review
from .passim_fallback import align_witness_to_lines
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


def _record_write(line, transcription, text, version_source, author, comparison,
                  disagreements, written_line_pks):
    stamp_line_transcription(line, transcription, text, version_source, author)
    written_line_pks.append(line.pk)
    if comparison is not None:
        other = comparison_text_for_line(line, comparison)
        disagreements.append({
            'line_pk': line.pk,
            'cer': normalised_cer(text, other),
            'ai_text': text,
            'comparison_text': other,
        })


def transcribe_one_line(backend, image, mask, prompt):
    crop = crop_line(image, mask)
    return backend.transcribe_region(crop, [LINE_KEY], prompt)


def _per_line_group(im, lines, masks, backend, config, transcription,
                    version_source, author, comparison, disagreements,
                    written_line_pks, written, flagged, tok_in, tok_out, cost,
                    witness_raw=""):
    """Tight single-line crops when colour-key is unsafe or incomplete."""
    line_prompt = fix_prompt(
        "", conventions_prompt(getattr(config, "conventions", None)))
    still_empty = []
    for line, mask in zip(lines, masks):
        try:
            line_result = transcribe_one_line(backend, im, mask, line_prompt)
        except Exception:
            logger.exception("ai: per-line fallback failed on line %s", line.pk)
            still_empty.append(line)
            continue
        tok_in += line_result.tokens_in
        tok_out += line_result.tokens_out
        cost += backend.cost(line_result.tokens_in, line_result.tokens_out)
        text = extract_line_text(line_result)
        if text:
            _record_write(
                line, transcription, text, version_source, author,
                comparison, disagreements, written_line_pks)
            written += 1
        else:
            still_empty.append(line)
    if still_empty and comparison is not None:
        ocr_pairs = [
            (ln.pk, comparison_text_for_line(ln, comparison) or "")
            for ln in still_empty
        ]
        aligned = align_witness_to_lines(witness_raw, ocr_pairs)
        leftover = []
        for ln in still_empty:
            text = (aligned.get(ln.pk) or "").strip()
            if text:
                _record_write(
                    ln, transcription, text, version_source, author,
                    comparison, disagreements, written_line_pks)
                written += 1
            else:
                leftover.append(ln)
        still_empty = leftover
    flagged += len(still_empty)
    return written, flagged, tok_in, tok_out, cost


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

            # PRE-FLIGHT (§5.A): fragments drop; overlap blocks colour-key
            # and falls back to per-line crops.
            decision = evaluate_crop(group_masks)
            for i, why in decision.drop:
                if why == "overlap":
                    continue
                logger.warning("ai: dropping line %s (%s) on part %s",
                               group[i].pk, why, part.pk)
                flagged += 1
            if not decision.send:
                overlap_idxs = [i for i, why in decision.drop if why == "overlap"]
                logger.warning("ai: colour-key skipped (%s) on part %s; "
                               "per-line fallback for %s lines",
                               decision.reason, part.pk, len(overlap_idxs))
                if not overlap_idxs:
                    continue
                written, flagged, tok_in, tok_out, cost = _per_line_group(
                    im, [group[i] for i in overlap_idxs],
                    [group_masks[i] for i in overlap_idxs],
                    backend, config, transcription, version_source, author,
                    comparison, disagreements, written_line_pks,
                    written, flagged, tok_in, tok_out, cost)
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

            stamped = keys_to_stamp(keys, result.text_by_key, result.unknown_keys)
            fallback_idxs = []
            for key, local_idx in key_to_idx.items():
                text = stamped.get(key, "")
                line = group[local_idx]
                if text:
                    _record_write(
                        line, transcription, text, version_source, author,
                        comparison, disagreements, written_line_pks)
                    written += 1
                else:
                    fallback_idxs.append(local_idx)

            if fallback_idxs:
                written, flagged, tok_in, tok_out, cost = _per_line_group(
                    im, [group[i] for i in fallback_idxs],
                    [group_masks[i] for i in fallback_idxs],
                    backend, config, transcription, version_source, author,
                    comparison, disagreements, written_line_pks,
                    written, flagged, tok_in, tok_out, cost,
                    witness_raw=result.raw)

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


def review_part_segmentation(part, backend):
    """One VLM call: numbered boxes → structured suggestions. No mask writes."""
    lines = list(part.lines.all().order_by('order'))
    masks = _line_masks(lines)
    usable = [(ln, m) for ln, m in zip(lines, masks) if m]
    if not usable:
        return dict(suggestions=[], tokens_in=0, tokens_out=0, cost=0.0, raw="")
    lines, masks = zip(*usable)
    lines, masks = list(lines), list(masks)
    with Image.open(part.image.path) as im:
        overlay = render_numbered_lines(im, masks)
        result = backend.transcribe_region(overlay, [REVIEW_KEY], SEG_PROMPT)
    raw = result.raw or result.text_by_key.get(REVIEW_KEY) or ""
    parsed = parse_review_json(raw, len(lines))
    suggestions = suggestions_from_review(lines, parsed)
    return dict(
        suggestions=suggestions,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        cost=backend.cost(result.tokens_in, result.tokens_out),
        raw=raw,
        parsed=parsed,
    )
