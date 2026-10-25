"""Provider interface."""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from grip_hook.config import AGENT_PROVIDERS
from grip_hook.git import Diff
from grip_hook.models import Answer, Difficulty, GradeSheet, Question, QuestionGrade, QuestionSet

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

    def appeal(
        self,
        diff: Diff,
        question: Question,
        answer: Answer,
        grade: QuestionGrade,
        *,
        justification: str,
        difficulty: Difficulty,
    ) -> QuestionGrade:
        """Grade one answer again, given the developer's case against ``grade``."""
        ...


def missing_credentials(provider: str, key_env: str) -> str:
    """What to do when ``provider`` has no key in ``key_env``: every way out, concretely.

    A coding agent installed on this machine is named, since it needs no key at all. The
    lines stay short enough not to wrap in an 80-column terminal.
    """
    installed = [name for name, command in AGENT_PROVIDERS if shutil.which(command)]
    if installed:
        others = f" (or {', '.join(installed[1:])})" if installed[1:] else ""
        agent = (
            f"use a coding agent installed here, no key needed: --provider {installed[0]}"
            f'{others}\n    or provider = "{installed[0]}" in .grip.toml'
        )
    else:
        agent = 'install Claude Code, Codex or Gemini CLI and sign in; provider = "auto"\n'
        agent += "    then uses it with no key"
    return (
        f"no credentials for the {provider} provider. Any of these fixes it:\n"
        f"  - set {key_env}: export {key_env}=...\n"
        f"  - {agent}\n"
        "  - try grip offline with --provider fake (canned questions, no model)"
    )


__all__ = ["Provider", "missing_credentials"]
