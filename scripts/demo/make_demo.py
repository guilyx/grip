"""Record and render the grip demo.

    python scripts/demo/make_demo.py record   # -> docs/assets/demo.cast (asciinema v2)
    python scripts/demo/make_demo.py render   # -> docs/assets/demo.gif + demo.webm
    python scripts/demo/make_demo.py all

``record`` builds a throwaway repository with a realistic diff, installs grip's
pre-commit hook with the scripted fake provider, and drives a real ``bash`` session
in a pseudo-terminal: one commit attempt with lazy answers (blocked), one with real
answers (goes through). Output is captured with timestamps in asciinema's v2 format,
so it also plays with ``asciinema play`` and converts with ``agg``.

``render`` replays the cast through ``pyte`` (a terminal emulator), draws each frame
with Pillow and writes an animated GIF, plus a WebM through ffmpeg when one is found.
Requires the ``demo`` extra: ``pip install -e ".[demo]"`` (pyte, Pillow, fontTools).
"""

from __future__ import annotations

import contextlib
import fcntl
import io
import json
import os
import pty
import random
import select
import shutil
import struct
import subprocess
import sys
import tempfile
import termios
import time
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ASSETS = ROOT / "docs" / "assets"
CAST = ASSETS / "demo.cast"
GIF = ASSETS / "demo.gif"
WEBM = ASSETS / "demo.webm"

COLS, ROWS = 96, 30
FPS = 12
PROMPT = "❯ "  # noqa: RUF001 - deliberate prompt glyph

LAZY_ANSWERS = [
    "it stops i guess",
    "",
    "nothing",
    "the lidar",
    "drive it around",
]
GOOD_ANSWERS = [
    "A zero Twist: with no scan for scan_timeout the watchdog stops the robot instead of "
    "trusting old data",
    "Because _on_scan never runs once scans stop; the check has to live on the path that "
    "still executes",
    "It won't move even though the lidar is healthy: age is header.stamp against the node clock",
    "stop_distance; at 0 the min_range < stop_distance test can never be true, so the "
    "obstacle stop is off",
    "Publish synthetic LaserScans from a test node, stop, send a command, assert /cmd_vel is zero",
]

# --------------------------------------------------------------------------- recording


