"""
Phase-0 spike — colour-keyed overlay renderer (ARCHITECTURE.md §5.A).

The novel mechanism: instead of asking a VLM for pixel coordinates (which it is
bad at) or aligning flowing prose to baselines (which the existing forced_align
does NOT do), we paint each kraken-detected line a distinct translucent colour,
send one region image, and ask for JSON keyed by colour name. The VLM tells us
which line each string belongs to — no alignment step to get wrong.

This module renders the overlay and returns the key->line-id map. It needs only
PIL and the line geometry that kraken already produces (baseline + mask polygon).
"""
from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

# ~8 distinct, reliably-nameable colours. Palette size is the constraint that
# makes region-granularity necessary (§5.A): keep crops under ~10 lines.
PALETTE: list[tuple[str, tuple[int, int, int]]] = [
    ("red",     (220, 30, 30)),
    ("blue",    (30, 80, 220)),
    ("green",   (20, 150, 40)),
    ("orange",  (240, 140, 0)),
    ("purple",  (150, 30, 200)),
    ("brown",   (140, 80, 20)),
    ("magenta", (230, 30, 160)),
    ("teal",    (0, 150, 150)),
]


@dataclass
class Line:
    """A kraken-style line: id + polygon mask. Mirrors core.models.Line."""
    id: int
    mask: list[tuple[int, int]]        # closed polygon
    text: str = ""                     # ground truth (spike only)


def assign_keys(lines: list[Line]) -> list[tuple[str, Line]]:
    """Colour primary; colour-band+number fallback past the palette (§5.A)."""
    keyed = []
    for i, ln in enumerate(lines):
        base_name, _ = PALETTE[i % len(PALETTE)]
        band = i // len(PALETTE)
        key = base_name if band == 0 else f"{base_name}-{band + 1}"
        keyed.append((key, ln))
    return keyed


def _color_for_key(key: str) -> tuple[int, int, int]:
    base = key.split("-")[0]
    for name, rgb in PALETTE:
        if name == base:
            return rgb
    return (0, 0, 0)


def render_keyed_overlay(image_path: str, lines: list[Line],
                         out_path: str, alpha: int = 90,
                         margin_px: int = 90) -> dict[str, int]:
    """Paint a translucent colour over each line's mask. Returns {key: line_id}.

    Translucent fill keeps the ink legible underneath (the whole point of using
    colour rather than a stamped number that occludes text). Key swatches/labels
    are drawn in an ADDED left margin, never on the page — on real manuscripts
    the mask often starts near x=0, so a page-gutter label would occlude margin
    text or fall off-canvas."""
    page = Image.open(image_path).convert("RGBA")
    # widen the canvas so labels live off the page image, not over it
    base = Image.new("RGBA", (page.width + margin_px, page.height), (255, 255, 255, 255))
    base.paste(page, (margin_px, 0))

    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    label_h = max(12, min(20, margin_px // 5))
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", label_h)
    except OSError:
        font = ImageFont.load_default()

    key_to_lineid: dict[str, int] = {}
    for key, ln in assign_keys(lines):
        rgb = _color_for_key(key)
        mask = [(x + margin_px, y) for (x, y) in ln.mask]  # shift into padded canvas
        draw.polygon(mask, fill=rgb + (alpha,))                     # translucent band
        draw.line(mask + [mask[0]], fill=rgb + (255,), width=2)     # solid edge
        # swatch + label in the added margin, aligned to the line's top
        y = min(p[1] for p in mask)
        draw.rectangle([2, y, margin_px - 6, y + label_h + 2], fill=rgb + (255,))
        draw.text((5, y + 1), key[:9], fill=(255, 255, 255, 255), font=font)
        key_to_lineid[key] = ln.id

    Image.alpha_composite(base, overlay).convert("RGB").save(out_path)
    return key_to_lineid
