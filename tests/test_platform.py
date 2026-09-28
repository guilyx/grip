"""Keep A Grip: credentials, the client, solve and submit."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from grip_hook import cli as cli_module
from grip_hook import platform as platform_module
from grip_hook.cli import cli
from grip_hook.git import Diff, Git
from grip_hook.models import Answer, Question, QuestionGrade, Report, Stage
from grip_hook.platform import (
    STATE_FILE,
    Client,
    Credentials,
    PlatformError,
    Problem,
    SolveState,
    attempt_payload,
    credentials_path,
    diff_stats,
    forget_credentials,
    load_credentials,
    read_state,
    save_credentials,
)

PROBLEM = {
    "id": "00000000-0000-0000-0000-000000000001",
    "slug": "cmd-vel-safety-filter",
    "title": "cmd_vel safety filter",
    "category": "ros2",
    "difficulty": "easy",
    "summary": "A node between teleop and the base.",
    "statement": "## The situation\n\nWrite the node.",
    "starter_repo": None,
    "starter_ref": None,
    "tests_command": None,
    "focus": ["QoS choices", "stale scans"],
    "tags": ["rclpy"],
    "passing_score": 60,
}


class FakePlatform:
    """An in-memory Keep A Grip, plugged in as the client's opener."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any] | None, str | None]] = []
        self.attempts: list[dict[str, Any]] = []

    def __call__(
        self, method: str, url: str, body: dict[str, Any] | None, token: str | None
    ) -> tuple[int, Any]:
        self.calls.append((method, url, body, token))
        path = (url.split("/", 3)[3] if url.count("/") >= 3 else "").split("?", 1)[0]
        if path == "api/me":
            if token != "kag_good":
                return 401, {"error": "invalid or revoked token"}
            return 200, {"id": "u1", "github_login": "guilyx", "avatar_url": None}
        if path == "api/problems":
            return 200, {"problems": [PROBLEM]}
        if path == f"api/problems/{PROBLEM['slug']}":
            return 200, PROBLEM
        if path.startswith("api/problems/"):
            return 404, {"error": "no such problem"}
        if path == "api/attempts":
            if token != "kag_good":
                return 401, {"error": "invalid or revoked token"}
            assert body is not None
            self.attempts.append(body)
            passed = (
                body["score"] >= PROBLEM["passing_score"] and body.get("tests_passed") is not False
            )
            return 201, {
                "attempt_id": "a1",
                "score": body["score"],
                "passed": passed,
                "passing_score": PROBLEM["passing_score"],
                "best_score": body["score"],
                "first_solve": passed,
                "points": 10 if passed else 0,
            }
        return 404, {"error": "not found"}


@pytest.fixture
def fake_platform(monkeypatch: pytest.MonkeyPatch) -> FakePlatform:
    fake = FakePlatform()
    monkeypatch.setattr(platform_module, "_urllib_opener", fake)
    monkeypatch.delenv("KEEPAGRIP_TOKEN", raising=False)
    monkeypatch.delenv("KEEPAGRIP_URL", raising=False)
    return fake


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ------------------------------------------------------------------ credentials


def test_credentials_roundtrip(data_home: Path) -> None:
    assert load_credentials() is None
    path = save_credentials(Credentials(url="https://kag.test", token="kag_abc", github_login="me"))
    assert path == credentials_path()
    assert path.parent == data_home
    creds = load_credentials()
    assert creds is not None
    assert (creds.url, creds.token, creds.github_login) == ("https://kag.test", "kag_abc", "me")
    assert forget_credentials() is True
    assert forget_credentials() is False
    assert load_credentials() is None


def test_credentials_env_override(data_home: Path) -> None:
    save_credentials(Credentials(url="https://kag.test", token="kag_file"))
    env = {"KEEPAGRIP_TOKEN": "kag_env"}
    creds = load_credentials(env=env)
    assert creds is not None
    assert creds.token == "kag_env"
    assert creds.url == platform_module.DEFAULT_URL
    creds = load_credentials(env={"KEEPAGRIP_URL": "https://other.test"})
    assert creds is not None
    assert (creds.token, creds.url) == ("kag_file", "https://other.test")


def test_credentials_file_garbage(data_home: Path) -> None:
    credentials_path().parent.mkdir(parents=True)
    credentials_path().write_text("not json")
    assert load_credentials() is None


# ------------------------------------------------------------------ client


def test_client_calls(fake_platform: FakePlatform) -> None:
    client = Client("https://kag.test/", "kag_good")
    assert client.whoami()["github_login"] == "guilyx"
    problem = client.problem("cmd-vel-safety-filter")
    assert isinstance(problem, Problem)
    assert problem.passing_score == 60
    assert [p["slug"] for p in client.problems("ros2")] == ["cmd-vel-safety-filter"]
    method, url, _, token = fake_platform.calls[0]
    assert (method, url, token) == ("GET", "https://kag.test/api/me", "kag_good")


