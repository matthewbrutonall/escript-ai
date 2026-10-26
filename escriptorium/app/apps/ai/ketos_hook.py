"""Held-out pages + optional `ketos test` after training (ARCHITECTURE.md §9.3).

Training still succeeds if ketos is missing. We never evaluate on unreviewed AI.
"""
from __future__ import annotations

import logging
import math
import random
import re
import shutil
import subprocess

logger = logging.getLogger(__name__)

HELD_OUT_FRACTION = 0.15
EVALUATION_NORMALIZATIONS = ('NFD', 'NFKD', 'NFC', 'NFKC')


def reserve_held_out_parts(part_ids, fraction=HELD_OUT_FRACTION, rng=None):
    """Hold out 10–20% of reviewed pages. Need ≥5 distinct parts."""
    ids = list(dict.fromkeys(part_ids))
    if len(ids) < 5:
        return []
    n = max(1, int(round(len(ids) * fraction)))
    n = min(n, len(ids) - 1)
    rng = rng or random.Random(0)
    return sorted(rng.sample(ids, n))


def parse_ketos_cer(output):
    """Read Kraken's exact counts, or a legacy CER/accuracy summary.

    Kraken 7.1 prints accuracy rounded to two decimals. Its adjacent character
    and error counts retain the precision needed for a held-out CER.
    """
    if not output:
        return None
    counts = re.search(
        r"^\s*(\d+)[ \t]+Characters[ \t]*\r?\n"
        r"[ \t]*(\d+)[ \t]+Errors[ \t]*$", output, re.I | re.M)
    if counts:
        characters, errors = map(int, counts.groups())
        if not characters:
            return None
        return errors / characters

    number = r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
    cer = re.search(
        rf"^[ \t]*(?:CER|character error rate)[:\s=]+{number}"
        r"[ \t]*(%?)[ \t]*$", output, re.I | re.M)
    if cer:
        value = float(cer.group(1))
        if cer.group(2) or value > 1:
            value /= 100
    else:
        accuracy = re.search(
            rf"^[ \t]*{number}%[ \t]+Character Accuracy[ \t]*$",
            output, re.I | re.M)
        if not accuracy:
            return None
        value = 1 - float(accuracy.group(1)) / 100
    return value if math.isfinite(value) and value >= 0 else None


def kept_training_parts(part_pks, held_out):
    """Pages that remain after hold-out. Empty → abort, do not train on held-out GT."""
    held = set(held_out or [])
    kept = [p for p in (part_pks or []) if p not in held]
    if not kept:
        raise ValueError(
            "No pages remain to train on after holding out the reviewed sample.")
    return kept


def eligible_collection_pairs(items, held_out):
    """(transcription_layer_id, document_part_id) pairs that may be trained on.

    An empty list means abort: do not filter LineTranscription with Q().
    """
    held = set(held_out or [])
    out = []
    for item in items:
        tid = item.get("transcription_layer_id")
        pid = item.get("document_part_id")
        if not tid or pid in held:
            continue
        out.append((tid, pid))
    return out


def evaluation_options(model):
    """Options recorded with a held-out result for this model."""
    normalization = getattr(model, 'evaluation_normalization', '') or None
    if normalization not in (None, *EVALUATION_NORMALIZATIONS):
        raise ValueError(f'Unsupported evaluation normalization: {normalization}')
    return {
        'normalization': normalization,
        'normalize_whitespace': bool(getattr(model, 'evaluation_normalize_whitespace', True)),
    }


def ketos_argv(model_path, eval_paths, format_type="binary",
               normalization=None, normalize_whitespace=True):
    """Command list, or None if ketos cannot be run."""
    if not model_path or not eval_paths:
        return None
    if normalization not in (None, *EVALUATION_NORMALIZATIONS):
        raise ValueError(f'Unsupported evaluation normalization: {normalization}')
    argv = ["ketos", "test", "-m", str(model_path), "-f", format_type]
    if normalization:
        argv.extend(["-u", normalization])
    argv.append("-n" if normalize_whitespace else "--no-normalize-whitespace")
    return [*argv, *[str(p) for p in eval_paths]]


def run_ketos_test(model_path, eval_paths, ketos_bin=None, format_type="binary",
                   normalization=None, normalize_whitespace=True):
    """Return CER float or None. Never raises for a missing binary."""
    argv = ketos_argv(
        model_path, eval_paths, format_type=format_type,
        normalization=normalization, normalize_whitespace=normalize_whitespace)
    if argv is None:
        return None
    binary = ketos_bin or shutil.which("ketos")
    if not binary:
        return None
    argv[0] = binary
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=3600, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning("ai: ketos test skipped (%s)", e)
        return None
    if proc.returncode != 0:
        logger.warning("ai: ketos test failed (exit %s)", proc.returncode)
        return None
    return parse_ketos_cer((proc.stdout or "") + "\n" + (proc.stderr or ""))
