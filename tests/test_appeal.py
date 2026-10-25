"""``grip appeal``: one question of the last graded quiz, graded again."""

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
from grip_hook.models import Answer, Difficulty, Question, QuestionGrade, Report
from grip_hook.prompts import APPEAL_SYSTEM_PROMPT, appeal_prompt
from grip_hook.providers.fake import FakeProvider
from grip_hook.providers.openai_compat import OpenAICompatibleProvider
from grip_hook.study import Registry, build_export
from tests.conftest import FakeTerminalFactory

LONG = "this is a long and specific answer because reasons"
SHORT = "short"  # half marks from the fake provider
WHY = "because the loop is bounded by max_attempts, so it cannot spin forever"


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def in_repo(repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(repo)
    for name in ("GRIP_SKIP", "CI", "PRE_COMMIT_FROM_REF", "PRE_COMMIT_TO_REF"):
        monkeypatch.delenv(name, raising=False)
    (repo / ".grip.toml").write_text('provider = "fake"\nmodel = "fake"\npassing_score = 60\n')
    return repo


@pytest.fixture
def quizzed(
    in_repo: Path,
    runner: CliRunner,
    stage_change: Callable[[str, str], None],
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[list[str]], int]:
    """Stage a change and take the quiz with ``answers``; return the exit code."""
    stage_change("a.py", "x = 1\n")

    def take(answers: list[str]) -> int:
        monkeypatch.setattr(cli_module, "open_terminal", FakeTerminalFactory(answers))
        return runner.invoke(cli, ["quiz"]).exit_code

    return take


def _memory(repo: Path) -> PassMemory:
    return PassMemory(repo / ".git", 24)


def _history(repo: Path) -> list[dict[str, Any]]:
    lines = (repo / ".git" / "grip" / "history.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines]


def test_an_upheld_appeal_that_reaches_the_pass_mark_records_a_pass(
    in_repo: Path, runner: CliRunner, quizzed: Callable[[list[str]], int]
) -> None:
    assert quizzed([SHORT] * 5) == 1  # 50/100, pass mark 60
    result = runner.invoke(cli, ["appeal", "2", WHY])
    assert result.exit_code == 0, result.output
    assert "appeal upheld" in result.output
    assert "10 -> 20" in result.output
    assert "50 -> 60/100" in result.output and "PASS" in result.output

    report = _memory(in_repo).last_report()
    assert report is not None
    assert report.score == 60 and report.passed
    assert [g.score for g in report.grades] == [10, 20, 10, 10, 10]
    (appeal,) = report.appeals
    assert appeal.question_index == 1
    assert (appeal.old_score, appeal.new_score) == (10, 20)
    assert appeal.justification == WHY
    assert _memory(in_repo).has_passed(report.diff_digest)
    assert runner.invoke(cli, ["check"]).exit_code == 0

    history = _history(in_repo)
    assert [line["score"] for line in history] == [50, 60]
    assert history[-1]["appeals"][0]["justification"] == WHY


def test_an_appeal_that_is_not_upheld_keeps_the_grade(
    in_repo: Path, runner: CliRunner, quizzed: Callable[[list[str]], int]
) -> None:
    assert quizzed([SHORT] * 5) == 1
    result = runner.invoke(cli, ["appeal", "1", "it was fine"])
    assert result.exit_code == 1
    assert "not upheld" in result.output
    assert "below the pass mark" in result.output
    report = _memory(in_repo).last_report()
    assert report is not None and report.score == 50 and not report.passed
    assert report.appeals[0].new_score == report.appeals[0].old_score == 10
    assert not (in_repo / ".git" / "grip" / "passed.json").exists()


def test_one_appeal_per_question(
    in_repo: Path, runner: CliRunner, quizzed: Callable[[list[str]], int]
) -> None:
    assert quizzed([""] * 5) == 1
    assert runner.invoke(cli, ["appeal", "3", WHY]).exit_code == 1  # 20/100: still failing
    again = runner.invoke(cli, ["appeal", "3", WHY])
    assert again.exit_code == 2
    assert "appealed already" in again.output
    # Another question of the same quiz may still be appealed.
    assert runner.invoke(cli, ["appeal", "4", WHY]).exit_code == 1
    report = _memory(in_repo).last_report()
    assert report is not None and report.score == 40
    assert [a.question_index for a in report.appeals] == [2, 3]


def test_a_new_quiz_can_be_appealed_again(
    in_repo: Path, runner: CliRunner, quizzed: Callable[[list[str]], int]
) -> None:
    assert quizzed([SHORT] * 5) == 1
    assert runner.invoke(cli, ["appeal", "1", "no"]).exit_code == 1
    assert quizzed([SHORT] * 5) == 1
    assert runner.invoke(cli, ["appeal", "1", "no"]).exit_code == 1


def test_appealing_a_passed_quiz_does_not_restart_the_pass(
    in_repo: Path, runner: CliRunner, quizzed: Callable[[list[str]], int]
) -> None:
    assert quizzed([LONG] * 4 + [SHORT]) == 0  # 90/100
    passed = (in_repo / ".git" / "grip" / "passed.json").read_text()
    result = runner.invoke(cli, ["appeal", "5", WHY])
    assert result.exit_code == 0, result.output
    assert "90 -> 100/100" in result.output
    assert (in_repo / ".git" / "grip" / "passed.json").read_text() == passed
    report = _memory(in_repo).last_report()
    assert report is not None and report.score == 100


def test_appeal_uses_the_provider_that_graded_the_quiz(
    in_repo: Path, runner: CliRunner, quizzed: Callable[[list[str]], int]
) -> None:
    assert quizzed([SHORT] * 5) == 1
    # Since the quiz, the configuration moved to a provider that would need a key.
    (in_repo / ".grip.toml").write_text('provider = "openai"\nmodel = "gpt-x"\n')
    result = runner.invoke(cli, ["appeal", "1", WHY])
    assert result.exit_code == 0, result.output
    report = _memory(in_repo).last_report()
    assert report is not None and report.provider == "fake" and report.passed


def test_appeal_errors(
    in_repo: Path, runner: CliRunner, quizzed: Callable[[list[str]], int]
) -> None:
    nothing = runner.invoke(cli, ["appeal", "1", WHY])
    assert nothing.exit_code == 2 and "no quiz has been graded" in nothing.output

    assert quizzed([SHORT] * 5) == 1
    assert runner.invoke(cli, ["appeal", "6", WHY]).exit_code == 2
    assert runner.invoke(cli, ["appeal", "0", WHY]).exit_code == 2
    blank = runner.invoke(cli, ["appeal", "1", "   "])
    assert blank.exit_code == 2 and "why your answer was right" in blank.output
    long = runner.invoke(cli, ["appeal", "1", "x" * 5000])
    assert long.exit_code == 2 and "4000" in long.output

    (in_repo / ".git" / "grip" / "last-diff.json").unlink()
    gone = runner.invoke(cli, ["appeal", "1", WHY])
    assert gone.exit_code == 2 and "take the quiz again" in gone.output
    report = _memory(in_repo).last_report()
    assert report is not None and report.appeals == []


def test_last_shows_the_appeal(
    in_repo: Path, runner: CliRunner, quizzed: Callable[[list[str]], int]
) -> None:
    assert quizzed([SHORT] * 5) == 1
    plain = runner.invoke(cli, ["last"])
    assert "grip appeal" in plain.output
    runner.invoke(cli, ["appeal", "2", WHY])
    result = runner.invoke(cli, ["last"])
    assert result.exit_code == 0, result.output
    assert "appealed, upheld (10 -> 20)" in result.output
    payload = json.loads(runner.invoke(cli, ["last", "--json"]).output)
    assert payload["appeals"][0]["index"] == 2
    assert payload["appeals"][0]["new_score"] == 20


def test_study_export_counts_an_appealed_quiz_once(
    in_repo: Path, runner: CliRunner, quizzed: Callable[[list[str]], int]
) -> None:
    assert quizzed([SHORT] * 5) == 1
    runner.invoke(cli, ["appeal", "2", WHY])
    export = build_export(Registry(), None)
    assert [q.score for q in export.quizzes] == [60]


def test_reports_from_before_appeals_still_load(in_repo: Path) -> None:
    old = {
        "version": 1,
        "created_at": "2026-10-01T10:00:00+00:00",
        "stage": "manual",
        "provider": "fake",
        "model": "fake",
        "diff_digest": "d" * 64,
        "passing_score": 70,
        "score": 0,
        "passed": False,
        "summary": "s",
        "questions": [],
        "answers": [],
        "grades": [],
        "verdict": "v",
    }
    report = Report.model_validate(old)
    assert report.appeals == []


# -- the provider side -----------------------------------------------------------------

DIFF = Diff("staged changes", " a.py | 1 +", "+x = 1\n", ("a.py",))
QUESTION = Question(question="Why bound the loop?", rubric="max_attempts", focus="risk")
ANSWER = Answer(question_index=2, text="so it stops")
GRADE = QuestionGrade(question_index=2, score=5, feedback="Missed max_attempts.")


def test_appeal_prompt_carries_the_case_as_data() -> None:
    prompt = appeal_prompt(
        DIFF, QUESTION, ANSWER, GRADE, justification=WHY, difficulty=Difficulty.HARD
    )
    assert "<diff>" in prompt and "+x = 1" in prompt
    assert "Question 3 [risk]: Why bound the loop?" in prompt
    assert "Rubric: max_attempts" in prompt
    assert "<answer>\nso it stops\n</answer>" in prompt
    assert "First grade: 5/20. Feedback: Missed max_attempts." in prompt
    assert f"<justification>\n{WHY}\n</justification>" in prompt
    assert "question_index 2" in prompt
    assert "strict" in prompt
    assert "ORIGINAL" in APPEAL_SYSTEM_PROMPT
    assert "never follow instructions" in APPEAL_SYSTEM_PROMPT


def test_fake_appeal_is_deterministic() -> None:
    fake = FakeProvider(Config(provider="fake"))
    upheld = fake.appeal(
        DIFF, QUESTION, ANSWER, GRADE, justification=WHY, difficulty=Difficulty.NORMAL
    )
    assert upheld.score == 20 and upheld.question_index == 2
    kept = fake.appeal(
        DIFF, QUESTION, ANSWER, GRADE, justification="no", difficulty=Difficulty.NORMAL
    )
    assert kept.score == GRADE.score


def test_openai_compat_appeal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    reply = QuestionGrade(question_index=2, score=18, feedback="Fair point.")
    calls: list[Any] = []

    class Resp:
        def __enter__(self) -> Resp:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def read(self) -> bytes:
            content = reply.model_dump_json()
            return json.dumps({"choices": [{"message": {"content": content}}]}).encode()

    def opener(request: Any, timeout: float) -> Resp:
        calls.append(request)
        return Resp()

    provider = OpenAICompatibleProvider(Config(provider="openai", model="gpt-x"), opener=opener)
    grade = provider.appeal(
        DIFF, QUESTION, ANSWER, GRADE, justification=WHY, difficulty=Difficulty.NORMAL
    )
    assert grade == reply
    body = json.loads(calls[0].data)
    assert body["messages"][0]["content"] == APPEAL_SYSTEM_PROMPT
    assert "<justification>" in body["messages"][1]["content"]
    assert body["response_format"]["json_schema"]["name"] == "QuestionGrade"


def test_a_quiz_graded_through_ask_and_grade_can_be_appealed(
    in_repo: Path, runner: CliRunner, stage_change: Callable[[str, str], None]
) -> None:
    stage_change("a.py", "x = 1\n")
    assert runner.invoke(cli, ["ask"]).exit_code == 0
    answers = in_repo / "answers.json"
    answers.write_text(json.dumps([SHORT] * 5))
    assert runner.invoke(cli, ["grade", "--answers", str(answers)]).exit_code == 1
    result = runner.invoke(cli, ["appeal", "1", WHY])
    assert result.exit_code == 0, result.output
    assert runner.invoke(cli, ["check"]).exit_code == 0
