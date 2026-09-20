from datetime import UTC, datetime, timedelta
from pathlib import Path

from grip_hook.memory import PassMemory
from grip_hook.models import Report, Stage


def test_record_and_expire(tmp_path: Path) -> None:
    mem = PassMemory(tmp_path, ttl_hours=1)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert not mem.has_passed("abc", now)
    mem.record_pass("abc", now)
    assert mem.has_passed("abc", now + timedelta(minutes=59))
    assert not mem.has_passed("abc", now + timedelta(minutes=61))


def test_disabled(tmp_path: Path) -> None:
    mem = PassMemory(tmp_path, ttl_hours=0)
    mem.record_pass("abc")
    assert not mem.enabled
    assert not mem.has_passed("abc")
    assert not mem.path.exists()


def test_corrupt_file_is_ignored(tmp_path: Path) -> None:
    mem = PassMemory(tmp_path, ttl_hours=1)
    mem.dir.mkdir()
    mem.path.write_text("{not json")
    assert not mem.has_passed("abc")
    mem.path.write_text('{"abc": "not-a-date", "def": 3}')
    assert not mem.has_passed("abc")
    mem.record_pass("xyz")
    assert mem.has_passed("xyz")


def test_forget_and_report(tmp_path: Path) -> None:
    mem = PassMemory(tmp_path, ttl_hours=1)
    mem.forget()  # no file yet: fine
    mem.record_pass("abc")
    mem.forget()
    assert not mem.has_passed("abc")

    report = Report(
        stage=Stage.MANUAL,
        provider="fake",
        model="fake",
        diff_digest="abc",
        passing_score=70,
        score=80,
        passed=True,
        summary="s",
        questions=[],
        answers=[],
        grades=[],
        verdict="v",
    )
    path = mem.save_report(report)
    assert path.exists()
    assert '"score": 80' in path.read_text()
    assert report.max_score == 100
