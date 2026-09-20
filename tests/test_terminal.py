import io

import pytest
from rich.console import Console

from grip_hook.terminal import StreamTerminal, TtyTerminal, has_terminal


def test_stream_terminal_reads_lines() -> None:
    out = io.StringIO()
    term = StreamTerminal(
        Console(file=out, force_terminal=False, width=80), io.StringIO("one\r\ntwo\n")
    )
    assert term.read_line("> ") == "one"
    assert term.read_line() == "two"
    with pytest.raises(EOFError):
        term.read_line()
    assert "> " in out.getvalue()
    term.close()


def test_tty_terminal_over_pipes() -> None:
    reader = io.StringIO("answer\n")
    writer = io.StringIO()
    term = TtyTerminal(reader, writer, owns=True)
    assert term.read_line("prompt") == "answer"
    assert "prompt" in writer.getvalue()
    with pytest.raises(EOFError):
        term.read_line()
    term.close()
    assert reader.closed and writer.closed


def test_has_terminal_is_bool() -> None:
    assert isinstance(has_terminal(), bool)
