"""Drive the quiz from a coding agent instead of a terminal.

Inside Claude Code, Codex or Gemini CLI there is no tty for grip to talk to, so the
agent relays the quiz: ``grip ask`` writes the questions as JSON (the rubrics stay on
disk in ``.git/grip/pending.json``), the agent shows them to the developer, and
``grip grade`` scores the answers and remembers a pass exactly like the hook does.
``grip check`` reports whether the current diff already passed, and ``grip agent-hook``
turns that into a Claude Code ``PreToolUse`` decision that blocks ``git push`` until it has,
unless the command itself sets ``GRIP_SKIP`` for git (``GRIP_SKIP=1 git push``), which the
git hook would honour too.
"""

from __future__ import annotations

import contextlib
import json
import re
import shlex
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from grip_hook import MAX_SCORE, POINTS_PER_QUESTION, QUESTION_COUNT
from grip_hook.config import Config
from grip_hook.errors import GripError, ProviderError
from grip_hook.git import Diff
from grip_hook.models import Answer, Difficulty, QuestionSet, Report, Stage
from grip_hook.providers.base import Provider

Gate = Literal["push", "commit", "both"]

# ``git [-C dir] [-c k=v] [--flag] push``: the subcommand after any global options.
_PUSH_RE = re.compile(r"(?:^|[\s;&|(])git\s+(?:-[Cc]\s+\S+\s+|-\S+\s+)*push\b")
_COMMIT_RE = re.compile(r"(?:^|[\s;&|(])git\s+(?:-[Cc]\s+\S+\s+|-\S+\s+)*commit\b")

SKIP_ENV = "GRIP_SKIP"
"""Set to a truthy value to skip grip once: the git hooks and the agent gate honour it."""

_TRUTHY = frozenset({"1", "true", "yes", "on"})
_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SEPARATOR_CHARS = frozenset(";&|()")


class PendingQuiz(BaseModel):
    """A question set waiting for answers, with everything needed to grade it later."""

    version: int = 1
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    stage: Stage
    provider: str
    model: str
    description: str
    stat: str
    patch: str
    files: list[str]
    truncated: bool
    question_set: QuestionSet
    difficulty: Difficulty | None = None
    """The difficulty the questions were written at, so grading matches it. ``None`` in
    files written before it was stored: grading then uses the configured difficulty."""

    @property
    def diff(self) -> Diff:
        """Rebuild the diff the questions were written about."""
        return Diff(self.description, self.stat, self.patch, tuple(self.files), self.truncated)


