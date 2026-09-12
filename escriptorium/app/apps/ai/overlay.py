"""
Colour-keyed overlay renderer (ARCHITECTURE.md §5.A).

Paint each line's mask a distinct translucent colour so a VLM can return text
keyed by colour name — proven on real handwriting in phase0-spike. Colour is the
primary key; colour+number is the fallback past the palette. Region granularity
(<=~8 lines/crop) is what keeps pure colour keys unique.
"""
from PIL import Image, ImageDraw, ImageFont

# ~8 reliably-nameable colours. Palette size is why crops must stay small.
PALETTE = [
    ("red", (220, 30, 30)), ("blue", (30, 80, 220)), ("green", (20, 150, 40)),
    ("orange", (240, 140, 0)), ("purple", (150, 30, 200)), ("brown", (140, 80, 20)),
    ("magenta", (230, 30, 160)), ("teal", (0, 150, 150)),
]
PALETTE_NAMES = [n for n, _ in PALETTE]


def key_for_index(i: int) -> str:
    name = PALETTE[i % len(PALETTE)][0]
    band = i // len(PALETTE)
    return name if band == 0 else f"{name}-{band + 1}"


def _rgb(key: str):
    base = key.split("-")[0]
    return dict(PALETTE).get(base, (0, 0, 0))


def render_crop(image: Image.Image, masks: list[list], margin: int = 110,
                pad: int = 12, alpha: int = 85):
    """Crop `image` to the bounding box of `masks` and paint one colour per mask.
    Returns (crop_image, {key: local_index}). Labels live in an added left margin,
    never over the ink."""
    xs = [x for m in masks for x, _ in m]
    ys = [y for m in masks for _, y in m]
    x0, y0 = max(0, min(xs) - pad), max(0, min(ys) - pad)
    x1, y1 = max(xs) + pad, max(ys) + pad
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
    for i, m in enumerate(masks):
        key = key_for_index(i)
        rgb = _rgb(key)
        pts = [(x - x0 + margin, y - y0) for x, y in m]
        d.polygon(pts, fill=rgb + (alpha,))
        d.line(pts + [pts[0]], fill=rgb + (255,), width=3)
        ty = min(p[1] for p in pts)
        d.rectangle([4, ty, margin - 8, ty + 24], fill=rgb + (255,))
        d.text((8, ty + 2), key, fill=(255, 255, 255, 255), font=font)
        key_to_idx[key] = i
    return Image.alpha_composite(canvas, ov).convert("RGB"), key_to_idx
