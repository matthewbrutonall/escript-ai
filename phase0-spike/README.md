# Phase-0 spike — colour-keyed region round-trip

Offline proof of Escript AI’s quality path (`ARCHITECTURE.md` §5.A). **No
network, no API spend, no live server**.

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

```bash
python3 real_page_overlay_test.py <page.xml> <image.jpg>
```

Empirical notes from private tests are intentionally not shipped. See
`ARCHITECTURE.md` §5 / `STATUS.md` for the implemented design.

## What it does not prove

Hard-hand CER without a reference file; dual-engine triage; local VLM; writing
into a live `LineTranscription` (use `../escript-ai/` against a throwaway
instance, never production).