class PendingStore:
    """``<git dir>/grip/pending.json``: the quiz in flight, if any."""

    def __init__(self, git_dir: Path) -> None:
        self.path = git_dir / "grip" / "pending.json"

    def load(self) -> PendingQuiz | None:
        """The pending quiz, or ``None`` when there is none or it is unreadable."""
        try:
            return PendingQuiz.model_validate_json(self.path.read_text("utf-8"))
        except (OSError, ValidationError):
            return None

    def save(self, pending: PendingQuiz) -> Path:
        """Persist ``pending`` and return its path."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(pending.model_dump_json(indent=2), "utf-8")
        return self.path

    def clear(self) -> None:
        """Forget the pending quiz."""
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()


def ask(diff: Diff, cfg: Config, provider: Provider, stage: Stage) -> PendingQuiz:
    """Generate the question set for ``diff`` without asking anything."""
    question_set = provider.generate_questions(diff, cfg.difficulty)
    if len(question_set.questions) != QUESTION_COUNT:
        raise ProviderError(
            f"provider returned {len(question_set.questions)} questions, expected {QUESTION_COUNT}"
        )
    return PendingQuiz(
        stage=stage,
        provider=provider.name,
        model=provider.model,
        description=diff.description,
        stat=diff.stat,
        patch=diff.patch,
        files=list(diff.files),
        truncated=diff.truncated,
        question_set=question_set,
        difficulty=cfg.difficulty,
    )


def questions_payload(pending: PendingQuiz, cfg: Config) -> dict[str, Any]:
    """What the agent may see: the questions, never the rubrics."""
    return {
        "status": "questions",
        "summary": pending.question_set.summary,
        "passing_score": cfg.passing_score,
        "max_score": MAX_SCORE,
        "diff": {
            "description": pending.description,
            "files": pending.files,
            "truncated": pending.truncated,
        },
        "questions": [
            {"index": i + 1, "focus": q.focus, "question": q.question}
            for i, q in enumerate(pending.question_set.questions)
        ],
        "next": "Show these to the developer, collect their answers, then run `grip grade`.",
    }


def parse_answers(text: str) -> list[str]:
    """Accept ``["a", "b", ...]`` or ``{"answers": [...]}``; missing answers count as blank.

    Raw line breaks inside a string are accepted and kept: an answer pasted in paragraphs
    is often written into the JSON as it is, and strict JSON would reject it.
    """
    try:
        data = json.loads(text, strict=False)
    except json.JSONDecodeError as exc:
        raise GripError(f"answers must be JSON: {exc}") from exc
    if isinstance(data, dict):
        data = data.get("answers")
    if not isinstance(data, list) or not all(isinstance(a, str) for a in data):
        raise GripError('answers must be a JSON list of strings or {"answers": [...]}')
    if len(data) > QUESTION_COUNT:
        raise GripError(f"got {len(data)} answers for {QUESTION_COUNT} questions")
    return [a.strip() for a in data] + [""] * (QUESTION_COUNT - len(data))


def grade(pending: PendingQuiz, answers: list[str], cfg: Config, provider: Provider) -> Report:
    """Grade ``answers`` against the pending questions and build the report."""
    answer_models = [Answer(question_index=i, text=a) for i, a in enumerate(answers)]
    diff = pending.diff
    difficulty = cfg.difficulty if pending.difficulty is None else pending.difficulty
    sheet = provider.grade(diff, pending.question_set.questions, answer_models, difficulty)
    if len(sheet.grades) != QUESTION_COUNT:
        raise ProviderError(
            f"provider returned {len(sheet.grades)} grades, expected {QUESTION_COUNT}"
        )
    sheet.grades.sort(key=lambda g: g.question_index)
    total = sheet.total
    return Report(
        stage=pending.stage,
        provider=provider.name,
        model=provider.model,
        diff_digest=diff.digest,
        passing_score=cfg.passing_score,
        score=total,
        passed=total >= cfg.passing_score,
        summary=pending.question_set.summary,
        questions=pending.question_set.questions,
        answers=answer_models,
        grades=sheet.grades,
        verdict=sheet.verdict,
    )


def report_payload(report: Report, report_path: Path) -> dict[str, Any]:
    """The graded result, for the agent to relay."""
    return {
        "status": "pass" if report.passed else "fail",
        "score": report.score,
        "passing_score": report.passing_score,
        "max_score": MAX_SCORE,
        "grades": [
            {
                "index": g.question_index + 1,
                "focus": report.questions[g.question_index].focus,
                "score": g.score,
                "max_score": POINTS_PER_QUESTION,
                "feedback": g.feedback,
            }
            for g in report.grades
        ],
        "verdict": report.verdict,
        "report": str(report_path),
    }


def gated_mode(command: str, gate: Gate) -> str | None:
    """Which diff a shell ``command`` must have passed: ``unpushed``, ``staged`` or none."""
    if gate in {"push", "both"} and _PUSH_RE.search(command):
        return "unpushed"
    if gate in {"commit", "both"} and _COMMIT_RE.search(command):
        return "staged"
    return None


def _simple_commands(command: str) -> list[list[str]] | None:
    """The words of each simple command in a shell line, or ``None`` if it does not parse.

    Split on ``;``, ``&&``, ``||``, ``|``, ``&``, parentheses and newlines, with quotes
    honoured, so ``git commit -m "a; b"`` stays one command.
    """
    lexer = shlex.shlex(command.replace("\n", ";"), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:  # an unclosed quote
        return None
    commands: list[list[str]] = [[]]
    for token in tokens:
        if token and set(token) <= _SEPARATOR_CHARS:
            commands.append([])
        else:
            commands[-1].append(token)
    return [words for words in commands if words]


def skip_requested(command: str, gate: Gate) -> bool:
    """Whether every gated git command in ``command`` runs with ``GRIP_SKIP`` set.

    This is what the shell would put in git's environment, and so what the git hook itself
    would see: a ``GRIP_SKIP=1`` assignment in front of the command (``GRIP_SKIP=1 git
    push``, also after ``env``), or an earlier ``export GRIP_SKIP=1`` in the same line. A
    plain ``GRIP_SKIP=1; git push`` does not reach git, so it does not count. When the line
    cannot be parsed, nothing is skipped.
    """
    commands = _simple_commands(command)
    if commands is None:
        return False
    exported: dict[str, str] = {}
    gated = 0
    for words in commands:
        if words[0] == "export":
            exported.update(_assignments(words[1:]))
            continue
        env = dict(exported)
        start = 0
        while start < len(words):
            word = words[start : start + 1]
            if word != ["env"] and not _assignments(word):
                break
            env.update(_assignments(word))
            start += 1
        if gated_mode(" ".join(words[start:]), gate) is None:
            continue
        gated += 1
        if env.get(SKIP_ENV, "").strip().lower() not in _TRUTHY:
            return False
    return gated > 0


def _assignments(words: list[str]) -> dict[str, str]:
    """The ``NAME=value`` words among ``words``, as a mapping."""
    found: dict[str, str] = {}
    for word in words:
        name, sep, value = word.partition("=")
        if sep and _NAME_RE.fullmatch(name):
            found[name] = value
    return found


def hook_request(text: str, gate: Gate) -> tuple[str, Path] | None:
    """``(mode, cwd)`` when a Claude Code ``PreToolUse`` payload needs a check, else ``None``.

    No check is needed when the command is not gated, or when it sets ``GRIP_SKIP`` for
    every gated git command in it (see :func:`skip_requested`).
    """
    try:
        payload = json.loads(text or "{}")
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
        return None
    command = (payload.get("tool_input") or {}).get("command")
    if not isinstance(command, str):
        return None
    mode = gated_mode(command, gate)
    if mode is None or skip_requested(command, gate):
        return None
    return mode, Path(payload.get("cwd") or Path.cwd())


def claude_code_decision(reason: str) -> dict[str, Any]:
    """The JSON a Claude Code ``PreToolUse`` hook prints to deny the tool call."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


__all__ = [
    "SKIP_ENV",
    "Gate",
    "PendingQuiz",
    "PendingStore",
    "ask",
    "claude_code_decision",
    "gated_mode",
    "grade",
    "hook_request",
    "parse_answers",
    "questions_payload",
    "report_payload",
    "skip_requested",
]
