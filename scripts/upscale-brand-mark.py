"""Recreate Enprato brand mark at high resolution (width >= 600)."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT_DIR = Path(__file__).resolve().parents[1] / "backend" / "static" / "icons"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def rounded_rect(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], radius: int, fill) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def draw_e(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], fill) -> None:
    left, top, right, bottom = box
    w = right - left
    h = bottom - top
    stroke = max(8, int(min(w, h) * 0.145))
    mid_y = (top + bottom) // 2
    # stem
    draw.rectangle([left, top, left + stroke, bottom], fill=fill)
    # bars
    draw.rectangle([left, top, right, top + stroke], fill=fill)
    draw.rectangle([left, mid_y - stroke // 2, left + int(w * 0.78), mid_y + stroke - stroke // 2], fill=fill)
    draw.rectangle([left, bottom - stroke, right, bottom], fill=fill)


def make(size: int = 1024) -> Image.Image:
    img = Image.new("RGB", (size, size))
    px = img.load()
    # diagonal split: top-left yellow, bottom-right pink
    yellow = (255, 214, 10)
    pink = (255, 45, 140)
    for y in range(size):
        for x in range(size):
            # diagonal from top-left to bottom-right
            px[x, y] = yellow if (x + y) < size else pink

    draw = ImageDraw.Draw(img)

    # icon square
    icon = int(size * 0.46)
    cx = size // 2
    cy = int(size * 0.42)
    x0 = cx - icon // 2
    y0 = cy - icon // 2
    x1 = x0 + icon
    y1 = y0 + icon
    radius = max(28, int(icon * 0.18))
    # subtle light border
    pad = max(2, size // 256)
    rounded_rect(draw, (x0 - pad, y0 - pad, x1 + pad, y1 + pad), radius + pad, (210, 210, 215))
    rounded_rect(draw, (x0, y0, x1, y1), radius, (8, 8, 10))

    # metallic-ish E (light gray/white)
    inset = int(icon * 0.22)
    draw_e(draw, (x0 + inset, y0 + inset, x1 - inset, y1 - inset), (236, 236, 240))

    # brand text
    text = "Enprato"
    font = None
    for candidate in (
        r"C:\Windows\Fonts\segoeuib.ttf",
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\arial.ttf",
    ):
        try:
            font = ImageFont.truetype(candidate, size=max(36, int(size * 0.085)))
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = (size - tw) // 2
    ty = y1 + int(size * 0.06)
    # slight shadow for readability on pink
    draw.text((tx + 2, ty + 2), text, font=font, fill=(120, 20, 70))
    draw.text((tx, ty), text, font=font, fill=(255, 255, 255))
    return img


def main() -> None:
    for size in (600, 1024, 1440):
        path = OUT_DIR / f"enprato-brand-{size}.png"
        make(size).save(path, format="PNG", optimize=True)
        print("wrote", path, size)


if __name__ == "__main__":
    main()
