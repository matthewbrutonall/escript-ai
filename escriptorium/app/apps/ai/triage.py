"""Dual-engine disagreement + sample pick (ARCHITECTURE.md §9).

CER = Levenshtein / max(len(reference), 1). Sort review by disagreement with
an existing kraken (or other) layer on the same lines. Does not run ketos.
"""
from __future__ import annotations

import random


def levenshtein(a, b):
    a = a or ""
    b = b or ""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            ins = cur[j - 1] + 1
            delete = prev[j] + 1
            sub = prev[j - 1] + (ca != cb)
            cur.append(min(ins, delete, sub))
        prev = cur
    return prev[-1]


def normalised_cer(hypothesis, reference):
    """Edit distance against the comparison (kraken) string."""
    ref = reference or ""
    hyp = hypothesis or ""
    denom = max(len(ref), 1)
    return levenshtein(hyp, ref) / denom


def sample_size(n_lines, min_n=50, pct=0.02):
    if n_lines <= 0:
        return 0
    return min(n_lines, max(min_n, int(round(n_lines * pct))))


def pick_sample(rows, min_n=50, pct=0.02, rng=None):
    """rows: iterable of dicts with line_pk and cer.

    Half the sample is the disagreement tail (highest CER); the rest is a
    random draw from what remains. Small documents review every line.
    """
    rng = rng or random.Random(0)
    ranked = sorted(rows, key=lambda r: (-float(r.get("cer") or 0), r["line_pk"]))
    n = sample_size(len(ranked), min_n=min_n, pct=pct)
    if n <= 0:
        return []
    tail_n = (n + 1) // 2
    tail = ranked[:tail_n]
    rest = ranked[tail_n:]
    need = n - len(tail)
    if need > 0 and rest:
        extra = rng.sample(rest, k=min(need, len(rest)))
    else:
        extra = []
    picked = tail + extra
    return [r["line_pk"] for r in picked]


def pick_random_sample(line_pks, min_n=50, pct=0.02, rng=None):
    """Review sample when there is no comparison layer: random, max(50, 2%)."""
    rng = rng or random.Random(0)
    pks = list(dict.fromkeys(line_pks))
    n = sample_size(len(pks), min_n=min_n, pct=pct)
    if n <= 0:
        return []
    if n >= len(pks):
        return pks
    return rng.sample(pks, n)
