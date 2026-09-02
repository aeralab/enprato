"""Generate minimal black/white Enprato home-screen icons."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "backend" / "static" / "icons"
OUT.mkdir(parents=True, exist_ok=True)


def draw_mark(size: int) -> Image.Image:
    """Black square + white geometric E (iOS home-screen safe margins)."""
    img = Image.new("RGB", (size, size), "#000000")
    draw = ImageDraw.Draw(img)
    m = int(size * 0.22)
    stroke = max(3, int(size * 0.095))
    left = m
    right = size - m
    top = m
    bottom = size - m
    mid_y = (top + bottom) // 2

    draw.rectangle([left, top, left + stroke, bottom], fill="#FFFFFF")
    draw.rectangle([left, top, right, top + stroke], fill="#FFFFFF")
    draw.rectangle([left, mid_y - stroke // 2, left + int((right - left) * 0.78), mid_y + (stroke - stroke // 2)], fill="#FFFFFF")
    draw.rectangle([left, bottom - stroke, right, bottom], fill="#FFFFFF")
    return img


def main() -> None:
    for size in (180, 192, 512):
        path = OUT / f"enprato-{size}.png"
        draw_mark(size).save(path, format="PNG", optimize=True)
        print("wrote", path)


if __name__ == "__main__":
    main()
