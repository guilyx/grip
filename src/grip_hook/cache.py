"""Reuse the questions and boards a model already wrote for a diff.

Writing questions, and above all drawing a whiteboard, is the slow and costly call. When a
quiz on a diff fails and the developer tries again, the diff is the same, so the questions
written for it can be too: a retry then costs one grading call instead of two. Grading
always runs; the answers are what changed.

Entries live in ``<git dir>/grip/cache/`` as one JSON file each, named by a hash of what
was asked: the kind (questions or board), the diff digest, the task context, the
difficulty, the provider and the model. Change any of those and the model is asked again.
Entries older than ``Config.cache_hours`` are ignored and pruned.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypeVar

from pydantic import BaseModel, ValidationError

from grip_hook.git import Diff
from grip_hook.models import Answer, Difficulty, GradeSheet, Question, QuestionGrade, QuestionSet

if TYPE_CHECKING:
    from grip_hook.providers.base import Provider
    from grip_hook.whiteboard import Board

Kind = Literal["questions", "board"]
T = TypeVar("T", bound=BaseModel)


class GenerationCache:
    """Small file-backed store of question sets and boards, keyed by what was asked."""

    def __init__(self, git_dir: Path, ttl_hours: float) -> None:
        self.dir = git_dir / "grip" / "cache"
        self.ttl = timedelta(hours=ttl_hours)

    @staticmethod
    def key(kind: Kind, diff: Diff, difficulty: Difficulty, provider: str, model: str) -> str:
        """The name of the entry for one request."""
        context = hashlib.sha256(diff.context.encode("utf-8")).hexdigest()
        parts = (kind, diff.digest, context, difficulty.value, provider, model)
        return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()

    def load(
        self, key: str, model: type[T], now: datetime | None = None
    ) -> tuple[T, datetime] | None:
        """The stored value and when it was written, or ``None`` if missing, stale or unreadable."""
        path = self.dir / f"{key}.json"
        try:
            entry = json.loads(path.read_text("utf-8"))
            written = datetime.fromisoformat(entry["created_at"])
            value = model.model_validate(entry["value"])
        except (OSError, ValueError, KeyError, TypeError, ValidationError):
            return None
        if (now or datetime.now(UTC)) - written > self.ttl:
            return None
        return value, written

    def store(self, key: str, value: BaseModel, now: datetime | None = None) -> None:
        """Write ``value`` under ``key`` and drop entries past their time."""
        now = now or datetime.now(UTC)
        with contextlib.suppress(OSError):
            self.dir.mkdir(parents=True, exist_ok=True)
            entry = {"created_at": now.isoformat(), "value": value.model_dump(mode="json")}
            (self.dir / f"{key}.json").write_text(json.dumps(entry), "utf-8")
            self.prune(now)

    def prune(self, now: datetime | None = None) -> None:
        """Delete entries older than the time to live."""
        cutoff = (now or datetime.now(UTC)) - self.ttl
        for path in self.dir.glob("*.json"):
            with contextlib.suppress(OSError):
                if datetime.fromtimestamp(path.stat().st_mtime, UTC) < cutoff:
                    path.unlink()

    def clear(self) -> int:
        """Delete every entry; return how many there were."""
        count = 0
        for path in self.dir.glob("*.json"):
            with contextlib.suppress(OSError):
                path.unlink()
                count += 1
        return count


class CachedProvider:
    """A provider that answers question and board requests from the cache when it can."""

    def __init__(
        self,
        inner: Provider,
        cache: GenerationCache,
        on_hit: Callable[[Kind, datetime], None] | None = None,
    ) -> None:
        self.inner = inner
        self.cache = cache
        self.on_hit = on_hit
        self.name = inner.name
        self.model = inner.model

    def _key(self, kind: Kind, diff: Diff, difficulty: Difficulty) -> str:
        return self.cache.key(kind, diff, difficulty, self.name, self.model)

    def _hit(self, kind: Kind, key: str, model: type[T]) -> T | None:
        found = self.cache.load(key, model)
        if found is None:
            return None
        value, written = found
        if self.on_hit is not None:
            self.on_hit(kind, written)
        return value

    def generate_questions(self, diff: Diff, difficulty: Difficulty) -> QuestionSet:
        """The questions written for this diff before, else new ones (then remembered)."""
        key = self._key("questions", diff, difficulty)
        if (hit := self._hit("questions", key, QuestionSet)) is not None:
            return hit
        questions = self.inner.generate_questions(diff, difficulty)
        self.cache.store(key, questions)
        return questions

    def generate_board(
        self, diff: Diff, difficulty: Difficulty, problems: tuple[str, ...]
    ) -> Board:
        """The board drawn for this diff before, else a new one (then remembered).

        A retry with ``problems`` always asks the model, and its board replaces the one it
        corrects, so the next run starts from the board that passed the checks.
        """
        from grip_hook.whiteboard import Board  # noqa: PLC0415 - whiteboard imports providers

        key = self._key("board", diff, difficulty)
        if not problems and (hit := self._hit("board", key, Board)) is not None:
            return hit
        board = self.inner.generate_board(diff, difficulty, problems)
        self.cache.store(key, board)
        return board

    def grade(
        self,
        diff: Diff,
        questions: list[Question],
        answers: list[Answer],
        difficulty: Difficulty,
    ) -> GradeSheet:
        """Always graded fresh: the answers are what changed."""
        return self.inner.grade(diff, questions, answers, difficulty)

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
        """Always graded fresh, like :meth:`grade`."""
        return self.inner.appeal(
            diff, question, answer, grade, justification=justification, difficulty=difficulty
        )


__all__ = ["CachedProvider", "GenerationCache"]
