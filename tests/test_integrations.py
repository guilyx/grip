"""Tests for `grip agents`: detection and the files it writes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from grip_hook import integrations
from grip_hook.cli import cli
from grip_hook.errors import GripError
from grip_hook.integrations import (
    AGENTS,
    END,
    PLUGIN_ID,
    START,
    Action,
    agent,
    apply,
    block,
    detect,
)


def _none(_: str) -> str | None:
    return None


def _which(*names: str):  # type: ignore[no-untyped-def]
    def which(name: str) -> str | None:
        return f"/usr/bin/{name}" if name in names else None

    return which


# -- detection -----------------------------------------------------------------------------


def test_detect_nothing_installed_offers_agents_md(tmp_path: Path) -> None:
    found = detect(home=tmp_path, which=_none)
    assert [a.key for a in found] == ["agents-md"]


def test_detect_by_binary_and_home_dir(tmp_path: Path) -> None:
    (tmp_path / ".gemini").mkdir()
    (tmp_path / ".vscode" / "extensions" / "saoudrizwan.claude-dev-3.1.0").mkdir(parents=True)
    found = {a.key for a in detect(home=tmp_path, which=_which("codex", "cursor"))}
    assert found == {"agents-md", "codex", "cursor", "gemini", "cline"}


def test_agent_lookup() -> None:
    assert agent("codex").name == "Codex CLI"
    with pytest.raises(GripError, match="unknown agent"):
        agent("emacs")


def test_every_agent_has_a_target_and_unique_key() -> None:
    keys = [a.key for a in AGENTS]
    assert len(keys) == len(set(keys))
    assert all(a.targets for a in AGENTS)


# -- blocks in files the developer owns -----------------------------------------------------


def test_block_written_updated_removed(tmp_path: Path) -> None:
    codex = agent("codex")
    [change] = apply(tmp_path, [codex])
    path = tmp_path / "AGENTS.md"
    assert change.path == path and change.action is Action.WRITTEN
    assert path.read_text() == block()

    [again] = apply(tmp_path, [codex])
    assert again.action is Action.UNCHANGED

    [removed] = apply(tmp_path, [codex], remove=True)
    assert removed.action is Action.REMOVED
    assert not path.exists()
    [absent] = apply(tmp_path, [codex], remove=True)
    assert absent.action is Action.ABSENT


def test_block_appended_to_existing_file_and_stripped_cleanly(tmp_path: Path) -> None:
    path = tmp_path / "AGENTS.md"
    original = "# My project\n\nRun the tests with `make test`.\n"
    path.write_text(original)
    [change] = apply(tmp_path, [agent("codex")])
    assert change.action is Action.UPDATED
    text = path.read_text()
    assert text.startswith(original)
    assert text.count(START) == 1 and text.endswith(END + "\n")

    # A stale block is replaced in place, keeping what surrounds it.
    path.write_text(text.replace("five-question", "three-question") + "\n## After\n\nmore\n")
    [change] = apply(tmp_path, [agent("codex")])
    assert change.action is Action.UPDATED
    updated = path.read_text()
    assert "five-question" in updated and "three-question" not in updated
    assert updated.startswith(original) and updated.endswith("## After\n\nmore\n")

    apply(tmp_path, [agent("codex")], remove=True)
    assert path.read_text() == original.rstrip("\n") + "\n## After\n\nmore\n"


def test_unterminated_block_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text(f"{START}\nbroken\n")
    with pytest.raises(GripError, match="no <!-- grip:end -->"):
        apply(tmp_path, [agent("codex")])


def test_shared_file_written_once(tmp_path: Path) -> None:
    changes = apply(tmp_path, [agent("agents-md"), agent("codex"), agent("opencode")])
    assert len(changes) == 1
    assert len(changes[0].agents) == 3


def test_dry_run_touches_nothing(tmp_path: Path) -> None:
    changes = apply(tmp_path, AGENTS, dry_run=True)
    assert {c.action for c in changes} == {Action.WRITTEN}
    assert list(tmp_path.iterdir()) == []


# -- whole files grip owns ------------------------------------------------------------------


def test_owned_files_carry_frontmatter(tmp_path: Path) -> None:
    changes = apply(tmp_path, [agent("cursor"), agent("windsurf"), agent("cline")])
    paths = {c.path.relative_to(tmp_path).as_posix(): c.action for c in changes}
    assert paths == {
        ".cursor/rules/grip.mdc": Action.WRITTEN,
        ".windsurf/rules/grip.md": Action.WRITTEN,
        ".clinerules/grip.md": Action.WRITTEN,
    }
    mdc = (tmp_path / ".cursor" / "rules" / "grip.mdc").read_text()
    assert mdc.startswith("---\n") and "alwaysApply: true" in mdc and mdc.endswith(END + "\n")
    assert (tmp_path / ".clinerules" / "grip.md").read_text() == block()

    for change in apply(
        tmp_path, [agent("cursor"), agent("windsurf"), agent("cline")], remove=True
    ):
        assert change.action is Action.REMOVED
        assert not change.path.exists()


# -- Claude Code settings ------------------------------------------------------------------


def test_settings_merged_and_restored(tmp_path: Path) -> None:
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"permissions": {"allow": ["Bash(ls)"]}}))

    changes = {c.path.name: c.action for c in apply(tmp_path, [agent("claude-code")])}
    assert changes == {"CLAUDE.md": Action.WRITTEN, "settings.json": Action.UPDATED}
    data = json.loads(settings.read_text())
    assert data["permissions"] == {"allow": ["Bash(ls)"]}
    assert data["enabledPlugins"] == {PLUGIN_ID: True}
    assert data["extraKnownMarketplaces"]["grip"]["source"]["repo"] == "guilyx/grip"

    assert all(c.action is Action.UNCHANGED for c in apply(tmp_path, [agent("claude-code")]))

    apply(tmp_path, [agent("claude-code")], remove=True)
    assert json.loads(settings.read_text()) == {"permissions": {"allow": ["Bash(ls)"]}}
    assert not (tmp_path / "CLAUDE.md").exists()


def test_settings_created_and_deleted_when_only_grip(tmp_path: Path) -> None:
    settings = tmp_path / ".claude" / "settings.json"
    apply(tmp_path, [agent("claude-code")])
    assert settings.exists()
    apply(tmp_path, [agent("claude-code")], remove=True)
    assert not settings.exists()


def test_settings_must_be_an_object(tmp_path: Path) -> None:
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text("[1, 2]")
    with pytest.raises(GripError, match="JSON object"):
        apply(tmp_path, [agent("claude-code")])
    settings.write_text("{not json")
    with pytest.raises(GripError, match="not valid JSON"):
        apply(tmp_path, [agent("claude-code")])


# -- command line -------------------------------------------------------------------------


@pytest.fixture
def in_repo(repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.chdir(repo)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(integrations.shutil, "which", _none)
    return repo


def test_cli_list_writes_nothing(in_repo: Path) -> None:
    result = CliRunner().invoke(cli, ["agents", "--list"])
    assert result.exit_code == 0, result.output
    assert "AGENTS.md" in result.output and "codex" in result.output
    assert not (in_repo / "AGENTS.md").exists()


def test_cli_detected_default(in_repo: Path) -> None:
    (Path.home() / ".codex").mkdir()
    result = CliRunner().invoke(cli, ["agents"])
    assert result.exit_code == 0, result.output
    assert (in_repo / "AGENTS.md").exists()
    assert not (in_repo / "GEMINI.md").exists()
    assert not (in_repo / "CLAUDE.md").exists()  # the real PATH is not consulted
    assert "written" in result.output and "skills add" in result.output


def test_cli_all_then_remove(in_repo: Path) -> None:
    runner = CliRunner()
    assert runner.invoke(cli, ["agents", "--all"]).exit_code == 0
    for rel in ("AGENTS.md", "CLAUDE.md", "GEMINI.md", ".cursor/rules/grip.mdc"):
        assert (in_repo / rel).exists(), rel
    result = runner.invoke(cli, ["agents", "--all", "--remove"])
    assert result.exit_code == 0, result.output
    for rel in ("AGENTS.md", "CLAUDE.md", "GEMINI.md", ".cursor/rules/grip.mdc"):
        assert not (in_repo / rel).exists(), rel


def test_cli_specific_agents_and_dry_run(in_repo: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["agents", "--agent", "gemini", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "GEMINI.md" in result.output and "would write" in result.output
    assert not (in_repo / "GEMINI.md").exists()
    assert runner.invoke(cli, ["agents", "--agent", "gemini", "--agent", "codex"]).exit_code == 0
    assert (in_repo / "GEMINI.md").exists() and (in_repo / "AGENTS.md").exists()
    assert not (in_repo / "CLAUDE.md").exists()
