"""Build the launch assets from the recorded demo.

    python scripts/launch/make_launch.py

Writes to ``docs/assets``:

* ``demo.mp4``: the demo as H.264, for LinkedIn, X and anything that dislikes WebM.
* ``launch/grip-producthunt-1270x760.png``: Product Hunt gallery image.
* ``launch/grip-thumbnail-240.png`` and ``-480.png``: the mark, square.
* ``launch/grip-launch-1080p.mp4``: an 84 second launch video, title cards around the demo,
  with a silent stereo track so upload sites treat it as a normal video.

Needs the ``demo`` extra (Pillow, imageio-ffmpeg) and ``docs/assets/demo.webm`` from
``scripts/demo/make_demo.py``. Colours, fonts and the mark come from
``scripts/demo/brandkit.py``, so everything matches the docs and ``branding/``.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "demo"))

from brandkit import colour, dot_grid, mark_image  # noqa: E402
from brandkit import font as brand_font  # noqa: E402

ASSETS = ROOT / "docs" / "assets"
OUT = ASSETS / "launch"
DEMO = ASSETS / "demo.webm"
DEMO_MP4 = ASSETS / "demo.mp4"

# Frames of the demo where the two score panels are fully drawn.
FAIL_AT, PASS_AT = 16.0, 49.0
DEMO_SECONDS = 54.0

BG = colour("graphite-975")
CARD = colour("graphite-950")
FG = colour("graphite-50")
DIM = colour("graphite-300")
MUTED = colour("graphite-500")
EMBER = colour("ember-400")
CODE = colour("ember-300")
PASS = colour("jade-300")
FAIL = colour("berry-300")

# Launch kinds to brand fonts: Archivo for display, Instrument Sans for text.
KINDS = {"bold": "display-800", "regular": "text", "strong": "text-bold", "mono": "mono",
         "wordmark": "display"}  # fmt: skip


def font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    """The brand font for ``kind`` at ``size`` pixels."""
    return brand_font(KINDS[kind], size)


def fit(
    d: ImageDraw.ImageDraw, text: str, kind: str, size: int, width: float
) -> ImageFont.FreeTypeFont:
    """``font(kind, size)``, shrunk until ``text`` fits in ``width`` pixels."""
    f = font(kind, size)
    while size > 8 and d.textlength(text, font=f) > width:
        size -= 1
        f = font(kind, size)
    return f


def find_ffmpeg() -> str:
    """An ffmpeg with libx264: imageio-ffmpeg's bundled build, else one on PATH."""
    try:
        import imageio_ffmpeg  # noqa: PLC0415 - optional dependency

        return str(imageio_ffmpeg.get_ffmpeg_exe())
    except ImportError:
        pass
    found = shutil.which("ffmpeg")
    if found:
        return found
    raise SystemExit("ffmpeg not found: pip install imageio-ffmpeg, or install ffmpeg")


FF = find_ffmpeg()


def ffmpeg(*args: str) -> None:
    """Run ffmpeg quietly, overwriting outputs."""
    subprocess.run([FF, "-y", "-hide_banner", "-loglevel", "error", *args], check=True)


# --------------------------------------------------------------------------- drawing helpers


def surface(size: tuple[int, int]) -> Image.Image:
    """The brand surface: graphite-975 with the faint dot grid."""
    img = Image.new("RGB", size, BG)
    dot_grid(ImageDraw.Draw(img), *size, step=max(28, size[0] // 60))
    return img


def rounded(img: Image.Image, radius: int) -> Image.Image:
    """``img`` with rounded corners (alpha)."""
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, img.width - 1, img.height - 1), radius, fill=255)
    out = img.convert("RGBA")
    out.putalpha(mask)
    return out


def shadowed(canvas: Image.Image, card: Image.Image, xy: tuple[int, int], blur: int = 28) -> None:
    """Paste ``card`` on ``canvas`` with a soft drop shadow."""
    x, y = xy
    shadow = Image.new("RGBA", (card.width + blur * 4, card.height + blur * 4), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (blur * 2, blur * 2 + 10, blur * 2 + card.width, blur * 2 + card.height + 10),
        18,
        fill=(0, 0, 0, 170),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(blur))
    canvas.alpha_composite(shadow, (x - blur * 2, y - blur * 2))
    canvas.alpha_composite(card, (x, y))


def label(
    d: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, color: str, f: ImageFont.FreeTypeFont
) -> None:
    """A small outlined tag."""
    x, y = xy
    tw = d.textlength(text, font=f)
    d.rounded_rectangle((x, y, x + tw + 22, y + f.size + 14), 8, fill=CARD, outline=color, width=2)
    d.text((x + 11, y + 7 - f.getbbox(text)[1]), text, fill=color, font=f)


def centred(
    d: ImageDraw.ImageDraw,
    y: float,
    text: str,
    color: str,
    f: ImageFont.FreeTypeFont,
    *,
    width: int,
) -> None:
    """Draw ``text`` centred horizontally at ``y``."""
    d.text(((width - d.textlength(text, font=f)) / 2, y), text, fill=color, font=f)


