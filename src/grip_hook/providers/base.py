"""Provider interface."""

from __future__ import annotations

import shutil
import time
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from grip_hook.config import AGENT_PROVIDERS
from grip_hook.git import Diff
from grip_hook.models import (
    Answer,
    Difficulty,
    GradeSheet,
    ModelCall,
    Question,
    QuestionGrade,
    QuestionSet,
)

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


_KINDS = {"QuestionSet": "questions", "Board": "board", "GradeSheet": "grade"}


def call_kind(schema: type) -> str:
    """What a request for ``schema`` is for: ``questions``, ``board`` or ``grade``."""
    return _KINDS.get(schema.__name__, schema.__name__.lower())


class CallLog:
    """Keeps the :class:`ModelCall` of every request a provider makes, oldest first.

    Providers that time their requests inherit it; :func:`calls_of` reads it.
    """

    model_calls: list[ModelCall]

    def _log_call(
        self,
        schema: type,
        started: float,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        """Record a request for ``schema`` that began at ``time.perf_counter() == started``."""
        if not hasattr(self, "model_calls"):
            self.model_calls = []
        self.model_calls.append(
            ModelCall(
                kind=call_kind(schema),
                seconds=round(time.perf_counter() - started, 3),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        )


def calls_of(provider: object) -> list[ModelCall]:
    """The requests ``provider`` made so far; empty for one that does not keep them."""
    calls = getattr(provider, "model_calls", None)
    return list(calls) if isinstance(calls, list) else []


def token_count(value: object) -> int | None:
    """``value`` when it is a usable token count (a non-negative int), else ``None``."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


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


__all__ = ["CallLog", "Provider", "call_kind", "calls_of", "missing_credentials", "token_count"]
