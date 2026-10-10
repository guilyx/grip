"""The grip brand for the recording scripts: colours, fonts and the mark, in Pillow terms.

Colours come from ``branding/tokens.json``, which ``branding/build.py`` generates and
checks, so the videos can never drift from the docs. Fonts are the brand's three Google
Fonts, fetched once as TTF into ``build/fonts`` (git-ignored); without network access the
scripts fall back to DejaVu and say so.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
from functools import cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
TOKENS = ROOT / "branding" / "tokens.json"
CACHE = ROOT / "build" / "fonts"
DEJAVU = Path("/usr/share/fonts/truetype/dejavu")

# kind: (Google Fonts css2 family spec, fallback DejaVu file)
FONTS = {
    "display": ("Archivo:wdth,wght@112.5,900", "DejaVuSans-Bold.ttf"),
    "display-800": ("Archivo:wdth,wght@112.5,800", "DejaVuSans-Bold.ttf"),
    "text": ("Instrument+Sans:wght@400", "DejaVuSans.ttf"),
    "text-bold": ("Instrument+Sans:wght@600", "DejaVuSans-Bold.ttf"),
    "mono": ("JetBrains+Mono:wght@400", "DejaVuSansMono.ttf"),
    "mono-bold": ("JetBrains+Mono:wght@700", "DejaVuSansMono-Bold.ttf"),
}


@cache
def _tokens() -> dict[str, str]:
    data = json.loads(TOKENS.read_text("utf-8"))
    out = {
        f"{name}-{step}": spec["hex"]
        for name, fam in data["families"].items()
        for step, spec in fam["steps"].items()
    }
    out.update({name: spec["hex"] for name, spec in data["surfaces"].items()})
    return out


def colour(token: str) -> str:
    """``ember-400``, ``graphite-975`` or ``chalk`` as a hex string."""
    return _tokens()[token]


@cache
def font_path(kind: str) -> Path:
    """A TTF for ``kind``, downloaded on first use; DejaVu if that fails."""
    spec, fallback = FONTS[kind]
    target = CACHE / f"{kind}.ttf"
    if target.exists():
        return target
    try:
        css = subprocess.run(
            ["curl", "-fsSL", f"https://fonts.googleapis.com/css2?family={spec}"],
            capture_output=True, text=True, check=True, timeout=30,
        ).stdout  # fmt: skip
        match = re.search(r"url\((https://[^)]+\.ttf)\)", css)
        if not match:
            raise ValueError("no TTF in the stylesheet")
        CACHE.mkdir(parents=True, exist_ok=True)
        subprocess.run(["curl", "-fsSL", "-o", str(target), match.group(1)], check=True, timeout=60)
        return target
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"brandkit: could not fetch {kind} ({exc}); using {fallback}")
        return DEJAVU / fallback


def font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    """The brand font ``kind`` at ``size`` pixels."""
    return ImageFont.truetype(str(font_path(kind)), size)


def draw_mark(
    d: ImageDraw.ImageDraw, cx: float, cy: float, size: float, *, tile: bool = True
) -> None:
    """The grip mark, a 315 degree ring closed inward into a G, centred on ``(cx, cy)``.

    Same geometry as ``branding/build.py``: on a 64 unit tile, radius 16 and stroke 7.
    """
    unit = size / 64
    if tile:
        half = size / 2
        d.rounded_rectangle(
            (cx - half, cy - half, cx + half, cy + half), radius=15.4 * unit,
            fill=colour("graphite-950"),
        )  # fmt: skip
    r, w = 16 * unit, 7 * unit
    ink = colour("ember-400")
    # Pillow draws arcs clockwise from 0 degrees at three o'clock: 0 to 315 is the ring.
    d.arc((cx - r - w / 2, cy - r - w / 2, cx + r + w / 2, cy + r + w / 2), 0, 315, fill=ink,
          width=round(w))  # fmt: skip
    d.line((cx + r * 0.12, cy, cx + r, cy), fill=ink, width=round(w))
    end = math.radians(315)
    for x, y in ((cx + r, cy), (cx + r * math.cos(end), cy + r * math.sin(end)),
                 (cx + r * 0.12, cy)):  # fmt: skip
        d.ellipse((x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill=ink)


def mark_image(size: int, *, tile: bool = True, scale: int = 4) -> Image.Image:
    """The mark as an anti-aliased RGBA image, drawn at ``scale`` times and reduced."""
    big = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
    draw_mark(ImageDraw.Draw(big), size * scale / 2, size * scale / 2, size * scale, tile=tile)
    return big.resize((size, size), Image.Resampling.LANCZOS)


def dot_grid(d: ImageDraw.ImageDraw, width: int, height: int, step: int = 28) -> None:
    """The faint dot grid used behind brand surfaces."""
    ink = colour("graphite-900")
    for y in range(step // 2, height, step):
        for x in range(step // 2, width, step):
            d.ellipse((x - 1.2, y - 1.2, x + 1.2, y + 1.2), fill=ink)


__all__ = ["colour", "dot_grid", "draw_mark", "font", "font_path", "mark_image"]
