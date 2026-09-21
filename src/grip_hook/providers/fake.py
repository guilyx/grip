"""A deterministic provider for tests, demos and ``grip quiz --provider fake``.

Two modes:

* **Default.** Questions are derived from the changed file names; an answer scores full
  marks when it mentions the word ``because`` or is at least twenty characters long,
  half marks when it is non-empty, and zero otherwise.
* **Scripted.** When ``GRIP_FAKE_SCRIPT`` points to a JSON file, questions, grading rules
  and verdicts come from that file (see :func:`load_script`). This is how the demo
  recording and some tests get realistic, reproducible output without a network.

``GRIP_FAKE_DELAY`` (seconds, float) makes each call sleep, so the spinner is visible.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from grip_hook import POINTS_PER_QUESTION, QUESTION_COUNT
from grip_hook.config import Config
from grip_hook.errors import ProviderError
from grip_hook.git import Diff
from grip_hook.models import Answer, Difficulty, GradeSheet, Question, QuestionGrade, QuestionSet

SCRIPT_ENV = "GRIP_FAKE_SCRIPT"
DELAY_ENV = "GRIP_FAKE_DELAY"

_TEMPLATES = (
    ("behaviour", "What does the change to {file} do, in one or two sentences?"),
    ("motivation", "Why was the change to {file} needed?"),
    ("edge case", "Which input or situation could make the change to {file} misbehave?"),
    ("risk", "What else in the codebase could be affected by the change to {file}?"),
    ("testing", "How would you verify the change to {file} works as intended?"),
)


class GradeRule(BaseModel):
    """Award ``score`` when the lower-cased answer contains ``match``."""

    match: str
    score: int = Field(ge=0, le=POINTS_PER_QUESTION)
    feedback: str


class DefaultGrade(BaseModel):
    """Grade used when no rule matches."""

    score: int = Field(default=0, ge=0, le=POINTS_PER_QUESTION)
    feedback: str = "No answer, or nothing that shows understanding of the change."


class ScriptedQuestion(Question):
    """A question plus the rules used to grade answers to it."""

    rules: list[GradeRule] = Field(default_factory=list)
    default: DefaultGrade = Field(default_factory=DefaultGrade)

    def grade(self, answer: str) -> tuple[int, str]:
        """First matching rule wins; rules are checked in order."""
        text = answer.lower()
        for rule in self.rules:
            if rule.match.lower() in text:
                return rule.score, rule.feedback
        return self.default.score, self.default.feedback


class Verdict(BaseModel):
    """Verdict text for totals at or above ``min``."""

    min: int = Field(ge=0, le=100)
    text: str


class Script(BaseModel):
    """The scripted provider's data file."""

    summary: str
    questions: list[ScriptedQuestion] = Field(min_length=QUESTION_COUNT, max_length=QUESTION_COUNT)
    verdicts: list[Verdict] = Field(default_factory=list)

    def verdict_for(self, total: int) -> str:
        """Pick the verdict with the highest ``min`` not above ``total``."""
        eligible = [v for v in self.verdicts if v.min <= total]
        if not eligible:
            return f"Scripted verdict: {total} points."
        return max(eligible, key=lambda v: v.min).text


def load_script(path: Path) -> Script:
    """Read and validate a script file."""
    try:
        data: Any = json.loads(path.read_text("utf-8"))
        return Script.model_validate(data)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise ProviderError(f"could not load {SCRIPT_ENV}={path}: {exc}") from exc


class FakeProvider:
    """Offline stand-in for a real model."""

    name = "fake"

    def __init__(self, cfg: Config) -> None:
        self.model = cfg.model or "fake"
        script_path = os.environ.get(SCRIPT_ENV)
        self.script = load_script(Path(script_path)) if script_path else None
        try:
            self.delay = float(os.environ.get(DELAY_ENV, "0") or 0)
        except ValueError:
            self.delay = 0.0

    def _pause(self) -> None:
        if self.delay > 0:
            time.sleep(self.delay)

    def generate_questions(self, diff: Diff, difficulty: Difficulty) -> QuestionSet:
        """Produce five questions, scripted or templated over the changed files."""
        self._pause()
        if self.script is not None:
            questions = [
                Question(question=q.question, rubric=q.rubric, focus=q.focus)
                for q in self.script.questions
            ]
            return QuestionSet(summary=self.script.summary, questions=questions)
        files = list(diff.files) or ["the diff"]
        questions = [
            Question(
                question=template.format(file=files[i % len(files)]),
                rubric="Any specific, non-empty explanation.",
                focus=focus,
            )
            for i, (focus, template) in enumerate(_TEMPLATES[:QUESTION_COUNT])
        ]
        return QuestionSet(
            summary=(
                f"Fake summary of {len(files)} changed file(s) at {difficulty.value} difficulty."
            ),
            questions=questions,
        )

    def grade(
        self,
        diff: Diff,
        questions: list[Question],
        answers: list[Answer],
        difficulty: Difficulty,
    ) -> GradeSheet:
        """Score answers deterministically, by script rules or by length."""
        self._pause()
        by_index = {a.question_index: a.text.strip() for a in answers}
        grades = []
        for i in range(len(questions)):
            text = by_index.get(i, "")
            if self.script is not None:
                score, feedback = self.script.questions[i].grade(text)
            elif not text:
                score, feedback = 0, "No answer given."
            elif "because" in text.lower() or len(text) >= 20:
                score, feedback = POINTS_PER_QUESTION, "Specific and complete."
            else:
                score, feedback = POINTS_PER_QUESTION // 2, "Too short to show understanding."
            grades.append(QuestionGrade(question_index=i, score=score, feedback=feedback))
        total = sum(g.score for g in grades)
        if self.script is not None:
            return GradeSheet(grades=grades, verdict=self.script.verdict_for(total))
        return GradeSheet(grades=grades, verdict=f"Fake verdict: {total} points.")


__all__ = ["DELAY_ENV", "SCRIPT_ENV", "FakeProvider", "Script", "load_script"]
