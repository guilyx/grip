"""Tests for the quiz history log, the repo registry and `grip study`."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from grip_hook import QUESTION_COUNT, __version__
from grip_hook import cli as cli_module
from grip_hook.cli import cli
from grip_hook.memory import PassMemory
from grip_hook.models import Answer, Question, QuestionGrade, Report, Stage
from grip_hook.study import (
    EXCLUDED_FIELDS,
    INCLUDED_FIELDS,
    Registry,
    build_export,
    data_home,
    read_history,
)
from tests.conftest import FakeTerminalFactory

GOOD = ["this is a long and specific answer because reasons"] * QUESTION_COUNT
SECRET_QUESTION = "What does the frobnicate function do to the widget?"
SECRET_RUBRIC = "Must mention the widget being frobnicated twice."
SECRET_ANSWER = "It frobnicates the widget, obviously."
SECRET_FEEDBACK = "Correct, the widget is indeed frobnicated."


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def in_repo(repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(repo)
    monkeypatch.delenv("GRIP_SKIP", raising=False)
    monkeypatch.delenv("CI", raising=False)
    (repo / ".grip.toml").write_text('provider = "fake"\nmodel = "fake"\n')
    return repo


@pytest.fixture
def terminal(monkeypatch: pytest.MonkeyPatch) -> Callable[[list[str]], FakeTerminalFactory]:
    def _install(answers: list[str]) -> FakeTerminalFactory:
        factory = FakeTerminalFactory(answers)
        monkeypatch.setattr(cli_module, "open_terminal", factory)
        return factory

    return _install


def _report(created_at: datetime, digest: str = "abc", score: int = 80) -> Report:
    return Report(
        created_at=created_at,
        stage=Stage.PRE_PUSH,
        provider="fake",
        model="fake-model",
        diff_digest=digest,
        passing_score=70,
        score=score,
        passed=score >= 70,
        summary="Adds frobnication to src/secret/widget.py",
        questions=[Question(question=SECRET_QUESTION, rubric=SECRET_RUBRIC, focus="behaviour")],
        answers=[Answer(question_index=0, text=SECRET_ANSWER)],
        grades=[QuestionGrade(question_index=0, score=score // 5, feedback=SECRET_FEEDBACK)],
        verdict="You clearly understand the widget.",
    )


def _history_lines(repo: Path) -> list[dict[str, object]]:
    path = repo / ".git" / "grip" / "history.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line]


# -- history.jsonl -----------------------------------------------------------------------


def test_terminal_flow_appends_history_and_registers_repo(
    runner: CliRunner,
    in_repo: Path,
    data_home: Path,
    stage_change: Callable[[str, str], None],
    terminal: Callable[[list[str]], FakeTerminalFactory],
) -> None:
    stage_change("a.py", "x = 1\n")
    terminal(GOOD)
    assert runner.invoke(cli, ["quiz"]).exit_code == 0
    lines = _history_lines(in_repo)
    assert len(lines) == 1
    assert lines[0]["score"] == 100 and lines[0]["stage"] == "manual"
    assert (in_repo / ".git" / "grip" / "last-report.json").exists()

    # A failed quiz is history too.
    stage_change("b.py", "y = 2\n")
    terminal([""] * QUESTION_COUNT)
    assert runner.invoke(cli, ["quiz"]).exit_code == 1
    lines = _history_lines(in_repo)
    assert len(lines) == 2
    assert lines[1]["passed"] is False

    registry = Registry()
    assert registry.home == data_home
    assert registry.list() == [(in_repo / ".git").resolve()]


def test_agent_flow_appends_history(
    runner: CliRunner, in_repo: Path, stage_change: Callable[[str, str], None]
) -> None:
    stage_change("a.py", "x = 1\n")
    assert runner.invoke(cli, ["ask"]).exit_code == 0
    result = runner.invoke(cli, ["grade", "--answers", "-"], input=json.dumps(GOOD))
    assert result.exit_code == 0, result.output
    lines = _history_lines(in_repo)
    assert len(lines) == 1
    assert lines[0]["passed"] is True
    assert Registry().list() == [(in_repo / ".git").resolve()]


def test_append_history_matches_report_dump(tmp_path: Path) -> None:
    mem = PassMemory(tmp_path, ttl_hours=1)
    report = _report(datetime(2026, 1, 1, tzinfo=UTC))
    path = mem.append_history(report)
    mem.append_history(report)
    lines = path.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == report.model_dump(mode="json")
    assert read_history(tmp_path) == [report, report]


def test_read_history_skips_corrupt_lines(tmp_path: Path) -> None:
    grip_dir = tmp_path / "grip"
    grip_dir.mkdir()
    report = _report(datetime(2026, 1, 1, tzinfo=UTC))
    good = json.dumps(report.model_dump(mode="json"))
    (grip_dir / "history.jsonl").write_text(f"{good}\nnot json\n\n{{}}\n{good}\n")
    assert len(read_history(tmp_path)) == 2
    assert read_history(tmp_path / "missing") == []


# -- registry ----------------------------------------------------------------------------


def test_data_home_resolution(tmp_path: Path) -> None:
    assert data_home({"GRIP_DATA_HOME": str(tmp_path)}) == tmp_path
    assert data_home({"XDG_DATA_HOME": "/xdg"}) == Path("/xdg/grip")
    assert data_home({}) == Path.home() / ".local" / "share" / "grip"


def test_registry_add_list_prune(tmp_path: Path) -> None:
    registry = Registry(tmp_path / "home")
    assert registry.list() == []
    a = tmp_path / "a" / ".git"
    b = tmp_path / "b" / ".git"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    registry.add(a)
    registry.add(a)
    registry.add(b)
    assert registry.list() == sorted([a.resolve(), b.resolve()])
    stored = json.loads(registry.path.read_text())
    assert set(stored) == {"version", "repos"}
    assert all(Path(p).is_absolute() for p in stored["repos"])

    b.rmdir()
    assert registry.prune() == [a.resolve()]
    assert registry.list() == [a.resolve()]


def test_registry_ignores_garbage(tmp_path: Path) -> None:
    registry = Registry(tmp_path)
    registry.path.write_text("{not json")
    assert registry.list() == []
    registry.path.write_text(json.dumps({"repos": ["relative/path", 3, str(tmp_path)]}))
    assert registry.list() == [tmp_path]
    registry.path.write_text(json.dumps(["/x"]))
    assert registry.list() == []


def test_installation_id_is_stable(tmp_path: Path) -> None:
    registry = Registry(tmp_path)
    first = registry.installation_id()
    assert Registry(tmp_path).installation_id() == first
    registry.id_path.write_text("garbage")
    assert registry.installation_id() != first


# -- export ------------------------------------------------------------------------------


def _seed(tmp_path: Path, name: str, *reports: Report) -> Path:
    git_dir = tmp_path / name / ".git"
    git_dir.mkdir(parents=True)
    mem = PassMemory(git_dir, ttl_hours=1)
    for report in reports:
        mem.append_history(report)
    return git_dir


def test_build_export_shape_and_anonymity(tmp_path: Path) -> None:
    now = datetime(2026, 9, 24, 12, tzinfo=UTC)
    registry = Registry(tmp_path / "home")
    one = _seed(tmp_path, "one", _report(now - timedelta(days=1), "d1", 80))
    two = _seed(
        tmp_path,
        "two",
        _report(now - timedelta(days=2), "d2", 40),
        _report(now - timedelta(days=60), "d3", 100),
    )
    registry.add(one)
    registry.add(two)

    export = build_export(registry, timedelta(days=45), now)
    doc = export.model_dump(mode="json")
    assert set(doc) == {"version", "generated_at", "installation_id", "grip_version", "quizzes"}
    assert doc["version"] == 1
    assert doc["grip_version"] == __version__
    assert doc["installation_id"] == registry.installation_id()
    assert len(doc["quizzes"]) == 2  # the 60 day old one is out
    assert export.repo_count == 2
    quiz = doc["quizzes"][0]  # oldest first
    assert set(quiz) == {
        "id",
        "repo",
        "created_at",
        "stage",
        "provider",
        "model",
        "passing_score",
        "score",
        "passed",
        "grades",
    }
    assert quiz["score"] == 40 and quiz["passed"] is False and quiz["stage"] == "pre-push"
    assert quiz["grades"] == [{"focus": "behaviour", "score": 8}]
    assert len(quiz["id"]) == 32 and len(quiz["repo"]) == 16
    assert doc["quizzes"][0]["repo"] != doc["quizzes"][1]["repo"]

    text = json.dumps(doc)
    for secret in (
        SECRET_QUESTION,
        SECRET_RUBRIC,
        SECRET_ANSWER,
        SECRET_FEEDBACK,
        "widget",
        "src/secret",
        str(tmp_path),
        "one",
        "two",
        "verdict",
        "summary",
        "diff_digest",
    ):
        assert secret not in text, secret

    # Deterministic ids, and --since 0 means everything.
    again = build_export(registry, timedelta(days=45), now)
    assert [q.id for q in again.quizzes] == [q.id for q in export.quizzes]
    assert len(build_export(registry, None, now).quizzes) == 3


def test_export_command_writes_file(
    runner: CliRunner,
    in_repo: Path,
    stage_change: Callable[[str, str], None],
    terminal: Callable[[list[str]], FakeTerminalFactory],
) -> None:
    stage_change("a.py", "x = 1\n")
    terminal(GOOD)
    assert runner.invoke(cli, ["quiz"]).exit_code == 0

    result = runner.invoke(cli, ["study", "export"])
    assert result.exit_code == 0, result.output
    assert "1 quizzes from 1 repos" in result.output
    files = list(in_repo.glob("grip-export-*.json"))
    assert len(files) == 1
    doc = json.loads(files[0].read_text())
    assert doc["version"] == 1 and len(doc["quizzes"]) == 1
    grades = doc["quizzes"][0]["grades"]
    assert len(grades) == QUESTION_COUNT and all(set(g) == {"focus", "score"} for g in grades)
    text = files[0].read_text()
    assert "a.py" not in text and str(in_repo) not in text and GOOD[0] not in text

    out = in_repo / "nested" / "custom.json"
    result = runner.invoke(cli, ["study", "export", "--out", str(out), "--since", "0"])
    assert result.exit_code == 0, result.output
    assert out.exists() and str(out.relative_to(in_repo)) in result.output


def test_export_respects_since(
    runner: CliRunner, tmp_path: Path, data_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    now = datetime.now(UTC)
    git_dir = _seed(
        tmp_path,
        "old",
        _report(now - timedelta(days=100), "old"),
        _report(now - timedelta(days=1), "new"),
    )
    Registry().add(git_dir)
    out = tmp_path / "x.json"
    assert runner.invoke(cli, ["study", "export", "--out", str(out)]).exit_code == 0
    assert len(json.loads(out.read_text())["quizzes"]) == 1
    args = ["study", "export", "--out", str(out), "--since", "200"]
    assert runner.invoke(cli, args).exit_code == 0
    assert len(json.loads(out.read_text())["quizzes"]) == 2
    assert runner.invoke(cli, ["study", "export", "--since", "-1"]).exit_code == 2


def test_export_with_nothing_registered(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli, ["study", "export"])
    assert result.exit_code == 0, result.output
    assert "no repositories" in result.output
    assert not list(tmp_path.glob("grip-export-*.json"))


def test_status_lists_fields(
    runner: CliRunner, in_repo: Path, stage_change: Callable[[str, str], None]
) -> None:
    result = runner.invoke(cli, ["study", "status"])
    assert result.exit_code == 0, result.output
    assert "registered repositories: 0" in result.output
    assert "quizzes to export: 0" in result.output

    stage_change("a.py", "x = 1\n")
    assert runner.invoke(cli, ["ask"]).exit_code == 0
    assert runner.invoke(cli, ["grade", "--answers", "-"], input=json.dumps(GOOD)).exit_code == 0
    result = runner.invoke(cli, ["study", "status"])
    assert result.exit_code == 0, result.output
    assert "registered repositories: 1" in result.output
    assert "quizzes to export: 1 from 1 repos (the last 45 days)" in result.output
    for field in (*INCLUDED_FIELDS, *EXCLUDED_FIELDS):
        assert field in result.output, field
    assert "rubrics" in result.output and "your answers" in result.output
