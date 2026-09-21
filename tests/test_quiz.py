from collections.abc import Callable

import pytest

from grip_hook import MAX_SCORE, QUESTION_COUNT
from grip_hook.config import Config
from grip_hook.errors import ProviderError
from grip_hook.git import Diff
from grip_hook.models import (
    Answer,
    Difficulty,
    GradeSheet,
    Question,
    QuestionGrade,
    QuestionSet,
    Stage,
)
from grip_hook.providers.fake import FakeProvider
from grip_hook.quiz import run_quiz
from tests.conftest import FakeTerminalFactory

DIFF = Diff("staged changes", " a.py | 1 +", "+print('hi')\n", ("a.py",))


def test_pass(make_terminal: Callable[[list[str]], FakeTerminalFactory]) -> None:
    factory = make_terminal(["It prints hi because we want output"] * QUESTION_COUNT)
    term = factory()
    cfg = Config(provider="fake", passing_score=70)
    outcome = run_quiz(
        diff=DIFF, cfg=cfg, provider=FakeProvider(cfg), term=term, stage=Stage.MANUAL
    )
    assert outcome.passed
    assert outcome.report.score == MAX_SCORE
    assert len(outcome.report.questions) == QUESTION_COUNT
    assert len(outcome.report.answers) == QUESTION_COUNT
    text = factory.text
    assert "Grip Score: 100/100" in text
    assert "PASS" in text
    assert "Q1/5" in text and "Q5/5" in text


def test_fail_and_empty_answers(make_terminal: Callable[[list[str]], FakeTerminalFactory]) -> None:
    factory = make_terminal(["short", ""])  # remaining answers hit EOF -> empty
    term = factory()
    cfg = Config(provider="fake", passing_score=70)
    outcome = run_quiz(
        diff=DIFF, cfg=cfg, provider=FakeProvider(cfg), term=term, stage=Stage.PRE_COMMIT
    )
    assert not outcome.passed
    assert outcome.report.score == 10
    assert "FAIL" in factory.text
    assert outcome.report.stage is Stage.PRE_COMMIT
    assert outcome.report.diff_digest == DIFF.digest


class BadCountProvider:
    name = "bad"
    model = "bad"

    def __init__(self, questions: int, grades: int) -> None:
        self.q = questions
        self.g = grades

    def generate_questions(self, diff: Diff, difficulty: Difficulty) -> QuestionSet:
        qs = [Question(question=f"q{i}", rubric="r", focus="f") for i in range(self.q)]
        return QuestionSet.model_construct(summary="s", questions=qs)

    def grade(
        self, diff: Diff, questions: list[Question], answers: list[Answer], difficulty: Difficulty
    ) -> GradeSheet:
        gs = [QuestionGrade(question_index=i, score=20, feedback="ok") for i in range(self.g)]
        return GradeSheet.model_construct(grades=gs, verdict="v")


def test_wrong_question_count_is_a_provider_error(
    make_terminal: Callable[[list[str]], FakeTerminalFactory],
) -> None:
    term = make_terminal(["a"] * 5)()
    with pytest.raises(ProviderError, match="questions"):
        run_quiz(
            diff=DIFF, cfg=Config(), provider=BadCountProvider(3, 5), term=term, stage=Stage.MANUAL
        )
    term = make_terminal(["a"] * 5)()
    with pytest.raises(ProviderError, match="grades"):
        run_quiz(
            diff=DIFF, cfg=Config(), provider=BadCountProvider(5, 4), term=term, stage=Stage.MANUAL
        )


def test_grade_sheet_total_is_capped() -> None:
    grades = [QuestionGrade(question_index=i, score=20, feedback="") for i in range(5)]
    sheet = GradeSheet(grades=grades, verdict="v")
    assert sheet.total == 100
    with pytest.raises(ValueError, match="less than or equal to 20"):
        QuestionGrade(question_index=0, score=21, feedback="")
