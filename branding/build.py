"""Generate grip's palette from first principles and check it before anyone uses it.

    python branding/build.py           # write tokens, swatch sheet and report
    python branding/build.py --check   # fail if a file is stale or a check fails

Every colour is defined in OKLCH, a perceptual space where equal steps in lightness look
like equal steps to a person, and hue stays put as chroma changes. Each family is a tonal
scale built from three numbers (hue, peak chroma, hue drift) against one shared lightness
ladder, so the same step in two families reads as the same weight. Colours that fall
outside sRGB are pulled back by lowering chroma only, never lightness or hue.

Then the palette is tested, not trusted:

* every text pairing the docs use must reach WCAG 2 contrast and an APCA Lc target;
* pass and fail must stay distinguishable under simulated protanopia, deuteranopia and
  tritanopia (Machado et al. 2009, full severity), measured as a distance in OKLab;
* the files in this folder and in ``docs/`` must match what this script would write.

No third-party code: the conversions are the published matrices, written out below.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DOCS_TOKENS = ROOT / "docs" / "stylesheets" / "tokens.css"
DOCS_BRAND = ROOT / "docs" / "assets" / "brand"

# --------------------------------------------------------------------------- colour math

Vec = tuple[float, float, float]


def _mul(m: tuple[Vec, Vec, Vec], v: Vec) -> Vec:
    return (
        m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
        m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
        m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2],
    )


def oklch_to_oklab(lch: Vec) -> Vec:
    """OKLCH (L, C, h in degrees) to OKLab."""
    lightness, chroma, hue = lch
    rad = math.radians(hue)
    return (lightness, chroma * math.cos(rad), chroma * math.sin(rad))


def oklab_to_linear(lab: Vec) -> Vec:
    """OKLab to linear sRGB (Ottosson 2020)."""
    lightness, a, b = lab
    l_ = lightness + 0.3963377774 * a + 0.2158037573 * b
    m_ = lightness - 0.1055613458 * a - 0.0638541728 * b
    s_ = lightness - 0.0894841775 * a - 1.2914855480 * b
    lms = (l_**3, m_**3, s_**3)
    return _mul(
        (
            (4.0767416621, -3.3077115913, 0.2309699292),
            (-1.2684380046, 2.6097574011, -0.3413193965),
            (-0.0041960863, -0.7034186147, 1.7076147010),
        ),
        lms,
    )


def linear_to_oklab(rgb: Vec) -> Vec:
    """Linear sRGB to OKLab."""
    lms = _mul(
        (
            (0.4122214708, 0.5363325363, 0.0514459929),
            (0.2119034982, 0.6806995451, 0.1073969566),
            (0.0883024619, 0.2817188376, 0.6299787005),
        ),
        rgb,
    )
    l_, m_, s_ = (math.copysign(abs(x) ** (1 / 3), x) for x in lms)
    return _mul(
        (
            (0.2104542553, 0.7936177850, -0.0040720468),
            (1.9779984951, -2.4285922050, 0.4505937099),
            (0.0259040371, 0.7827717662, -0.8086757660),
        ),
        (l_, m_, s_),
    )


def _encode(c: float) -> float:
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def _decode(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def in_gamut(rgb: Vec, eps: float = 1e-6) -> bool:
    """Whether linear ``rgb`` is displayable in sRGB."""
    return all(-eps <= c <= 1 + eps for c in rgb)


def gamut_map(lch: Vec) -> Vec:
    """Lower chroma until the colour fits sRGB. Lightness and hue never move."""
    lightness, chroma, hue = lch
    if in_gamut(oklab_to_linear(oklch_to_oklab(lch))):
        return lch
    lo, hi = 0.0, chroma
    for _ in range(40):
        mid = (lo + hi) / 2
        if in_gamut(oklab_to_linear(oklch_to_oklab((lightness, mid, hue)))):
            lo = mid
        else:
            hi = mid
    return (lightness, lo, hue)


def to_hex(lch: Vec) -> str:
    """OKLCH to an sRGB hex string (assumes the colour is in gamut)."""
    rgb = oklab_to_linear(oklch_to_oklab(lch))
    out = (round(min(1.0, max(0.0, _encode(c))) * 255) for c in rgb)
    return "#" + "".join(f"{c:02x}" for c in out)


def hex_to_linear(value: str) -> Vec:
    """``#rrggbb`` to linear sRGB."""
    value = value.lstrip("#")
    r, g, b = (int(value[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return (_decode(r), _decode(g), _decode(b))


def wcag(fg: str, bg: str) -> float:
    """WCAG 2 contrast ratio between two hex colours."""

    def lum(h: str) -> float:
        r, g, b = hex_to_linear(h)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    a, b = sorted((lum(fg), lum(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def apca(fg: str, bg: str) -> float:
    """APCA lightness contrast Lc (0.0.98G-4g constants). Positive: dark on light."""

    def y(h: str) -> float:
        h = h.lstrip("#")
        r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))
        val = 0.2126729 * r**2.4 + 0.7151522 * g**2.4 + 0.0721750 * b**2.4
        return val + (0.022 - val) ** 1.414 if val < 0.022 else val

    text, back = y(fg), y(bg)
    if abs(back - text) < 0.0005:
        return 0.0
    if back > text:
        sapc = (back**0.56 - text**0.57) * 1.14
        out = 0.0 if sapc < 0.1 else sapc - 0.027
    else:
        sapc = (back**0.65 - text**0.62) * 1.14
        out = 0.0 if sapc > -0.1 else sapc + 0.027
    return out * 100


CVD = {
    "protanopia": (
        (0.152286, 1.052583, -0.204868),
        (0.114503, 0.786281, 0.099216),
        (-0.003882, -0.048116, 1.051998),
    ),
    "deuteranopia": (
        (0.367322, 0.860646, -0.227968),
        (0.280085, 0.672501, 0.047413),
        (-0.011820, 0.042940, 0.968881),
    ),
    "tritanopia": (
        (1.255528, -0.076749, -0.178779),
        (-0.078411, 0.930809, 0.147602),
        (0.004733, 0.691367, 0.303900),
    ),
}
"""Machado, Oliveira and Fernandes (2009), severity 1.0, applied to linear sRGB."""


def delta_ok(a: str, b: str, deficiency: str | None = None) -> float:
    """Euclidean distance in OKLab, optionally after simulating a colour deficiency."""
    la, lb = hex_to_linear(a), hex_to_linear(b)
    if deficiency:
        m = CVD[deficiency]
        la = tuple(min(1.0, max(0.0, c)) for c in _mul(m, la))  # type: ignore[assignment]
        lb = tuple(min(1.0, max(0.0, c)) for c in _mul(m, lb))  # type: ignore[assignment]
    pa, pb = linear_to_oklab(la), linear_to_oklab(lb)
    return math.dist(pa, pb)


# --------------------------------------------------------------------------- the palette

STEPS = (50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950)
LIGHTNESS = (0.975, 0.945, 0.885, 0.815, 0.745, 0.665, 0.575, 0.49, 0.40, 0.31, 0.235)
"""One lightness ladder for every family, so step 600 weighs the same in all of them."""


@dataclass(frozen=True)
class Family:
    """A tonal scale: constant intent, perceptually even lightness."""

    name: str
    role: str
    hue: float
    """Hue at the middle of the scale, in OKLCH degrees."""
    chroma: float
    """Peak chroma, reached around step 400 to 500; real values are gamut-limited."""
    drift: float = 0.0
    """Hue change from lightest to darkest step. Light tints lean warm, deep shades cool,
    as pigments do (the Bezold-Brücke effect), which keeps tints from looking chalky."""

    def lch(self, i: int) -> Vec:
        """OKLCH of step ``i`` (0 is the lightest)."""
        t = i / (len(STEPS) - 1)
        # Chroma follows a sine bell peaking mid-scale. The ends keep about a third of
        # the peak, so deep shades stay recognisably rust, teal or plum, not grey.
        chroma = self.chroma * math.sin(math.pi * (0.12 + t * 0.76))
        hue = (self.hue + self.drift * (t - 0.5)) % 360
        return gamut_map((LIGHTNESS[i], chroma, hue))

    def scale(self) -> dict[int, str]:
        """Step to hex."""
        return {step: to_hex(self.lch(i)) for i, step in enumerate(STEPS)}


FAMILIES = (
    Family("ember", "brand, focus", hue=46, chroma=0.2, drift=-12),
    Family("sky", "links, information", hue=226, chroma=0.15, drift=-6),
    Family("jade", "pass", hue=160, chroma=0.15, drift=8),
    Family("berry", "fail, danger", hue=352, chroma=0.2, drift=8),
    Family("honey", "warning, caution", hue=96, chroma=0.16, drift=10),
    Family("graphite", "text, surfaces", hue=55, chroma=0.012, drift=0),
)
"""Ember and sky are complements, 46 and 226 degrees, exactly opposite: orange against
cyan-blue is the contrast every colour-vision deficiency keeps. Jade and berry carry pass
and fail, placed for colour-blind separation rather than symmetry, and kept apart by
lightness as well as hue. Graphite is a neutral tinted toward ember, so surfaces are warm
without looking coloured."""


def palette() -> dict[str, dict[int, str]]:
    """Every family's scale."""
    return {f.name: f.scale() for f in FAMILIES}


# Surfaces outside the 50 to 950 ladder: the page background in dark mode and pure ends.
EXTRA = {
    "graphite-975": gamut_map((0.175, 0.010, 55)),
    "chalk": gamut_map((0.988, 0.005, 75)),
}


def extras() -> dict[str, str]:
    """Named surfaces outside the scales."""
    return {name: to_hex(lch) for name, lch in EXTRA.items()}


# --------------------------------------------------------------------------- checks

TEXT_PAIRS = (
    # (name, fg, bg, min WCAG, min |APCA Lc|)
    ("light body text", "graphite-900", "chalk", 7.0, 90),
    ("light muted text", "graphite-700", "chalk", 4.5, 60),
    ("light link text", "sky-700", "chalk", 4.5, 60),
    ("light code text", "graphite-900", "graphite-100", 7.0, 75),
    ("dark body text", "graphite-100", "graphite-975", 7.0, 90),
    ("dark muted text", "graphite-300", "graphite-975", 4.5, 60),
    ("dark link text", "ember-300", "graphite-975", 4.5, 60),
    ("dark code text", "graphite-100", "graphite-900", 7.0, 75),
    ("header text", "chalk", "graphite-950", 7.0, 90),
    ("brand text on header", "ember-300", "graphite-950", 4.5, 60),
    ("brand mark on header, non-text", "ember-400", "graphite-950", 3.0, 45),
    # Buttons are 18 px bold; APCA's minimum for that size and weight is Lc 55.
    ("primary button, 18 px bold", "graphite-975", "ember-400", 4.5, 55),
    ("pass badge", "jade-800", "jade-100", 4.5, 60),
    ("fail badge", "berry-800", "berry-100", 4.5, 60),
    ("warning badge", "honey-900", "honey-100", 4.5, 60),
    ("info badge", "sky-800", "sky-100", 4.5, 60),
    ("pass text on light", "jade-700", "chalk", 4.5, 60),
    ("brand ink on light", "ember-700", "chalk", 4.5, 60),
    ("brand ink on dark", "ember-300", "graphite-975", 4.5, 60),
    ("pass text on dark", "jade-300", "graphite-975", 4.5, 60),
    ("link underline, non-text", "ember-600", "chalk", 3.0, 45),
    ("active nav item", "ember-800", "chalk", 4.5, 60),
    ("search highlight", "graphite-950", "ember-200", 7.0, 75),
    ("code: plain", "graphite-100", "graphite-950", 7.0, 75),
    ("code: string", "jade-300", "graphite-950", 4.5, 60),
    ("code: keyword", "sky-300", "graphite-950", 4.5, 60),
    ("code: number", "honey-300", "graphite-950", 4.5, 60),
    ("code: function", "ember-300", "graphite-950", 4.5, 60),
    ("code: special", "berry-300", "graphite-950", 4.5, 60),
    ("code: comment", "graphite-400", "graphite-950", 4.5, 45),
)
"""Every foreground/background pair the docs and the CLI branding rely on."""

CVD_PAIRS = (
    ("pass vs fail, on light", "jade-600", "berry-500", 0.10),
    ("pass vs fail, on dark", "jade-300", "berry-500", 0.10),
    ("pass vs warning", "jade-400", "honey-600", 0.10),
    ("info vs fail", "sky-400", "berry-500", 0.10),
    ("info vs fail, on dark", "sky-300", "berry-500", 0.10),
    ("brand vs fail", "ember-400", "berry-600", 0.10),
    ("brand vs info", "ember-400", "sky-600", 0.10),
)
"""Pairs that carry meaning by colour. Minimum OKLab distance under every deficiency;
0.02 is roughly one just-noticeable difference, so 0.10 is five of them. Where two hues
sit close for a colour-blind eye (jade and berry, ember and berry), the pair is taken from
different steps so lightness keeps them apart, which survives every deficiency."""


def resolve(token: str, colours: dict[str, str]) -> str:
    """``ember-400`` or ``chalk`` to hex."""
    return colours[token]


def flat(pal: dict[str, dict[int, str]]) -> dict[str, str]:
    """``{family-step: hex}`` plus the extra surfaces."""
    out = {f"{name}-{step}": hx for name, scale in pal.items() for step, hx in scale.items()}
    out.update(extras())
    return out


@dataclass(frozen=True)
class Result:
    """One check: what, the measured value, the bar, pass or fail."""

    kind: str
    name: str
    detail: str
    ok: bool


def run_checks(colours: dict[str, str]) -> list[Result]:
    """Contrast and colour-vision checks, in report order."""
    results: list[Result] = []
    for name, fg, bg, min_wcag, min_apca in TEXT_PAIRS:
        ratio = wcag(colours[fg], colours[bg])
        lc = abs(apca(colours[fg], colours[bg]))
        ok = ratio >= min_wcag and lc >= min_apca
        detail = f"`{fg}` on `{bg}`: {ratio:.2f}:1 (min {min_wcag}), Lc {lc:.0f} (min {min_apca})"
        results.append(Result("contrast", name, detail, ok))
    for name, a, b, minimum in CVD_PAIRS:
        worst = min(
            [delta_ok(colours[a], colours[b])] + [delta_ok(colours[a], colours[b], d) for d in CVD]
        )
        parts = ", ".join(f"{d} {delta_ok(colours[a], colours[b], d):.3f}" for d in CVD)
        detail = f"`{a}` vs `{b}`: normal {delta_ok(colours[a], colours[b]):.3f}, {parts}"
        results.append(Result("colour vision", name, detail, worst >= minimum))
    return results


# --------------------------------------------------------------------------- the mark

RING_SWEEP = 315
"""Degrees of the ring that are drawn. 315 of 360 is 87.5: a pass, never a perfect one."""


def _ring_path(cx: float, cy: float, r: float) -> str:
    """A ``G`` drawn as an open score ring plus the bar that closes it inward."""
    start = math.radians(-(RING_SWEEP - 270))  # upper right, the ring runs anticlockwise
    sx, sy = cx + r * math.cos(start), cy + r * math.sin(start)
    return f"M{sx:.2f} {sy:.2f} A{r} {r} 0 1 0 {cx + r:.2f} {cy:.2f} H{cx + r * 0.12:.2f}"


def mark_svg(stroke: str, tile: str | None, size: int = 64) -> str:
    """The grip mark on a rounded tile, or bare when ``tile`` is ``None``."""
    c = size / 2
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 {size} {size}" role="img" aria-label="grip">'
    ]
    if tile:
        parts.append(f'<rect width="{size}" height="{size}" rx="{size * 0.24:.1f}" fill="{tile}"/>')
    r = size * (0.25 if tile else 0.36)
    width = size * (0.11 if tile else 0.15)
    parts.append(
        f'<path d="{_ring_path(c, c, r)}" fill="none" stroke="{stroke}" '
        f'stroke-width="{width:.1f}" stroke-linecap="round" stroke-linejoin="round"/>'
    )
    parts.append("</svg>")
    return "".join(parts) + "\n"


def marks(colours: dict[str, str]) -> dict[str, str]:
    """Every variant of the mark, by file name."""
    ember, ink, chalk = colours["ember-400"], colours["graphite-950"], colours["chalk"]
    return {
        "mark.svg": mark_svg(ember, ink),
        "mark-light.svg": mark_svg(ink, ember),
        "mark-bare-ember.svg": mark_svg(ember, None),
        "mark-bare-ink.svg": mark_svg(ink, None),
        "mark-bare-chalk.svg": mark_svg(chalk, None),
        "favicon.svg": mark_svg(ember, ink),
    }


# --------------------------------------------------------------------------- outputs


def tokens_json(pal: dict[str, dict[int, str]]) -> str:
    """Tokens for tools: hex plus the OKLCH they came from."""
    data: dict[str, object] = {
        "$description": "grip colour tokens. Generated by branding/build.py; do not edit.",
        "families": {
            f.name: {
                "role": f.role,
                "steps": {
                    str(step): {
                        "hex": pal[f.name][step],
                        "oklch": [round(v, 4) for v in f.lch(i)],
                    }
                    for i, step in enumerate(STEPS)
                },
            }
            for f in FAMILIES
        },
        "surfaces": {
            name: {"hex": hx, "oklch": [round(v, 4) for v in EXTRA[name]]}
            for name, hx in extras().items()
        },
    }
    return json.dumps(data, indent=2) + "\n"


def tokens_css(pal: dict[str, dict[int, str]]) -> str:
    """CSS custom properties, ``--grip-ember-400`` and so on."""
    lines = ["/* grip colour tokens. Generated by branding/build.py; do not edit. */", ":root {"]
    for f in FAMILIES:
        lines.append(f"  /* {f.name}: {f.role} */")
        for i, step in enumerate(STEPS):
            lightness, chroma, hue = f.lch(i)
            lines.append(
                f"  --grip-{f.name}-{step}: {pal[f.name][step]};"
                f" /* oklch({lightness:.3f} {chroma:.3f} {hue:.1f}) */"
            )
    for name, hx in extras().items():
        lines.append(f"  --grip-{name}: {hx};")
    lines.append("}")
    return "\n".join(lines) + "\n"


def swatches_svg(pal: dict[str, dict[int, str]]) -> str:
    """A swatch sheet: one row per family, step and hex under each chip."""
    cell, gap, label_w, top = 76, 8, 150, 56
    width = label_w + len(STEPS) * (cell + gap) + 24
    height = top + len(FAMILIES) * (cell + 44) + 16
    bg, ink, muted = extras()["chalk"], pal["graphite"][900], pal["graphite"][600]
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="Instrument Sans, Inter, '
        f'system-ui, sans-serif">',
        f'<rect width="100%" height="100%" fill="{bg}"/>',
        f'<text x="24" y="34" font-size="20" font-weight="700" fill="{ink}">grip palette</text>',
        f'<text x="{width - 24}" y="34" font-size="12" fill="{muted}" text-anchor="end">'
        "OKLCH tonal scales, one lightness ladder</text>",
    ]
    for row, f in enumerate(FAMILIES):
        y = top + row * (cell + 44)
        out.append(
            f'<text x="24" y="{y + 34}" font-size="15" font-weight="700" fill="{ink}">'
            f"{f.name}</text>"
        )
        out.append(f'<text x="24" y="{y + 52}" font-size="11" fill="{muted}">{f.role}</text>')
        for col, step in enumerate(STEPS):
            x = label_w + col * (cell + gap)
            hx = pal[f.name][step]
            out.append(
                f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="12" fill="{hx}"/>'
            )
            out.append(
                f'<text x="{x}" y="{y + cell + 15}" font-size="11" font-weight="700" '
                f'fill="{ink}">{step}</text>'
            )
            out.append(
                f'<text x="{x}" y="{y + cell + 29}" font-size="10" fill="{muted}" '
                f'font-family="JetBrains Mono, ui-monospace, monospace">{hx}</text>'
            )
    out.append("</svg>")
    return "\n".join(out) + "\n"


def report(pal: dict[str, dict[int, str]], results: list[Result]) -> str:
    """``palette.md``: the scales and every check, for humans."""
    lines = [
        "# Palette report",
        "",
        "Generated by `python branding/build.py`. Do not edit by hand.",
        "",
        "![grip palette](palette.svg)",
        "",
        "## Scales",
        "",
        "| family | role | " + " | ".join(str(s) for s in STEPS) + " |",
        "|---|---|" + "---|" * len(STEPS),
    ]
    for f in FAMILIES:
        cells = " | ".join(f"`{pal[f.name][s]}`" for s in STEPS)
        lines.append(f"| {f.name} | {f.role} | {cells} |")
    lines += ["", "Surfaces: " + ", ".join(f"`{k}` `{v}`" for k, v in extras().items()), ""]
    lines += ["## Checks", "", "| | check | kind | measured |", "|---|---|---|---|"]
    for r in results:
        lines.append(f"| {'pass' if r.ok else '**FAIL**'} | {r.name} | {r.kind} | {r.detail} |")
    passed = sum(r.ok for r in results)
    lines += ["", f"{passed} of {len(results)} checks pass.", ""]
    return "\n".join(lines)


def outputs() -> tuple[dict[Path, str], list[Result]]:
    """Every generated file and the check results."""
    pal = palette()
    colours = flat(pal)
    results = run_checks(colours)
    css = tokens_css(pal)
    sheet = swatches_svg(pal)
    files = {
        HERE / "tokens.json": tokens_json(pal),
        HERE / "tokens.css": css,
        HERE / "palette.svg": sheet,
        HERE / "palette.md": report(pal, results),
        DOCS_TOKENS: css,
        DOCS_BRAND / "palette.svg": sheet,
    }
    for name, svg in marks(colours).items():
        files[HERE / "logo" / name] = svg
        files[DOCS_BRAND / name] = svg
    return files, results


def main(argv: list[str] | None = None) -> int:
    """Write or check the generated files; exit 1 on a stale file or a failed check."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="verify, write nothing")
    args = parser.parse_args(argv)
    files, results = outputs()
    failed = [r for r in results if not r.ok]
    for r in failed:
        print(f"FAIL {r.kind}: {r.name}: {r.detail}", file=sys.stderr)
    stale = []
    for path, text in files.items():
        current = path.read_text("utf-8") if path.exists() else None
        if current == text:
            continue
        if args.check:
            stale.append(path)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, "utf-8")
            print(f"wrote {path.relative_to(ROOT)}")
    for path in stale:
        print(f"stale: {path.relative_to(ROOT)} (run python branding/build.py)", file=sys.stderr)
    print(f"{len(results) - len(failed)} of {len(results)} palette checks pass")
    return 1 if failed or stale else 0


if __name__ == "__main__":
    sys.exit(main())
