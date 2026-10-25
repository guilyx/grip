from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from grip_hook import cli as cli_module
from grip_hook.cli import cli
from grip_hook.errors import NoTerminalError
from grip_hook.git import ZERO_SHA, Git
from tests.conftest import FakeTerminalFactory

GOOD = ["this is a long and specific answer because reasons"] * 5
BAD = ["", "", "", "", ""]


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def in_repo(repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(repo)
    monkeypatch.delenv("GRIP_SKIP", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("PRE_COMMIT_FROM_REF", raising=False)
    monkeypatch.delenv("PRE_COMMIT_TO_REF", raising=False)
    (repo / ".grip.toml").write_text('provider = "fake"\nmodel = "fake"\n')
    return repo


@pytest.fixture
def terminal(monkeypatch: pytest.MonkeyPatch) -> Callable[[list[str]], FakeTerminalFactory]:
    def _install(answers: list[str]) -> FakeTerminalFactory:
        factory = FakeTerminalFactory(answers)
        monkeypatch.setattr(cli_module, "open_terminal", factory)
        return factory

    return _install


def test_version(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "grip" in result.output


def test_quiz_nothing_staged(runner: CliRunner, in_repo: Path) -> None:
    result = runner.invoke(cli, ["quiz"])
    assert result.exit_code == 0


def test_quiz_pass_then_remembered(
    runner: CliRunner,
    in_repo: Path,
    stage_change: Callable[[str, str], None],
    terminal: Callable[[list[str]], FakeTerminalFactory],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stage_change("a.py", "x = 1\n")
    factory = terminal(GOOD)
    report = in_repo / "out" / "report.json"
    result = runner.invoke(cli, ["quiz", "--report", str(report)])
    assert result.exit_code == 0, result.output
    assert "PASS" in factory.text
    data = json.loads(report.read_text())
    assert data["passed"] is True and data["score"] == 100
    assert (in_repo / ".git" / "grip" / "last-report.json").exists()

    # Same diff again: remembered, no terminal needed.
    def boom() -> None:
        raise AssertionError("terminal should not be opened")

    monkeypatch.setattr(cli_module, "open_terminal", boom)
    result = runner.invoke(cli, ["quiz"])
    assert result.exit_code == 0
    # forget, then it is quizzed again
    assert runner.invoke(cli, ["forget"]).exit_code == 0
    terminal(GOOD)
    assert runner.invoke(cli, ["quiz"]).exit_code == 0


def test_quiz_fail(
    runner: CliRunner,
    in_repo: Path,
    stage_change: Callable[[str, str], None],
    terminal: Callable[[list[str]], FakeTerminalFactory],
) -> None:
    stage_change("a.py", "x = 1\n")
    factory = terminal(BAD)
    result = runner.invoke(cli, ["quiz", "--passing-score", "10"])
    assert result.exit_code == 1
    assert "FAIL" in factory.text
    assert not (in_repo / ".git" / "grip" / "passed.json").exists()


def test_flags_override_config(
    runner: CliRunner,
    in_repo: Path,
    stage_change: Callable[[str, str], None],
    terminal: Callable[[list[str]], FakeTerminalFactory],
) -> None:
    stage_change("a.py", "x = 1\n")
    factory = terminal(["short"] * 5)  # 50 points
    result = runner.invoke(cli, ["quiz", "--passing-score", "50", "--difficulty", "easy"])
    assert result.exit_code == 0, result.output
    assert "Grip Score: 50/100" in factory.text


def test_no_terminal_skips_or_fails(
    runner: CliRunner,
    in_repo: Path,
    stage_change: Callable[[str, str], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stage_change("a.py", "x = 1\n")

    def no_tty() -> None:
        raise NoTerminalError("no tty")

    monkeypatch.setattr(cli_module, "open_terminal", no_tty)
    result = runner.invoke(cli, ["quiz"])
    assert result.exit_code == 0
    monkeypatch.setenv("GRIP_REQUIRE_TTY", "1")
    result = runner.invoke(cli, ["quiz"])
    assert result.exit_code == 2


def test_provider_error_blocks_unless_fail_open(
    runner: CliRunner,
    in_repo: Path,
    stage_change: Callable[[str, str], None],
    terminal: Callable[[list[str]], FakeTerminalFactory],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stage_change("a.py", "x = 1\n")
    terminal(GOOD)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = runner.invoke(cli, ["quiz", "--provider", "openai"])
    assert result.exit_code == 2
    assert "GRIP_SKIP" in result.output
    monkeypatch.setenv("GRIP_FAIL_OPEN", "true")
    terminal(GOOD)
    result = runner.invoke(cli, ["quiz", "--provider", "openai"])
    assert result.exit_code == 0


def test_quiz_range_and_unpushed(
    runner: CliRunner,
    in_repo: Path,
    run_git: Callable[..., str],
    stage_change: Callable[[str, str], None],
    terminal: Callable[[list[str]], FakeTerminalFactory],
) -> None:
    base = run_git("rev-parse", "HEAD").strip()
    stage_change("a.py", "x = 1\n")
    run_git("commit", "-q", "-m", "a")
    terminal(GOOD)
    assert runner.invoke(cli, ["quiz", "--range", f"{base}..HEAD"]).exit_code == 0
    assert runner.invoke(cli, ["quiz", "--range", "nonsense"]).exit_code == 2
    # No remotes at all: everything since the root commit is unpushed.
    factory = terminal(BAD)
    result = runner.invoke(cli, ["quiz", "--unpushed"])
    assert result.exit_code == 1
    assert "README.md" in factory.text


def test_hook_skip_envs(
    runner: CliRunner,
    in_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage_change: Callable[[str, str], None],
) -> None:
    stage_change("a.py", "x = 1\n")
    monkeypatch.setenv("GRIP_SKIP", "1")
    result = runner.invoke(cli, ["hook", "pre-commit"])
    assert result.exit_code == 0 and "GRIP_SKIP" in result.output
    monkeypatch.delenv("GRIP_SKIP")
    monkeypatch.setenv("CI", "true")
    result = runner.invoke(cli, ["hook", "pre-commit"])
    assert result.exit_code == 0 and "CI" in result.output


def test_hook_pre_commit(
    runner: CliRunner,
    in_repo: Path,
    stage_change: Callable[[str, str], None],
    terminal: Callable[[list[str]], FakeTerminalFactory],
) -> None:
    stage_change("a.py", "x = 1\n")
    terminal(BAD)
    assert runner.invoke(cli, ["hook", "pre-commit"]).exit_code == 1
    terminal(GOOD)
    assert runner.invoke(cli, ["hook", "pre-commit"]).exit_code == 0


def test_hook_pre_push_stdin_and_pre_commit_env(
    runner: CliRunner,
    in_repo: Path,
    tmp_path: Path,
    run_git: Callable[..., str],
    stage_change: Callable[[str, str], None],
    terminal: Callable[[list[str]], FakeTerminalFactory],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bare = tmp_path / "remote.git"
    bare.mkdir()
    Git(bare).run("init", "-q", "--bare")
    run_git("remote", "add", "origin", str(bare))
    run_git("push", "-q", "-u", "origin", "main")
    pushed = run_git("rev-parse", "HEAD").strip()
    stage_change("a.py", "x = 1\n")
    run_git("commit", "-q", "-m", "a")
    head = run_git("rev-parse", "HEAD").strip()

    stdin = f"refs/heads/main {head} refs/heads/main {pushed}\n"
    factory = terminal(GOOD)
    result = runner.invoke(cli, ["hook", "pre-push", "origin", str(bare)], input=stdin)
    assert result.exit_code == 0, result.output
    assert "a.py" in factory.text

    # Nothing to push (delete only) -> skip
    result = runner.invoke(
        cli, ["hook", "pre-push", "origin"], input=f"(delete) {ZERO_SHA} refs/heads/x {pushed}\n"
    )
    assert result.exit_code == 0

    # pre-commit framework style
    runner.invoke(cli, ["forget"])
    monkeypatch.setenv("PRE_COMMIT_FROM_REF", pushed)
    monkeypatch.setenv("PRE_COMMIT_TO_REF", head)
    terminal(BAD)
    result = runner.invoke(cli, ["hook", "pre-push"])
    assert result.exit_code == 1


def test_install_status_uninstall(runner: CliRunner, in_repo: Path) -> None:
    result = runner.invoke(cli, ["install"])
    assert result.exit_code == 0, result.output
    hook = in_repo / ".git" / "hooks" / "pre-push"
    assert hook.exists()
    if os.name != "nt":
        assert os.access(hook, os.X_OK)

    result = runner.invoke(cli, ["install", "--stage", "pre-commit", "--stage", "pre-push"])
    assert result.exit_code == 0
    assert (in_repo / ".git" / "hooks" / "pre-commit").exists()

    result = runner.invoke(cli, ["status"])
    assert result.exit_code == 0
    assert "installed" in result.output
    assert "passing_score" in result.output

    result = runner.invoke(cli, ["config"])
    assert result.exit_code == 0 and "fake" in result.output

    result = runner.invoke(cli, ["uninstall"])
    assert result.exit_code == 0
    assert not hook.exists()
    assert not (in_repo / ".git" / "hooks" / "pre-commit").exists()
    result = runner.invoke(cli, ["uninstall"])
    assert "nothing to remove" in result.output


def test_install_foreign_hook_error(runner: CliRunner, in_repo: Path) -> None:
    hook = in_repo / ".git" / "hooks" / "pre-push"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text("#!/bin/sh\necho hi\n")
    result = runner.invoke(cli, ["install"])
    assert result.exit_code == 2
    assert "--append" in result.output
    result = runner.invoke(cli, ["install", "--append"])
    assert result.exit_code == 0 and "appended" in result.output


def test_main_entry_point(in_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli_module.main(["--version"])
    assert exc.value.code == 0
    with pytest.raises(SystemExit) as exc:
        cli_module.main(["quiz", "--range", "bad"])
    assert exc.value.code == 2
    with pytest.raises(SystemExit) as exc:
        cli_module.main(["nope"])
    assert exc.value.code == 2


def test_main_outside_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        cli_module.main(["status"])
    assert exc.value.code == 2


def test_last_shows_the_graded_quiz(
    runner: CliRunner,
    in_repo: Path,
    stage_change: Callable[[str, str], None],
    terminal: Callable[[list[str]], FakeTerminalFactory],
) -> None:
    result = runner.invoke(cli, ["last"])
    assert result.exit_code == 2
    assert "no quiz has been graded" in result.output

    stage_change("a.py", "x = 1\n")
    terminal(GOOD)
    assert runner.invoke(cli, ["quiz"]).exit_code == 0

    result = runner.invoke(cli, ["last"])
    assert result.exit_code == 0, result.output
    assert "Grip Score 100/100" in result.output and "PASS" in result.output
    assert "Q1" in result.output and GOOD[0] in result.output
    assert "rubric" not in result.output.lower()

    data = json.loads(runner.invoke(cli, ["last", "--json"]).output)
    assert data["status"] == "pass" and data["score"] == 100
    assert data["answers"] == GOOD and len(data["questions"]) == 5
    assert "summary" in data and "at" in data
    assert "rubric" not in json.dumps(data).lower()


# -- paper cuts ----------------------------------------------------------------------------


class InterruptingTerminal(FakeTerminalFactory):
    """A terminal where the developer presses Ctrl-C at the first question."""

    def __call__(self) -> Any:
        term = super().__call__()

        def interrupt(prompt: str = "") -> str:
            raise KeyboardInterrupt

        term.read_line = interrupt  # type: ignore[method-assign]
        return term


def test_ctrl_c_exits_130_and_records_nothing(
    runner: CliRunner,
    in_repo: Path,
    stage_change: Callable[[str, str], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stage_change("a.py", "x = 1\n")
    monkeypatch.setattr(cli_module, "open_terminal", InterruptingTerminal([]))
    for args in (["quiz"], ["hook", "pre-commit"]):
        result = runner.invoke(cli, args)
        assert result.exit_code == 130, result.output
        assert "grip: stopped, nothing recorded." in result.output
        assert "Aborted" not in result.output
    assert not (in_repo / ".git" / "grip" / "last-report.json").exists()


def test_forget_says_when_there_was_nothing(
    runner: CliRunner,
    in_repo: Path,
    stage_change: Callable[[str, str], None],
    terminal: Callable[[list[str]], FakeTerminalFactory],
) -> None:
    assert "no remembered passes" in runner.invoke(cli, ["forget"]).output
    stage_change("a.py", "x = 1\n")
    terminal(GOOD)
    assert runner.invoke(cli, ["quiz"]).exit_code == 0
    assert "forgot 1 remembered pass." in runner.invoke(cli, ["forget"]).output
    assert "no remembered passes" in runner.invoke(cli, ["forget"]).output


def test_binary_only_changes_are_not_quizzed(
    runner: CliRunner,
    in_repo: Path,
    run_git: Callable[..., str],
    stage_change: Callable[[str, str], None],
    terminal: Callable[[list[str]], FakeTerminalFactory],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (in_repo / "blob.bin").write_bytes(b"\x00\x01\x02binary\x00")
    run_git("add", "blob.bin")

    def boom() -> None:
        raise AssertionError("terminal should not be opened")

    monkeypatch.setattr(cli_module, "open_terminal", boom)
    for args in (["quiz"], ["hook", "pre-commit"]):
        result = runner.invoke(cli, args)
        assert result.exit_code == 0, result.output
        assert "grip: only binary files changed, nothing to quiz." in result.output
    assert runner.invoke(cli, ["check"]).exit_code == 0
    payload = json.loads(runner.invoke(cli, ["ask"]).output)
    assert payload["status"] == "nothing-to-quiz"

    stage_change("a.py", "x = 1\n")  # with a text file alongside, the quiz runs
    factory = terminal(GOOD)
    assert runner.invoke(cli, ["quiz"]).exit_code == 0
    assert "PASS" in factory.text


def test_hook_paths_are_relative_to_the_repo(
    runner: CliRunner, in_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = runner.invoke(cli, ["install"])
    assert "installed at .git/hooks/pre-push" in result.output
    (in_repo / "sub").mkdir()
    monkeypatch.chdir(in_repo / "sub")
    result = runner.invoke(cli, ["status"])
    assert result.exit_code == 0, result.output
    assert ".git/hooks/pre-push" in result.output
    assert ".git/hooks/pre-commit" in result.output
    assert "…" not in result.output
    assert "removed from .git/hooks/pre-push" in runner.invoke(cli, ["uninstall"]).output


def test_grade_help_has_no_question_options(runner: CliRunner) -> None:
    grade_help = runner.invoke(cli, ["grade", "--help"]).output
    assert "--fresh" not in grade_help and "--difficulty" not in grade_help
    assert "--passing-score" in grade_help and "--provider" in grade_help
    assert "--report" in grade_help
    quiz_help = runner.invoke(cli, ["quiz", "--help"]).output
    assert "--fresh" in quiz_help and "--difficulty" in quiz_help
