"""
End-to-end WRITE-PATH proof against a LOCAL/throwaway eScriptorium.

    kraken segmentation (_hard_seg.xml)
      -> create Project / Document / Part (upload image) / Lines via REST
      -> group lines into <=6-line crops (overlap pre-flight)
      -> colour-keyed overlay -> Gemini -> JSON by colour
      -> create a NEW Transcription layer "AI — gemini-2.5-flash"
      -> write one LineTranscription per line with version_source
      -> read the layer back and print it

Proves ARCHITECTURE.md §4: AI output lands as an additive named layer via the
stock API, provenance-stamped. LOCAL instance only; never production. One live
Gemini pass (~$0.001, printed before spend).
"""
from __future__ import annotations

import os
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
SPIKE = os.path.join(HERE, "..", "phase0-spike")
sys.path.insert(0, SPIKE)

from esc_client import Esc
import real_page_gemini_test as rp   # load_lines, render_crop, to_b64, call_gemini, PROMPT, parse_json, crop_overlap_frac

SEG_XML = os.path.join(SPIKE, "_hard_seg.xml")
# Local page image you are allowed to upload. Never commit scans into the repo.
IMAGE = os.environ.get("ESCRIPT_AI_DEMO_IMAGE", "")
PER_CROP = 6
VERSION_SOURCE = "gemini-2.5-flash"


def baseline_from_mask(mask):
    xs = [x for x, _ in mask]; ys = [y for _, y in mask]
    y = int(max(ys) - (max(ys) - min(ys)) * 0.2)   # ~near the bottom
    return [[min(xs), y], [max(xs), y]]


def main():
    if not IMAGE or not os.path.isfile(IMAGE):
        sys.exit(
            "Set ESCRIPT_AI_DEMO_IMAGE to a page JPEG you are allowed to upload "
            "(unpublished scans must not be committed to this repo)."
        )
    e = Esc()
    print(f"target: {e.base}  (local throwaway instance — never production)")

    # ---- 1. lines from kraken segmentation --------------------------------
    lines = rp.load_lines(SEG_XML)
    print(f"kraken segmentation: {len(lines)} lines")

    # ---- 2. create doc + part + lines -------------------------------------
    proj = e.create_project("AI write-path demo")
    doc = e.create_document("write-path-demo", proj["slug"])
    part = e.upload_part(e.pk(doc), IMAGE)
    dpk, ppk = e.pk(doc), e.pk(part)
    print(f"created project={proj['slug']} document={dpk} part={ppk}")

    line_pks = []
    for ln in lines:
        created = e.create_line(dpk, ppk, mask=ln["mask"],
                                baseline=baseline_from_mask(ln["mask"]))
        line_pks.append(e.pk(created))
    print(f"created {len(line_pks)} lines via API")

    # ---- 3. Gemini over colour-keyed crops --------------------------------
    image = Image.open(IMAGE)
    groups = [list(range(i, min(i + PER_CROP, len(lines))))
              for i in range(0, len(lines), PER_CROP)]
    est = 0.0011 * len(groups)
    print(f"\n[cost pre-flight] {len(groups)} crops x Gemini 2.5 Flash "
          f"=> est ~${est:.4f} (free tier may make this $0)")

    pred_by_lineidx: dict[int, str] = {}
    total_cost = 0.0
    for gi, idxs in enumerate(groups):
        group = [lines[i] for i in idxs]
        ov = rp.crop_overlap_frac(group)
        crop_img, key_to_localidx = rp.render_crop(image, group)
        b64, w, h = rp.to_b64(crop_img)
        data = rp.call_gemini(b64)
        raw = data["candidates"][0]["content"]["parts"][0]["text"]
        u = data.get("usageMetadata", {})
        total_cost += (u.get("promptTokenCount", 0) * rp.IN_USD_PER_1M
                       + u.get("candidatesTokenCount", 0) * rp.OUT_USD_PER_1M) / 1e6
        parsed = rp.parse_json(raw)
        for key, local_idx in key_to_localidx.items():
            txt = str(parsed.get(key, "")).strip()
            if txt:
                pred_by_lineidx[idxs[local_idx]] = txt
        print(f"  crop {gi}: overlap={ov:.0%}  {len(parsed)} lines read")
    print(f"[actual] Gemini spend this run: ~${total_cost:.5f}")

    # ---- 4. create the AI layer + write line transcriptions ---------------
    layer = e.create_transcription(dpk, f"AI — {VERSION_SOURCE}")
    tpk = e.pk(layer)
    print(f"\ncreated transcription layer pk={tpk} name={layer['name']!r}")

    written = 0
    for idx, lpk in enumerate(line_pks):
        content = pred_by_lineidx.get(idx)
        if not content:
            continue
        e.write_line_transcription(dpk, ppk, lpk, tpk, content, VERSION_SOURCE)
        written += 1
    print(f"wrote {written} LineTranscription rows with version_source={VERSION_SOURCE!r}")

    # ---- 5. read back -----------------------------------------------------
    rows = e.list_line_transcriptions(dpk, ppk, tpk)
    print(f"\n=== read back {len(rows)} rows from the layer (proves the write) ===")
    for r in sorted(rows, key=lambda r: r.get("line") or 0):
        print(f"  line {r.get('line')}  src={r.get('version_source')!r}  "
              f"{r.get('content','')[:60]!r}")
    print(f"\nDone. View at {e.base}/document/{dpk}/part/{ppk}/edit/")


if __name__ == "__main__":
    main()
