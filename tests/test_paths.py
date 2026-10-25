"""Tests for ``[[paths]]``: a different pass mark, difficulty or skip per part of the repo."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from click.testing import CliRunner

from grip_hook import cli as cli_module
from grip_hook.cli import cli
from grip_hook.config import Config, PathRule, describe, load_config, path_matches
from grip_hook.errors import ConfigError
from grip_hook.models import Difficulty
from tests.conftest import FakeTerminalFactory

SHORT = ["short"] * 5  # the fake grader gives 10 points each: 50
MONOREPO = """\
provider = "fake"
model = "fake"
passing_score = 40

[[paths]]
match = "services/payments/**"
passing_score = 90
difficulty = "hard"

[[paths]]
match = ["docs/**", "*.md"]
skip = true

[[paths]]
match = "prototypes"
passing_score = 20
difficulty = "easy"
"""


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def in_repo(repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(repo)
    monkeypatch.delenv("GRIP_SKIP", raising=False)
    monkeypatch.delenv("CI", raising=False)
    (repo / ".grip.toml").write_text(MONOREPO)
    return repo


@pytest.fixture
def terminal(monkeypatch: pytest.MonkeyPatch) -> Callable[[list[str]], FakeTerminalFactory]:
    def _install(answers: list[str]) -> FakeTerminalFactory:
        factory = FakeTerminalFactory(answers)
        monkeypatch.setattr(cli_module, "open_terminal", factory)
        return factory

    return _install


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    (tmp_path / ".grip.toml").write_text(MONOREPO)
    return load_config(tmp_path, env={})


# -- matching ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("docs/**", "docs/guide/intro.md", True),
        ("docs/**", "src/docs/intro.md", False),
        ("docs", "src/docs/intro.md", True),  # no slash: any depth, like exclude
        ("docs/", "docs/intro.md", True),
        ("*.md", "a/b/README.md", True),
        ("services/api", "services/api/main.py", True),  # a directory covers its files
        ("services/api", "services/api2/main.py", False),
        ("/build", "build/out.js", True),
        ("/build", "src/build/out.js", False),
        ("src/*.py", "src/pkg/mod.py", False),  # * stays inside one directory
        ("src/**/*.py", "src/mod.py", True),
        ("src/**/*.py", "src/pkg/mod.py", True),
        ("test_[ab].py", "tests/test_a.py", True),
        ("test_[!ab].py", "tests/test_a.py", False),
        ("**/migrations/**", "apps/shop/migrations/0001.py", True),
    ],
)
def test_path_matches(pattern: str, path: str, expected: bool) -> None:
    assert path_matches(pattern, path) is expected


# -- parsing -----------------------------------------------------------------------------


def test_paths_are_parsed_from_grip_toml(cfg: Config) -> None:
    assert cfg.paths == (
        PathRule(("services/payments/**",), passing_score=90, difficulty=Difficulty.HARD),
        PathRule(("docs/**", "*.md"), skip=True),
        PathRule(("prototypes",), passing_score=20, difficulty=Difficulty.EASY),
    )
    assert cfg.passing_score == 40


def test_paths_from_pyproject_and_grip_toml_replaces_them(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.grip]\npassing_score = 60\n\n[[tool.grip.paths]]\nmatch = "api/**"\n'
        "passing_score = 80\n"
    )
    cfg = load_config(tmp_path, env={})
    assert cfg.paths == (PathRule(("api/**",), passing_score=80),)
    assert cfg.for_files(["api/x.py"]).passing_score == 80

    (tmp_path / ".grip.toml").write_text('[[paths]]\nmatch = "web/**"\nskip = true\n')
    assert load_config(tmp_path, env={}).paths == (PathRule(("web/**",), skip=True),)


def test_paths_are_not_read_from_the_environment(tmp_path: Path) -> None:
    cfg = load_config(tmp_path, env={"GRIP_PATHS": "docs/**"})
    assert cfg.paths == ()


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("[[paths]]\npassing_score = 90\n", "needs `match`"),
        ("[[paths]]\nmatch = []\n", "needs `match`"),
        ("[[paths]]\nmatch = [1]\n", "needs `match`"),
        ("[[paths]]\nmatch = 'a/**'\nexclude = ['x']\n", "unknown option 'exclude'"),
        ("[[paths]]\nmatch = 'a/**'\npassing_score = 120\n", "between 0 and 100"),
        ("[[paths]]\nmatch = 'a/**'\npassing_score = 'high'\n", "must be an integer"),
        ("[[paths]]\nmatch = 'a/**'\ndifficulty = 'brutal'\n", "difficulty must be one of"),
        ("[[paths]]\nmatch = 'a/**'\nskip = 'yes'\n", "true or false"),
        ("paths = 'docs/**'\n", "array of tables"),
        ("paths = ['docs/**']\n", "must be a table"),
    ],
)
def test_invalid_paths(tmp_path: Path, content: str, message: str) -> None:
    (tmp_path / ".grip.toml").write_text(content)
    with pytest.raises(ConfigError, match=message):
        load_config(tmp_path, env={})


def test_describe_lists_the_rules(cfg: Config) -> None:
    text = dict(describe(cfg))["paths"]
    assert "services/payments/** (passing_score 90, hard)" in text
    assert "docs/**, *.md (skip)" in text
    assert dict(describe(Config()))["paths"] == "(none)"


# -- applying ----------------------------------------------------------------------------


def test_no_rules_changes_nothing() -> None:
    cfg = Config()
    assert cfg.for_files(["a.py"]) is cfg
    assert cfg.skips(["a.py"]) is False


def test_a_matching_rule_sets_the_bar(cfg: Config) -> None:
    scoped = cfg.for_files(["services/payments/charge.py"])
    assert (scoped.passing_score, scoped.difficulty) == (90, Difficulty.HARD)
    scoped = cfg.for_files(["prototypes/spike.py"])
    assert (scoped.passing_score, scoped.difficulty) == (20, Difficulty.EASY)
    scoped = cfg.for_files(["lib/util.py"])
    assert (scoped.passing_score, scoped.difficulty) == (40, Difficulty.NORMAL)


def test_the_strictest_file_wins(cfg: Config) -> None:
    scoped = cfg.for_files(["prototypes/spike.py", "services/payments/charge.py"])
    assert (scoped.passing_score, scoped.difficulty) == (90, Difficulty.HARD)
    # A rule that lowers the bar for its files never lowers it for the rest of the diff.
    scoped = cfg.for_files(["prototypes/spike.py", "lib/util.py"])
    assert (scoped.passing_score, scoped.difficulty) == (40, Difficulty.NORMAL)


def test_overlapping_rules_take_the_strictest() -> None:
    cfg = Config(
        paths=(
            PathRule(("services/**",), passing_score=80, difficulty=Difficulty.EASY),
            PathRule(("*.sql",), passing_score=60, difficulty=Difficulty.HARD),
        )
    )
    scoped = cfg.for_files(["services/db/schema.sql"])
    assert (scoped.passing_score, scoped.difficulty) == (80, Difficulty.HARD)


def test_skipped_files_do_not_count(cfg: Config) -> None:
    assert cfg.skips(["docs/intro.md", "README.md"]) is True
    assert cfg.skips(["docs/intro.md", "prototypes/spike.py"]) is False
    assert cfg.skips([]) is False
    assert cfg.for_files(["docs/intro.md", "prototypes/spike.py"]).passing_score == 20


# -- the commands ------------------------------------------------------------------------


def test_quiz_skips_a_diff_made_only_of_skipped_files(
    runner: CliRunner,
    in_repo: Path,
    stage_change: Callable[[str, str], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stage_change("docs/intro.md", "# intro\n")
    stage_change("CHANGES.md", "- a change\n")

    def boom() -> None:
        raise AssertionError("terminal should not be opened")

    monkeypatch.setattr(cli_module, "open_terminal", boom)
    for args in (["quiz"], ["hook", "pre-commit"]):
        result = runner.invoke(cli, args)
        assert result.exit_code == 0, result.output
        assert "[[paths]] rule with skip = true" in " ".join(result.output.split())
    assert runner.invoke(cli, ["check"]).exit_code == 0
    payload = json.loads(runner.invoke(cli, ["ask"]).output)
    assert payload["status"] == "nothing-to-quiz"
    assert "skip = true" in payload["message"]
    status = " ".join(runner.invoke(cli, ["status"]).output.split())
    assert "staged changes skipped by [[paths]] (2 files)" in status


def test_quiz_uses_the_bar_of_the_files_it_touches(
    runner: CliRunner,
    in_repo: Path,
    stage_change: Callable[[str, str], None],
    terminal: Callable[[list[str]], FakeTerminalFactory],
) -> None:
    stage_change("lib/util.py", "x = 1\n")
    factory = terminal(SHORT)
    assert runner.invoke(cli, ["quiz"]).exit_code == 0  # 50 clears the repository's 40
    assert "Pass mark: 40/100" in factory.text

    stage_change("services/payments/charge.py", "y = 2\n")
    factory = terminal(SHORT)
    result = runner.invoke(cli, ["quiz"])
    assert result.exit_code == 1, result.output  # the payments rule asks for 90
    assert "Pass mark: 90/100" in factory.text


def test_ask_and_grade_use_the_bar_of_the_files(
    runner: CliRunner, in_repo: Path, stage_change: Callable[[str, str], None]
) -> None:
    stage_change("services/payments/charge.py", "y = 2\n")
    payload = json.loads(runner.invoke(cli, ["ask"]).output)
    assert payload["passing_score"] == 90
    result = runner.invoke(cli, ["grade", "--answers", "-"], input=json.dumps(SHORT))
    assert result.exit_code == 1, result.output
    graded = json.loads(result.output)
    assert (graded["score"], graded["passing_score"]) == (50, 90)
