"""Tests for `grip init` and the configuration template it writes."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from grip_hook import cli as cli_module
from grip_hook import integrations
from grip_hook.cli import cli
from grip_hook.config import load_config, render_template
from grip_hook.errors import ConfigError


def _none(_: str) -> str | None:
    return None


@pytest.fixture
def in_repo(repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.chdir(repo)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(integrations.shutil, "which", _none)
    return repo


def test_template_round_trips(tmp_path: Path) -> None:
    (tmp_path / ".grip.toml").write_text(
        render_template(provider="ollama", passing_score=85, difficulty="hard")
    )
    cfg = load_config(tmp_path)
    assert (cfg.provider, cfg.passing_score, cfg.difficulty.value) == ("ollama", 85, "hard")
    assert cfg.remember_passes_hours == 24.0  # commented-out lines stay comments


def test_template_rejects_bad_values() -> None:
    with pytest.raises(ConfigError):
        render_template(provider="fake", difficulty="brutal")
    with pytest.raises(ConfigError):
        render_template(provider="fake", passing_score=101)


def test_init_writes_everything(in_repo: Path) -> None:
    result = CliRunner().invoke(cli, ["init", "--provider", "fake", "--passing-score", "60"])
    assert result.exit_code == 0, result.output
    cfg = load_config(in_repo)
    assert cfg.provider == "fake" and cfg.passing_score == 60
    assert (in_repo / ".git" / "hooks" / "pre-push").exists()
    assert not (in_repo / ".git" / "hooks" / "pre-commit").exists()
    assert (in_repo / "AGENTS.md").exists()
    assert "grip quiz --provider fake" in result.output


def test_init_keeps_existing_toml_unless_forced(in_repo: Path) -> None:
    (in_repo / ".grip.toml").write_text("passing_score = 42\n")
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--no-hook", "--no-agents"])
    assert result.exit_code == 0, result.output
    assert "keeping it" in result.output
    assert load_config(in_repo).passing_score == 42
    assert not (in_repo / ".git" / "hooks" / "pre-push").exists()
    assert not (in_repo / "AGENTS.md").exists()

    result = runner.invoke(cli, ["init", "--force", "--provider", "fake", "--no-hook"])
    assert result.exit_code == 0, result.output
    assert load_config(in_repo).passing_score == 70


def test_init_picks_an_installed_agent(in_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        integrations.shutil, "which", lambda name: "/usr/bin/codex" if name == "codex" else None
    )
    result = CliRunner().invoke(cli, ["init", "--no-hook", "--no-agents"])
    assert result.exit_code == 0, result.output
    assert load_config(in_repo).provider == "codex"


def test_init_defaults_to_anthropic(in_repo: Path) -> None:
    result = CliRunner().invoke(cli, ["init", "--no-hook", "--no-agents"])
    assert result.exit_code == 0, result.output
    assert load_config(in_repo).provider == "anthropic"


def test_init_appends_to_a_foreign_hook(in_repo: Path) -> None:
    hook = in_repo / ".git" / "hooks" / "pre-push"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\necho mine\n")
    result = CliRunner().invoke(cli, ["init", "--provider", "fake", "--no-agents"])
    assert result.exit_code == 0, result.output
    assert "appended" in result.output
    assert hook.read_text().startswith("#!/bin/sh\necho mine")
    assert "grip hook pre-push" in hook.read_text()


def test_init_stages(in_repo: Path) -> None:
    args = ["init", "--provider", "fake", "--no-agents", "--stage", "pre-commit"]
    assert CliRunner().invoke(cli, args).exit_code == 0
    assert (in_repo / ".git" / "hooks" / "pre-commit").exists()
    assert not (in_repo / ".git" / "hooks" / "pre-push").exists()


def test_init_asks_in_a_terminal(in_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        integrations.shutil, "which", lambda name: "/usr/bin/claude" if name == "claude" else None
    )
    monkeypatch.setattr(cli_module, "_interactive", lambda: True)
    result = CliRunner().invoke(cli, ["init", "--no-agents"], input="\n85\nboth\n")
    assert result.exit_code == 0, result.output
    assert "Which model asks and grades the questions?" in result.output
    cfg = load_config(in_repo)
    assert (cfg.provider, cfg.passing_score) == ("claude-code", 85)  # Enter kept the default
    hooks = in_repo / ".git" / "hooks"
    assert (hooks / "pre-commit").exists() and (hooks / "pre-push").exists()


def test_init_yes_takes_the_defaults_without_asking(
    in_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli_module, "_interactive", lambda: True)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = CliRunner().invoke(cli, ["init", "--yes", "--no-hook", "--no-agents"])
    assert result.exit_code == 0, result.output
    assert "Which model" not in result.output
    assert load_config(in_repo).provider == "anthropic"
    assert "ANTHROPIC_API_KEY" in result.output or "ant auth login" in result.output


def test_install_says_which_model_auto_picked(
    in_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        integrations.shutil, "which", lambda name: "/usr/bin/codex" if name == "codex" else None
    )
    result = CliRunner().invoke(cli, ["install"])
    assert result.exit_code == 0, result.output
    assert "codex (provider auto)" in result.output
