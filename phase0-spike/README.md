# Phase-0 spike — colour-keyed region round-trip

Offline proof of Escript AI’s quality path (`ARCHITECTURE.md` §5.A). **No
network, no API spend, no live server** unless you explicitly run the guarded
Gemini adapters.

## What it proves

Kraken (or any line masks) → paint each line a **colour** → VLM returns **JSON
keyed by colour** → map back to line ids. There is no prose-to-baseline
alignment step (`forced_align` / Passim do not do that job).

`spike.py` uses **synthetic printed Latin** at known baselines (Pillow only):

1. Colour keying keeps ink legible (`python3 spike.py` writes `_overlay_*.png`).
2. Lossless round-trip on the happy path.
3. Dropped/merged lines surface as flags, not silent GT.

```bash
cd phase0-spike
python3 spike.py    # Pillow only
```

Generated `_*.png` and `_results.json` are **local artifacts, not in git**.

## Geometry fixture

`_hard_seg.xml` is **masks only** (empty `String` content) for unit tests
(`ai.tests.test_preflight`). The `fileName` is a placeholder. The page image
that produced it is **not in this repository**.

`real_page_overlay.py` can overlay kraken’s public test PAGE
(`../kraken/tests/resources/page/cPAS-2000.xml`) — no VLM.

## Guarded live Gemini (optional)

`gemini_read_test.py` / `real_page_gemini_test.py` call Gemini **only** with a
key in the environment or `~/.config/escript-ai/gemini.key` **and** an explicit
spend confirmation / `--dry-run` first. They take **your** PAGE XML + image as
arguments. Do not commit unpublished scans.

```bash
python3 real_page_gemini_test.py <page.xml> <image.jpg> --dry-run
```

Empirical notes (images **not** shipped): tight PAGE + neat English cursive
scored ~0.5% CER vs human GT; overlapping/shattered ALTO fails assignment;
degenerate slivers can hallucinate. See `ARCHITECTURE.md` §5 / `STATUS.md`.

## What it does not prove

Hard-hand CER without a reference file; dual-engine triage; local VLM; writing
into a live `LineTranscription` (use `../escript-ai/` against a throwaway
instance, never production).
