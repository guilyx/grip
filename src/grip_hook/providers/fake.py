"""A deterministic provider for tests, demos and ``grip quiz --provider fake``.

Questions are derived from the changed file names; an answer scores full marks when
it mentions the word ``because`` or is at least twenty characters long, half marks
when it is non-empty, and zero otherwise. This is intentionally simple: the point is
to exercise the whole quiz flow without any network access.
"""

from __future__ import annotations

from grip_hook import POINTS_PER_QUESTION, QUESTION_COUNT
from grip_hook.config import Config
from grip_hook.git import Diff
from grip_hook.models import Answer, Difficulty, GradeSheet, Question, QuestionGrade, QuestionSet

_TEMPLATES = (
    ("behaviour", "What does the change to {file} do, in one or two sentences?"),
    ("motivation", "Why was the change to {file} needed?"),
    ("edge case", "Which input or situation could make the change to {file} misbehave?"),
    ("risk", "What else in the codebase could be affected by the change to {file}?"),
    ("testing", "How would you verify the change to {file} works as intended?"),
)


class FakeProvider:
    """Offline stand-in for a real model."""

    name = "fake"

    def __init__(self, cfg: Config) -> None:
        self.model = cfg.model or "fake"

    def generate_questions(self, diff: Diff, difficulty: Difficulty) -> QuestionSet:
        """Produce five templated questions over the changed files."""
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
        """Score answers by length, deterministically."""
        by_index = {a.question_index: a.text.strip() for a in answers}
        grades = []
        for i in range(len(questions)):
            text = by_index.get(i, "")
            if not text:
                score, feedback = 0, "No answer given."
            elif "because" in text.lower() or len(text) >= 20:
                score, feedback = POINTS_PER_QUESTION, "Specific and complete."
            else:
                score, feedback = POINTS_PER_QUESTION // 2, "Too short to show understanding."
            grades.append(QuestionGrade(question_index=i, score=score, feedback=feedback))
        total = sum(g.score for g in grades)
        return GradeSheet(grades=grades, verdict=f"Fake verdict: {total} points.")


__all__ = ["FakeProvider"]
