"""Record one short video per new feature, plus a stitched "what's new" cut.

    python scripts/demo/make_features.py            # every clip, then the stitched video
    python scripts/demo/make_features.py init skip  # only these clips (no stitched video)

Each clip drives a real ``bash`` in a pseudo-terminal against the real ``grip`` from this
virtualenv, in a throwaway repository, with the offline ``fake`` provider so it needs no
network and no key. Its scripted mode (``features-scenario.json``) supplies questions and
feedback written for the clips' rate-limiter diff, the way ``scenario.json`` does for the
main demo. Nothing else is staged: what you see is what the commands print. Recording
and rendering reuse ``make_demo.py``, title cards use ``brandkit.py``. Output goes to
``docs/assets/features/``: ``<clip>.mp4`` and ``<clip>.gif`` per clip, then
``grip-whats-new.mp4`` and its poster. Needs the ``demo`` extra.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import pty
import struct
import subprocess
import sys
import tempfile
import termios
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import make_demo  # noqa: E402
from brandkit import colour, dot_grid, draw_mark  # noqa: E402
from brandkit import font as brand_font  # noqa: E402
from make_demo import COLS, PROMPT, ROWS, Session, render  # noqa: E402

ROOT = HERE.parents[1]
OUT = ROOT / "docs" / "assets" / "features"
GRIP = Path(sys.executable).with_name("grip")
FPS_OUT = 24

ANSWERS = [
    "It caps each API key at 60 requests a minute with a token bucket and returns 429 "
    "with Retry-After when the bucket is empty",
    "Because one tenant's batch job was starving everyone else at month end",
    "Two app servers each keep their own bucket, so a key can get twice the limit until "
    "the buckets move to Redis",
    "Clients that retry without reading Retry-After will hammer us harder, the mobile app "
    "needs the backoff fix first",
    "A test that fires 61 requests in a minute and asserts the last one is a 429 with "
    "a Retry-After header",
]

RATE_LIMIT_BEFORE = '''"""Request handling for the public API."""


def handle(request, route):
    return route(request)
'''

RATE_LIMIT_AFTER = '''"""Request handling for the public API."""

import time

RATE = 60  # requests per minute per key
_buckets: dict[str, tuple[float, float]] = {}


def _allow(key: str, now: float) -> tuple[bool, float]:
    tokens, last = _buckets.get(key, (RATE, now))
    tokens = min(RATE, tokens + (now - last) * RATE / 60)
    if tokens < 1:
        _buckets[key] = (tokens, now)
        return False, (1 - tokens) * 60 / RATE
    _buckets[key] = (tokens - 1, now)
    return True, 0.0


def handle(request, route):
    ok, wait = _allow(request.api_key, time.monotonic())
    if not ok:
        return 429, {"Retry-After": str(int(wait) + 1)}, b"slow down"
    return route(request)
'''


# --------------------------------------------------------------------------- repo helpers


def _env(workdir: Path) -> dict[str, str]:
    env = make_demo._git_env()
    env.update(
        {
            # Only grip, git and coreutils: the coding agents grip detects are whatever
            # each clip puts in HOME, not what happens to be installed here.
            "PATH": f"{GRIP.parent}{os.pathsep}/usr/local/bin:/usr/bin:/bin",
            "HOME": str(workdir / "home"),
            "GRIP_DATA_HOME": str(workdir / "data"),
            "TERM": "xterm-256color",
            "COLORTERM": "truecolor",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PS1": PROMPT,
            "PROMPT_COMMAND": "",
            "HISTFILE": "/dev/null",
            # Realistic questions and feedback for the rate-limiter diff, still offline.
            "GRIP_FAKE_SCRIPT": str(HERE / "features-scenario.json"),
        }
    )
    for key in ("CI", "GRIP_SKIP", "GRIP_FAKE_DELAY", "VIRTUAL_ENV"):
        env.pop(key, None)
    return env


def _run(cwd: Path, env: dict[str, str], *args: str, stdin: str | None = None) -> str:
    return subprocess.run(
        args, cwd=cwd, env=env, check=True, capture_output=True, text=True, input=stdin
    ).stdout


def _repo(workdir: Path, env: dict[str, str], name: str, *, toml: str | None) -> Path:
    repo = workdir / name
    (repo / "api").mkdir(parents=True)
    (repo / "api" / "__init__.py").write_text("")
    (repo / "api" / "handler.py").write_text(RATE_LIMIT_BEFORE)
    (repo / "README.md").write_text(f"# {name}\n")
    if toml is not None:
        (repo / ".grip.toml").write_text(toml)
    _run(repo, env, "git", "init", "-q", "-b", "main")
    _run(repo, env, "git", "add", "-A")
    _run(repo, env, "git", "commit", "-q", "-m", "api: public request handler")
    return repo


def _graded_quiz(repo: Path, env: dict[str, str]) -> None:
    """Stage the rate limiter and grade a quiz on it, the way an agent would."""
    (repo / "api" / "handler.py").write_text(RATE_LIMIT_AFTER)
    _run(repo, env, "git", "add", "-A")
    env = {**env, "GRIP_FAKE_DELAY": "0"}
    _run(repo, env, "grip", "ask")
    _run(repo, env, "grip", "grade", "--answers", "-", stdin=json.dumps(ANSWERS))


FAKE = 'provider = "fake"\npassing_score = 70\n'


# --------------------------------------------------------------------------- clips


@dataclass(frozen=True)
class Clip:
    """One feature video: a title card, a repo setup and the commands typed."""

    name: str
    title: str
    subtitle: str
    setup: Callable[[Path, dict[str, str]], Path]
    drive: Callable[[Session], None]


def _setup_init(workdir: Path, env: dict[str, str]) -> Path:
    home = Path(env["HOME"])
    for agent_dir in (".codex", ".gemini", ".cursor"):
        (home / agent_dir).mkdir(parents=True, exist_ok=True)
    return _repo(workdir, env, "fleet-api", toml=None)


def _drive_init(s: Session) -> None:
    s.wait_for(PROMPT)
    s.idle(0.8)
    s.type("grip init")
    s.wait_for(PROMPT)
    s.idle(2.2)
    s.type("cat .grip.toml")
    s.wait_for(PROMPT)
    s.idle(2.4)
    s.type("git status --short")
    s.wait_for(PROMPT)
    s.idle(2.6)


def _setup_agents(workdir: Path, env: dict[str, str]) -> Path:
    home = Path(env["HOME"])
    for agent_dir in (".claude", ".codex", ".cursor", ".codeium/windsurf"):
        (home / agent_dir).mkdir(parents=True, exist_ok=True)
    return _repo(workdir, env, "fleet-api", toml=FAKE)


def _drive_agents(s: Session) -> None:
    s.wait_for(PROMPT)
    s.idle(0.8)
    s.type("grip agents --list")
    s.wait_for(PROMPT)
    s.idle(2.6)
    s.type("grip agents")
    s.wait_for(PROMPT)
    s.idle(2.6)
    s.type("tail -n 9 AGENTS.md")
    s.wait_for(PROMPT)
    s.idle(3.0)


def _setup_gate(workdir: Path, env: dict[str, str]) -> Path:
    origin = workdir / "origin.git"
    _run(workdir, env, "git", "init", "-q", "--bare", "-b", "main", str(origin))
    repo = _repo(workdir, env, "fleet-api", toml=FAKE)
    _run(repo, env, "git", "remote", "add", "origin", str(origin))
    _run(repo, env, "git", "push", "-q", "origin", "main")
    (repo / "api" / "handler.py").write_text(RATE_LIMIT_AFTER)
    _run(repo, env, "git", "commit", "-q", "-am", "api: rate limit per key")
    payload = {"tool_name": "Bash", "tool_input": {"command": "git push"}, "cwd": str(repo)}
    (workdir / "claude-push.json").write_text(json.dumps(payload))
    (workdir / "answers.json").write_text(json.dumps(ANSWERS, indent=1))
    return repo


def _drive_gate(s: Session) -> None:
    s.wait_for(PROMPT)
    s.idle(0.8)
    # What the Claude Code plugin's PreToolUse hook answers when Claude runs `git push`.
    s.type("grip agent-hook claude-code < ../claude-push.json | jq .hookSpecificOutput")
    s.wait_for(PROMPT)
    s.idle(3.4)
    s.type("grip ask --unpushed | jq -r '.questions[] | \"\\(.index). \\(.question)\"'")
    s.wait_for(PROMPT)
    s.idle(3.0)
    s.type("grip grade --answers ../answers.json | jq '{status, score}'")
    s.wait_for(PROMPT)
    s.idle(2.2)
    s.type("grip check --unpushed && git push origin main")
    s.wait_for(PROMPT)
    s.idle(3.0)


def _setup_skip(workdir: Path, env: dict[str, str]) -> Path:
    repo = _repo(workdir, env, "fleet-api", toml=FAKE)
    _graded_quiz(repo, env)
    _run(repo, env, "git", "commit", "-q", "-m", "api: rate limit per key")
    _run(repo, env, "grip", "install", "--stage", "pre-commit")
    (repo / "README.md").write_text("# fleet-api\n\nRate limited at 60 requests a minute.\n")
    _run(repo, env, "git", "add", "-A")
    return repo


def _drive_skip(s: Session) -> None:
    s.wait_for(PROMPT)
    s.idle(0.8)
    s.type("grip statusline")
    s.wait_for(PROMPT)
    s.idle(1.6)
    s.type("grip skip")
    s.wait_for(PROMPT)
    s.idle(1.2)
    s.type('git commit -m "docs: mention the rate limit"')
    s.wait_for(PROMPT)
    s.idle(2.4)
    s.type("grip skip --hours 1")
    s.wait_for(PROMPT)
    s.idle(1.4)
    s.type("grip statusline")
    s.wait_for(PROMPT)
    s.idle(1.6)
    s.type("grip resume")
    s.wait_for(PROMPT)
    s.idle(2.4)


def _setup_last(workdir: Path, env: dict[str, str]) -> Path:
    repo = _repo(workdir, env, "fleet-api", toml=FAKE)
    _graded_quiz(repo, env)
    return repo


def _drive_last(s: Session) -> None:
    s.wait_for(PROMPT)
    s.idle(0.8)
    s.type("grip last")
    s.wait_for(PROMPT)
    s.idle(5.0)


CLIPS: tuple[Clip, ...] = (
    Clip("init", "grip init", "config, git hook and agent rules in one command",
         _setup_init, _drive_init),
    Clip("agents", "grip agents", "every coding agent on your machine runs the quiz",
         _setup_agents, _drive_agents),
    Clip("agent-gate", "the agent push gate", "Claude can't push until you've passed",
         _setup_gate, _drive_gate),
    Clip("skip", "grip skip", "one push without the quiz, or a pause, no env vars",
         _setup_skip, _drive_skip),
    Clip("last", "grip last", "your own explanation, ready for the commit message",
         _setup_last, _drive_last),
)  # fmt: skip


# --------------------------------------------------------------------------- recording


def record(clip: Clip, cast: Path) -> None:
    """Drive ``clip`` in a pty and write an asciinema v2 cast."""
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        env = _env(workdir)
        Path(env["HOME"]).mkdir(parents=True)
        repo = clip.setup(workdir, env)
        env["GRIP_FAKE_DELAY"] = "0.8"
        pid, fd = pty.fork()
        if pid == 0:  # pragma: no cover - child
            os.chdir(repo)
            os.execvpe("bash", ["bash", "--noprofile", "--norc", "-i"], env)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
        session = Session(pid=pid, fd=fd, started=time.monotonic(), events=[])
        try:
            clip.drive(session)
        finally:
            with contextlib.suppress(OSError):
                os.write(fd, b"exit\r")
            session.idle(0.3)
            with contextlib.suppress(OSError):
                os.close(fd)
            with contextlib.suppress(OSError):
                os.waitpid(pid, 0)
    header = {"version": 2, "width": COLS, "height": ROWS, "title": clip.title}
    with cast.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(header) + "\n")
        for stamp, text in session.events:
            fh.write(json.dumps([round(stamp, 4), "o", text], ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------- encoding


def _ffmpeg(*args: str, stdin: bytes | None = None) -> None:
    import imageio_ffmpeg  # noqa: PLC0415 - optional 'demo' extra

    exe = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run(
        [exe, "-y", "-hide_banner", "-loglevel", "error", *args], input=stdin, check=True
    )


def _to_mp4(webm: Path, mp4: Path) -> None:
    _ffmpeg(
        "-i", str(webm), "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,fps=24",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "high", "-crf", "18",
        "-movflags", "+faststart", str(mp4),
    )  # fmt: skip


def _size(mp4: Path) -> tuple[int, int]:
    from PIL import Image  # noqa: PLC0415

    with tempfile.TemporaryDirectory() as tmp:
        frame = Path(tmp) / "f.jpg"
        _ffmpeg("-i", str(mp4), "-frames:v", "1", str(frame))
        with Image.open(frame) as img:
            return img.size


Line = tuple[str, str, int, str]  # text, brandkit font kind, pixel size, colour token


def _card_mp4(lines: list[Line], size: tuple[int, int], seconds: float, out: Path, *,
              mark: int = 0) -> None:  # fmt: skip
    """A still brand card as an mp4 segment matching the clips: dot grid, mark, text."""
    import io  # noqa: PLC0415

    from PIL import Image, ImageDraw  # noqa: PLC0415

    w, h = size
    img = Image.new("RGB", size, colour("graphite-975"))
    d = ImageDraw.Draw(img)
    dot_grid(d, w, h)
    gap = 16
    total = (mark + 28 if mark else 0) + sum(px + gap for _, _, px, _ in lines) - gap
    y = (h - total) / 2
    if mark:
        draw_mark(d, w / 2, y + mark / 2, mark)
        y += mark + 28
    for text, kind, px, token in lines:
        face = brand_font(kind, px)
        top = face.getbbox(text)[1] if text else 0
        x = (w - d.textlength(text, font=face)) / 2
        d.text((x, y - top), text, fill=colour(token), font=face)
        y += px + gap
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    frames = int(seconds * FPS_OUT)
    _ffmpeg(
        "-f", "image2pipe", "-c:v", "mjpeg", "-framerate", str(FPS_OUT), "-i", "pipe:0",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "high", "-crf", "18",
        "-r", str(FPS_OUT), str(out),
        stdin=buf.getvalue() * frames,
    )  # fmt: skip


def stitch(clips: list[Clip], out: Path) -> None:
    """Title card, then each clip after its own card, then a closing card."""
    size = _size(OUT / f"{clips[0].name}.mp4")
    with tempfile.TemporaryDirectory() as tmp:
        parts: list[Path] = []
        intro = Path(tmp) / "intro.mp4"
        _card_mp4(
            [
                ("grip", "display", 76, "graphite-50"),
                ("what's new", "display-800", 34, "ember-400"),
                ("a quiz on your own diff, now in every coding agent", "text", 22,
                 "graphite-400"),
            ],
            size, 2.8, intro, mark=96,
        )  # fmt: skip
        parts.append(intro)
        for n, clip in enumerate(clips, 1):
            card = Path(tmp) / f"card-{clip.name}.mp4"
            _card_mp4(
                [
                    (f"{n:02d} / {len(clips):02d}", "mono-bold", 18, "ember-400"),
                    (clip.title, "display", 48, "graphite-50"),
                    (clip.subtitle, "text", 24, "graphite-300"),
                ],
                size, 2.2, card,
            )  # fmt: skip
            parts += [card, OUT / f"{clip.name}.mp4"]
        outro = Path(tmp) / "outro.mp4"
        _card_mp4(
            [
                ("npx skills add guilyx/grip -g", "mono-bold", 32, "ember-300"),
                ("guilyx.github.io/grip", "text-bold", 24, "graphite-300"),
            ],
            size, 3.2, outro, mark=72,
        )  # fmt: skip
        parts.append(outro)
        listing = Path(tmp) / "parts.txt"
        listing.write_text("".join(f"file '{p}'\n" for p in parts))
        _ffmpeg(
            "-f", "concat", "-safe", "0", "-i", str(listing),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "high", "-crf", "18",
            "-r", str(FPS_OUT), "-movflags", "+faststart", str(out),
        )  # fmt: skip


def poster(video: Path, out: Path) -> None:
    """The homepage poster: the intro card's first second, as a JPEG."""
    _ffmpeg("-ss", "1.4", "-i", str(video), "-frames:v", "1", "-q:v", "3", str(out))


def main(argv: list[str]) -> None:
    """Record and render the clips named in ``argv`` (all by default)."""
    if not GRIP.exists():
        raise SystemExit("run this from the project virtualenv (grip must be installed)")
    wanted = set(argv[1:])
    unknown = wanted - {c.name for c in CLIPS}
    if unknown:
        raise SystemExit(f"unknown clip(s): {', '.join(sorted(unknown))}")
    chosen = [c for c in CLIPS if not wanted or c.name in wanted]
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        for clip in chosen:
            cast, webm = Path(tmp) / f"{clip.name}.cast", Path(tmp) / f"{clip.name}.webm"
            record(clip, cast)
            render(cast, OUT / f"{clip.name}.gif", webm)
            _to_mp4(webm, OUT / f"{clip.name}.mp4")
            print(f"wrote {OUT / clip.name}.mp4")
    if not wanted:
        stitch(list(CLIPS), OUT / "grip-whats-new.mp4")
        poster(OUT / "grip-whats-new.mp4", OUT / "grip-whats-new.jpg")
        print(f"wrote {OUT / 'grip-whats-new.mp4'} and its poster")


if __name__ == "__main__":
    main(sys.argv)
