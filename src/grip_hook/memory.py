"""Remember diffs that already passed so a developer is not quizzed twice.

State lives in ``<git dir>/grip/passed.json``: a map from diff digest to the pass: when
it happened (UTC), the score, the pass mark it was graded against, and the provider and
model that graded it. Entries expire after ``Config.remember_passes_hours``.

A remembered pass is checked against the configuration in force when it is read: the
score must reach the current pass mark, and a pass graded by the ``fake`` provider counts
only while ``fake`` is the configured provider. ``--passing-score 0`` or ``--provider
fake`` can pass a quiz, but that pass does not open the hook. Entries written by older
versions (digest to timestamp only) cannot be checked that way, so they never count.

The same directory holds ``last-report.json`` (the most recent report), ``last-diff.json``
(the diff it was graded on, for ``grip appeal``), ``history.jsonl`` (one line per graded
quiz, appended forever, and once more for each appeal; ``grip study export`` reads it) and
``skip.json`` (a one-off or timed pause set by ``grip skip``).
"""

from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from grip_hook.config import Config
from grip_hook.git import Diff
from grip_hook.models import Report

FAKE_PROVIDER = "fake"


class PassRecord(BaseModel):
    """One remembered pass: who graded it, and how it scored against which pass mark."""

    at: datetime
    score: int
    passing_score: int
    provider: str
    model: str

    def counts_for(self, cfg: Config) -> bool:
        """Whether this pass still lets the diff through under ``cfg``.

        The score has to reach ``cfg``'s pass mark, whatever mark it was graded against,
        and a pass from the offline ``fake`` grader only counts while ``fake`` is the
        configured provider (demos and tests), never for a real one.
        """
        if self.score < cfg.passing_score:
            return False
        return self.provider != FAKE_PROVIDER or cfg.provider_name == FAKE_PROVIDER


