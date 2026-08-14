#!/usr/bin/env python3
"""Generate Shoppa's PWA icons.

Shoppa's mark is a white shopping **basket** on a green rounded square — a sibling
to Sugar Daddy's blue-square/white-drop and Naggy's amber-square/white-bell icons,
but clearly its own thing (different hue, different glyph). Everything is drawn
from primitives at 4x supersampling and downscaled with LANCZOS for clean
anti-aliased edges, so the icons are fully reproducible: re-run this script to
regenerate them.

    python tools/make_icons.py

Writes into shoppa/static/icons/: icon-192.png, icon-512.png,
icon-maskable-512.png (full-bleed, glyph in the safe zone) and
apple-touch-icon.png (180px, opaque square).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "shoppa" / "static" / "icons"
SS = 4  # supersample factor

GREEN_TOP = (87, 217, 163)    # #57d9a3
GREEN_BOT = (32, 166, 122)    # #20a67a
WHITE = (255, 255, 255, 255)


def _draw_basket(draw: ImageDraw.ImageDraw, box: tuple[float, float, float]) -> None:
    """Shopping basket silhouette in a 0..100 unit box mapped into `box`."""
    bx, by, g = box

    def u(x: float) -> float:
        return bx + x / 100.0 * g

    def v(y: float) -> float:
        return by + y / 100.0 * g

    def s(n: float) -> float:
        return n / 100.0 * g

    # Handle: the top half of a circle, stroked. Drawn first so the rim below
    # covers where its two ends stop.
    draw.arc(
        [u(30), v(8), u(70), v(52)],
        start=180, end=360, fill=WHITE, width=int(round(s(7))),
    )
    # Rim — the lip the handle hangs from, wider than the body.
    draw.rounded_rectangle([u(12), v(38), u(88), v(52)], radius=s(6), fill=WHITE)
    # Body: a trapezoid narrowing towards the base, with the bottom corners
    # softened by a rounded rect drawn over the same footprint.
    draw.polygon([(u(19), v(50)), (u(81), v(50)), (u(71), v(88)), (u(29), v(88))], fill=WHITE)
    draw.rounded_rectangle([u(29), v(78), u(71), v(90)], radius=s(6), fill=WHITE)


def _gradient_square(px: int) -> Image.Image:
    """Vertical green gradient, fully opaque."""
    grad = Image.new("RGB", (1, px))
    for y in range(px):
        t = y / max(px - 1, 1)
        grad.putpixel(
            (0, y),
            tuple(round(GREEN_TOP[i] + (GREEN_BOT[i] - GREEN_TOP[i]) * t) for i in range(3)),
        )
    return grad.resize((px, px))


def make(size: int, *, rounded: bool, glyph_frac: float, opaque: bool) -> Image.Image:
    px = size * SS
    canvas = _gradient_square(px).convert("RGBA")

    if rounded:
        mask = Image.new("L", (px, px), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [0, 0, px - 1, px - 1], radius=int(px * 0.22), fill=255
        )
        canvas.putalpha(mask)

    # centre the glyph box
    g = px * glyph_frac
    _draw_basket(ImageDraw.Draw(canvas), ((px - g) / 2, (px - g) / 2, g))

    out = canvas.resize((size, size), Image.LANCZOS)
    if opaque:
        flat = Image.new("RGB", (size, size), GREEN_BOT)
        flat.paste(out, (0, 0), out)
        out = flat
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    make(192, rounded=True, glyph_frac=0.66, opaque=False).save(OUT / "icon-192.png")
    make(512, rounded=True, glyph_frac=0.66, opaque=False).save(OUT / "icon-512.png")
    # maskable: full-bleed, glyph kept well inside the safe zone
    make(512, rounded=False, glyph_frac=0.56, opaque=True).save(OUT / "icon-maskable-512.png")
    # apple: opaque square (iOS applies its own mask)
    make(180, rounded=False, glyph_frac=0.64, opaque=True).save(OUT / "apple-touch-icon.png")
    print(f"wrote 4 icons to {OUT}")


if __name__ == "__main__":
    main()
