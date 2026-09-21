"""Interactive terminal access that works from inside git hooks.

Git hooks (and the ``pre-commit`` framework in particular) frequently run with
stdin closed and stdout captured. To ask the developer questions we talk to the
controlling terminal directly (``/dev/tty`` on POSIX, ``CONIN$``/``CONOUT$`` on
Windows) and fall back to the regular standard streams when they are a TTY.
"""

from __future__ import annotations

import contextlib
import os
import sys
from dataclasses import dataclass
from typing import IO, Protocol

from rich.console import Console

from grip_hook.errors import NoTerminalError

_MIN_WIDTH = 40
"""Narrower terminals get a fixed width; rich cannot lay out panels below this."""


class Terminal(Protocol):
    """Minimal interface the quiz needs: a rich console plus a way to read a line."""

    console: Console

    def read_line(self, prompt: str = "") -> str:
        """Read one line of input from the developer (without the trailing newline)."""
        ...

    def close(self) -> None:
        """Release any file handles."""
        ...


@dataclass
class StreamTerminal:
    """A terminal over explicit streams. Used for tests and non-TTY fallbacks."""

    console: Console
    reader: IO[str]

    def read_line(self, prompt: str = "") -> str:
        """Print ``prompt`` and read one line; raises :class:`EOFError` at end of input."""
        if prompt:
            self.console.print(prompt, end="")
            self.console.file.flush()
        line = self.reader.readline()
        if line == "":
            raise EOFError
        return line.rstrip("\r\n")

    def close(self) -> None:  # noqa: D102 - protocol documented
        return None


class TtyTerminal:
    """A terminal bound to the controlling TTY, independent of stdin/stdout."""

    def __init__(self, reader: IO[str], writer: IO[str], *, owns: bool) -> None:
        self._reader = reader
        self._writer = writer
        self._owns = owns
        width: int | None = None
        try:
            width = os.get_terminal_size(writer.fileno()).columns or None
        except (OSError, ValueError):
            width = None
        if width is not None and width < _MIN_WIDTH:
            width = _MIN_WIDTH
        self.console = Console(file=writer, force_terminal=True, width=width, highlight=False)

    def read_line(self, prompt: str = "") -> str:
        """Print ``prompt`` and read one line from the TTY."""
        if prompt:
            self.console.print(prompt, end="")
        self._writer.flush()
        line = self._reader.readline()
        if line == "":
            raise EOFError
        return line.rstrip("\r\n")

    def close(self) -> None:
        """Close the TTY handles if we opened them."""
        if self._owns:
            for stream in (self._reader, self._writer):
                with contextlib.suppress(OSError):
                    stream.close()


def _open_controlling_tty() -> tuple[IO[str], IO[str]] | None:
    if sys.platform == "win32":
        names = ("CONIN$", "CONOUT$")
    else:
        names = ("/dev/tty", "/dev/tty")
    try:
        reader = open(names[0], encoding="utf-8", errors="replace")  # noqa: SIM115
        writer = open(names[1], "w", encoding="utf-8", errors="replace")  # noqa: SIM115
    except OSError:
        return None
    return reader, writer


def has_terminal() -> bool:
    """``True`` when an interactive terminal can be reached."""
    if sys.stdin.isatty() and sys.stdout.isatty():
        return True
    handles = _open_controlling_tty()
    if handles is None:
        return False
    for h in handles:
        h.close()
    return True


def open_terminal() -> Terminal:
    """Return a :class:`Terminal` bound to the developer's screen and keyboard.

    Raises:
        NoTerminalError: when neither the standard streams nor the controlling TTY
            are interactive (CI, editors that run hooks headlessly, ...).
    """
    if sys.stdin.isatty() and sys.stdout.isatty():
        return TtyTerminal(sys.stdin, sys.stdout, owns=False)
    handles = _open_controlling_tty()
    if handles is None:
        raise NoTerminalError(
            "grip needs an interactive terminal to ask questions, but none is available."
        )
    reader, writer = handles
    return TtyTerminal(reader, writer, owns=True)


__all__ = ["StreamTerminal", "Terminal", "TtyTerminal", "has_terminal", "open_terminal"]
