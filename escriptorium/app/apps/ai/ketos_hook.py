"""Held-out pages + optional `ketos test` after training (ARCHITECTURE.md §9.3).

Training still succeeds if ketos is missing. We never evaluate on unreviewed AI.
"""
from __future__ import annotations

import logging
import random
import re
import shutil
import subprocess

logger = logging.getLogger(__name__)

HELD_OUT_FRACTION = 0.15


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
    if not output:
        return None
    m = re.search(r"\bCER\b[:\s=]+([0-9.]+)", output, re.I)
    if not m:
        m = re.search(r"character error rate[:\s=]+([0-9.]+)", output, re.I)
    if not m:
        return None
    val = float(m.group(1))
    if val > 1:
        val = val / 100.0
    return val


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


def ketos_argv(model_path, eval_paths, format_type="binary"):
    """Command list, or None if ketos cannot be run."""
    if not model_path or not eval_paths:
        return None
    return [
        "ketos", "test", "-m", str(model_path), "-f", format_type,
        *[str(p) for p in eval_paths],
    ]


def run_ketos_test(model_path, eval_paths, ketos_bin=None, format_type="binary"):
    """Return CER float or None. Never raises for a missing binary."""
    argv = ketos_argv(model_path, eval_paths, format_type=format_type)
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
    return parse_ketos_cer((proc.stdout or "") + "\n" + (proc.stderr or ""))