class PassMemory:
    """Small JSON-backed store of passed diff digests."""

    def __init__(self, git_dir: Path, ttl_hours: float) -> None:
        self.dir = git_dir / "grip"
        self.path = self.dir / "passed.json"
        self.report_path = self.dir / "last-report.json"
        self.diff_path = self.dir / "last-diff.json"
        self.history_path = self.dir / "history.jsonl"
        self.ttl = timedelta(hours=ttl_hours)

    @property
    def enabled(self) -> bool:
        """``False`` when the TTL is zero, i.e. remembering is switched off."""
        return self.ttl > timedelta(0)

    def _load(self) -> dict[str, PassRecord]:
        """Readable entries only: corrupt ones and the old digest-to-time format are dropped."""
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        records: dict[str, PassRecord] = {}
        for digest, entry in data.items():
            if not isinstance(entry, dict):
                continue  # a bare timestamp, written before passes recorded their grader
            try:
                record = PassRecord.model_validate(entry)
            except ValidationError:
                continue
            if record.at.tzinfo is not None:
                records[digest] = record
        return records

    def _save(self, data: dict[str, PassRecord]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        raw = {digest: record.model_dump(mode="json") for digest, record in data.items()}
        self.path.write_text(json.dumps(raw, indent=2, sort_keys=True), "utf-8")

    def _prune(self, data: dict[str, PassRecord], now: datetime) -> dict[str, PassRecord]:
        return {digest: record for digest, record in data.items() if now - record.at <= self.ttl}

    def get(self, digest: str, now: datetime | None = None) -> PassRecord | None:
        """The pass remembered for ``digest`` within the TTL, whether or not it still counts."""
        if not self.enabled:
            return None
        now = now or datetime.now(UTC)
        return self._prune(self._load(), now).get(digest)

    def has_passed(self, digest: str, cfg: Config, now: datetime | None = None) -> bool:
        """``True`` when ``digest`` passed within the TTL and that pass counts under ``cfg``.

        See :meth:`PassRecord.counts_for`: a pass below the current pass mark, or graded by
        the ``fake`` provider while a real one is configured, does not count.
        """
        record = self.get(digest, now)
        return record is not None and record.counts_for(cfg)

    def record_pass(self, report: Report, now: datetime | None = None) -> None:
        """Remember ``report``'s diff as passed at ``now``, with its score and grader."""
        if not self.enabled:
            return
        now = now or datetime.now(UTC)
        data = self._prune(self._load(), now)
        data[report.diff_digest] = PassRecord(
            at=now,
            score=report.score,
            passing_score=report.passing_score,
            provider=report.provider,
            model=report.model,
        )
        self._save(data)

    def forget(self, now: datetime | None = None) -> int:
        """Drop all remembered passes; return how many were still remembered."""
        count = len(self._prune(self._load(), now or datetime.now(UTC))) if self.enabled else 0
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()
        return count

    def last_report(self) -> Report | None:
        """The most recent graded quiz, or ``None`` when there is none or it is unreadable."""
        try:
            return Report.model_validate_json(self.report_path.read_text("utf-8"))
        except (OSError, ValidationError):
            return None

    def latest_for(self, digest: str) -> Report | None:
        """The most recent graded quiz on the diff ``digest``, pass or fail, if any."""
        try:
            lines = self.history_path.read_text("utf-8").splitlines()
        except OSError:
            return None
        for line in reversed(lines):
            if digest not in line:
                continue
            try:
                report = Report.model_validate_json(line)
            except ValidationError:
                continue
            if report.diff_digest == digest:
                return report
        return None

    def save_report(self, report: Report) -> Path:
        """Write the last report as JSON and return its path."""
        self.dir.mkdir(parents=True, exist_ok=True)
        self.report_path.write_text(report.model_dump_json(indent=2), "utf-8")
        return self.report_path

    def save_diff(self, diff: Diff) -> Path:
        """Keep the diff the last report was graded on, so ``grip appeal`` can grade again."""
        self.dir.mkdir(parents=True, exist_ok=True)
        data = {
            "digest": diff.digest,
            "description": diff.description,
            "stat": diff.stat,
            "patch": diff.patch,
            "files": list(diff.files),
            "truncated": diff.truncated,
            "context": diff.context,
            "commits": diff.commits,
        }
        self.diff_path.write_text(json.dumps(data), "utf-8")
        return self.diff_path

    def load_diff(self, digest: str) -> Diff | None:
        """The diff kept by :meth:`save_diff`, if it is the one with ``digest``."""
        try:
            data = json.loads(self.diff_path.read_text("utf-8"))
            diff = Diff(
                description=str(data["description"]),
                stat=str(data["stat"]),
                patch=str(data["patch"]),
                files=tuple(str(f) for f in data["files"]),
                truncated=bool(data["truncated"]),
                context=str(data.get("context", "")),
                commits=str(data.get("commits", "")),
            )
        except (OSError, ValueError, KeyError, TypeError):
            return None
        return diff if diff.digest == digest else None

    def append_history(self, report: Report) -> Path:
        """Append ``report`` as one JSON line to ``history.jsonl`` and return its path."""
        self.dir.mkdir(parents=True, exist_ok=True)
        line = json.dumps(report.model_dump(mode="json"), sort_keys=True)
        with self.history_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return self.history_path


def local_time(when: datetime) -> str:
    """``2026-01-01 15:00 CET``: ``when`` in the local time zone, named."""
    return f"{when.astimezone():%Y-%m-%d %H:%M %Z}".rstrip()


def _parse(stamp: object) -> datetime | None:
    if not isinstance(stamp, str):
        return None
    try:
        return datetime.fromisoformat(stamp)
    except ValueError:
        return None


class SkipState:
    """``<git dir>/grip/skip.json``: skip the next hook run, or pause hooks until a time.

    Set by ``grip skip``, cleared by ``grip resume`` or by being consumed. Only the hooks
    (and the agent push gate) look at it; a manual ``grip quiz`` never does.
    """

    def __init__(self, git_dir: Path) -> None:
        self.path = git_dir / "grip" / "skip.json"

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return dict(data) if isinstance(data, dict) else {}

    def _save(self, data: dict[str, Any]) -> None:
        if not data:
            self.clear()
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, sort_keys=True), "utf-8")

    def set_once(self) -> None:
        """Skip the next hook run only."""
        data = self._load()
        data["once"] = True
        self._save(data)

    def set_until(self, until: datetime) -> None:
        """Skip every hook run until ``until``."""
        data = self._load()
        data["until"] = until.isoformat()
        self._save(data)

    def clear(self) -> None:
        """Forget any skip."""
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()

    def describe(self, now: datetime | None = None) -> str | None:
        """What is currently set, for ``grip status``, or ``None`` when hooks run normally."""
        data = self._load()
        now = now or datetime.now(UTC)
        until = _parse(data.get("until"))
        if until is not None and until > now:
            return f"paused until {local_time(until)}"
        if data.get("once"):
            return "the next hook run"
        return None

    def consume(self, now: datetime | None = None) -> str | None:
        """Why this hook run is skipped, using up a one-off; ``None`` when it is not."""
        data = self._load()
        now = now or datetime.now(UTC)
        until = _parse(data.get("until"))
        if until is not None and until > now:
            return f"paused by `grip skip` until {local_time(until)}"
        data.pop("until", None)  # expired
        once = bool(data.pop("once", False))
        self._save(data)
        return "`grip skip` was set for this run" if once else None


__all__ = ["FAKE_PROVIDER", "PassMemory", "PassRecord", "SkipState", "local_time"]
