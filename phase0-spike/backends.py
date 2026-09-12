"""
Phase-0 spike — provider abstraction (ARCHITECTURE.md §7).

Defines the AIBackend seam and two implementations:
  - MockBackend   : zero-cost, no network. Simulates a VLM reading a keyed overlay.
  - GeminiBackend : the real adapter. NOT run until (a) a key exists and
                    (b) the per-image cost is shown and approved (global-memory
                    paid-API rule). It prints a cost estimate and refuses to
                    proceed without an explicit confirm flag.

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


# --------------------------------------------------------------------------- #
# Gemini backend — REAL adapter, guarded. Not executed in the spike.
# --------------------------------------------------------------------------- #
# Gemini 2.5 Flash image-input pricing is dominated by *image tokens*, not the
# prompt (ARCHITECTURE.md §11). A downscaled region (~1024px max edge) is on the
# order of a few hundred to ~1k tokens; one call is a small fraction of a cent.
# Still: the paid-API rule forbids spending without showing the estimate and
# getting an explicit go-ahead. This adapter enforces that.
GEMINI_FLASH_INPUT_USD_PER_1M = 0.30   # approximate; verify before first spend
GEMINI_FLASH_OUTPUT_USD_PER_1M = 2.50  # approximate; verify before first spend


class SpendNotApprovedError(RuntimeError):
    pass


class GeminiBackend:
    name = "gemini-2.5-flash"

    def __init__(self, api_key: str | None = None, model: str = "gemini-2.5-flash",
                 max_edge_px: int = 1024, confirm_spend: bool = False):
        self.api_key = api_key
        self.model = model
        self.max_edge_px = max_edge_px
        self.confirm_spend = confirm_spend

    def _estimate(self, est_image_tokens: int, est_output_tokens: int) -> float:
        return (est_image_tokens * GEMINI_FLASH_INPUT_USD_PER_1M / 1_000_000
                + est_output_tokens * GEMINI_FLASH_OUTPUT_USD_PER_1M / 1_000_000)

    def transcribe_region(self, image_path: str, keys: list[str],
                          prompt: str) -> AIResult:
        if not self.api_key:
            raise SpendNotApprovedError(
                "No Gemini API key. Set one and pass confirm_spend=True after "
                "reviewing the printed cost estimate.")
        # Rough pre-flight estimate (real impl would count downscaled image tokens).
        est_in, est_out = 900, 60 * len(keys)
        est = self._estimate(est_in, est_out)
        print(f"[cost pre-flight] ~{est_in} in + ~{est_out} out tokens "
              f"=> ~${est:.5f} for this region ({len(keys)} lines).")
        if not self.confirm_spend:
            raise SpendNotApprovedError(
                f"Refusing to spend ~${est:.5f} without confirm_spend=True.")
        # --- real call (deferred; needs `pip install google-genai`) ---------
        from google import genai            # noqa: F401  (import here so spike runs without SDK)
        from PIL import Image
        client = genai.Client(api_key=self.api_key)
        img = Image.open(image_path)
        img.thumbnail((self.max_edge_px, self.max_edge_px))  # downscale FIRST
        # re-estimate image tokens from the DOWNSCALED size, else we under-quote
        est_in = self._image_tokens(img)
        est = self._estimate(est_in, est_out)
        print(f"[cost pre-flight, post-downscale] ~{est_in} in + ~{est_out} out "
              f"tokens => ~${est:.5f}")
        resp = client.models.generate_content(model=self.model, contents=[prompt, img])
        raw = resp.text or ""
        text_by_key, unknown = _parse_json_by_key(raw, keys)
        if unknown:
            # a parser/key-name failure must NOT masquerade as a VLM dropped line
            print(f"[warn] backend returned {len(unknown)} unrecognised key(s): "
                  f"{sorted(unknown)} — logging raw for inspection, NOT counting "
                  f"as dropped lines.")
        return AIResult(text_by_key=text_by_key,
                        tokens_in=getattr(getattr(resp, "usage_metadata", None),
                                          "prompt_token_count", est_in),
                        tokens_out=getattr(getattr(resp, "usage_metadata", None),
                                           "candidates_token_count", est_out),
                        est_cost_usd=est, raw=raw)

    @staticmethod
    def _image_tokens(img) -> int:
        # rough Gemini image-token heuristic: ~1 token per 750 px area, tile-capped
        return max(258, min(4096, (img.width * img.height) // 750))


def _parse_json_by_key(raw: str, keys: list[str]) -> tuple[dict[str, str], set[str]]:
    """Tolerant JSON extraction. Returns (known_text_by_key, unknown_keys).

    Unknown keys are returned separately so a parser/key-naming failure (VLM
    said "light blue" instead of "blue") is visible and is NOT silently counted
    as a dropped line downstream (§5.A quality-event logic depends on this)."""
    import json, re
    known = set(keys)
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return {}, set()
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}, set()
    text_by_key = {k: str(v) for k, v in data.items() if k in known}
    unknown = {k for k in data if k not in known}
    return text_by_key, unknown
