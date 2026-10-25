"""Data models shared across grip.

The pydantic models double as the JSON schemas sent to LLM providers for
structured output, so keep field descriptions precise: the model reads them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from grip_hook import MAX_SCORE, POINTS_PER_QUESTION, QUESTION_COUNT


class Stage(StrEnum):
    """Where in the git workflow grip is running."""

    PRE_COMMIT = "pre-commit"
    PRE_PUSH = "pre-push"
    MANUAL = "manual"


class Difficulty(StrEnum):
    """How demanding the questions and grading should be."""

    EASY = "easy"
    NORMAL = "normal"
    HARD = "hard"


class Question(BaseModel):
    """One quiz question, generated from the diff."""

    question: str = Field(
        description=(
            "A single, specific question about the change, phrased so a developer who "
            "wrote the code could answer in one to three sentences."
        )
    )
    rubric: str = Field(
        description=(
            "The key points a fully correct answer must cover. Never shown to the "
            "developer; used only for grading."
        )
    )
    focus: str = Field(
        description=(
            "A short label for what the question tests, e.g. 'behaviour', 'edge case', "
            "'motivation', 'risk', 'testing', 'dependency'."
        )
    )


class QuestionSet(BaseModel):
    """The full set of questions for one quiz."""

    summary: str = Field(
        description="One or two sentences describing what the change does, for the developer."
    )
    questions: list[Question] = Field(
        description=f"Exactly {QUESTION_COUNT} questions.",
        min_length=QUESTION_COUNT,
        max_length=QUESTION_COUNT,
    )


class Answer(BaseModel):
    """The developer's answer to a question."""

    question_index: int
    text: str

    @field_validator("text")
    @classmethod
    def _tidy(cls, text: str) -> str:
        """Line breaks kept as newlines, whatever the platform sent; outer blanks trimmed."""
        return text.replace("\r\n", "\n").replace("\r", "\n").strip()


class QuestionGrade(BaseModel):
    """The grade for a single answer."""

    question_index: int = Field(description="Zero-based index of the question being graded.")
    score: int = Field(
        ge=0,
        le=POINTS_PER_QUESTION,
        description=f"Points awarded, from 0 to {POINTS_PER_QUESTION} inclusive.",
    )
    feedback: str = Field(
        description=(
            "One or two sentences: what was right and, when points were lost, where in "
            "the code to look again and what kind of point is missing or wrong. Never "
            "state the missing rubric point itself."
        )
    )


class GradeSheet(BaseModel):
    """Grades for every answer plus an overall verdict."""

    grades: list[QuestionGrade] = Field(
        description=f"Exactly one grade per question, {QUESTION_COUNT} in total, in order.",
        min_length=QUESTION_COUNT,
        max_length=QUESTION_COUNT,
    )
    verdict: str = Field(
        description=(
            "One sentence for the developer summarising how well they understand this change."
        )
    )

    @property
    def total(self) -> int:
        """Sum of all per-question scores, capped at :data:`grip_hook.MAX_SCORE`."""
        return min(sum(g.score for g in self.grades), MAX_SCORE)


class Appeal(BaseModel):
    """A developer's appeal against the grade of one question, and how it ended."""

    question_index: int
    """Zero-based index of the appealed question."""
    justification: str
    """Why the developer says their answer was right, as they wrote it."""
    old_score: int
    """Points before the appeal."""
    new_score: int
    """Points after it: never lower than ``old_score``."""
    feedback: str
    """What the second grading said about the appeal."""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def upheld(self) -> bool:
        """Whether the appeal raised the score."""
        return self.new_score > self.old_score


class Report(BaseModel):
    """Everything about one quiz run, persisted as JSON for later inspection."""

    version: int = 1
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    stage: Stage
    provider: str
    model: str
    diff_digest: str
    passing_score: int
    score: int
    passed: bool
    summary: str
    questions: list[Question]
    answers: list[Answer]
    grades: list[QuestionGrade]
    verdict: str
    appeals: list[Appeal] = Field(default_factory=list)
    """``grip appeal`` re-grades, oldest first; at most one per question."""

    @property
    def max_score(self) -> int:
        """The maximum reachable score."""
        return MAX_SCORE
