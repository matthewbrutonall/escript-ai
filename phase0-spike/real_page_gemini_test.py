"""
REAL-page colour-key read test on genuine handwriting + human ground truth.
(ARCHITECTURE.md §5.A / §16 Q1 — the load-bearing unknown.)

Input: a real eScriptorium PAGE export (line masks + human-corrected Unicode
text) and its image. Chunks lines into <=N-line crops (colour palette limit),
paints a distinct colour per line, sends each crop to Gemini, asks for JSON
keyed by colour, maps back to lines, and scores against the human transcription
with CER.

--dry-run renders the crops and prints the cost estimate WITHOUT any API call,
so we verify overlays for free before spending. Live mode reports actual tokens
and cost per crop (global-memory paid-API rule).

Usage:
  python3 real_page_gemini_test.py <page.xml> <image.jpg> [--per-crop 6] [--limit N] [--dry-run]
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

import requests
from PIL import Image, ImageDraw, ImageFont

from overlay import PALETTE, _color_for_key

KEY_PATH = os.path.expanduser("~/.config/escript-ai/gemini.key")
MODEL = "gemini-2.5-flash"
ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
MAX_EDGE = 1024
IN_USD_PER_1M, OUT_USD_PER_1M = 0.30, 2.50
HERE = os.path.dirname(os.path.abspath(__file__))


def levenshtein(a: str, b: str) -> int:
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(gt: str, pred: str) -> float:
    return levenshtein(gt, pred) / max(1, len(gt))


def load_lines(xml_path: str):
    """Handle both PAGE (polygon Coords + Unicode) and ALTO (rect + String CONTENT).
    'text' is the reference transcription in the file (human GT for PAGE exports,
    machine output for HTRflow ALTO — labelled accordingly by the caller)."""
    r = ET.parse(xml_path).getroot()
    ns = r.tag.split("}")[0][1:]
    P = f"{{{ns}}}"
    lines = []
    if r.tag.endswith("alto"):  # ALTO
        for tl in r.iter(f"{P}TextLine"):
            text = " ".join(s.attrib.get("CONTENT", "") for s in tl.iter(f"{P}String")).strip()
            poly = tl.find(f"{P}Shape/{P}Polygon")  # prefer real polygon if present
            if poly is not None and poly.attrib.get("POINTS"):
                nums = [int(float(v)) for v in poly.attrib["POINTS"].replace(",", " ").split()]
                mask = list(zip(nums[0::2], nums[1::2]))
                if len(mask) < 3:
                    continue
                lines.append({"mask": mask, "text": text,
                              "top": min(y for _, y in mask), "geom": "poly"})
            else:  # fall back to bounding box (OUT OF SPEC for colour-keying)
                try:
                    x, y = int(float(tl.attrib["HPOS"])), int(float(tl.attrib["VPOS"]))
                    w, h = int(float(tl.attrib["WIDTH"])), int(float(tl.attrib["HEIGHT"]))
                except KeyError:
                    continue
                mask = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
                lines.append({"mask": mask, "text": text, "top": y, "geom": "box"})
    else:  # PAGE: polygons
        pg = r.find(f"{P}Page")
        for tl in pg.iter(f"{P}TextLine"):
            coords = tl.find(f"{P}Coords")
            te = tl.find(f"{P}TextEquiv/{P}Unicode")
            if coords is None:
                continue
            pts = [tuple(map(int, p.split(","))) for p in coords.attrib["points"].split()]
            if len(pts) < 3:
                continue
            text = (te.text or "").strip() if te is not None else ""
            lines.append({"mask": pts, "text": text,
                          "top": min(y for _, y in pts), "geom": "poly"})
    lines.sort(key=lambda l: l["top"])
    return lines


def _bbox(mask):
    xs = [x for x, _ in mask]; ys = [y for _, y in mask]
    return min(xs), min(ys), max(xs), max(ys)


def crop_overlap_frac(group) -> float:
    """Max pairwise bbox-overlap fraction within a crop (0 = disjoint)."""
    worst = 0.0
    for i in range(len(group)):
        ax0, ay0, ax1, ay1 = _bbox(group[i]["mask"])
        aa = max(1, (ax1 - ax0) * (ay1 - ay0))
        for j in range(i + 1, len(group)):
            bx0, by0, bx1, by1 = _bbox(group[j]["mask"])
            ix = max(0, min(ax1, bx1) - max(ax0, bx0))
            iy = max(0, min(ay1, by1) - max(ay0, by0))
            inter = ix * iy
            bb = max(1, (bx1 - bx0) * (by1 - by0))
            worst = max(worst, inter / min(aa, bb))
    return worst


def render_crop(image: Image.Image, group, margin=110, pad=12):
    """Crop the image to a line group and paint one colour per line."""
    xs = [x for ln in group for x, _ in ln["mask"]]
    ys = [y for ln in group for _, y in ln["mask"]]
    x0, y0, x1, y1 = max(0, min(xs) - pad), max(0, min(ys) - pad), max(xs) + pad, max(ys) + pad
    crop = image.crop((x0, y0, x1, y1)).convert("RGBA")

    canvas = Image.new("RGBA", (crop.width + margin, crop.height), (255, 255, 255, 255))
    canvas.paste(crop, (margin, 0))
    ov = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 20)
    except OSError:
        font = ImageFont.load_default()

    key_to_idx = {}
    for i, ln in enumerate(group):
        name = PALETTE[i % len(PALETTE)][0]
        rgb = _color_for_key(name)
        mask = [(x - x0 + margin, y - y0) for x, y in ln["mask"]]
        d.polygon(mask, fill=rgb + (85,))
        d.line(mask + [mask[0]], fill=rgb + (255,), width=3)
        ty = min(p[1] for p in mask)
        d.rectangle([4, ty, margin - 8, ty + 24], fill=rgb + (255,))
        d.text((8, ty + 2), name, fill=(255, 255, 255, 255), font=font)
        key_to_idx[name] = i
    out = Image.alpha_composite(canvas, ov).convert("RGB")
    return out, key_to_idx


def to_b64(im: Image.Image):
    im2 = im.copy()
    im2.thumbnail((MAX_EDGE, MAX_EDGE))
    buf = io.BytesIO()
    im2.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode(), im2.width, im2.height


PROMPT = (
    "This is a crop of a handwritten manuscript. Each text line is highlighted "
    "with a distinct translucent colour and labelled by colour name in the left "
    "margin. Transcribe each coloured line diplomatically: keep original "
    "spelling, do not expand abbreviations, do not add or remove punctuation. "
    "Return ONLY a JSON object mapping each colour name (e.g. \"red\", \"blue\", "
    "\"green\", \"orange\", \"purple\", \"brown\") to that line's text."
)


def parse_json(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


def call_gemini(b64: str):
    payload = {"contents": [{"parts": [
        {"text": PROMPT},
        {"inline_data": {"mime_type": "image/png", "data": b64}}]}],
        "generationConfig": {"temperature": 0}}
    with open(KEY_PATH) as f:
        key = f.read().strip()
    r = requests.post(ENDPOINT, headers={"x-goog-api-key": key}, json=payload, timeout=180)
    r.raise_for_status()
    return r.json()


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print(__doc__); sys.exit(1)
    xml_path, image_path = args[0], args[1]
    per_crop = int(args[args.index("--per-crop") + 1]) if "--per-crop" in args else 6
    limit = int(args[args.index("--limit") + 1]) if "--limit" in args else None
    dry = "--dry-run" in args

    lines = load_lines(xml_path)
    image = Image.open(image_path)
    groups = [lines[i:i + per_crop] for i in range(0, len(lines), per_crop)]
    if limit:
        groups = groups[:limit]

    # ---- pre-flight (§5.A): box-only ALTO or overlapping masks are OUT OF SPEC.
    box_only = lines and all(l.get("geom") == "box" for l in lines)
    OVERLAP_THRESH = 0.10
    print(f"page: {os.path.basename(image_path)}  {image.width}x{image.height}  "
          f"{len(lines)} lines -> {len(groups)} crops of <= {per_crop}")
    if box_only:
        print("  PRE-FLIGHT: source is box-only ALTO (no polygons). Colour-keying "
              "is OUT OF SPEC here — re-segment with kraken blla before keying. "
              "Running anyway for evidence; results are a segmentation stress test.")

    results = []            # JSON dump: per-crop pair data for the next reviewer
    total_cost = 0.0
    all_cer = []
    for gi, group in enumerate(groups):
        ov = crop_overlap_frac(group)
        if ov > OVERLAP_THRESH:
            print(f"  [pre-flight] crop {gi}: mask overlap {ov:.0%} > "
                  f"{OVERLAP_THRESH:.0%} — would SKIP + re-segment in production.")
        crop_img, key_to_idx = render_crop(image, group)
        b64, w, h = to_b64(crop_img)
        if dry:
            path = os.path.join(HERE, f"_realcrop_{gi}.png")
            crop_img.save(path)
            import math
            est_in = max(258, math.ceil(w / 768) * math.ceil(h / 768) * 258) + 120
            est = est_in * IN_USD_PER_1M / 1e6 + 400 * OUT_USD_PER_1M / 1e6
            total_cost += est
            print(f"  crop {gi}: {len(group)} lines, {w}x{h} -> {os.path.basename(path)}  est ~${est:.5f}")
            continue

        data = call_gemini(b64)
        raw = data["candidates"][0]["content"]["parts"][0]["text"]
        u = data.get("usageMetadata", {})
        tin, tout = u.get("promptTokenCount", 0), u.get("candidatesTokenCount", 0)
        cost = tin * IN_USD_PER_1M / 1e6 + tout * OUT_USD_PER_1M / 1e6
        total_cost += cost
        parsed = parse_json(raw)
        returned = set(parsed)
        unexpected = sorted(returned - set(key_to_idx))  # keys the VLM invented
        print(f"\n  === crop {gi} ({len(group)} lines)  {tin}+{tout} tok  "
              f"~${cost:.5f}  overlap={ov:.0%} ===")
        if unexpected:
            print(f"    [post-call] unexpected keys {unexpected} — parser/naming "
                  f"issue, NOT dropped lines.")
        for name, idx in key_to_idx.items():
            gt = group[idx]["text"]
            pred = str(parsed.get(name, ""))
            if gt:  # reference present → score
                c = cer(gt, pred)
                all_cer.append(c)
                flag = "OK  " if c == 0 else ("~   " if c < 0.15 else "BAD ")
                print(f"    {name:8} dis={c:4.0%} {flag} ref={gt[:52]!r}")
                if c > 0:
                    print(f"    {'':8}            pred={pred[:52]!r}")
            else:   # no reference (segmentation-only) → eyeball the pred
                print(f"    {name:8} pred={pred[:60]!r}")
            results.append({"crop": gi, "key": name, "geom": group[idx].get("geom"),
                            "overlap": round(ov, 3), "ref": gt, "pred": pred,
                            "disagreement": round(cer(gt, pred), 3) if gt else None})

    print(f"\nTOTAL cost this run: ~${total_cost:.5f}"
          + (" (DRY RUN — no API calls made)" if dry else ""))
    if all_cer:
        mean_cer = sum(all_cer) / len(all_cer)
        exact = sum(1 for c in all_cer if c == 0)
        label = "mean disagreement" if box_only else "mean CER"
        print(f"{label}: {mean_cer:.1%}   exact: {exact}/{len(all_cer)}"
              + ("   (ref is machine output, not human GT)" if box_only else ""))
    if results:
        jpath = os.path.join(HERE, "_results.json")
        with open(jpath, "w") as f:
            json.dump({"page": os.path.basename(image_path), "box_only": box_only,
                       "rows": results}, f, indent=2, ensure_ascii=False)
        print(f"wrote pair dump -> {os.path.basename(jpath)}")


if __name__ == "__main__":
    main()
