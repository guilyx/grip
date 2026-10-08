"""The evals harness runs offline and its committed fake-provider snapshot is current."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "evals" / "run.py"
SNAPSHOT = ROOT / "evals" / "snapshots" / "fake.json"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RUN), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def _comparable(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Everything that must be reproducible: drop timestamps and timings."""
    data = json.loads(json.dumps(snapshot))
    data.pop("generated_at", None)
    for case in data["cases"]:
        case.pop("seconds", None)
    return data


def test_fake_snapshot_is_current(tmp_path: Path) -> None:
    out = tmp_path / "fake.json"
    result = _run("--provider", "fake", "--repeats", "2", "--out", str(out))
    assert result.returncode == 0, result.stderr
    fresh = json.loads(out.read_text("utf-8"))
    committed = json.loads(SNAPSHOT.read_text("utf-8"))
    assert _comparable(fresh) == _comparable(committed), (
        "evals/snapshots/fake.json is stale: run `python evals/run.py --provider fake --repeats 2`"
    )


def test_snapshot_shape_and_blank_arm() -> None:
    data = json.loads(SNAPSHOT.read_text("utf-8"))
    assert data["provider"] == "fake"
    assert {"blank", "generic", "notes"} == set(data["summary"]["arms"])
    assert len(data["cases"]) >= 8
    for case in data["cases"]:
        assert len(case["questions"]) == data["question_count"]
        assert case["files"], case["name"]
        blank = case["arms"]["blank"]
        assert blank["passed"] is False and blank["max"] == 0
        for arm in case["arms"].values():
            assert len(arm["answers"]) == data["question_count"]
            assert len(arm["scores"]) == data["repeats"]
    # The fake grader scores by length, so generic answers pass it. That is a documented
    # weakness of the stand-in, and the reason real-model snapshots matter.
    assert data["summary"]["arms"]["generic"]["pass_rate"] == 1.0


def test_case_selection_and_markdown(tmp_path: Path) -> None:
    out = tmp_path / "subset.json"
    result = _run("--provider", "fake", "--cases", "clamp-add,rename-only", "--out", str(out))
    assert result.returncode == 0, result.stderr
    assert "| clamp-add |" in result.stdout and "| rename-only |" in result.stdout
    assert "| ci-cache |" not in result.stdout
    data = json.loads(out.read_text("utf-8"))
    assert [c["name"] for c in data["cases"]] == ["clamp-add", "rename-only"]
    assert data["summary"]["separation"] is not None


@pytest.mark.parametrize("args", [["--cases", "nope"], ["--arms", "blank,wild"]])
def test_bad_arguments(args: list[str]) -> None:
    result = _run("--provider", "fake", *args)
    assert result.returncode != 0
    assert "nope" in result.stderr or "wild" in result.stderr
