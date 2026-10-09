# grip identity

grip asks one thing of a developer: can you explain what you are about to ship? The
identity says the same thing with as little as possible. One mark, one loud colour, a
quiet neutral, a typeface with grip of its own, and numbers you can check.

![grip lockup](logo/lockup-dark.png)

## The idea

**A grip is a hold that is nearly closed.** The mark is a score ring drawn to 315 of 360
degrees, 87.5 percent, a pass but never a perfect one. A short bar turns the ring inward
into a **G**. The gap is the point: understanding is something you keep doing, not a box
you tick once.

The ring comes back everywhere the score does. The social card, the docs homepage and
the CLI's score all draw it the same way: an ember arc on a dark track, starting at twelve
o'clock and running clockwise.

## Mark

| File | Use it on |
| --- | --- |
| [`logo/mark.svg`](logo/mark.svg) | anything. Ember on a graphite tile. The default and the favicon. |
| [`logo/mark-light.svg`](logo/mark-light.svg) | dark or busy places where the tile needs to glow: graphite on ember |
| [`logo/mark-bare-ember.svg`](logo/mark-bare-ember.svg) | dark surfaces, no tile |
| [`logo/mark-bare-ink.svg`](logo/mark-bare-ink.svg) | light surfaces, no tile |
| [`logo/mark-bare-chalk.svg`](logo/mark-bare-chalk.svg) | photos and dark colour fields |
| [`logo/lockup-dark.png`](logo/lockup-dark.png), [`-light`](logo/lockup-light.png), [`-ember`](logo/lockup-ember.png) | mark and wordmark together |
| [`logo/og-card.png`](logo/og-card.png) | link previews, 1200 by 630 |

- **Construction.** On the 64 unit tile: ring radius 16, stroke 7, round caps, corner
  radius 15.4. The ring starts at 315 degrees and runs anticlockwise to 0; the bar runs
  from 0 degrees to just right of centre. `build.py` draws it, so it is never redrawn by
  hand.
- **Clear space.** Half the tile width on every side.
- **Smallest size.** 16 pixels with the tile, 20 without.
- **Don't** rotate it, outline it, put ember on white without the tile, close the ring, or
  set the wordmark in anything but Archivo.

## Colour

![palette](palette.svg)

Six families, each an eleven-step tonal scale from 50 to 950.

| Family | Role | Signature step |
| --- | --- | --- |
| **ember** | brand, focus | `ember-400` `#ff8644` |
| **sky** | links on light, information | `sky-700` |
| **jade** | pass | `jade-600` on light, `jade-300` on dark |
| **berry** | fail, danger | `berry-500` |
| **honey** | warning | `honey-600` and darker |
| **graphite** | text and surfaces | `graphite-950` header, `chalk` page |

**Why ember.** Developer tools live in blue and purple, and lime on ink is taken. Ember is
the orange of safety gear and of a climbing hold you can spot from the ground: hot enough
to say "look here", not red enough to say "error". It is also a hard colour to use well,
because orange cannot be both light and saturated, which is why the scale and the checks
below exist.

### How the palette is made

[`build.py`](build.py) generates every colour. Nothing is picked from a colour wheel.

1. **OKLCH, not HSL.** In OKLCH equal lightness steps look equal to a person, and hue
   does not wander as chroma changes. HSL's "50 % lightness" yellow and blue differ by a
   factor of eight in luminance; OKLCH's do not.
2. **One lightness ladder.** Every family uses the same eleven lightness values, so
   `ember-600` and `berry-600` weigh the same on the page and swap without re-checking
   contrast.
3. **A chroma bell.** Chroma peaks mid-scale and keeps about a third at both ends, so
   pale tints are not chalky and deep shades are rust, teal, forest and plum, not grey.
4. **Hue drift.** Light steps lean toward yellow and dark steps away from it, as real
   pigments do (the Bezold-Brücke shift): ember's tints are apricot, its shades rust.
