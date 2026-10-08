"""Tests for `grip init` and the configuration template it writes."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

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
