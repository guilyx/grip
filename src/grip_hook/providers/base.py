"""Provider interface."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from grip_hook.git import Diff
from grip_hook.models import Answer, Difficulty, GradeSheet, Question, QuestionSet

if TYPE_CHECKING:
    from grip_hook.whiteboard import Board


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

    def generate_board(
        self, diff: Diff, difficulty: Difficulty, problems: tuple[str, ...]
    ) -> Board:
        """Draw the whiteboard for ``diff``; ``problems`` explain why a first try was rejected."""
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
