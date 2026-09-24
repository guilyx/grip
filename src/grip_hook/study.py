"""Anonymised quiz history across repositories, for study platforms.

Every graded quiz is appended to ``<git dir>/grip/history.jsonl`` and the repository's
git dir is recorded in a registry under the grip data directory
(``$XDG_DATA_HOME/grip``, default ``~/.local/share/grip``; ``GRIP_DATA_HOME`` overrides
both). ``grip study export`` reads the history of every registered repository and writes
one JSON document with scores only: no diff, no file paths, no question text, no rubrics,
no answers, no feedback, no repository names. Nothing in this module talks to the
network; the developer uploads the file themselves, if at all.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from grip_hook import __version__
from grip_hook.models import Report, Stage

DATA_HOME_ENV = "GRIP_DATA_HOME"
"""Environment variable that overrides the data directory (used by tests)."""

EXPORT_VERSION = 1
"""Schema version written to the export document."""

DEFAULT_SINCE_DAYS = 45
"""How far back ``grip study export`` looks by default."""

INCLUDED_FIELDS: tuple[str, ...] = (
    "id (hash of installation id, diff digest and time)",
    "repo (hash of installation id and repository path)",
    "created_at",
    "stage",
    "provider",
    "model",
    "passing_score",
    "score",
    "passed",
    "grades[].focus (the short label of what each question tested)",
    "grades[].score",
)
"""What each exported quiz contains, for ``grip study status``."""

EXCLUDED_FIELDS: tuple[str, ...] = (
    "diff content",
    "file paths",
    "repository names or paths",
    "question text",
    "rubrics",
    "your answers",
    "feedback text",
    "verdict text",
    "the change summary",
)
"""What never leaves the machine, for ``grip study status``."""


def data_home(env: Mapping[str, str] | None = None) -> Path:
    """The directory holding ``repos.json`` and the installation id.

    Args:
        env: Environment mapping. Defaults to :data:`os.environ`.
    """
    environ = os.environ if env is None else env
    override = environ.get(DATA_HOME_ENV)
    if override:
        return Path(override)
    xdg = environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "grip"


_Paths = list[Path]


class Registry:
    """The list of repositories that have been quizzed on this machine.

    Stored as ``repos.json`` next to ``installation-id``. Only absolute git dir paths are
    kept, nothing else.
    """

    def __init__(self, home: Path | None = None) -> None:
        self.home = home or data_home()
        self.path = self.home / "repos.json"
        self.id_path = self.home / "installation-id"

    def list(self) -> _Paths:
        """Registered git dirs, sorted, whether or not they still exist."""
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        repos = data.get("repos", []) if isinstance(data, dict) else []
        paths = {Path(p) for p in repos if isinstance(p, str) and Path(p).is_absolute()}
        return sorted(paths)

    def _save(self, paths: _Paths) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "repos": [str(p) for p in sorted(set(paths))]}
        self.path.write_text(json.dumps(payload, indent=2) + "\n", "utf-8")

    def add(self, git_dir: Path) -> None:
        """Record ``git_dir`` (made absolute). Adding an existing entry is a no-op."""
        resolved = git_dir.resolve()
        paths = self.list()
        if resolved in paths:
            return
        self._save([*paths, resolved])

    def prune(self) -> _Paths:
        """Drop entries whose directory no longer exists and return what is left."""
        paths = self.list()
        kept = [p for p in paths if p.is_dir()]
        if kept != paths:
            self._save(kept)
        return kept

    def installation_id(self) -> str:
        """A random UUID for this machine, created on first use and then stable."""
        try:
            existing = self.id_path.read_text("utf-8").strip()
            return str(uuid.UUID(existing))
        except (OSError, ValueError):
            pass
        fresh = str(uuid.uuid4())
        self.home.mkdir(parents=True, exist_ok=True)
        self.id_path.write_text(fresh + "\n", "utf-8")
        return fresh


class GradeSummary(BaseModel):
    """What one question tested and how many points it earned."""

    focus: str
    score: int


class QuizSummary(BaseModel):
    """One quiz, reduced to scores and opaque identifiers."""

    id: str
    repo: str
    created_at: datetime
    stage: Stage
    provider: str
    model: str
    passing_score: int
    score: int
    passed: bool
    grades: list[GradeSummary]


class Export(BaseModel):
    """The document ``grip study export`` writes."""

    version: int = EXPORT_VERSION
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    installation_id: str
    grip_version: str = __version__
    quizzes: list[QuizSummary]

    @property
    def repo_count(self) -> int:
        """How many distinct repositories the quizzes come from."""
        return len({q.repo for q in self.quizzes})


def _digest(*parts: str, length: int) -> str:
    return hashlib.sha256("".join(parts).encode("utf-8")).hexdigest()[:length]


def repo_id(installation_id: str, git_dir: Path) -> str:
    """Opaque, per-machine identifier for a repository."""
    return _digest(installation_id, str(git_dir), length=16)


def summarise(report: Report, installation_id: str, git_dir: Path) -> QuizSummary:
    """Reduce ``report`` to the fields that may leave the machine."""
    created_at = report.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    focus = {i: q.focus for i, q in enumerate(report.questions)}
    grades = [
        GradeSummary(focus=focus.get(g.question_index, ""), score=g.score) for g in report.grades
    ]
    return QuizSummary(
        id=_digest(installation_id, report.diff_digest, created_at.isoformat(), length=32),
        repo=repo_id(installation_id, git_dir),
        created_at=created_at,
        stage=report.stage,
        provider=report.provider,
        model=report.model,
        passing_score=report.passing_score,
        score=report.score,
        passed=report.passed,
        grades=grades,
    )


def read_history(git_dir: Path) -> list[Report]:
    """Parse ``<git dir>/grip/history.jsonl``. Unreadable or corrupt lines are skipped."""
    path = git_dir / "grip" / "history.jsonl"
    try:
        text = path.read_text("utf-8")
    except OSError:
        return []
    reports: list[Report] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            reports.append(Report.model_validate_json(line))
        except ValidationError:
            continue
    return reports


def build_export(
    registry: Registry,
    since: timedelta | None,
    now: datetime | None = None,
) -> Export:
    """Collect every registered repository's history into one :class:`Export`.

    Args:
        registry: Where to find the repositories and the installation id.
        since: Only quizzes newer than ``now - since``; ``None`` for all of them.
        now: Reference time. Defaults to the current UTC time.
    """
    now = now or datetime.now(UTC)
    cutoff = None if since is None else now - since
    installation_id = registry.installation_id()
    quizzes: list[QuizSummary] = []
    for git_dir in registry.prune():
        for report in read_history(git_dir):
            summary = summarise(report, installation_id, git_dir)
            if cutoff is not None and summary.created_at < cutoff:
                continue
            quizzes.append(summary)
    quizzes.sort(key=lambda q: q.created_at)
    return Export(installation_id=installation_id, quizzes=quizzes, generated_at=now)


__all__ = [
    "DATA_HOME_ENV",
    "DEFAULT_SINCE_DAYS",
    "EXCLUDED_FIELDS",
    "EXPORT_VERSION",
    "INCLUDED_FIELDS",
    "Export",
    "GradeSummary",
    "QuizSummary",
    "Registry",
    "build_export",
    "data_home",
    "read_history",
    "repo_id",
    "summarise",
]
