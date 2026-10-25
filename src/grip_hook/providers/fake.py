"""A deterministic provider for tests, demos and ``grip quiz --provider fake``.

Two modes:

* **Default.** Questions are derived from the changed file names; an answer scores full
  marks when it mentions the word ``because`` or is at least twenty characters long,
  half marks when it is non-empty, and zero otherwise.
* **Scripted.** When ``GRIP_FAKE_SCRIPT`` points to a JSON file, questions, grading rules
  and verdicts come from that file (see :func:`load_script`). This is how the demo
  recording and some tests get realistic, reproducible output without a network. An
  optional ``board`` in the script is the whiteboard; its pins are graded by the rules of
  the scripted question at the same position.

Without a script, the whiteboard is drawn from the changed files: an untrusted caller, one
box per file in order, and the five whiteboard questions pinned across them.

An appeal (``grip appeal``) is upheld with full marks when the justification says
``because``, or with a scripted rule's score when one matches it and beats the first grade.

``GRIP_FAKE_DELAY`` (seconds, float) makes each call sleep, so the spinner is visible.

Every call is timed and reports fixed token counts (:data:`FAKE_USAGE`), so the cost and
latency shown after a quiz can be tested.
"""

from __future__ import annotations

import itertools
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
from grip_hook.providers.base import CallLog, call_kind
from grip_hook.whiteboard import MAX_LABEL, Board, Edge, Node, Pin, PinKind

SCRIPT_ENV = "GRIP_FAKE_SCRIPT"
DELAY_ENV = "GRIP_FAKE_DELAY"
FAKE_USAGE: dict[str, tuple[int, int]] = {
    "questions": (1200, 400),
    "board": (1500, 700),
    "grade": (1400, 300),
    "appeal": (900, 100),
}
"""``(input, output)`` tokens the fake provider reports for each kind of request."""

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
    board: Board | None = None

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


class FakeProvider(CallLog):
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
        self.model_calls = []

    def _pause(self) -> float:
        """Sleep for ``GRIP_FAKE_DELAY``; return when the call started, for the log."""
        started = time.perf_counter()
        if self.delay > 0:
            time.sleep(self.delay)
        return started

    def _done(self, schema: type, started: float) -> None:
        self._log_call(schema, started, *FAKE_USAGE[call_kind(schema)])

    def generate_questions(self, diff: Diff, difficulty: Difficulty) -> QuestionSet:
        """Produce five questions, scripted or templated over the changed files."""
        started = self._pause()
        self._done(QuestionSet, started)
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

    def generate_board(
        self, diff: Diff, difficulty: Difficulty, problems: tuple[str, ...]
    ) -> Board:
        """The scripted board, or one drawn from the changed files."""
        self._done(Board, self._pause())
        if self.script is not None and self.script.board is not None:
            return self.script.board
        return _board_from_files(list(diff.files)[:5] or ["the diff"])

    def grade(
        self,
        diff: Diff,
        questions: list[Question],
        answers: list[Answer],
        difficulty: Difficulty,
    ) -> GradeSheet:
        """Score answers deterministically, by script rules or by length."""
        self._done(GradeSheet, self._pause())
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
        """Uphold an appeal that says ``because`` (or matches a scripted rule), else keep it."""
        self._done(QuestionGrade, self._pause())
        index = answer.question_index
        score, feedback = grade.score, "Appeal not upheld: the first grade stands."
        if self.script is not None:
            scripted, why = self.script.questions[index].grade(justification)
            if scripted > score:
                score, feedback = scripted, f"Appeal upheld. {why}"
        elif "because" in justification.lower():
            score, feedback = POINTS_PER_QUESTION, "Appeal upheld: the answer was right."
        return QuestionGrade(question_index=index, score=score, feedback=feedback)


def _board_from_files(files: list[str]) -> Board:
    ids = [f"file-{i + 1}" for i in range(len(files))]
    nodes = [
        Node(
            id="caller",
            label="caller",
            role="whoever sends the input",
            kind="external",
            trust="untrusted",
            files=[],
        )
    ]
    nodes += [
        Node(
            id=node_id,
            label=Path(f).name[:MAX_LABEL],
            role="changed in this diff",
            kind="component" if f != "the diff" else "context",
            trust="trusted",
            files=[f] if f != "the diff" else [],
        )
        for node_id, f in zip(ids, files, strict=True)
    ]
    edges = [Edge(source="caller", target=ids[0], label="input")]
    edges += [Edge(source=a, target=b, label="calls") for a, b in itertools.pairwise(ids)]
    first, last = ids[0], ids[-1]
    pins = [
        Pin(
            on=first,
            kind=PinKind.WHY,
            question=f"Why is {files[0]} built this way?",
            rubric=["Names the approach", "Names an alternative and why not"],
        ),
        Pin(
            on=f"caller->{first}",
            kind=PinKind.ADVERSARY,
            question="What could a hostile caller do with this input?",
            rubric=["Names an abuse", "Says what stops it"],
        ),
        Pin(
            on=last,
            kind=PinKind.FAILURE,
            question=f"Where does {files[-1]} fail?",
            rubric=["Names an input or state", "Says what happens"],
        ),
        Pin(
            on=first,
            kind=PinKind.VERIFY,
            question="How would you show this works?",
            rubric=["Names a test", "Says what it asserts"],
        ),
        Pin(
            on=first,
            kind=PinKind.HOW,
            question=f"What does the change to {files[0]} do?",
            rubric=["Describes the behaviour", "In the developer's own words"],
        ),
    ]
    return Board(
        summary=f"Fake board over {len(files)} file(s).", nodes=nodes, edges=edges, pins=pins
    )


__all__ = ["DELAY_ENV", "SCRIPT_ENV", "FakeProvider", "Script", "load_script"]
