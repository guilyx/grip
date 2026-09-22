"""Tests for the agent-driven flow: ask, grade, check and the Claude Code hook."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from click.testing import CliRunner

from grip_hook import QUESTION_COUNT
from grip_hook.agent import PendingStore, gated_mode, parse_answers
from grip_hook.cli import cli
from grip_hook.errors import GripError

GOOD = ["this is a long and specific answer because reasons"] * QUESTION_COUNT
BAD = [""] * QUESTION_COUNT


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


def _json(result: object) -> dict[str, object]:
    output = getattr(result, "output", "")
    return json.loads(output)  # type: ignore[no-any-return]


def _hook_payload(command: str, cwd: Path) -> str:
    return json.dumps(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "cwd": str(cwd),
        }
    )


# -- ask ---------------------------------------------------------------------------------


def test_ask_nothing_staged(runner: CliRunner, in_repo: Path) -> None:
    result = runner.invoke(cli, ["ask"])
    assert result.exit_code == 0, result.output
    assert _json(result)["status"] == "nothing-to-quiz"
    assert not (in_repo / ".git" / "grip" / "pending.json").exists()


def test_ask_hides_rubrics_and_stores_them(
    runner: CliRunner, in_repo: Path, stage_change: Callable[[str, str], None]
) -> None:
    stage_change("a.py", "x = 1\n")
    result = runner.invoke(cli, ["ask"])
    assert result.exit_code == 0, result.output
    payload = _json(result)
    assert payload["status"] == "questions"
    questions = payload["questions"]
    assert isinstance(questions, list) and len(questions) == QUESTION_COUNT
    assert [q["index"] for q in questions] == list(range(1, QUESTION_COUNT + 1))
    assert all(set(q) == {"index", "focus", "question"} for q in questions)
    assert "rubric" not in result.output
    assert payload["diff"] == {
        "description": "staged changes",
        "files": ["a.py"],
        "truncated": False,
    }

    pending = PendingStore(in_repo / ".git").load()
    assert pending is not None
    assert pending.question_set.questions[0].rubric
    assert pending.diff.files == ("a.py",)
    assert pending.provider == "fake"


# -- grade -------------------------------------------------------------------------------


def test_grade_pass_is_remembered(
    runner: CliRunner, in_repo: Path, stage_change: Callable[[str, str], None]
) -> None:
    stage_change("a.py", "x = 1\n")
    assert runner.invoke(cli, ["ask"]).exit_code == 0
    assert runner.invoke(cli, ["check"]).exit_code == 1

    report = in_repo / "out" / "report.json"
    result = runner.invoke(
        cli, ["grade", "--answers", "-", "--report", str(report)], input=json.dumps(GOOD)
    )
    assert result.exit_code == 0, result.output
    payload = _json(result)
    assert payload["status"] == "pass"
    assert payload["score"] == 100
    grades = payload["grades"]
    assert isinstance(grades, list) and len(grades) == QUESTION_COUNT
    assert grades[0]["max_score"] == 20
    assert json.loads(report.read_text())["passed"] is True
    assert (in_repo / ".git" / "grip" / "last-report.json").exists()
    assert not (in_repo / ".git" / "grip" / "pending.json").exists()

    assert runner.invoke(cli, ["check"]).exit_code == 0
    assert _json(runner.invoke(cli, ["ask"]))["status"] == "already-passed"


def test_grade_fail(
    runner: CliRunner, in_repo: Path, stage_change: Callable[[str, str], None]
) -> None:
    stage_change("a.py", "x = 1\n")
    assert runner.invoke(cli, ["ask"]).exit_code == 0
    answers = in_repo / "answers.json"
    answers.write_text(json.dumps({"answers": BAD}))
    result = runner.invoke(cli, ["grade", "--answers", str(answers), "--passing-score", "10"])
    assert result.exit_code == 1, result.output
    assert _json(result)["status"] == "fail"
    assert not (in_repo / ".git" / "grip" / "passed.json").exists()
    assert runner.invoke(cli, ["check"]).exit_code == 1


def test_grade_without_pending(runner: CliRunner, in_repo: Path) -> None:
    result = runner.invoke(cli, ["grade", "--answers", "-"], input="[]")
    assert result.exit_code == 2
    assert "grip ask" in result.output


def test_grade_from_file_with_fewer_answers(
    runner: CliRunner, in_repo: Path, stage_change: Callable[[str, str], None]
) -> None:
    stage_change("a.py", "x = 1\n")
    assert runner.invoke(cli, ["ask"]).exit_code == 0
    result = runner.invoke(cli, ["grade", "--answers", "-"], input=json.dumps(GOOD[:2]))
    assert result.exit_code == 1
    grades = _json(result)["grades"]
    assert isinstance(grades, list)
    assert grades[0]["score"] > 0 and grades[-1]["score"] == 0


def test_parse_answers() -> None:
    assert parse_answers('["a", "b"]') == ["a", "b", "", "", ""]
    assert parse_answers('{"answers": [" x "]}') == ["x", "", "", "", ""]
    for bad in ("not json", '{"answers": "x"}', "[1, 2]", json.dumps(["a"] * 6)):
        with pytest.raises(GripError):
            parse_answers(bad)


# -- check and the Claude Code hook ------------------------------------------------------


def test_check_unpushed(
    runner: CliRunner,
    in_repo: Path,
    run_git: Callable[..., str],
    stage_change: Callable[[str, str], None],
) -> None:
    # No remotes: everything since the root commit is unpushed.
    assert runner.invoke(cli, ["check", "--unpushed"]).exit_code == 1
    assert runner.invoke(cli, ["ask", "--unpushed"]).exit_code == 0
    assert runner.invoke(cli, ["grade", "--answers", "-"], input=json.dumps(GOOD)).exit_code == 0
    assert runner.invoke(cli, ["check", "--unpushed"]).exit_code == 0
    # A new commit changes the unpushed diff, so the pass no longer applies.
    stage_change("b.py", "y = 2\n")
    run_git("commit", "-q", "-m", "b")
    assert runner.invoke(cli, ["check", "--unpushed"]).exit_code == 1


@pytest.mark.parametrize(
    ("command", "gate", "expected"),
    [
        ("git push", "push", "unpushed"),
        ("git push -u origin main", "push", "unpushed"),
        ("git add -A && git commit -m x && git push", "push", "unpushed"),
        ("git -C sub push", "push", "unpushed"),
        ("git -c push.default=simple push", "push", "unpushed"),
        ("git commit -m x", "push", None),
        ("git commit -m x", "commit", "staged"),
        ("git commit -m x", "both", "staged"),
        ("git push", "commit", None),
        ("echo git pushed", "both", None),
        ("npm run push", "both", None),
        ("git log --oneline", "both", None),
        ("gitk push", "both", None),
    ],
)
def test_gated_mode(command: str, gate: str, expected: str | None) -> None:
    assert gated_mode(command, gate) == expected  # type: ignore[arg-type]


def test_agent_hook_denies_push_until_passed(
    runner: CliRunner, in_repo: Path, stage_change: Callable[[str, str], None]
) -> None:
    payload = _hook_payload("git push origin main", in_repo)
    result = runner.invoke(cli, ["agent-hook", "claude-code"], input=payload)
    assert result.exit_code == 0, result.output
    decision = _json(result)["hookSpecificOutput"]
    assert isinstance(decision, dict)
    assert decision["permissionDecision"] == "deny"
    assert "/grip:quiz --unpushed" in str(decision["permissionDecisionReason"])

    assert runner.invoke(cli, ["ask", "--unpushed"]).exit_code == 0
    assert runner.invoke(cli, ["grade", "--answers", "-"], input=json.dumps(GOOD)).exit_code == 0
    result = runner.invoke(cli, ["agent-hook", "claude-code"], input=payload)
    assert result.exit_code == 0
    assert result.output.strip() == ""


def test_agent_hook_ignores_other_commands_and_tools(runner: CliRunner, in_repo: Path) -> None:
    for payload in (
        _hook_payload("git status", in_repo),
        _hook_payload("git commit -m x", in_repo),  # default gate is push only
        json.dumps({"tool_name": "Edit", "tool_input": {"file_path": "x"}, "cwd": str(in_repo)}),
        "not json",
        "",
    ):
        result = runner.invoke(cli, ["agent-hook", "claude-code"], input=payload)
        assert result.exit_code == 0, result.output
        assert result.output.strip() == ""


def test_agent_hook_gate_commit(
    runner: CliRunner, in_repo: Path, stage_change: Callable[[str, str], None]
) -> None:
    payload = _hook_payload("git commit -m x", in_repo)
    # Nothing staged: nothing to gate.
    result = runner.invoke(cli, ["agent-hook", "claude-code", "--gate", "both"], input=payload)
    assert result.output.strip() == ""
    stage_change("a.py", "x = 1\n")
    result = runner.invoke(cli, ["agent-hook", "claude-code", "--gate", "both"], input=payload)
    assert "deny" in result.output
    assert "/grip:quiz and" in result.output


def test_agent_hook_skip_envs_and_outside_repo(
    runner: CliRunner,
    in_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _hook_payload("git push", in_repo)
    monkeypatch.setenv("GRIP_SKIP", "1")
    assert runner.invoke(cli, ["agent-hook", "claude-code"], input=payload).output.strip() == ""
    monkeypatch.delenv("GRIP_SKIP")
    monkeypatch.setenv("CI", "true")
    assert runner.invoke(cli, ["agent-hook", "claude-code"], input=payload).output.strip() == ""
    monkeypatch.delenv("CI")
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    result = runner.invoke(
        cli, ["agent-hook", "claude-code"], input=_hook_payload("git push", outside)
    )
    assert result.exit_code == 0
    assert result.output.strip() == ""


# -- plugin files are well-formed --------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]


def test_plugin_manifests() -> None:
    marketplace = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    plugin_dir = ROOT / marketplace["plugins"][0]["source"]
    manifest = json.loads((plugin_dir / ".claude-plugin" / "plugin.json").read_text())
    assert marketplace["name"] == "grip"
    assert manifest["name"] == marketplace["plugins"][0]["name"] == "grip"
    hooks = json.loads((plugin_dir / "hooks" / "hooks.json").read_text())
    command = hooks["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert "grip agent-hook claude-code" in command
    skill = (plugin_dir / "skills" / "quiz" / "SKILL.md").read_text()
    assert skill.startswith("---\nname: quiz\n")
    assert "grip ask" in skill and "grip grade" in skill