# --------------------------------------------------------------------------- assets


def demo_frames(workdir: Path) -> tuple[Image.Image, Image.Image]:
    """The blocked and passed frames of the demo."""
    fail, pas = workdir / "fail.png", workdir / "pass.png"
    ffmpeg("-ss", str(FAIL_AT), "-i", str(DEMO), "-frames:v", "1", str(fail))
    ffmpeg("-ss", str(PASS_AT), "-i", str(DEMO), "-frames:v", "1", str(pas))
    return Image.open(fail), Image.open(pas)


def demo_mp4() -> Path:
    """The demo as H.264 MP4 with a faststart header."""
    ffmpeg(
        "-i", str(DEMO),
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "high", "-crf", "18",
        "-preset", "slow", "-r", "24", "-movflags", "+faststart", "-an", str(DEMO_MP4),
    )  # fmt: skip
    return DEMO_MP4


def gallery(fail_frame: Image.Image, pass_frame: Image.Image) -> Path:
    """Product Hunt gallery image: pitch on the left, the two real score panels on the right."""
    w, h = 1270, 760
    img = surface((w, h)).convert("RGBA")
    scale = 0.64
    fail = fail_frame.crop((10, 480, 890, 590))
    pas = pass_frame.crop((10, 222, 890, 588))
    fail = fail.resize((int(fail.width * scale), int(fail.height * scale)), Image.LANCZOS)
    pas = pas.resize((int(pas.width * scale), int(pas.height * scale)), Image.LANCZOS)
    cx = w - fail.width - 48
    column = cx - 72 - 48

    x = 72
    img.alpha_composite(mark_image(84), (x, 92))
    d = ImageDraw.Draw(img)
    d.text((x + 108, 84), "grip", fill=FG, font=font("wordmark", 92))
    d.text((x, 206), "keep a grip on your code", fill=EMBER, font=font("strong", 26))
    y = 270
    head = ("A git hook that quizzes", "you on your own diff", "before you push.")
    hf = fit(d, max(head, key=len), "bold", 42, column)
    for line in head:
        d.text((x, y), line, fill=FG, font=hf)
        y += hf.size + 12
    y += 24
    for line in ("5 questions. A score out of 100.", "Under the pass mark, the push is blocked."):
        d.text((x, y), line, fill=DIM, font=font("regular", 24))
        y += 34
    y += 26
    install = "curl -fsSL guilyx.github.io/grip/install.sh | sh"
    d.text((x, y), install, fill=CODE, font=fit(d, install, "mono", 19, column))
    y += 40
    providers = "Claude Code  ·  Codex  ·  Gemini CLI  ·  API key  ·  Ollama"
    d.text((x, y), providers, fill=MUTED, font=fit(d, providers, "regular", 19, column))
    d.text((x, h - 58), "github.com/guilyx/grip", fill=MUTED, font=font("mono", 19))

    shadowed(img, rounded(fail, 14), (cx, 96))
    shadowed(img, rounded(pas, 14), (cx, 96 + fail.height + 70))
    d = ImageDraw.Draw(img)
    small = font("strong", 17)
    label(d, (cx, 96 + fail.height + 16), "lazy answers", FAIL, small)
    label(d, (cx, 96 + fail.height + 70 + pas.height + 16), "after reading the diff", PASS, small)

    path = OUT / "grip-producthunt-1270x760.png"
    img.convert("RGB").save(path, optimize=True)
    return path


def thumbnail() -> Path:
    """Square icon: the mark on its tile."""
    img = mark_image(480)
    img.resize((240, 240), Image.LANCZOS).save(OUT / "grip-thumbnail-240.png", optimize=True)
    path = OUT / "grip-thumbnail-480.png"
    img.save(path, optimize=True)
    return path


