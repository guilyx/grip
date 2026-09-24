"""Remember diffs that already passed so a developer is not quizzed twice.

State lives in ``<git dir>/grip/passed.json``: a map from diff digest to the UTC
timestamp of the pass. Entries expire after ``Config.remember_passes_hours``.

The same directory holds ``last-report.json`` (the most recent report) and
``history.jsonl`` (one line per graded quiz, appended forever; ``grip study export``
reads it).
"""

from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

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


__all__ = ["PassMemory"]
