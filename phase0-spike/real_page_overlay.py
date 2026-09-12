"""
Phase-0 spike — colour keying on REAL manuscript geometry (ARCHITECTURE.md §16 Q1).

The main spike used clean printed Latin on cream with well-spaced textbbox
rectangles. Grok's strongest concrete objection: real kraken masks are thin
polygons stacked tightly down a full column, an 8-colour palette recycles long
before a 30+ line region ends, and translucent bands bleed into neighbours.

This renders the ACTUAL line masks from kraken's own test page
(../kraken/tests/resources/page/cPAS-2000.xml, 3631x4734) so we can look at
whether the colours stay distinct on real geometry, and quantify the palette /
line-count / inter-line-gap problem.

Honest limit: the real .jpg for this XML is not in the clone, so we render the
masks on a neutral canvas. This tests band DISTINCTNESS and geometry — NOT
ink/palette collision or faint-ink legibility, which still need a real image +
a VLM (deferred, key + spend approval). No network, no server, no spend here.
"""
from __future__ import annotations

import os
import statistics
import xml.etree.ElementTree as ET

from overlay import Line, PALETTE, assign_keys, render_keyed_overlay

HERE = os.path.dirname(os.path.abspath(__file__))
XML = os.path.abspath(os.path.join(
    HERE, "..", "kraken", "tests", "resources", "page", "cPAS-2000.xml"))


def _points(coords_el) -> list[tuple[int, int]]:
    pts = []
    for pair in coords_el.attrib["points"].split():
        x, y = pair.split(",")
        pts.append((int(x), int(y)))
    return pts


def load_lines() -> tuple[list[Line], int, int, int]:
    tree = ET.parse(XML)
    root = tree.getroot()
    # derive the PAGE namespace from the root tag (schema year/scheme varies)
    ns = root.tag.split("}")[0][1:] if "}" in root.tag else ""
    global PAGE_NS
    PAGE_NS = f"{{{ns}}}" if ns else ""
    page = root.find(f"{PAGE_NS}Page")
    W = int(page.attrib["imageWidth"])
    H = int(page.attrib["imageHeight"])

    lines: list[Line] = []
    n_regions = 0
    lid = 0
    for region in page.iter(f"{PAGE_NS}TextRegion"):
        n_regions += 1
        for tl in region.iter(f"{PAGE_NS}TextLine"):
            coords = tl.find(f"{PAGE_NS}Coords")
            if coords is None:
                continue
            mask = _points(coords)
            if len(mask) < 3:
                continue
            lines.append(Line(id=lid, mask=mask))
            lid += 1
    return lines, W, H, n_regions


def stats(lines: list[Line]) -> None:
    heights, tops = [], []
    for ln in lines:
        ys = [p[1] for p in ln.mask]
        heights.append(max(ys) - min(ys))
        tops.append(min(ys))
    tops.sort()
    gaps = [b - a for a, b in zip(tops, tops[1:])]
    n = len(lines)
    print(f"  lines: {n}   palette size: {len(PALETTE)}   "
          f"colour recycles: {n // len(PALETTE)}x "
          f"(=> {'colour-band+number keys needed' if n > len(PALETTE) else 'pure colour ok'})")
    if heights:
        print(f"  line height  px: min {min(heights)}  median {int(statistics.median(heights))}  max {max(heights)}")
    if gaps:
        print(f"  inter-line gap px: min {min(gaps)}  median {int(statistics.median(gaps))}  "
              f"(overlap risk where gap < height)")
    # how many adjacent line-pairs share the same colour (the real failure mode)
    keyed = assign_keys(lines)
    same_colour_adjacent = 0
    for (ka, _), (kb, _) in zip(keyed, keyed[1:]):
        if ka.split("-")[0] == kb.split("-")[0]:
            same_colour_adjacent += 1
    print(f"  adjacent lines sharing a base colour: {same_colour_adjacent} "
          f"(these are the ones a VLM could confuse)")


def main() -> None:
    lines, W, H, n_regions = load_lines()
    print(f"real page {os.path.basename(XML)}  {W}x{H}px  regions={n_regions}")
    stats(lines)

    # render on a neutral canvas at true dimensions
    from PIL import Image
    canvas = os.path.join(HERE, "_real_canvas.png")
    Image.new("RGB", (W, H), (245, 243, 236)).save(canvas)

    out_full = os.path.join(HERE, "_real_overlay_full.png")
    render_keyed_overlay(canvas, lines, out_full, alpha=90, margin_px=140)

    # downscale a viewable preview
    prev = os.path.join(HERE, "_real_overlay_preview.png")
    im = Image.open(out_full)
    im.thumbnail((1100, 1100))
    im.save(prev)
    print(f"\n  wrote {os.path.basename(out_full)} (full) and "
          f"{os.path.basename(prev)} (preview)")
    print("  NOTE: neutral canvas — tests band distinctness/geometry only, "
          "NOT ink collision or faint-ink legibility (needs real image + VLM).")


if __name__ == "__main__":
    main()