def test_client_errors(fake_platform: FakePlatform) -> None:
    with pytest.raises(PlatformError, match="401"):
        Client("https://kag.test", "kag_bad").whoami()
    with pytest.raises(PlatformError, match="404"):
        Client("https://kag.test").problem("nope")
    with pytest.raises(PlatformError, match="not a problem slug"):
        Client("https://kag.test").problem("../etc")
    with pytest.raises(PlatformError, match="not logged in"):
        Client.from_credentials(None)


def test_client_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A dead host is reported as a PlatformError, not a traceback."""
    monkeypatch.setenv("no_proxy", "*")
    with pytest.raises(PlatformError, match="could not reach"):
        Client("http://127.0.0.1:9").whoami()


# ------------------------------------------------------------------ payloads


def test_diff_stats() -> None:
    diff = Diff(
        "x",
        " a.py | 3 ++-\n b.py | 1 +\n 2 files changed, 3 insertions(+), 1 deletion(-)",
        "patch",
        ("a.py", "b.py"),
    )
    assert diff_stats(diff) == platform_module.DiffStats(2, 3, 1)
    assert diff_stats(Diff("x", "", "", ())) == platform_module.DiffStats(0, 0, 0)


def _report(score: int) -> Report:
    questions = [Question(question=f"q{i}", rubric="r", focus=f"focus{i}") for i in range(5)]
    grades = [QuestionGrade(question_index=i, score=score // 5, feedback="ok") for i in range(5)]
    return Report(
        stage=Stage.MANUAL,
        provider="fake",
        model="fake-1",
        diff_digest="d",
        passing_score=60,
        score=score,
        passed=score >= 60,
        summary="s",
        questions=questions,
        answers=[Answer(question_index=i, text="a") for i in range(5)],
        grades=grades,
        verdict="v",
    )


def test_attempt_payload() -> None:
    started = datetime(2026, 1, 1, tzinfo=UTC)
    state = SolveState(
        url="https://kag.test",
        problem="p",
        title="P",
        base="abc",
        passing_score=60,
        focus=["a"],
        started_at=started,
    )
    diff = Diff("x", " 1 file changed, 4 insertions(+)", "patch", ("a.py",))
    payload = attempt_payload(state, _report(80), diff, True, now=started + timedelta(minutes=5))
    assert payload["problem"] == "p"
    assert payload["score"] == 80
    assert payload["grades"] == [{"focus": f"focus{i}", "score": 16} for i in range(5)]
    assert (payload["files"], payload["insertions"], payload["deletions"]) == (1, 4, 0)
    assert payload["tests_passed"] is True
    assert payload["duration_s"] == 300
    assert payload["source"] == "cli"
    assert payload["stage"] == "solve"


# ------------------------------------------------------------------ commands


def test_login_logout(runner: CliRunner, fake_platform: FakePlatform, data_home: Path) -> None:
    result = runner.invoke(cli, ["login", "kag_good", "--url", "https://kag.test"])
    assert result.exit_code == 0, result.output
    assert "guilyx" in result.output
    creds = load_credentials()
    assert creds is not None
    assert creds.token == "kag_good"

    result = runner.invoke(cli, ["whoami"])
    assert result.exit_code == 0
    assert "guilyx at https://kag.test" in result.output

    result = runner.invoke(cli, ["login", "kag_bad", "--url", "https://kag.test"])
    assert result.exit_code == 2
    assert "401" in result.output

    assert runner.invoke(cli, ["logout"]).exit_code == 0
    assert load_credentials() is None
    result = runner.invoke(cli, ["whoami"])
    assert result.exit_code == 2
    assert "not logged in" in result.output


def test_problems_list(runner: CliRunner, fake_platform: FakePlatform) -> None:
    result = runner.invoke(cli, ["problems", "--category", "ros2"])
    assert result.exit_code == 0, result.output
    assert "cmd-vel-safety-filter" in result.output
    assert "easy" in result.output


def test_solve_sets_up_repo(runner: CliRunner, fake_platform: FakePlatform, tmp_path: Path) -> None:
    target = tmp_path / "work"
    result = runner.invoke(cli, ["solve", "cmd-vel-safety-filter", "--dir", str(target)])
    assert result.exit_code == 0, result.output
    assert "grip submit" in result.output
    git = Git(target)
    assert (target / "PROBLEM.md").read_text().startswith("# cmd_vel safety filter")
    assert "QoS choices" in (target / "PROBLEM.md").read_text()
    assert (target / ".grip.toml").read_text() == "passing_score = 60\n"
    assert git.run("status", "--porcelain") == ""
    state = read_state(git.git_dir())
    assert state.problem == "cmd-vel-safety-filter"
    assert state.base == git.run("rev-parse", "HEAD").strip()
    assert state.focus == ["QoS choices", "stale scans"]

    result = runner.invoke(cli, ["solve", "cmd-vel-safety-filter", "--dir", str(target)])
    assert result.exit_code == 2
    assert "not empty" in result.output


def test_submit_reports_attempt(
    runner: CliRunner,
    fake_platform: FakePlatform,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    make_terminal: Any,
) -> None:
    target = tmp_path / "work"
    assert (
        runner.invoke(cli, ["solve", "cmd-vel-safety-filter", "--dir", str(target)]).exit_code == 0
    )
    monkeypatch.chdir(target)
    (target / "filter.py").write_text("def clamp(v, lo, hi):\n    return max(lo, min(hi, v))\n")

    result = runner.invoke(cli, ["submit", "--provider", "fake"])
    assert result.exit_code == 2
    assert "not logged in" in result.output
    assert fake_platform.attempts == []

    save_credentials(Credentials(url="https://kag.test", token="kag_good"))
    factory = make_terminal(["because it clamps the velocity to the configured limits"] * 5)
    monkeypatch.setattr(cli_module, "open_terminal", factory)
    result = runner.invoke(cli, ["submit", "--provider", "fake"])
    assert result.exit_code == 0, result.output
    assert "solved" in result.output
    assert "https://kag.test/problems/cmd-vel-safety-filter" in result.output
    assert len(fake_platform.attempts) == 1
    attempt = fake_platform.attempts[0]
    assert attempt["problem"] == "cmd-vel-safety-filter"
    assert attempt["score"] == 100
    assert attempt["files"] == 1
    assert attempt["tests_passed"] is None
    assert len(attempt["grades"]) == 5
    # The quiz saw the problem context, and the uncommitted file made it into the diff.
    assert "filter.py" in factory.text
    assert (Git(target).git_dir() / STATE_FILE).exists()


def test_submit_fail_and_dry_run(
    runner: CliRunner,
    fake_platform: FakePlatform,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    make_terminal: Any,
) -> None:
    target = tmp_path / "work"
    assert (
        runner.invoke(cli, ["solve", "cmd-vel-safety-filter", "--dir", str(target)]).exit_code == 0
    )
    monkeypatch.chdir(target)
    save_credentials(Credentials(url="https://kag.test", token="kag_good"))

    result = runner.invoke(cli, ["submit", "--provider", "fake"])
    assert result.exit_code == 2
    assert "nothing has changed" in result.output

    (target / "filter.py").write_text("x = 1\n")
    monkeypatch.setattr(cli_module, "open_terminal", make_terminal(["", "", "", "", ""]))
    result = runner.invoke(cli, ["submit", "--provider", "fake", "--dry-run"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output[result.output.index("{") :])
    assert payload["score"] == 0
    assert fake_platform.attempts == []

    monkeypatch.setattr(cli_module, "open_terminal", make_terminal(["", "", "", "", ""]))
    result = runner.invoke(cli, ["submit", "--provider", "fake"])
    assert result.exit_code == 1
    assert "not yet" in result.output
    assert fake_platform.attempts[-1]["score"] == 0


def test_submit_runs_tests(
    runner: CliRunner,
    fake_platform: FakePlatform,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    make_terminal: Any,
) -> None:
    monkeypatch.setitem(PROBLEM, "tests_command", "test -f passes.txt")
    target = tmp_path / "work"
    assert (
        runner.invoke(cli, ["solve", "cmd-vel-safety-filter", "--dir", str(target)]).exit_code == 0
    )
    monkeypatch.chdir(target)
    save_credentials(Credentials(url="https://kag.test", token="kag_good"))
    (target / "filter.py").write_text("x = 1\n")
    monkeypatch.setattr(cli_module, "open_terminal", make_terminal(["because " * 4] * 5))

    result = runner.invoke(cli, ["submit", "--provider", "fake"])
    assert result.exit_code == 1, result.output
    assert "tests failed" in result.output
    assert fake_platform.attempts[-1]["tests_passed"] is False

    (target / "passes.txt").write_text("ok")
    monkeypatch.setattr(cli_module, "open_terminal", make_terminal(["because " * 4] * 5))
    result = runner.invoke(cli, ["submit", "--provider", "fake"])
    assert result.exit_code == 0, result.output
    assert fake_platform.attempts[-1]["tests_passed"] is True

    monkeypatch.setattr(cli_module, "open_terminal", make_terminal(["because " * 4] * 5))
    result = runner.invoke(cli, ["submit", "--provider", "fake", "--no-tests"])
    assert result.exit_code == 0, result.output
    assert fake_platform.attempts[-1]["tests_passed"] is None


def test_submit_outside_solve_repo(
    runner: CliRunner, git: Git, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(git.cwd)
    result = runner.invoke(cli, ["submit"])
    assert result.exit_code == 2
    assert "grip solve" in result.output
