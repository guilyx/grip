"""The branch as a whole: `grip quiz --branch`, the push hint and `grip status`."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from click.testing import CliRunner

from grip_hook import cli as cli_module
from grip_hook.cli import cli
from grip_hook.git import ZERO_SHA, Git
from tests.conftest import FakeTerminalFactory

GOOD = ["this is a long and specific answer because reasons"] * 5
BAD = [""] * 5


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def in_repo(repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(repo)
    for name in ("GRIP_SKIP", "CI", "PRE_COMMIT_FROM_REF", "PRE_COMMIT_TO_REF"):
        monkeypatch.delenv(name, raising=False)
    (repo / ".grip.toml").write_text('provider = "fake"\nmodel = "fake"\n')
    return repo


@pytest.fixture
def terminal(monkeypatch: pytest.MonkeyPatch) -> Callable[[list[str]], FakeTerminalFactory]:
    def _install(answers: list[str]) -> FakeTerminalFactory:
        factory = FakeTerminalFactory(answers)
        monkeypatch.setattr(cli_module, "open_terminal", factory)
        return factory

    return _install


@pytest.fixture
def feature(
    in_repo: Path,
    tmp_path: Path,
    run_git: Callable[..., str],
    stage_change: Callable[[str, str], None],
) -> Path:
    """``feat`` with two commits off ``main``, which is on ``origin``; ``feat`` is not."""
    bare = tmp_path / "remote.git"
    bare.mkdir()
    Git(bare).run("init", "-q", "--bare")
    run_git("remote", "add", "origin", str(bare))
    run_git("push", "-q", "-u", "origin", "main")
    run_git("checkout", "-q", "-b", "feat")
    stage_change("a.py", "x = 1\n")
    run_git("commit", "-q", "-m", "a")
    stage_change("b.py", "y = 2\n")
    run_git("commit", "-q", "-m", "b")
    return in_repo


def test_default_branch(git: Git, repo: Path, run_git: Callable[..., str], tmp_path: Path) -> None:
    assert git.default_branch() == "main"
    run_git("branch", "-m", "trunk")
    assert git.default_branch() is None
    bare = tmp_path / "remote.git"
    bare.mkdir()
    Git(bare).run("init", "-q", "--bare")
    run_git("remote", "add", "upstream", str(bare))
    run_git("push", "-q", "upstream", "trunk")
    run_git("remote", "set-head", "upstream", "trunk")
    assert git.default_branch() == "upstream/trunk"


def test_quiz_branch_covers_every_commit_since_main(
    runner: CliRunner, feature: Path, terminal: Callable[[list[str]], FakeTerminalFactory]
) -> None:
    factory = terminal(GOOD)
    result = runner.invoke(cli, ["quiz", "--branch"])
    assert result.exit_code == 0, result.output
    assert "a.py" in factory.text and "b.py" in factory.text
    assert "README.md" not in factory.text
    assert "feat vs origin/main" in factory.text
    assert runner.invoke(cli, ["check", "--branch"]).exit_code == 0


def test_quiz_branch_without_a_default_branch(
    runner: CliRunner, in_repo: Path, run_git: Callable[..., str]
) -> None:
    run_git("branch", "-m", "trunk")
    result = runner.invoke(cli, ["quiz", "--branch"])
    assert result.exit_code == 2
    assert "--range BASE..HEAD" in result.output


def test_a_push_of_commits_already_on_the_remote_points_at_the_branch(
    runner: CliRunner,
    feature: Path,
    run_git: Callable[..., str],
    terminal: Callable[[list[str]], FakeTerminalFactory],
) -> None:
    # Pushed without the hook, then pushed again under another name: nothing new is sent.
    run_git("push", "-q", "--no-verify", "-u", "origin", "feat")
    head = run_git("rev-parse", "HEAD").strip()
    stdin = f"refs/heads/feat {head} refs/heads/copy {ZERO_SHA}\n"
    result = runner.invoke(cli, ["hook", "pre-push", "origin"], input=stdin)
    assert result.exit_code == 0, result.output
    out = " ".join(result.output.split())
    assert "already on origin/feat" in out
    assert "feat vs origin/main, 2 files" in out and "grip quiz --branch" in out

    terminal(GOOD)
    assert runner.invoke(cli, ["quiz", "--branch"]).exit_code == 0
    result = runner.invoke(cli, ["hook", "pre-push", "origin"], input=stdin)
    assert "grip quiz --branch" not in result.output
    assert "nothing to quiz" in result.output


def test_status_shows_the_branch(
    runner: CliRunner, feature: Path, terminal: Callable[[list[str]], FakeTerminalFactory]
) -> None:
    result = runner.invoke(cli, ["status"])
    assert "branch vs origin/main" in result.output
    assert "not quizzed (2 files)" in result.output

    terminal(BAD)
    failed = runner.invoke(cli, ["quiz", "--branch"])
    assert failed.exit_code == 1
    assert "`grip last` shows the feedback again" in " ".join(failed.output.split())
    result = runner.invoke(cli, ["status"])
    assert "failed 0/100 at " in result.output and "grip last" in result.output

    terminal(GOOD)
    assert runner.invoke(cli, ["quiz", "--branch"]).exit_code == 0
    result = runner.invoke(cli, ["status"])
    assert "passed 100/100 at " in result.output
