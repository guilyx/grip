"""The question and board cache: a retry on the same diff reuses what the model wrote."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from grip_hook.cache import CachedProvider, GenerationCache
from grip_hook.cli import cli
from grip_hook.config import Config
from grip_hook.git import Diff
from grip_hook.models import Difficulty
from grip_hook.providers.fake import FakeProvider

DIFF = Diff("staged changes", " a.py | 1 +", "+x = 1\n", ("a.py",))


class Counting(FakeProvider):
    """The fake provider, counting the calls that would cost money."""

    def __init__(self) -> None:
        super().__init__(Config(provider="fake"))
        self.calls: dict[str, int] = {"questions": 0, "board": 0, "grade": 0}

    def generate_questions(self, diff: Diff, difficulty: Difficulty) -> Any:
        self.calls["questions"] += 1
        return super().generate_questions(diff, difficulty)

    def generate_board(self, diff: Diff, difficulty: Difficulty, problems: tuple[str, ...]) -> Any:
        self.calls["board"] += 1
        return super().generate_board(diff, difficulty, problems)

    def grade(self, *args: Any) -> Any:
        self.calls["grade"] += 1
        return super().grade(*args)


@pytest.fixture
def cached(tmp_path: Path) -> tuple[CachedProvider, Counting, list[str]]:
    inner = Counting()
    hits: list[str] = []
    provider = CachedProvider(
        inner, GenerationCache(tmp_path, 24), lambda kind, _when: hits.append(kind)
    )
    return provider, inner, hits


def test_a_retry_reuses_the_questions_but_grades_again(
    cached: tuple[CachedProvider, Counting, list[str]],
) -> None:
    provider, inner, hits = cached
    first = provider.generate_questions(DIFF, Difficulty.NORMAL)
    second = provider.generate_questions(DIFF, Difficulty.NORMAL)
    assert second == first
    assert inner.calls["questions"] == 1
    assert hits == ["questions"]
    for _ in range(2):
        provider.grade(DIFF, first.questions, [], Difficulty.NORMAL)
    assert inner.calls["grade"] == 2


@pytest.mark.parametrize(
    "change",
    [
        lambda d: Diff(d.description, d.stat, d.patch + "+y = 2\n", d.files),
        lambda d: Diff(d.description, d.stat, d.patch, d.files, context="a task"),
        lambda d: Diff(d.description, d.stat, d.patch, d.files, commits="Reworded"),
    ],
)
def test_another_diff_or_context_asks_again(
    cached: tuple[CachedProvider, Counting, list[str]], change: Callable[[Diff], Diff]
) -> None:
    provider, inner, _ = cached
    provider.generate_questions(DIFF, Difficulty.NORMAL)
    provider.generate_questions(change(DIFF), Difficulty.NORMAL)
    provider.generate_questions(DIFF, Difficulty.HARD)
    assert inner.calls["questions"] == 3


def test_the_key_includes_provider_and_model() -> None:
    keys = {
        GenerationCache.key("questions", DIFF, Difficulty.NORMAL, provider, model)
        for provider, model in [("codex", ""), ("claude-code", ""), ("claude-code", "opus")]
    }
    keys.add(GenerationCache.key("board", DIFF, Difficulty.NORMAL, "codex", ""))
    assert len(keys) == 4


def test_entries_expire(tmp_path: Path) -> None:
    cache = GenerationCache(tmp_path, ttl_hours=1)
    key = GenerationCache.key("questions", DIFF, Difficulty.NORMAL, "fake", "")
    questions = Counting().generate_questions(DIFF, Difficulty.NORMAL)
    cache.store(key, questions, now=datetime.now(UTC) - timedelta(hours=2))
    assert cache.load(key, type(questions)) is None
    cache.store(key, questions)
    assert cache.load(key, type(questions)) is not None


def test_an_unreadable_entry_is_a_miss(tmp_path: Path) -> None:
    cache = GenerationCache(tmp_path, ttl_hours=1)
    key = GenerationCache.key("questions", DIFF, Difficulty.NORMAL, "fake", "")
    cache.dir.mkdir(parents=True)
    (cache.dir / f"{key}.json").write_text("{not json")
    questions = Counting().generate_questions(DIFF, Difficulty.NORMAL)
    assert cache.load(key, type(questions)) is None


def test_a_board_retry_asks_the_model_and_replaces_the_entry(
    cached: tuple[CachedProvider, Counting, list[str]],
) -> None:
    provider, inner, hits = cached
    provider.generate_board(DIFF, Difficulty.NORMAL, ())
    provider.generate_board(DIFF, Difficulty.NORMAL, ("pin 2 is on a missing node",))
    assert inner.calls["board"] == 2
    provider.generate_board(DIFF, Difficulty.NORMAL, ())
    assert inner.calls["board"] == 2
    assert hits == ["board"]


# -- through the command line ------------------------------------------------------------


@pytest.fixture
def in_repo(repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(repo)
    for name in ("GRIP_SKIP", "CI", "GRIP_CACHE_HOURS"):
        monkeypatch.delenv(name, raising=False)
    (repo / ".grip.toml").write_text('provider = "fake"\nmodel = "fake"\n')
    return repo


def _ask(*args: str) -> tuple[dict[str, Any], str]:
    result = CliRunner().invoke(cli, ["ask", *args])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout), result.stderr


def test_ask_twice_reuses_and_fresh_does_not(
    in_repo: Path, stage_change: Callable[[str, str], None]
) -> None:
    stage_change("a.py", "x = 1\n")
    first, err = _ask()
    assert "same diff" not in err
    second, err = _ask()
    assert "same diff as" in err and "--fresh" in err
    assert second["questions"] == first["questions"]
    _, err = _ask("--fresh")
    assert "same diff" not in err
    assert list((in_repo / ".git" / "grip" / "cache").glob("*.json"))


def test_cache_hours_zero_turns_it_off(
    in_repo: Path, stage_change: Callable[[str, str], None], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GRIP_CACHE_HOURS", "0")
    stage_change("a.py", "x = 1\n")
    _ask()
    _, err = _ask()
    assert "same diff" not in err
    assert not (in_repo / ".git" / "grip" / "cache").exists()
