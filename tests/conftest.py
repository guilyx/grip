"""Shared fixtures."""

from __future__ import annotations

import io
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from rich.console import Console

from grip_hook.git import Git
from grip_hook.terminal import StreamTerminal


def _git(cwd: Path, *args: str, input_text: str | None = None) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        input=input_text,
    ).stdout


@pytest.fixture(autouse=True)
def data_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep the study registry out of the real home directory."""
    home = tmp_path / "data-home"
    monkeypatch.setenv("GRIP_DATA_HOME", str(home))
    return home


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A fresh git repository with one commit."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "commit.gpgsign", "false")
    (root / "README.md").write_text("# hello\n")
    _git(root, "add", "README.md")
    _git(root, "commit", "-q", "-m", "init")
    return root


@pytest.fixture
def git(repo: Path) -> Git:
    return Git(repo)


@pytest.fixture
def run_git(repo: Path) -> Callable[..., str]:
    def _run(*args: str, input_text: str | None = None) -> str:
        return _git(repo, *args, input_text=input_text)

    return _run


@pytest.fixture
def stage_change(repo: Path, run_git: Callable[..., str]) -> Callable[[str, str], None]:
    """Write a file and stage it."""

    def _stage(name: str, content: str) -> None:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        run_git("add", name)

    return _stage


class FakeTerminalFactory:
    """Builds StreamTerminals fed with scripted answers and captures output."""

    def __init__(self, answers: list[str]) -> None:
        self.answers = answers
        self.output = io.StringIO()

    def __call__(self) -> StreamTerminal:
        console = Console(file=self.output, force_terminal=False, width=100, highlight=False)
        return StreamTerminal(console=console, reader=io.StringIO("\n".join(self.answers) + "\n"))

    @property
    def text(self) -> str:
        return self.output.getvalue()


@pytest.fixture
def make_terminal() -> Callable[[list[str]], FakeTerminalFactory]:
    return FakeTerminalFactory
