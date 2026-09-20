"""Provider interface."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from grip_hook.git import Diff
from grip_hook.models import Answer, Difficulty, GradeSheet, Question, QuestionSet


@runtime_checkable
class Provider(Protocol):
    """Something that can write quiz questions and grade answers."""

    name: str
    """Short provider name shown in reports."""

    model: str
    """Model identifier shown in reports."""

    def generate_questions(self, diff: Diff, difficulty: Difficulty) -> QuestionSet:
        """Write the question set for ``diff``."""
        ...

    def grade(
        self,
        diff: Diff,
        questions: list[Question],
        answers: list[Answer],
        difficulty: Difficulty,
    ) -> GradeSheet:
        """Grade every answer in one go."""
        ...


__all__ = ["Provider"]