5. **Gamut mapping by chroma only.** When a colour falls outside sRGB, chroma drops until
   it fits. Lightness and hue never move, so the ladder stays true.
6. **Complements for the brand.** Ember and sky sit at 46 and 226 degrees, exactly
   opposite. Orange against cyan-blue is the one contrast every colour-vision deficiency
   keeps. Jade (160) and berry (352) carry pass and fail. Graphite is a neutral tinted
   toward ember (hue 55, chroma 0.012), so surfaces are warm without looking coloured.

### What the palette has to pass

`build.py` refuses to write a palette that fails any of these. The full table with every
measured value is in [`palette.md`](palette.md).

- **Contrast, twice.** Every text pairing the docs use must clear a WCAG 2 ratio (7:1 for
  body text, 4.5:1 for the rest) and an APCA lightness contrast (Lc 90 for body text,
  60 for interface text, 55 for the 18 px bold button label). WCAG 2 is the legal bar;
  APCA is the better predictor of what people can actually read.
- **Colour vision.** Pass and fail, pass and warning, info and fail, brand and fail,
  brand and info must stay at least 0.10 apart in OKLab (about five just-noticeable
  differences) under simulated protanopia, deuteranopia and tritanopia (Machado 2009,
  full severity).

Three decisions came straight out of those checks:

- Pass has its own colour. Ember is the brand, but orange reads as "warning", and against
  berry it nearly vanished for deuteranopes. Jade does the job.
- Where two hues sit close for a colour-blind eye, the pair is taken from different
  steps, so lightness keeps them apart: pass on dark is `jade-300` against `berry-500`,
  info is `sky-400` against `berry-500`.
- The primary button puts `graphite-975` text on `ember-400`, at 18 px bold. Orange this
  vivid is only mid-light, so the darkest surface is the one dark enough for it.

### Semantic rules

- **Never colour alone.** A score always says PASS or FAIL in words next to the colour.
- On light surfaces: pass `jade-600`, fail `berry-500`, warning `honey-700`, info `sky-700`.
- On dark surfaces: pass `jade-300`, fail `berry-500`, warning `honey-400`, info `sky-300`.
- Badges: the 100 step as background, the 800 or 900 step as text.
- Ember is for the brand, focus and links, never for a state. On a light page it appears
  as a tile, a highlight or a thick underline; `ember-700` is the lightest step allowed
  as text there.

## Type

| Role | Face | Setting |
| --- | --- | --- |
| Display, wordmark | [Archivo](https://fonts.google.com/specimen/Archivo) | 900 for the wordmark, 700 to 800 for headings, width 112, tracking -0.035em |
| Text | [Instrument Sans](https://fonts.google.com/specimen/Instrument+Sans) | 400 and 600, line height 1.6 |
| Code, numbers in UI | [JetBrains Mono](https://fonts.google.com/specimen/JetBrains+Mono) | 500 |

Archivo is a grotesque with a width axis. Set slightly wide and very heavy it is sturdy,
a little industrial, and its single-storey **g** with the flat ear gives the wordmark a
shape no default sans has. Instrument Sans is calm and narrow enough for long reference
pages. All three are open source (SIL OFL) and served by Google Fonts.

## Voice

- **We, not I.** grip is a shared habit, not a scold.
- **Plain and short.** One idea per sentence. The README, the CLI and the docs all read
  like a colleague explaining, not like marketing.
- **Honest about limits.** We say what is measured and what is not
  ([honest numbers](../docs/honest-numbers.md)). Never claim more than the evals show.
- **No blame.** "Re-read the diff" rather than "you failed".

## Files and regeneration

```bash
python branding/build.py            # tokens.css, tokens.json, palette.svg, palette.md, the marks
python branding/build.py --check    # what CI runs: stale files or failed checks exit 1
node branding/render.mjs            # lockups and the social card (needs Playwright + Chromium)
```

`build.py` also writes `docs/stylesheets/tokens.css` and the marks under
`docs/assets/brand/`, so the docs site always uses the generated palette. Edit the
families in `build.py`, never the generated files.
