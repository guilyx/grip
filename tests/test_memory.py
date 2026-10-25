import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from grip_hook.config import Config
from grip_hook.memory import PassMemory
from grip_hook.models import Report, Stage

REAL = Config(provider="anthropic", passing_score=70)
FAKE = Config(provider="fake", passing_score=70)


def _report(
    digest: str = "abc", *, score: int = 80, passing_score: int = 70, provider: str = "anthropic"
) -> Report:
    return Report(
        stage=Stage.MANUAL,
        provider=provider,
        model="m",
        diff_digest=digest,
        passing_score=passing_score,
        score=score,
        passed=score >= passing_score,
        summary="s",
        questions=[],
        answers=[],
        grades=[],
        verdict="v",
    )


def test_record_and_expire(tmp_path: Path) -> None:
    mem = PassMemory(tmp_path, ttl_hours=1)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert not mem.has_passed("abc", REAL, now)
    mem.record_pass(_report(), now)
    assert mem.has_passed("abc", REAL, now + timedelta(minutes=59))
    assert not mem.has_passed("abc", REAL, now + timedelta(minutes=61))


def test_pass_records_its_grader(tmp_path: Path) -> None:
    mem = PassMemory(tmp_path, ttl_hours=1)
    mem.record_pass(_report(score=85))
    entry = json.loads(mem.path.read_text())["abc"]
    assert {k: entry[k] for k in ("score", "passing_score", "provider", "model")} == {
        "score": 85,
        "passing_score": 70,
        "provider": "anthropic",
        "model": "m",
    }
    assert datetime.fromisoformat(entry["at"]).tzinfo is not None


def test_fake_pass_counts_only_for_a_fake_config(tmp_path: Path) -> None:
    mem = PassMemory(tmp_path, ttl_hours=1)
    mem.record_pass(_report(score=100, provider="fake"))
    assert mem.get("abc") is not None
    assert not mem.has_passed("abc", REAL)
    assert mem.has_passed("abc", FAKE)


def test_low_pass_mark_does_not_count_against_the_configured_one(tmp_path: Path) -> None:
    mem = PassMemory(tmp_path, ttl_hours=1)
    mem.record_pass(_report(score=0, passing_score=0))  # `--passing-score 0`, blank answers
    assert not mem.has_passed("abc", REAL)
    assert mem.has_passed("abc", Config(provider="anthropic", passing_score=0))
    mem.record_pass(_report(score=75, passing_score=50))  # low mark, but the score is enough
    assert mem.has_passed("abc", REAL)
    assert not mem.has_passed("abc", Config(provider="anthropic", passing_score=90))


def test_old_format_is_not_honoured_and_is_replaced(tmp_path: Path) -> None:
    mem = PassMemory(tmp_path, ttl_hours=1)
    mem.dir.mkdir()
    stamp = datetime.now(UTC).isoformat()
    mem.path.write_text(json.dumps({"abc": stamp, "def": stamp}))
    assert mem.get("abc") is None
    assert not mem.has_passed("abc", REAL)
    mem.record_pass(_report("xyz"))
    assert set(json.loads(mem.path.read_text())) == {"xyz"}
    assert mem.has_passed("xyz", REAL)


def test_disabled(tmp_path: Path) -> None:
    mem = PassMemory(tmp_path, ttl_hours=0)
    mem.record_pass(_report())
    assert not mem.enabled
    assert not mem.has_passed("abc", REAL)
    assert not mem.path.exists()


def test_corrupt_file_is_ignored(tmp_path: Path) -> None:
    mem = PassMemory(tmp_path, ttl_hours=1)
    mem.dir.mkdir()
    mem.path.write_text("{not json")
    assert not mem.has_passed("abc", REAL)
    mem.path.write_text("[1, 2]")
    assert not mem.has_passed("abc", REAL)
    naive = datetime(2026, 1, 1).isoformat()  # a hand-edited time with no zone
    mem.path.write_text(
        json.dumps(
            {
                "abc": {"at": "not-a-date", "score": 90},
                "def": 3,
                "ghi": {
                    "at": naive,
                    "score": 90,
                    "passing_score": 70,
                    "provider": "x",
                    "model": "",
                },
            }
        )
    )
    assert not mem.has_passed("abc", REAL)
    assert not mem.has_passed("ghi", REAL)
    mem.record_pass(_report("xyz"))
    assert mem.has_passed("xyz", REAL)


def test_forget_and_report(tmp_path: Path) -> None:
    mem = PassMemory(tmp_path, ttl_hours=1)
    mem.forget()  # no file yet: fine
    mem.record_pass(_report())
    mem.forget()
    assert not mem.has_passed("abc", REAL)

    report = _report(provider="fake")
    path = mem.save_report(report)
    assert path.exists()
    assert '"score": 80' in path.read_text()
    assert report.max_score == 100
