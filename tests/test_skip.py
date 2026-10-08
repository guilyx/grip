"""Tests for `grip skip`, `grip resume`, the `grip status` activity lines and `grip statusline`."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from grip_hook import QUESTION_COUNT
from grip_hook import cli as cli_module
from grip_hook.cli import cli
from grip_hook.memory import SkipState
from tests.conftest import FakeTerminalFactory

GOOD = ["this is a long and specific answer because reasons"] * QUESTION_COUNT


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def in_repo(repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(repo)
    monkeypatch.delenv("GRIP_SKIP", raising=False)
    monkeypatch.delenv("CI", raising=False)
    (repo / ".grip.toml").write_text('provider = "fake"\nmodel = "fake"\n')
    return repo


@pytest.fixture
def terminal(monkeypatch: pytest.MonkeyPatch) -> Callable[[list[str]], FakeTerminalFactory]:
    def _install(answers: list[str]) -> FakeTerminalFactory:
        factory = FakeTerminalFactory(answers)
        monkeypatch.setattr(cli_module, "open_terminal", factory)
        return factory

    return _install


# -- SkipState -----------------------------------------------------------------------------


def test_once_is_consumed_exactly_once(tmp_path: Path) -> None:
    state = SkipState(tmp_path)
    assert state.consume() is None and state.describe() is None
    state.set_once()
    assert state.describe() == "the next hook run"
    assert state.consume() is not None
    assert state.consume() is None
    assert not state.path.exists()


def test_until_pauses_then_expires(tmp_path: Path) -> None:
    state = SkipState(tmp_path)
    now = datetime(2026, 1, 1, 12, tzinfo=UTC)
    state.set_until(now + timedelta(hours=2))
    assert state.describe(now) == "paused until 2026-01-01 14:00 UTC"
    assert state.consume(now + timedelta(hours=1)) is not None
    assert state.consume(now + timedelta(hours=1)) is not None  # a pause is not used up
    assert state.consume(now + timedelta(hours=3)) is None
    assert not state.path.exists()  # expired entries are cleaned away


def test_corrupt_file_means_no_skip(tmp_path: Path) -> None:
    state = SkipState(tmp_path)
    state.path.parent.mkdir()
    state.path.write_text("{nope")
    assert state.consume() is None
    state.path.write_text('{"until": "not a date", "once": "yes"}')
    assert state.consume() is not None  # a truthy once still counts
    assert state.consume() is None


# -- hooks honour it ------------------------------------------------------------------------


def test_skip_once_lets_one_hook_run_through(
    runner: CliRunner,
    in_repo: Path,
    stage_change: Callable[[str, str], None],
    terminal: Callable[[list[str]], FakeTerminalFactory],
) -> None:
    stage_change("a.py", "x = 1\n")
    assert runner.invoke(cli, ["skip"]).exit_code == 0
    result = runner.invoke(cli, ["hook", "pre-commit"])
    assert result.exit_code == 0, result.output
    assert "skipping the quiz" in result.output

    factory = terminal(GOOD)
    result = runner.invoke(cli, ["hook", "pre-commit"])
    assert result.exit_code == 0, result.output
    assert "PASS" in factory.text  # the second run was quizzed


def test_pause_and_resume(
    runner: CliRunner,
    in_repo: Path,
    stage_change: Callable[[str, str], None],
    terminal: Callable[[list[str]], FakeTerminalFactory],
) -> None:
    stage_change("a.py", "x = 1\n")
    result = runner.invoke(cli, ["skip", "--hours", "1"])
    assert result.exit_code == 0 and "paused until" in result.output
    for _ in range(2):
        assert "paused" in runner.invoke(cli, ["hook", "pre-commit"]).output
    result = runner.invoke(cli, ["status"])
    assert "paused until" in result.output

    assert "resume from the next run" in runner.invoke(cli, ["resume"]).output
    assert "nothing was skipped" in runner.invoke(cli, ["resume"]).output
    factory = terminal(GOOD)
    assert runner.invoke(cli, ["hook", "pre-commit"]).exit_code == 0
    assert "PASS" in factory.text


def test_manual_quiz_ignores_skip(
    runner: CliRunner,
    in_repo: Path,
    stage_change: Callable[[str, str], None],
    terminal: Callable[[list[str]], FakeTerminalFactory],
) -> None:
    stage_change("a.py", "x = 1\n")
    runner.invoke(cli, ["skip"])
    factory = terminal(GOOD)
    assert runner.invoke(cli, ["quiz"]).exit_code == 0
    assert "PASS" in factory.text
    assert SkipState(in_repo / ".git").describe() == "the next hook run"


def test_agent_hook_honours_skip(
    runner: CliRunner, in_repo: Path, stage_change: Callable[[str, str], None]
) -> None:
    stage_change("a.py", "x = 1\n")
    payload = json.dumps(
        {"tool_name": "Bash", "tool_input": {"command": "git commit -m x"}, "cwd": str(in_repo)}
    )
    args = ["agent-hook", "claude-code", "--gate", "both"]
    assert "deny" in runner.invoke(cli, args, input=payload).output
    runner.invoke(cli, ["skip"])
    assert runner.invoke(cli, args, input=payload).output.strip() == ""
    assert "deny" in runner.invoke(cli, args, input=payload).output


# -- status and statusline -----------------------------------------------------------------


def test_status_activity(
    runner: CliRunner,
    in_repo: Path,
    stage_change: Callable[[str, str], None],
    terminal: Callable[[list[str]], FakeTerminalFactory],
) -> None:
    result = runner.invoke(cli, ["status"])
    assert result.exit_code == 0, result.output
    assert "none yet" in result.output and "nothing" in result.output

    stage_change("a.py", "x = 1\n")
    result = runner.invoke(cli, ["status"])
    assert "not quizzed yet" in result.output

    terminal(GOOD)
    assert runner.invoke(cli, ["quiz"]).exit_code == 0
    result = runner.invoke(cli, ["status"])
    assert "100/100 PASS" in result.output and "just now" in result.output
    assert "passed, remembered" in result.output


def test_statusline(
    runner: CliRunner,
    in_repo: Path,
    stage_change: Callable[[str, str], None],
    terminal: Callable[[list[str]], FakeTerminalFactory],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert runner.invoke(cli, ["statusline"]).output == ""
    data = json.loads(runner.invoke(cli, ["statusline", "--json"]).output)
    assert data["repo"] is True and data["score"] is None

    stage_change("a.py", "x = 1\n")
    terminal(GOOD)
    runner.invoke(cli, ["quiz"])
    assert runner.invoke(cli, ["statusline"]).output.strip() == "grip 100/100 pass"
    runner.invoke(cli, ["skip"])
    assert runner.invoke(cli, ["statusline"]).output.strip() == "grip 100/100 pass paused"

    # Claude Code passes a JSON payload with the working directory on stdin.
    payload = json.dumps({"cwd": str(in_repo), "model": {"display_name": "x"}})
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    monkeypatch.chdir(outside)
    assert "100/100" in runner.invoke(cli, ["statusline"], input=payload).output
    assert runner.invoke(cli, ["statusline"], input="{}").output == ""
    data = json.loads(runner.invoke(cli, ["statusline", "--json"], input="{}").output)
    assert data == {"repo": False}


def test_ago() -> None:
    now = datetime(2026, 1, 2, tzinfo=UTC)
    assert cli_module._ago(now, now) == "just now"
    assert cli_module._ago(now - timedelta(minutes=1), now) == "1 minute ago"
    assert cli_module._ago(now - timedelta(hours=5), now) == "5 hours ago"
    assert cli_module._ago(now - timedelta(days=3), now) == "3 days ago"
