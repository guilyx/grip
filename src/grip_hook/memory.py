"""Remember diffs that already passed so a developer is not quizzed twice.

State lives in ``<git dir>/grip/passed.json``: a map from diff digest to the UTC
timestamp of the pass. Entries expire after ``Config.remember_passes_hours``.

The same directory holds ``last-report.json`` (the most recent report), ``history.jsonl``
(one line per graded quiz, appended forever; ``grip study export`` reads it) and
``skip.json`` (a one-off or timed pause set by ``grip skip``).
"""

from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from grip_hook.models import Report


class PassMemory:
    """Small JSON-backed store of passed diff digests."""

    def __init__(self, git_dir: Path, ttl_hours: float) -> None:
        self.dir = git_dir / "grip"
        self.path = self.dir / "passed.json"
        self.report_path = self.dir / "last-report.json"
        self.history_path = self.dir / "history.jsonl"
        self.ttl = timedelta(hours=ttl_hours)

    @property
    def enabled(self) -> bool:
        """``False`` when the TTL is zero, i.e. remembering is switched off."""
        return self.ttl > timedelta(0)

    def _load(self) -> dict[str, str]:
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}

    def _save(self, data: dict[str, str]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, sort_keys=True), "utf-8")

    def _prune(self, data: dict[str, str], now: datetime) -> dict[str, str]:
        kept: dict[str, str] = {}
        for digest, stamp in data.items():
            try:
                when = datetime.fromisoformat(stamp)
            except ValueError:
                continue
            if now - when <= self.ttl:
                kept[digest] = stamp
        return kept

    def has_passed(self, digest: str, now: datetime | None = None) -> bool:
        """``True`` when ``digest`` passed within the TTL."""
        if not self.enabled:
            return False
        now = now or datetime.now(UTC)
        return digest in self._prune(self._load(), now)

    def record_pass(self, digest: str, now: datetime | None = None) -> None:
        """Store ``digest`` as passed at ``now``."""
        if not self.enabled:
            return
        now = now or datetime.now(UTC)
        data = self._prune(self._load(), now)
        data[digest] = now.isoformat()
        self._save(data)

    def forget(self) -> None:
        """Drop all remembered passes."""
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()

    def last_report(self) -> Report | None:
        """The most recent graded quiz, or ``None`` when there is none or it is unreadable."""
        try:
            return Report.model_validate_json(self.report_path.read_text("utf-8"))
        except (OSError, ValidationError):
            return None

    def save_report(self, report: Report) -> Path:
        """Write the last report as JSON and return its path."""
        self.dir.mkdir(parents=True, exist_ok=True)
        self.report_path.write_text(report.model_dump_json(indent=2), "utf-8")
        return self.report_path

    def append_history(self, report: Report) -> Path:
        """Append ``report`` as one JSON line to ``history.jsonl`` and return its path."""
        self.dir.mkdir(parents=True, exist_ok=True)
        line = json.dumps(report.model_dump(mode="json"), sort_keys=True)
        with self.history_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return self.history_path


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
            return f"paused until {until:%Y-%m-%d %H:%M} UTC"
        if data.get("once"):
            return "the next hook run"
        return None

    def consume(self, now: datetime | None = None) -> str | None:
        """Why this hook run is skipped, using up a one-off; ``None`` when it is not."""
        data = self._load()
        now = now or datetime.now(UTC)
        until = _parse(data.get("until"))
        if until is not None and until > now:
            return f"paused by `grip skip` until {until:%Y-%m-%d %H:%M} UTC"
        data.pop("until", None)  # expired
        once = bool(data.pop("once", False))
        self._save(data)
        return "`grip skip` was set for this run" if once else None


__all__ = ["PassMemory", "SkipState"]