@dataclass
class Session:
    """A bash session in a pty whose output is captured with timestamps."""

    pid: int
    fd: int
    started: float
    events: list[tuple[float, str]]
    buffer: str = ""

    def pump(self, timeout: float) -> bool:
        """Read whatever is available within ``timeout``; return False on EOF."""
        ready, _, _ = select.select([self.fd], [], [], timeout)
        if not ready:
            return True
        try:
            chunk = os.read(self.fd, 65536)
        except OSError:
            return False
        if not chunk:
            return False
        text = chunk.decode("utf-8", "replace")
        self.events.append((time.monotonic() - self.started, text))
        self.buffer += text
        return True

    def wait_for(self, marker: str, timeout: float = 30.0) -> None:
        """Block until ``marker`` appears in output produced since the last wait."""
        deadline = time.monotonic() + timeout
        while marker not in self.buffer:
            if time.monotonic() > deadline:
                raise TimeoutError(f"did not see {marker!r} within {timeout}s")
            if not self.pump(0.05):
                raise EOFError("session ended")
        self.buffer = ""

    def idle(self, seconds: float) -> None:
        """Keep capturing output for ``seconds`` without sending anything."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.pump(min(0.05, max(0.0, end - time.monotonic())))

    def type(self, text: str, cps: float = 28.0) -> None:
        """Type ``text`` with human-ish timing, then press Enter."""
        rng = random.Random(len(text))
        for ch in text:
            os.write(self.fd, ch.encode())
            delay = 1.0 / cps * rng.uniform(0.6, 1.6)
            if ch in " ,.;:":
                delay *= 1.6
            self.idle(delay)
        self.idle(0.25)
        os.write(self.fd, b"\r")


def _build_repo(workdir: Path, grip_bin: Path) -> Path:
    repo = workdir / "amr-safety"
    repo.mkdir()
    env = _git_env()

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    pkg = repo / "amr_safety"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    fixture = HERE / "fixture"
    (pkg / "cmd_vel_filter.py").write_text((fixture / "cmd_vel_filter_before.py").read_text())
    (repo / ".grip.toml").write_text('provider = "fake"\npassing_score = 70\n')
    git("add", "-A")
    git("commit", "-q", "-m", "safety: lidar stop in the cmd_vel filter")
    subprocess.run(
        [str(grip_bin), "install", "--stage", "pre-commit"],
        cwd=repo,
        env=env,
        check=True,
        capture_output=True,
    )
    # The change under quiz.
    (pkg / "cmd_vel_filter.py").write_text((fixture / "cmd_vel_filter_after.py").read_text())
    git("add", "-A")
    return repo


def _git_env() -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "GIT_AUTHOR_NAME": "Erwin",
            "GIT_AUTHOR_EMAIL": "erwin@example.com",
            "GIT_COMMITTER_NAME": "Erwin",
            "GIT_COMMITTER_EMAIL": "erwin@example.com",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
        }
    )
    return env


def record(cast_path: Path = CAST) -> None:
    """Drive the demo session and write an asciinema v2 cast."""
    grip_bin = Path(sys.executable).with_name("grip")
    if not grip_bin.exists():
        raise SystemExit("run this from the project virtualenv (grip must be installed)")

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        repo = _build_repo(workdir, grip_bin)
        env = _git_env()
        env.update(
            {
                "PATH": f"{grip_bin.parent}{os.pathsep}{env.get('PATH', '')}",
                "TERM": "xterm-256color",
                "COLORTERM": "truecolor",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PS1": PROMPT,
                "PROMPT_COMMAND": "",
                "HISTFILE": "/dev/null",
                "GRIP_FAKE_SCRIPT": str(HERE / "scenario.json"),
                "GRIP_FAKE_DELAY": "1.3",
                "HOME": str(workdir),
            }
        )
        env.pop("CI", None)
        env.pop("GRIP_SKIP", None)

        pid, fd = pty.fork()
        if pid == 0:  # pragma: no cover - child
            os.chdir(repo)
            os.execvpe("bash", ["bash", "--noprofile", "--norc", "-i"], env)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
        session = Session(pid=pid, fd=fd, started=time.monotonic(), events=[])
        try:
            _drive(session)
        finally:
            with contextlib.suppress(OSError):
                os.write(fd, b"exit\r")
            session.idle(0.3)
            with contextlib.suppress(OSError):
                os.close(fd)
            with contextlib.suppress(OSError):
                os.waitpid(pid, 0)

    header = {
        "version": 2,
        "width": COLS,
        "height": ROWS,
        "timestamp": int(time.time()),
        "title": "grip: keep a grip on your code",
        "env": {"TERM": "xterm-256color", "SHELL": "/bin/bash"},
    }
    cast_path.parent.mkdir(parents=True, exist_ok=True)
    with cast_path.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(header) + "\n")
        for stamp, text in session.events:
            fh.write(json.dumps([round(stamp, 4), "o", text], ensure_ascii=False) + "\n")
    print(f"wrote {cast_path} ({len(session.events)} events, {session.events[-1][0]:.1f}s)")


def _drive(s: Session) -> None:
    s.wait_for(PROMPT)
    s.idle(0.8)
    s.type("git status --short")
    s.wait_for(PROMPT)
    s.idle(1.2)

    # Attempt 1: lazy answers, blocked.
    s.type('git commit -m "safety: stop when the scan goes stale"')
    for answer in LAZY_ANSWERS:
        s.wait_for("> ")
        s.idle(0.5)
        s.type(answer, cps=30)
    s.wait_for(PROMPT, timeout=60)
    s.idle(3.2)

    # Attempt 2: after actually reading the diff.
    s.type("git diff --cached --stat")
    s.wait_for(PROMPT)
    s.idle(1.0)
    s.type('git commit -m "safety: stop when the scan goes stale"')
    for answer in GOOD_ANSWERS:
        s.wait_for("> ")
        s.idle(0.6)
        s.type(answer, cps=34)
    s.wait_for(PROMPT, timeout=60)
    s.idle(2.6)
    s.type("git log --oneline -2")
    s.wait_for(PROMPT)
    s.idle(3.0)


# --------------------------------------------------------------------------- rendering

THEME = {
    "bg": "#1e1e2e",
    "fg": "#cdd6f4",
    "cursor": "#f5e0dc",
    "chrome": "#181825",
    "black": "#45475a",
    "red": "#f38ba8",
    "green": "#a6e3a1",
    "brown": "#f9e2af",
    "yellow": "#f9e2af",
    "blue": "#89b4fa",
    "magenta": "#f5c2e7",
    "cyan": "#94e2d5",
    "white": "#bac2de",
    "brightblack": "#585b70",
    "brightred": "#f38ba8",
    "brightgreen": "#a6e3a1",
    "brightbrown": "#f9e2af",
    "brightyellow": "#f9e2af",
    "brightblue": "#89b4fa",
    "brightmagenta": "#f5c2e7",
    "brightcyan": "#94e2d5",
    "brightwhite": "#a6adc8",
}
FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
FONT_SIZE = 15
PAD = 18
TITLE_H = 34


def _color(value: str, fallback: str) -> str:
    if value == "default":
        return fallback
    if value in THEME:
        return THEME[value]
    if len(value) == 6:
        return f"#{value}"
    return fallback


def render(cast_path: Path = CAST, gif_path: Path = GIF, webm_path: Path = WEBM) -> None:
    """Replay the cast through a terminal emulator and encode GIF and WebM."""
    import pyte  # noqa: PLC0415 - optional 'demo' extra
    from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415

    lines = cast_path.read_text("utf-8").splitlines()
    header = json.loads(lines[0])
    cols, rows = header["width"], header["height"]
    events = [json.loads(line) for line in lines[1:]]
    events = [(float(t), data) for t, kind, data in events if kind == "o"]
    total = events[-1][0] + 0.2

    from fontTools.ttLib import TTFont  # noqa: PLC0415

    regular = ImageFont.truetype(str(FONT_DIR / "DejaVuSansMono.ttf"), FONT_SIZE)
    bold = ImageFont.truetype(str(FONT_DIR / "DejaVuSansMono-Bold.ttf"), FONT_SIZE)
    fallback = ImageFont.truetype(str(FONT_DIR / "DejaVuSans.ttf"), FONT_SIZE - 1)
    mono_cmap = TTFont(str(FONT_DIR / "DejaVuSansMono.ttf")).getBestCmap()

    def has_glyph(ch: str) -> bool:
        return ord(ch) in mono_cmap

    cell_w = round(regular.getlength("M"))
    cell_h = FONT_SIZE + 5
    width = cols * cell_w + 2 * PAD
    height = rows * cell_h + 2 * PAD + TITLE_H

    screen = pyte.Screen(cols, rows)
    stream = pyte.ByteStream(screen)

    def draw_frame() -> Image.Image:
        img = Image.new("RGB", (width, height), THEME["bg"])
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, width, TITLE_H], fill=THEME["chrome"])
        for i, colour in enumerate(("#f38ba8", "#f9e2af", "#a6e3a1")):
            x = 16 + i * 22
            d.ellipse([x, 11, x + 12, 23], fill=colour)
        d.text(
            (width / 2, TITLE_H / 2),
            "grip — keep a grip on your code",
            fill="#6c7086",
            font=regular,
            anchor="mm",
        )
        top = TITLE_H + PAD
        for y in range(rows):
            line = screen.buffer[y]
            x = 0
            while x < cols:
                ch = line[x]
                style = (ch.fg, ch.bg, ch.bold, ch.reverse)
                run_start = x
                text = ""
                while x < cols and (line[x].fg, line[x].bg, line[x].bold, line[x].reverse) == style:
                    text += line[x].data or " "
                    x += 1
                fg = _color(style[0], THEME["fg"])
                bg = _color(style[1], THEME["bg"])
                if style[3]:
                    fg, bg = bg, fg
                px = PAD + run_start * cell_w
                py = top + y * cell_h
                if bg != THEME["bg"]:
                    d.rectangle([px, py, px + len(text) * cell_w, py + cell_h], fill=bg)
                if text.strip():
                    font = bold if style[2] else regular
                    if all(has_glyph(c) or c == " " for c in text):
                        d.text((px, py + 2), text, fill=fg, font=font)
                    else:  # draw char by char so missing glyphs use the fallback font
                        for k, c in enumerate(text):
                            f = font if has_glyph(c) else fallback
                            d.text((px + k * cell_w, py + 2), c, fill=fg, font=f)
        if not screen.cursor.hidden:
            cx = PAD + screen.cursor.x * cell_w
            cy = top + screen.cursor.y * cell_h
            d.rectangle([cx, cy + 2, cx + cell_w - 1, cy + cell_h - 1], fill=THEME["cursor"])
            under = screen.buffer[screen.cursor.y][screen.cursor.x].data
            if under.strip():
                d.text((cx, cy + 2), under, fill=THEME["bg"], font=regular)
        return img

    frames: list[Image.Image] = []
    durations: list[int] = []
    jpegs: list[bytes] = []
    idx = 0
    t = 0.0
    step = 1.0 / FPS
    last_bytes: bytes | None = None
    n = 0
    while t <= total:
        while idx < len(events) and events[idx][0] <= t:
            stream.feed(events[idx][1].encode("utf-8"))
            idx += 1
        img = draw_frame()
        raw = img.tobytes()
        if raw == last_bytes:
            durations[-1] += int(1000 / FPS)
        else:
            frames.append(img.quantize(colors=64, method=Image.Quantize.MEDIANCUT, dither=0))
            durations.append(int(1000 / FPS))
            last_bytes = raw
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=92)
        jpegs.append(buf.getvalue())
        n += 1
        t += step

    gif_path.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        gif_path,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=1,
    )
    print(f"wrote {gif_path} ({gif_path.stat().st_size / 1e6:.1f} MB, {len(frames)} unique frames)")

    ffmpeg = _find_ffmpeg()
    if ffmpeg:
        # JPEG frames over a pipe: works with minimal ffmpeg builds that lack a PNG decoder.
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-loglevel",
                "error",
                "-f",
                "image2pipe",
                "-c:v",
                "mjpeg",
                "-framerate",
                str(FPS),
                "-i",
                "pipe:0",
                "-c:v",
                "libvpx",
                "-b:v",
                "1200k",
                "-auto-alt-ref",
                "0",
                "-pix_fmt",
                "yuv420p",
                str(webm_path),
            ],
            input=b"".join(jpegs),
            check=True,
        )
        print(f"wrote {webm_path} ({webm_path.stat().st_size / 1e6:.1f} MB, {n} frames)")
    else:
        print("ffmpeg not found; skipped the WebM")


def _find_ffmpeg() -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return found
    for candidate in sorted(Path("/opt/pw-browsers").glob("ffmpeg-*/ffmpeg-linux")):
        return str(candidate)
    return None


def main(argv: list[str]) -> None:
    """Dispatch on the sub-command: record, render or all."""
    command = argv[1] if len(argv) > 1 else "all"
    if command in {"record", "all"}:
        record()
    if command in {"render", "all"}:
        render()
    if command not in {"record", "render", "all"}:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
