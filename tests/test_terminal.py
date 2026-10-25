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
    """A terminal fed in bursts: the lines of one burst arrive together, like a paste.

    A line is pending while the current burst has lines left. Reading past the burst
    waits for the next one, as when the developer types or presses Enter.
    """

    def __init__(self, *bursts: list[str]) -> None:
        super().__init__(Console(file=io.StringIO()), io.StringIO())
        self.bursts = [list(burst) for burst in bursts]

    def read_line(self, prompt: str = "") -> str:
        while self.bursts and not self.bursts[0]:
            self.bursts.pop(0)
        if not self.bursts:
            raise EOFError
        return self.bursts[0].pop(0)

    def pending(self) -> bool:
        return bool(self.bursts and self.bursts[0])


def test_a_typed_line_is_one_answer() -> None:
    term = _Pasting(["typed"], ["next"])
    assert read_answer(term, "> ") == "typed"
    assert read_answer(term, "> ") == "next"


def test_a_paste_keeps_its_lines_and_ends_at_an_empty_line() -> None:
    # Pasted: "a", "", "b" arrive at once; "c" was left mid-line and needed Enter;
    # the empty line ends the answer, and the next line answers the next question.
    term = _Pasting(["a", "", "b"], ["c"], [""], ["next"])
    assert read_answer(term, "> ") == "a\n\nb\nc"
    assert read_answer(term, "> ") == "next"


def test_a_paste_at_the_end_of_input() -> None:
    assert read_answer(_Pasting(["a", "b"]), "> ") == "a\nb"


@pytest.mark.parametrize(
    ("paste", "answer"),
    [
        (["line1", "line2", ""], "line1\nline2"),
        (["a", "", "b", ""], "a\n\nb"),
        (["a", "", ""], "a"),
        (["one line", ""], "one line"),
    ],
)
def test_a_paste_ending_in_an_empty_line_ends_the_answer(paste: list[str], answer: str) -> None:
    # Nothing more is waiting after the paste's own empty line, so no extra Enter is needed:
    # the next line typed answers the next question.
    term = _Pasting(paste, ["next"])
    assert read_answer(term, "> ") == answer
    assert read_answer(term, "> ") == "next"


def test_a_paste_cut_mid_line_runs_on_to_the_next_empty_line() -> None:
    term = _Pasting(["a", "b"], ["c"], ["d"], [""], ["next"])
    assert read_answer(term, "> ") == "a\nb\nc\nd"
    assert read_answer(term, "> ") == "next"


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
