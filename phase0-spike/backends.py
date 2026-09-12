"""
Phase-0 spike — provider abstraction (ARCHITECTURE.md §7).

Defines the AIBackend seam and two implementations:
  - MockBackend   : zero-cost, no network. Simulates a VLM reading a keyed overlay.

The interface deliberately takes a *region* (one image with several colour-keyed
lines) and returns text keyed by colour — this is the §5.A "keyed region" path.
It also accepts a list of single-line crops for the §5.B per-line fallback.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class AIResult:
    # text keyed by line key (colour name for §5.A, or line-id for §5.B)
    text_by_key: dict[str, str]
    # optional weak self-scores 0..1 keyed the same way (§9 — weak prior only)
    self_scores: dict[str, float] = field(default_factory=dict)
    # token accounting for the ledger (§11); 0 for local/mock
    tokens_in: int = 0
    tokens_out: int = 0
    est_cost_usd: float = 0.0
    raw: str = ""


class AIBackend(Protocol):
    name: str

    def transcribe_region(self, image_path: str, keys: list[str],
                          prompt: str) -> AIResult:
        """One keyed-overlay image in, {key: text} out (§5.A)."""
        ...


# --------------------------------------------------------------------------- #
# Mock backend — proves the round-trip with no network and no spend.
# --------------------------------------------------------------------------- #
class MockBackend:
    """Simulates a VLM. Given the ground-truth text it 'reads', it returns text
    keyed by colour. Supports failure injection to prove §5.A's claim that
    mismatches (dropped / merged lines) are *detectable*, not silent."""
    name = "mock"

    def __init__(self, ground_truth_by_key: dict[str, str],
                 drop_keys: tuple[str, ...] = (),
                 merge_into: dict[str, str] | None = None):
        self._gt = ground_truth_by_key
        self._drop = set(drop_keys)
        self._merge_into = merge_into or {}  # {victim_key: absorbing_key}

    def transcribe_region(self, image_path: str, keys: list[str],
                          prompt: str) -> AIResult:
        out: dict[str, str] = {}
        for k in keys:
            if k in self._drop:
                continue  # simulate the VLM missing a line
            text = self._gt.get(k, "")
            if k in self._merge_into:
                # simulate two lines returned as one: append to the absorber
                absorber = self._merge_into[k]
                out[absorber] = (out.get(absorber, self._gt.get(absorber, "")) + " " + text).strip()
                continue
            out[k] = text
        return AIResult(text_by_key=out,
                        self_scores={k: 0.9 for k in out},
                        raw="<mock>")