def card(lines: list[tuple[str, str, str, int]], *, mark: int = 0) -> Image.Image:
    """A 1920x1080 title card. ``lines`` are (text, font kind, colour, size), centred."""
    w, h = 1920, 1080
    img = surface((w, h)).convert("RGBA")
    d = ImageDraw.Draw(img)
    fonts = [fit(d, text, kind, size, w - 240) for text, kind, _, size in lines]
    gap = 26
    total = sum(f.size for f in fonts) + gap * (len(lines) - 1) + (mark + 48 if mark else 0)
    y = (h - total) / 2 - 20
    if mark:
        img.alpha_composite(mark_image(mark), ((w - mark) // 2, int(y)))
        y += mark + 48
    for (text, _, color, _), f in zip(lines, fonts, strict=True):
        top = f.getbbox(text)[1] if text else 0
        centred(d, y - top, text, color, f, width=w)
        y += f.size + gap
    return img.convert("RGB")


def backdrop() -> Image.Image:
    """The frame the demo plays in."""
    w, h = 1920, 1080
    img = surface((w, h)).convert("RGBA")
    img.alpha_composite(mark_image(56), (70, 34))
    d = ImageDraw.Draw(img)
    d.text((142, 30), "grip", fill=FG, font=font("wordmark", 44))
    context = "pre-commit hook  ·  amr-safety  ·  a ROS 2 cmd_vel filter gains a scan watchdog"
    d.text((290, 50), context, fill=MUTED, font=font("regular", 24))
    f = font("mono", 24)
    url = "github.com/guilyx/grip"
    d.text((w - 70 - d.textlength(url, font=f), 50), url, fill=MUTED, font=f)
    return img.convert("RGB")


CARDS: list[tuple[str, float, list[tuple[str, str, str, int]]]] = [
    ("c1", 3.5, [("grip", "wordmark", FG, 200), ("keep a grip on your code", "strong", EMBER, 52)]),
    ("c2", 4.0, [
        ("We ship more code with AI than ever.", "bold", FG, 76),
        ("And explain less of it.", "bold", DIM, 76),
    ]),
    ("c3", 5.0, [
        ("Ask a dev to walk you through a PR from last week.", "regular", FG, 56),
        ("Why that guard clause is there.", "regular", DIM, 56),
        ("What happens when the list is empty.", "regular", DIM, 56),
    ]),
    ("c4", 4.0, [
        ("grip quizzes you on your own diff", "bold", FG, 76),
        ("before it goes upstream.", "bold", EMBER, 76),
    ]),
    ("c5", 3.5, [
        ("5 questions  ·  score out of 100", "bold", FG, 64),
        ("under the pass mark, the push is blocked", "regular", DIM, 48),
    ]),
    ("demo", DEMO_SECONDS, []),
    ("c6", 5.0, [
        ("One curl to install.", "bold", FG, 72),
        ("curl -fsSL guilyx.github.io/grip/install.sh | sh", "mono", CODE, 40),
        ("", "regular", FG, 10),
        ("Claude Code  ·  Codex  ·  Gemini CLI  ·  API key  ·  Ollama", "regular", DIM, 40),
    ]),
    ("c7", 5.0, [
        ("Owning your code is still on you.", "bold", FG, 76),
        ("github.com/guilyx/grip", "mono", EMBER, 48),
    ]),
]  # fmt: skip

# Cards that open and close the video carry the mark.
MARKED = {"c1": 160, "c7": 112}


def launch_video(workdir: Path) -> Path:
    """Title cards, the demo in a frame, closing cards; 1080p, 24 fps, fades between segments."""
    fade = 0.5
    inputs: list[str] = []
    filters: list[str] = []
    idx = 0
    for name, seconds, lines in CARDS:
        if name == "demo":
            bd = workdir / "backdrop.png"
            backdrop().save(bd)
            inputs += ["-i", str(bd), "-i", str(DEMO)]
            filters.append(
                f"[{idx}:v]loop=loop=-1:size=1:start=0,fps=24,trim=duration={seconds},"
                f"setpts=PTS-STARTPTS[bd];"
                f"[{idx + 1}:v]fps=24,scale=1230:916:flags=lanczos,setpts=PTS-STARTPTS[dm];"
                f"[bd][dm]overlay=(W-w)/2:120:shortest=1,"
                f"fade=t=in:st=0:d={fade},fade=t=out:st={seconds - fade}:d={fade},"
                f"format=yuv420p[v{name}]"
            )
            idx += 2
            continue
        png = workdir / f"{name}.png"
        card(lines, mark=MARKED.get(name, 0)).save(png)
        inputs += ["-loop", "1", "-t", str(seconds), "-i", str(png)]
        filters.append(
            f"[{idx}:v]fps=24,fade=t=in:st=0:d={fade},fade=t=out:st={seconds - fade}:d={fade},"
            f"format=yuv420p[v{name}]"
        )
        idx += 1
    concat = "".join(f"[v{name}]" for name, _, _ in CARDS) + f"concat=n={len(CARDS)}:v=1:a=0[v]"
    out = OUT / "grip-launch-1080p.mp4"
    ffmpeg(
        *inputs,
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-filter_complex", ";".join([*filters, concat]),
        "-map", "[v]", "-map", f"{idx}:a",
        "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-r", "24",
        "-c:a", "aac", "-b:a", "96k", "-shortest", "-movflags", "+faststart", str(out),
    )  # fmt: skip
    return out


def main() -> None:
    """Build everything."""
    if not DEMO.exists():
        raise SystemExit(f"{DEMO} is missing; run scripts/demo/make_demo.py first")
    OUT.mkdir(parents=True, exist_ok=True)
    workdir = ROOT / "build" / "launch"
    workdir.mkdir(parents=True, exist_ok=True)
    fail_frame, pass_frame = demo_frames(workdir)
    for path in (demo_mp4(), gallery(fail_frame, pass_frame), thumbnail(), launch_video(workdir)):
        print(f"wrote {path.relative_to(ROOT)} ({path.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    sys.exit(main())
