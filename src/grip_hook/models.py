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
            "One or two sentences: what was right, what was missing or wrong. "
            "Be concrete and refer to the code."
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


class ModelCall(BaseModel):
    """One request to the model: what it was for, how long it took, what it used."""

    kind: str
    """``questions``, ``board`` or ``grade``."""
    seconds: float
    """Wall time of the request, retries included."""
    input_tokens: int | None = None
    """Tokens the model read, when the provider reports it (the APIs do; most coding-agent
    CLIs do not)."""
    output_tokens: int | None = None
    """Tokens the model wrote, when the provider reports it."""

    @property
    def tokens(self) -> int | None:
        """Input plus output tokens, or ``None`` when either is unknown."""
        if self.input_tokens is None or self.output_tokens is None:
            return None
        return self.input_tokens + self.output_tokens


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
    calls: list[ModelCall] = Field(default_factory=list)
    """The model requests behind this report, oldest first. Questions or a board reused
    from the cache made none. Empty in reports from before grip measured them."""

    @property
    def max_score(self) -> int:
        """The maximum reachable score."""
        return MAX_SCORE

    @property
    def seconds(self) -> float | None:
        """Time spent waiting for the model, or ``None`` when nothing was measured."""
        return sum(c.seconds for c in self.calls) if self.calls else None

    @property
    def tokens(self) -> int | None:
        """Tokens used by every request, or ``None`` when one of them did not say."""
        counts = [c.tokens for c in self.calls]
        if not counts or any(n is None for n in counts):
            return None
        return sum(n for n in counts if n is not None)
