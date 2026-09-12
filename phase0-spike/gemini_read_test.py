"""
Gemini colour-key READ test (ARCHITECTURE.md §16 Q1 — the load-bearing unknown).

Sends a colour-keyed overlay image to Gemini and asks for JSON keyed by colour,
then checks whether the model actually (a) read the colours and (b) assigned the
right text to each. This is the one thing no offline mock could prove.

COST DISCIPLINE (global-memory paid-API rule):
  - Uses REST generateContent (billable). Runs ONLY when invoked explicitly.
  - Prints a per-image cost estimate BEFORE the call and reports ACTUAL token
    usage + cost AFTER. Downscales images first so we never over-send tokens.
  - Key read from ~/.config/escript-ai/gemini.key (600, outside the repo).

Usage:
  python3 gemini_read_test.py <overlay.png> [--truth-from-synthetic]
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import sys

import requests
from PIL import Image

KEY_PATH = os.path.expanduser("~/.config/escript-ai/gemini.key")
MODEL = "gemini-2.5-flash"
ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
MAX_EDGE = 1024

# Gemini 2.5 Flash pricing (verify before relying on it). Free tier may apply.
IN_USD_PER_1M = 0.30
OUT_USD_PER_1M = 2.50


def load_key() -> str:
    with open(KEY_PATH) as f:
        return f.read().strip()


def downscale_b64(path: str) -> tuple[str, int, int]:
    im = Image.open(path).convert("RGB")
    im.thumbnail((MAX_EDGE, MAX_EDGE))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    b = buf.getvalue()
    return base64.b64encode(b).decode(), im.width, im.height


def est_image_tokens(w: int, h: int) -> int:
    # Gemini tiles images ~768px; ~258 tokens/tile, min 258.
    import math
    tiles = max(1, math.ceil(w / 768) * math.ceil(h / 768))
    return max(258, tiles * 258)


PROMPT = (
    "This manuscript image has each text line highlighted in a distinct colour "
    "(see the coloured band over each line and the colour label in the left "
    "margin). Transcribe the text of each coloured line diplomatically — do not "
    "expand abbreviations, do not modernise spelling, do not add punctuation. "
    "Return ONLY a JSON object mapping each colour name (exactly as labelled: "
    "e.g. \"red\", \"blue\", \"green\", \"orange\", \"purple\", \"brown\", "
    "\"magenta\", \"teal\") to that line's text. No commentary."
)


def parse_json(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    overlay = sys.argv[1]
    b64, w, h = downscale_b64(overlay)

    est_in = est_image_tokens(w, h) + 120  # + prompt
    est_out = 400
    est_cost = est_in * IN_USD_PER_1M / 1e6 + est_out * OUT_USD_PER_1M / 1e6
    print(f"[pre-flight] image {w}x{h}, ~{est_in} in + ~{est_out} out tokens "
          f"=> est ~${est_cost:.5f} (free tier may make this $0)")

    payload = {
        "contents": [{"parts": [
            {"text": PROMPT},
            {"inline_data": {"mime_type": "image/png", "data": b64}},
        ]}],
        "generationConfig": {"temperature": 0},
    }
    r = requests.post(ENDPOINT, headers={"x-goog-api-key": load_key()},
                      json=payload, timeout=120)
    r.raise_for_status()
    data = r.json()

    raw = data["candidates"][0]["content"]["parts"][0]["text"]
    usage = data.get("usageMetadata", {})
    tin = usage.get("promptTokenCount", est_in)
    tout = usage.get("candidatesTokenCount", est_out)
    actual = tin * IN_USD_PER_1M / 1e6 + tout * OUT_USD_PER_1M / 1e6
    print(f"[actual] {tin} in + {tout} out tokens => ~${actual:.5f} "
          f"(before any free-tier discount)")

    parsed = parse_json(raw)
    print("\n--- Gemini returned (keyed by colour) ---")
    for k, v in parsed.items():
        print(f"  {k:9}: {v!r}")

    # optional accuracy check against the synthetic ground truth
    if "--truth-from-synthetic" in sys.argv:
        from spike import GROUND_TRUTH
        from overlay import PALETTE
        gt = {PALETTE[i][0]: GROUND_TRUTH[i] for i in range(len(GROUND_TRUTH))}
        print("\n--- accuracy vs synthetic ground truth ---")
        hits = 0
        for colour, truth in gt.items():
            got = parsed.get(colour)
            ok = got == truth
            hits += ok
            print(f"  {colour:9}: {'OK ' if ok else 'MISS'} got={got!r}")
        print(f"\n  {hits}/{len(gt)} colours read correctly.")

    print("\n--- raw response (for inspection) ---")
    print(raw[:800])


if __name__ == "__main__":
    main()
