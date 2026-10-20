import io
import os
import sys

import pytest
from rich.console import Console

from grip_hook.quiz import read_answer
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


class _Pasting(StreamTerminal):
    """A terminal that says a line is waiting while ``waiting`` lines remain."""

    def __init__(self, text: str, waiting: list[bool]) -> None:
        super().__init__(Console(file=io.StringIO()), io.StringIO(text))
        self.waiting = waiting

    def pending(self) -> bool:
        return self.waiting.pop(0) if self.waiting else False


def test_a_typed_line_is_one_answer() -> None:
    term = _Pasting("typed\nnext\n", [False])
    assert read_answer(term, "> ") == "typed"
    assert read_answer(term, "> ") == "next"


def test_a_paste_keeps_its_lines_and_ends_at_an_empty_line() -> None:
    # Pasted: "a", "", "b" arrive at once; "c" was left mid-line and needed Enter;
    # the empty line ends the answer, and the next line answers the next question.
    term = _Pasting("a\n\nb\nc\n\nnext\n", [True, True, False])
    assert read_answer(term, "> ") == "a\n\nb\nc"
    assert read_answer(term, "> ") == "next"


def test_a_paste_at_the_end_of_input() -> None:
    assert read_answer(_Pasting("a\nb\n", [True, False]), "> ") == "a\nb"


@pytest.mark.skipif(sys.platform == "win32", reason="the console cannot be polled")
def test_tty_pending_sees_waiting_input() -> None:
    read_fd, write_fd = os.pipe()
    with open(read_fd, encoding="utf-8") as reader, io.StringIO() as writer:
        term = TtyTerminal(reader, writer, owns=False)
        assert term.pending() is False
        os.write(write_fd, b"more\n")
        assert term.pending() is True
        os.close(write_fd)
    assert TtyTerminal(io.StringIO(), io.StringIO(), owns=False).pending() is False
