"""How long a quiz took and how many tokens it used: measured, stored and shown."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from grip_hook import cli as cli_module
from grip_hook.cli import cli
from grip_hook.config import Config
from grip_hook.git import Diff
from grip_hook.memory import PassMemory
from grip_hook.models import Difficulty, ModelCall, QuestionSet, Report
from grip_hook.providers.anthropic import AnthropicProvider
from grip_hook.providers.base import calls_of, token_count
from grip_hook.providers.fake import FAKE_USAGE, FakeProvider
from grip_hook.providers.openai_compat import OpenAICompatibleProvider
from grip_hook.quiz import usage_line
from tests.conftest import FakeTerminalFactory

GOOD = ["this is a long and specific answer because reasons"] * 5
DIFF = Diff("staged changes", " a.py | 1 +", "+x = 1\n", ("a.py",))
QUIZ_TOKENS = sum(FAKE_USAGE["questions"]) + sum(FAKE_USAGE["grade"])


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


def _quiz(runner: CliRunner, monkeypatch: pytest.MonkeyPatch, *args: str) -> FakeTerminalFactory:
    factory = FakeTerminalFactory(GOOD)
    monkeypatch.setattr(cli_module, "open_terminal", factory)
    result = runner.invoke(cli, ["quiz", *args])
    assert result.exit_code == 0, result.output
    return factory


def _report(repo: Path) -> Report:
    report = PassMemory(repo / ".git", 24).last_report()
    assert report is not None
    return report


# -- the report -----------------------------------------------------------------------


def test_report_totals() -> None:
    calls = [
        ModelCall(kind="questions", seconds=6.25, input_tokens=1000, output_tokens=500),
        ModelCall(kind="grade", seconds=3.5, input_tokens=1200, output_tokens=440),
    ]
    base = Report.model_validate(
        {
            "stage": "manual",
            "provider": "fake",
            "model": "fake",
            "diff_digest": "d",
            "passing_score": 70,
            "score": 0,
            "passed": False,
            "summary": "",
            "questions": [],
            "answers": [],
            "grades": [],
            "verdict": "",
        }
    )
    assert base.calls == [] and base.seconds is None and base.tokens is None
    assert usage_line(base) is None  # a report from before grip measured

    measured = base.model_copy(update={"calls": calls})
    assert measured.seconds == pytest.approx(9.75)
    assert measured.tokens == 3140
    assert usage_line(measured) == "took 9.8 s · 3,140 tokens"

    unknown = base.model_copy(
        update={"calls": [*calls, ModelCall(kind="grade", seconds=1.0)], "provider": "codex"}
    )
    assert unknown.tokens is None
    assert usage_line(unknown) == "took 10.8 s · tokens not reported by codex"


def test_token_count_ignores_junk() -> None:
    assert token_count(12) == 12
    assert token_count(0) == 0
    for junk in (None, -1, "12", 1.5, True):
        assert token_count(junk) is None


# -- the providers --------------------------------------------------------------------


def test_fake_provider_reports_fixed_usage() -> None:
    fake = FakeProvider(Config(provider="fake"))
    questions = fake.generate_questions(DIFF, Difficulty.NORMAL)
    fake.grade(DIFF, questions.questions, [], Difficulty.NORMAL)
    calls = calls_of(fake)
    assert [c.kind for c in calls] == ["questions", "grade"]
    assert [(c.input_tokens, c.output_tokens) for c in calls] == [
        FAKE_USAGE["questions"],
        FAKE_USAGE["grade"],
    ]
    assert all(c.seconds >= 0 for c in calls)


class _Usage:
    input_tokens = 2100
    output_tokens = 640


class _Parsed:
    def __init__(self, parsed: Any, usage: Any) -> None:
        self.parsed_output = parsed
        self.stop_reason = "end_turn"
        self.stop_details = None
        self.usage = usage


class _Client:
    def __init__(self, response: Any) -> None:
        self.messages = self
        self.response = response

    def parse(self, **kwargs: Any) -> Any:
        return self.response


QUESTION_SET = FakeProvider(Config(provider="fake")).generate_questions(DIFF, Difficulty.NORMAL)


def test_anthropic_records_usage_and_time() -> None:
    client = _Client(_Parsed(QUESTION_SET, _Usage()))
    provider = AnthropicProvider(Config(model="claude-opus-5"), client=client)  # type: ignore[arg-type]
    provider.generate_questions(DIFF, Difficulty.NORMAL)
    (call,) = calls_of(provider)
    assert call.kind == "questions"
    assert (call.input_tokens, call.output_tokens) == (2100, 640)

    bare = AnthropicProvider(
        Config(model="claude-opus-5"), client=_Client(_Parsed(QUESTION_SET, None))
    )  # type: ignore[arg-type]
    bare.generate_questions(DIFF, Difficulty.NORMAL)
    assert calls_of(bare)[0].tokens is None


class _Resp:
    def __init__(self, body: dict[str, Any]) -> None:
        self.body = body

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.body).encode()


def test_openai_records_usage_when_the_server_sends_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    content = QuestionSet.model_validate(QUESTION_SET.model_dump()).model_dump_json()
    bodies = [
        {
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 900, "completion_tokens": 300, "total_tokens": 1200},
        },
        {"choices": [{"message": {"content": content}}]},
    ]

    def opener(request: Any, timeout: float) -> _Resp:
        return _Resp(bodies.pop(0))

    provider = OpenAICompatibleProvider(Config(provider="openai", model="gpt-x"), opener=opener)
    provider.generate_questions(DIFF, Difficulty.NORMAL)
    provider.generate_questions(DIFF, Difficulty.NORMAL)
    first, second = calls_of(provider)
    assert first.tokens == 1200
    assert second.tokens is None and second.seconds >= 0


# -- the commands ---------------------------------------------------------------------


def test_quiz_prints_and_stores_time_and_tokens(
    in_repo: Path,
    runner: CliRunner,
    stage_change: Callable[[str, str], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stage_change("a.py", "x = 1\n")
    factory = _quiz(runner, monkeypatch)
    assert "took " in factory.text and f"{QUIZ_TOKENS:,} tokens" in factory.text

    report = _report(in_repo)
    assert [c.kind for c in report.calls] == ["questions", "grade"]
    assert report.tokens == QUIZ_TOKENS

    result = runner.invoke(cli, ["last"])
    assert result.exit_code == 0, result.output
    assert f"{QUIZ_TOKENS:,} tokens" in result.output
    assert "(questions " in result.output and ", grading " in result.output
    payload = json.loads(runner.invoke(cli, ["last", "--json"]).output)
    assert payload["tokens"] == QUIZ_TOKENS
    assert [c["kind"] for c in payload["calls"]] == ["questions", "grade"]


def test_a_retry_from_the_cache_only_counts_grading(
    in_repo: Path,
    runner: CliRunner,
    stage_change: Callable[[str, str], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stage_change("a.py", "x = 1\n")
    monkeypatch.setattr(cli_module, "open_terminal", FakeTerminalFactory([""] * 5))
    assert runner.invoke(cli, ["quiz"]).exit_code == 1
    _quiz(runner, monkeypatch)
    report = _report(in_repo)
    assert [c.kind for c in report.calls] == ["grade"]
    assert report.tokens == sum(FAKE_USAGE["grade"])


def test_ask_then_grade_keeps_both_calls(
    in_repo: Path, runner: CliRunner, stage_change: Callable[[str, str], None]
) -> None:
    stage_change("a.py", "x = 1\n")
    assert runner.invoke(cli, ["ask"]).exit_code == 0
    answers = in_repo / "answers.json"
    answers.write_text(json.dumps(GOOD))
    result = runner.invoke(cli, ["grade", "--answers", str(answers)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["tokens"] == QUIZ_TOKENS
    assert payload["seconds"] >= 0
    assert [c.kind for c in _report(in_repo).calls] == ["questions", "grade"]


def test_status_averages_the_recent_quizzes(
    in_repo: Path,
    runner: CliRunner,
    stage_change: Callable[[str, str], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = runner.invoke(cli, ["status"])
    assert "not measured yet" in before.output

    for i in range(3):
        stage_change(f"f{i}.py", f"x = {i}\n")
        _quiz(runner, monkeypatch, "--fresh")
    result = runner.invoke(cli, ["status"])
    assert result.exit_code == 0, result.output
    assert f"{QUIZ_TOKENS:,} tokens per quiz" in result.output
    assert "over 3 quizzes" in result.output


def test_recent_reads_the_tail_of_the_history(in_repo: Path) -> None:
    memory = PassMemory(in_repo / ".git", 24)
    assert memory.recent(20) == []
    template = {
        "stage": "manual",
        "provider": "fake",
        "model": "fake",
        "passing_score": 70,
        "passed": True,
        "summary": "",
        "questions": [],
        "answers": [],
        "grades": [],
        "verdict": "",
    }
    for i in range(25):
        memory.append_history(Report.model_validate({**template, "diff_digest": "d", "score": i}))
    with memory.history_path.open("a", encoding="utf-8") as fh:
        fh.write("not json\n\n")
    recent = memory.recent(20)
    assert [r.score for r in recent] == list(range(5, 25))
