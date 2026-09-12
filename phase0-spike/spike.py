"""
Phase-0 spike — end-to-end, fully local, no network, no API spend, no server.

Proves the single most novel mechanism in ARCHITECTURE.md (§5.A): the
colour-keyed region round-trip.

    synthesize a page with known lines
      -> render a colour-keyed overlay        (overlay.py)
      -> a MockBackend "reads" it             (backends.py, no network)
      -> parse JSON keyed by colour
      -> map keys back to line ids
      -> emit the LineTranscription rows that WOULD be written

It also injects failures (a dropped line, a merged pair) to demonstrate the
claim that key mismatches are *detectable quality events*, not silent GT
poisoning.

The synthetic page contains real printed Latin at known baselines, so the SAME
script becomes a genuine end-to-end test the moment a Gemini key + spend
approval exist — just swap MockBackend for GeminiBackend. Nothing here contacts
the eScriptorium server or any API.
"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFont

from backends import MockBackend
from overlay import Line, assign_keys, render_keyed_overlay

HERE = os.path.dirname(os.path.abspath(__file__))

# A short Vulgate passage — real transcribable text (Latin), one string per line.
GROUND_TRUTH = [
    "In principio erat Verbum",
    "et Verbum erat apud Deum",
    "et Deus erat Verbum",
    "hoc erat in principio apud Deum",
    "omnia per ipsum facta sunt",
    "et sine ipso factum est nihil",
]


def synthesize_page(path: str) -> list[Line]:
    """Draw the passage as a page image; return Lines with known mask polygons."""
    W, margin, line_h, top = 760, 60, 46, 30
    H = top + line_h * len(GROUND_TRUTH) + 30
    img = Image.new("RGB", (W, H), (250, 248, 240))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSerif.ttf", 26)
    except OSError:
        font = ImageFont.load_default()

    lines: list[Line] = []
    for i, text in enumerate(GROUND_TRUTH):
        y = top + i * line_h
        draw.text((margin, y), text, fill=(20, 20, 30), font=font)
        # bounding polygon for the line (what kraken's mask would give us)
        bbox = draw.textbbox((margin, y), text, font=font)
        pad = 6
        mask = [
            (bbox[0] - pad, bbox[1] - pad),
            (bbox[2] + pad, bbox[1] - pad),
            (bbox[2] + pad, bbox[3] + pad),
            (bbox[0] - pad, bbox[3] + pad),
        ]
        lines.append(Line(id=1000 + i, mask=mask, text=text))
    img.save(path)
    return lines


def run(scenario: str, mock_kwargs: dict) -> None:
    print(f"\n=== scenario: {scenario} ===")
    page_path = os.path.join(HERE, "_page.png")
    overlay_path = os.path.join(HERE, f"_overlay_{scenario}.png")

    lines = synthesize_page(page_path)
    key_to_lineid = render_keyed_overlay(page_path, lines, overlay_path)
    print(f"rendered keyed overlay -> {os.path.basename(overlay_path)}")
    print(f"keys assigned: {list(key_to_lineid.keys())}")

    # ground truth keyed by colour, for the mock to 'read'
    gt_by_key = {key: ln.text for key, ln in assign_keys(lines)}
    backend = MockBackend(gt_by_key, **mock_kwargs)

    prompt = ("Transcribe each coloured line. Return JSON mapping each colour "
              "name to that line's text, diplomatically, no added punctuation.")
    result = backend.transcribe_region(overlay_path, list(key_to_lineid), prompt)

    # ---- map keys back to line ids; this is what becomes LineTranscription ----
    returned = set(result.text_by_key)
    expected = set(key_to_lineid)
    missing = expected - returned          # dropped lines  -> detectable
    unexpected = returned - expected        # hallucinated keys -> detectable

    print("\n  would-write LineTranscription rows "
          "(line_id, version_source, content):")
    for key, line_id in key_to_lineid.items():
        content = result.text_by_key.get(key)
        if content is None:
            print(f"    line {line_id}  [{key:9}] -- NO TEXT (flagged, left empty)")
        else:
            ok = "OK " if content == lines[line_id - 1000].text else "DIFF"
            print(f"    line {line_id}  [{key:9}] {ok} version_source='mock'  "
                  f"content={content!r}")

    print("\n  quality-event check (§5.A: mismatches must be detectable):")
    print(f"    lines expected : {len(expected)}")
    print(f"    keys returned  : {len(returned)}")
    if missing:
        print(f"    MISSING keys (dropped lines) : {sorted(missing)}  -> page flagged")
    if unexpected:
        print(f"    UNEXPECTED keys (hallucinated): {sorted(unexpected)} -> page flagged")
    if not missing and not unexpected:
        print("    clean: key set matches line set exactly.")


if __name__ == "__main__":
    # 1) happy path: perfect VLM
    run("clean", {})
    # 2) failure injection: VLM drops one line
    run("dropped", {"drop_keys": ("green",)})
    # 3) failure injection: VLM merges two lines into one
    run("merged", {"merge_into": {"blue": "red"}})

    print("\nDone. Open _overlay_clean.png to see the colour keying. "
          "No network, no API, no server touched.")
