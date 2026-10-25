"""Diffs with nothing to explain: renames, mode changes, whitespace, skip_paths, tiny diffs."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from click.testing import CliRunner

from grip_hook import cli as cli_module
from grip_hook.cli import _trivial, cli
from grip_hook.config import Config
from grip_hook.git import Diff, DiffStats, Git, matches_path
from tests.conftest import FakeTerminalFactory

GOOD = ["this is a long and specific answer because reasons"] * 5
CODE = b"def f(x):\n    if x:\n        y = x + 1\n        return y\n    return 0\n"


@pytest.fixture
def committed(repo: Path, run_git: Callable[..., str]) -> Path:
    """The repository with ``app.py`` and ``notes.txt`` committed."""
    (repo / "app.py").write_bytes(CODE)
    (repo / "notes.txt").write_bytes(b"one\ntwo\n")
    run_git("add", "app.py", "notes.txt")
    run_git("commit", "-q", "-m", "add app")
    return repo


@pytest.fixture
def in_repo(committed: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(committed)
    for name in ("GRIP_SKIP", "CI", "PRE_COMMIT_FROM_REF", "PRE_COMMIT_TO_REF"):
        monkeypatch.delenv(name, raising=False)
    (committed / ".grip.toml").write_text('provider = "fake"\nmodel = "fake"\n')
    return committed


def _no_terminal() -> None:
    raise AssertionError("a trivial diff must not open the terminal")


# -- what git measures ----------------------------------------------------------------


def test_stats_count_added_and_removed_lines(
    git: Git, committed: Path, run_git: Callable[..., str]
) -> None:
    (committed / "app.py").write_bytes(CODE.replace(b"x + 1", b"x + 2") + b"# end\n")
    run_git("add", "app.py")
    stats = git.staged_diff().stats
    assert stats == DiffStats(added=2, removed=1, significant=3)
    assert stats.changed == 3


def test_a_pure_rename_and_a_mode_change_change_no_lines(
    git: Git, committed: Path, run_git: Callable[..., str]
) -> None:
    run_git("mv", "notes.txt", "renamed.txt")
    run_git("update-index", "--chmod=+x", "app.py")  # works on Windows too, unlike chmod
    diff = git.staged_diff()
    assert not diff.is_empty
    assert set(diff.files) == {"app.py", "renamed.txt"}
    assert diff.stats is not None and diff.stats.changed == 0


def test_whitespace_inside_lines_is_not_significant(
    git: Git, committed: Path, run_git: Callable[..., str]
) -> None:
    (committed / "app.py").write_bytes(CODE.replace(b"y = x + 1", b"y = x  +  1   "))
    run_git("add", "app.py")
    stats = git.staged_diff().stats
    assert stats is not None and stats.changed == 2 and stats.significant == 0


def test_a_reindent_is_significant(git: Git, committed: Path, run_git: Callable[..., str]) -> None:
    # `git diff -w` calls this empty, but it moves the return out of the `if`.
    (committed / "app.py").write_bytes(CODE.replace(b"        return y", b"    return y"))
    run_git("add", "app.py")
    stats = git.staged_diff().stats
    assert stats is not None and stats.changed == 2 and stats.significant == 2


def test_a_binary_file_is_flagged(git: Git, committed: Path, run_git: Callable[..., str]) -> None:
    (committed / "logo.bin").write_bytes(b"\x00\x01\x02binary\x00")
    run_git("add", "logo.bin")
    stats = git.staged_diff().stats
    assert stats is not None and stats.binary


def test_push_diff_adds_up_the_stats_of_each_ref() -> None:
    a = DiffStats(added=1, removed=2, significant=0)
    b = DiffStats(added=3, removed=0, significant=3, binary=True)
    assert a + b == DiffStats(added=4, removed=2, significant=3, binary=True)


@pytest.mark.parametrize(
    ("path", "patterns", "expected"),
    [
        ("README.md", ("*.md",), True),
        ("docs/guide/intro.md", ("*.md",), True),
        ("docs/guide/intro.md", ("docs/**",), True),
        ("docs/guide/intro.md", ("docs",), True),
        ("docs/guide/intro.md", ("docs/",), True),
        ("src/docs/x.py", ("docs/**",), False),
        ("src/docs/x.py", ("docs",), True),  # no slash: a docs directory at any depth
        ("docs/x.py", ("docs/*.md",), False),
        ("docs/a/b.md", ("docs/*.md",), False),
        ("docs/a/b.md", ("docs/**/*.md",), True),
        ("docs/b.md", ("docs/**/*.md",), True),
        ("src/app.py", ("*.md", "docs/**"), False),
        ("CHANGELOG.md", ("/CHANGELOG.md",), True),
        ("a/CHANGELOG.md", ("/CHANGELOG.md",), False),  # a leading slash anchors it
        ("notes.txt", ("note?.txt",), True),
        ("a+b.md", ("a+b.md",), True),
        ("anything", ("",), False),
    ],
)
def test_matches_path(path: str, patterns: tuple[str, ...], expected: bool) -> None:
    assert matches_path(path, patterns) is expected


# -- what is trivial ------------------------------------------------------------------


def _diff(files: tuple[str, ...], stats: DiffStats | None) -> Diff:
    return Diff("staged changes", "", "diff --git a/x b/x\n", files, stats=stats)


@pytest.mark.parametrize(
    ("stats", "cfg", "reason"),
    [
        (DiffStats(0, 0, 0), Config(), "no lines"),
        (DiffStats(3, 3, 0), Config(), "whitespace"),
        (DiffStats(1, 1, 2), Config(min_changed_lines=3), "2 changed lines"),
        (DiffStats(1, 0, 1), Config(min_changed_lines=3), "1 changed line,"),
        (DiffStats(2, 1, 3), Config(min_changed_lines=3), None),
        (DiffStats(1, 0, 1), Config(), None),
        (DiffStats(0, 0, 0, binary=True), Config(min_changed_lines=50), None),
        (None, Config(min_changed_lines=50), None),
    ],
)
def test_trivial(stats: DiffStats | None, cfg: Config, reason: str | None) -> None:
    found = _trivial(_diff(("app.py",), stats), cfg)
    if reason is None:
        assert found is None
    else:
        assert found is not None and reason in found


def test_skip_paths_needs_every_file_to_match() -> None:
    cfg = Config(skip_paths=("docs/**", "*.md"))
    stats = DiffStats(40, 0, 40)
    assert "skip_paths" in (_trivial(_diff(("docs/a.md", "README.md"), stats), cfg) or "")
    assert _trivial(_diff(("docs/a.md", "src/app.py"), stats), cfg) is None
    assert _trivial(_diff(("README.md",), stats), Config()) is None


# -- the commands ---------------------------------------------------------------------


def test_quiz_says_why_it_skips_a_rename_and_force_quizzes_it(
    in_repo: Path, run_git: Callable[..., str], monkeypatch: pytest.MonkeyPatch
) -> None:
    run_git("mv", "notes.txt", "renamed.txt")
    runner = CliRunner()
    monkeypatch.setattr(cli_module, "open_terminal", _no_terminal)
    result = runner.invoke(cli, ["quiz"])
    assert result.exit_code == 0, result.output
    assert "changes no lines" in result.output
    assert "--force" in result.output

    factory = FakeTerminalFactory(GOOD)
    monkeypatch.setattr(cli_module, "open_terminal", factory)
    result = runner.invoke(cli, ["quiz", "--force"])
    assert result.exit_code == 0, result.output
    assert "PASS" in factory.text


def test_hook_skips_whitespace_only_changes(
    in_repo: Path, run_git: Callable[..., str], monkeypatch: pytest.MonkeyPatch
) -> None:
    (in_repo / "app.py").write_bytes(CODE.replace(b"return 0", b"return 0   "))
    run_git("add", "app.py")
    monkeypatch.setattr(cli_module, "open_terminal", _no_terminal)
    result = CliRunner().invoke(cli, ["hook", "pre-commit"])
    assert result.exit_code == 0, result.output
    assert "only changes whitespace" in result.output
    assert "skipping the quiz" in result.output


def test_hook_still_quizzes_a_reindent(
    in_repo: Path, run_git: Callable[..., str], monkeypatch: pytest.MonkeyPatch
) -> None:
    (in_repo / "app.py").write_bytes(CODE.replace(b"        return y", b"    return y"))
    run_git("add", "app.py")
    factory = FakeTerminalFactory([""] * 5)
    monkeypatch.setattr(cli_module, "open_terminal", factory)
    result = CliRunner().invoke(cli, ["hook", "pre-commit"])
    assert result.exit_code == 1
    assert "FAIL" in factory.text


def test_skip_paths_and_min_changed_lines_from_config(
    in_repo: Path, run_git: Callable[..., str], monkeypatch: pytest.MonkeyPatch
) -> None:
    (in_repo / ".grip.toml").write_text(
        'provider = "fake"\nskip_paths = ["docs/**", "*.md"]\nmin_changed_lines = 3\n'
    )
    monkeypatch.setattr(cli_module, "open_terminal", _no_terminal)
    runner = CliRunner()

    (in_repo / "docs").mkdir()
    (in_repo / "docs" / "guide.md").write_bytes(b"# Guide\n\nLots of words.\n" * 10)
    run_git("add", "docs/guide.md")
    result = runner.invoke(cli, ["hook", "pre-commit"])
    assert result.exit_code == 0 and "skip_paths" in result.output
    assert runner.invoke(cli, ["check"]).exit_code == 0

    run_git("reset", "-q")
    (in_repo / "app.py").write_bytes(CODE.replace(b"return 0", b"return -1"))
    run_git("add", "app.py")
    result = runner.invoke(cli, ["hook", "pre-commit"])
    assert result.exit_code == 0 and "min_changed_lines" in result.output
    assert runner.invoke(cli, ["check"]).exit_code == 0


def test_check_still_blocks_a_real_change(in_repo: Path, run_git: Callable[..., str]) -> None:
    (in_repo / "app.py").write_bytes(CODE + b"def g():\n    return 2\n")
    run_git("add", "app.py")
    assert CliRunner().invoke(cli, ["check"]).exit_code == 1
