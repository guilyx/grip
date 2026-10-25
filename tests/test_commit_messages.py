"""The messages of the commits being quizzed reach the model, as data."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from grip_hook import cli as cli_module
from grip_hook.agent import PendingStore
from grip_hook.cli import cli
from grip_hook.config import Config
from grip_hook.git import COMMITS_MAX_BYTES, Diff, Git
from grip_hook.models import Difficulty
from grip_hook.providers.fake import FakeProvider
from tests.conftest import FakeTerminalFactory

GOOD = ["this is a long and specific answer because reasons"] * 5


def _commit(repo: Path, run_git: Callable[..., str], name: str, *message: str) -> str:
    (repo / name).write_text(f"{name} = 1\n")
    run_git("add", name)
    args = [part for m in message for part in ("-m", m)]
    run_git("commit", "-q", *args)
    return run_git("rev-parse", "HEAD").strip()


class Recording(FakeProvider):
    """The fake provider, remembering the commit messages each call saw."""

    seen: list[tuple[str, str]]

    def __init__(self, cfg: Config) -> None:
        super().__init__(cfg)
        self.seen = []

    def generate_questions(self, diff: Diff, difficulty: Difficulty) -> Any:
        self.seen.append(("questions", diff.commits))
        return super().generate_questions(diff, difficulty)

    def grade(self, diff: Diff, *args: Any) -> Any:
        self.seen.append(("grade", diff.commits))
        return super().grade(diff, *args)


@pytest.fixture
def recording(monkeypatch: pytest.MonkeyPatch) -> list[Recording]:
    made: list[Recording] = []

    def factory(cfg: Config) -> Recording:
        made.append(Recording(cfg))
        return made[-1]

    monkeypatch.setattr(cli_module, "get_provider", factory)
    return made


@pytest.fixture
def in_repo(repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(repo)
    for name in ("GRIP_SKIP", "CI", "PRE_COMMIT_FROM_REF", "PRE_COMMIT_TO_REF"):
        monkeypatch.delenv(name, raising=False)
    (repo / ".grip.toml").write_text('provider = "fake"\nmodel = "fake"\ncache_hours = 0\n')
    return repo


# -- git ------------------------------------------------------------------------------


def test_commit_messages_newest_first_with_bodies(
    git: Git, repo: Path, run_git: Callable[..., str]
) -> None:
    base = run_git("rev-parse", "HEAD").strip()
    _commit(repo, run_git, "a.py", "Add a", "Because b needs it.")
    head = _commit(repo, run_git, "b.py", "Add b")
    text = git.commit_messages(base, head)
    assert text.index("Add b") < text.index("Add a")
    assert "Because b needs it." in text
    assert "init" not in text
    assert "---" in text  # one commit from the next


def test_commit_messages_from_the_empty_tree_cover_the_root(git: Git, repo: Path) -> None:
    assert "init" in git.commit_messages(git.empty_tree(), "HEAD")


def test_commit_messages_leave_out_merges(
    git: Git, repo: Path, run_git: Callable[..., str]
) -> None:
    base = run_git("rev-parse", "HEAD").strip()
    run_git("checkout", "-q", "-b", "side")
    _commit(repo, run_git, "side.py", "Side work")
    run_git("checkout", "-q", "main")
    _commit(repo, run_git, "main.py", "Main work")
    run_git("merge", "-q", "--no-ff", "-m", "Merge branch side", "side")
    text = git.commit_messages(base, "HEAD")
    assert "Side work" in text and "Main work" in text
    assert "Merge branch" not in text


def test_commit_messages_are_capped(git: Git, repo: Path, run_git: Callable[..., str]) -> None:
    base = run_git("rev-parse", "HEAD").strip()
    _commit(repo, run_git, "a.py", "Old subject", "x" * (2 * COMMITS_MAX_BYTES))
    _commit(repo, run_git, "b.py", "New subject")
    text = git.commit_messages(base, "HEAD")
    assert text.startswith("New subject")
    assert "omitted" in text
    assert len(text.encode("utf-8")) < COMMITS_MAX_BYTES + 100


def test_range_diff_carries_messages_only_when_asked(
    git: Git, repo: Path, run_git: Callable[..., str]
) -> None:
    base = run_git("rev-parse", "HEAD").strip()
    _commit(repo, run_git, "a.py", "Add a")
    assert git.range_diff(base, "HEAD").commits == ""
    with_messages = git.range_diff(base, "HEAD", commits=True)
    assert with_messages.commits == "Add a"
    assert with_messages.digest == git.range_diff(base, "HEAD").digest


def test_staged_diff_has_no_messages(git: Git, stage_change: Callable[[str, str], None]) -> None:
    stage_change("a.py", "x = 1\n")
    assert git.staged_diff().commits == ""


# -- the commands ---------------------------------------------------------------------


def test_quiz_sends_the_messages_to_questions_and_grading(
    in_repo: Path,
    run_git: Callable[..., str],
    recording: list[Recording],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = run_git("rev-parse", "HEAD").strip()
    _commit(in_repo, run_git, "a.py", "Add a", "So that b can import it.")
    monkeypatch.setattr(cli_module, "open_terminal", FakeTerminalFactory(GOOD))
    result = CliRunner().invoke(cli, ["quiz", "--range", f"{base}..HEAD"])
    assert result.exit_code == 0, result.output
    seen = recording[-1].seen
    assert [kind for kind, _ in seen] == ["questions", "grade"]
    assert all("So that b can import it." in commits for _, commits in seen)


def test_include_commit_messages_false_sends_none(
    in_repo: Path,
    run_git: Callable[..., str],
    recording: list[Recording],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _commit(in_repo, run_git, "a.py", "Add a")
    monkeypatch.setenv("GRIP_INCLUDE_COMMIT_MESSAGES", "false")
    monkeypatch.setattr(cli_module, "open_terminal", FakeTerminalFactory(GOOD))
    result = CliRunner().invoke(cli, ["quiz", "--unpushed"])
    assert result.exit_code == 0, result.output
    assert recording[-1].seen == [("questions", ""), ("grade", "")]


def test_ask_keeps_the_messages_for_grade(
    in_repo: Path, run_git: Callable[..., str], recording: list[Recording]
) -> None:
    _commit(in_repo, run_git, "a.py", "Add a", "Why: b imports it.")
    runner = CliRunner()
    assert runner.invoke(cli, ["ask", "--unpushed"]).exit_code == 0
    pending = PendingStore(in_repo / ".git").load()
    assert pending is not None and "Why: b imports it." in pending.diff.commits

    answers = in_repo / "answers.json"
    answers.write_text('["' + GOOD[0] + '"]')
    runner.invoke(cli, ["grade", "--answers", str(answers)])
    assert ("grade", pending.commits) in recording[-1].seen
