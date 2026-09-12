"""Declarative diplomatic conventions → prompt lines (ARCHITECTURE.md §9.5).

Few-shot image priming (§10.1) is not in this release. These toggles are the
part of Phase 2 that can ship without an example store.
"""
from __future__ import annotations

DEFAULT_CONVENTIONS = {
    "expand_abbreviations": False,
    "modernise_spelling": False,
    "retain_original_punctuation": True,
    "keep_long_s": True,
    "keep_uv_as_written": True,
    "keep_line_break_hyphens": True,
}

_LINES = {
    "expand_abbreviations": (
        "Expand abbreviations to full words.",
        "Keep abbreviations as written; do NOT expand them.",
    ),
    "modernise_spelling": (
        "Modernise spelling to present-day forms.",
        "Keep original spelling; do NOT modernise.",
    ),
    "retain_original_punctuation": (
        "Keep original punctuation; do NOT add or remove marks.",
        "Normalise punctuation if needed.",
    ),
    "keep_long_s": (
        "Keep long s (ſ) as written; do NOT convert it to s.",
        "You may transcribe long s as s.",
    ),
    "keep_uv_as_written": (
        "Keep u/v (and i/j) as written; do NOT regularise them.",
        "You may regularise u/v and i/j.",
    ),
    "keep_line_break_hyphens": (
        "Keep hyphens that mark a word broken across a line.",
        "You may join hyphenated line-breaks into one word.",
    ),
}


def merge_conventions(raw=None):
    out = dict(DEFAULT_CONVENTIONS)
    if isinstance(raw, dict):
        for key in DEFAULT_CONVENTIONS:
            if key in raw:
                out[key] = bool(raw[key])
    return out


def conventions_prompt(raw=None):
    conv = merge_conventions(raw)
    lines = []
    for key, (when_true, when_false) in _LINES.items():
        lines.append(when_true if conv[key] else when_false)
    return "Conventions: " + " ".join(lines)
