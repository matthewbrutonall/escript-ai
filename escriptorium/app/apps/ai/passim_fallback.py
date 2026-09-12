"""Witness → OCR-line alignment without silently merging kraken text.

ARCHITECTURE.md §5.C: treat VLM page text as a temporary witness, snap it onto
existing OCR lines, leave unmatched lines empty. Never copy the kraken string
into a gap (`merge=False`).
"""
from __future__ import annotations

from difflib import SequenceMatcher

DEFAULT_THRESHOLD = 0.35


def tokenize(text: str):
    return (text or "").split()


def align_witness_to_lines(witness, ocr_lines, threshold=DEFAULT_THRESHOLD):
    """Map witness words onto ordered OCR lines.

    `ocr_lines` is ``[(line_id, ocr_text), ...]`` in reading order.
    Returns ``{line_id: aligned_text}`` for matches at or above `threshold`.
    Unmatched ids are omitted (caller flags them empty). The OCR string is
    only a locator — it is never returned as the transcription.
    """
    words = tokenize(witness)
    if not words:
        return {}
    cursor = 0
    out = {}
    for line_id, ocr in ocr_lines:
        ocr_words = tokenize(ocr)
        if not ocr_words:
            continue
        n = max(1, len(ocr_words))
        best_score = 0.0
        best_text = None
        best_end = None
        window_end = min(len(words), cursor + n * 3 + 1)
        for start in range(cursor, window_end):
            for length in range(max(1, n // 2), n * 2 + 1):
                end = start + length
                if end > len(words):
                    break
                chunk = words[start:end]
                score = SequenceMatcher(None, ocr_words, chunk).ratio()
                if score > best_score:
                    best_score = score
                    best_text = " ".join(chunk)
                    best_end = end
        if best_text is not None and best_score >= threshold and best_end is not None:
            out[line_id] = best_text
            cursor = best_end
    return out
