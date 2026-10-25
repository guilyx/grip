from pathlib import Path

import pytest

from grip_hook import config as config_module
from grip_hook.config import DEFAULT_EXCLUDES, Config, auto_provider, describe, load_config
from grip_hook.errors import ConfigError
from grip_hook.models import Difficulty


def test_defaults(tmp_path: Path) -> None:
    cfg = load_config(tmp_path, env={})
    assert cfg == Config()
    assert cfg.passing_score == 70
    assert cfg.exclude == DEFAULT_EXCLUDES


def test_pyproject_then_grip_toml_precedence(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.grip]\npassing_score = 50\nmodel = "from-pyproject"\n'
    )
    (tmp_path / ".grip.toml").write_text('model = "from-grip-toml"\ndifficulty = "hard"\n')
    cfg = load_config(tmp_path, env={})
    assert cfg.passing_score == 50
    assert cfg.model == "from-grip-toml"
    assert cfg.difficulty is Difficulty.HARD


def test_env_beats_files_and_flags_beat_env(tmp_path: Path) -> None:
    (tmp_path / ".grip.toml").write_text("passing_score = 50\n")
    env = {"GRIP_PASSING_SCORE": "60", "GRIP_EXCLUDE": "a.txt, b.txt", "GRIP_REQUIRE_TTY": "yes"}
    cfg = load_config(tmp_path, env=env)
    assert cfg.passing_score == 60
    assert cfg.exclude == ("a.txt", "b.txt")
    assert cfg.require_tty is True

    cfg = load_config(tmp_path, env=env, overrides={"passing_score": 90, "model": None})
    assert cfg.passing_score == 90
    assert cfg.model == Config().model


def test_dashed_keys_and_bools(tmp_path: Path) -> None:
    (tmp_path / ".grip.toml").write_text("fail-open = true\nremember_passes_hours = 0\n")
    cfg = load_config(tmp_path, env={})
    assert cfg.fail_open is True
    assert cfg.remember_passes_hours == 0


@pytest.mark.parametrize(
    "content",
    [
        "passing_score = 101\n",
        "passing_score = 'abc'\n",
        "difficulty = 'brutal'\n",
        "unknown_option = 1\n",
        "exclude = 3\n",
        "timeout = 0\n",
        "hook_mode = 'slides'\n",
        "this is not toml\n",
    ],
)
def test_invalid_config(tmp_path: Path, content: str) -> None:
    (tmp_path / ".grip.toml").write_text(content)
    with pytest.raises(ConfigError):
        load_config(tmp_path, env={})


def test_include_commit_messages(tmp_path: Path) -> None:
    assert Config().include_commit_messages is True
    (tmp_path / ".grip.toml").write_text("include_commit_messages = false\n")
    assert load_config(tmp_path, env={}).include_commit_messages is False
    env = {"GRIP_INCLUDE_COMMIT_MESSAGES": "1"}
    assert load_config(tmp_path, env=env).include_commit_messages is True


def test_no_root() -> None:
    assert load_config(None, env={}) == Config()


def test_describe_renders_every_field() -> None:
    rows = dict(describe(Config()))
    assert rows["difficulty"] == "normal"
    assert rows["api_key_env"] == "(provider default)"
    assert rows["model"] == "(provider default)"
    assert "*.lock" in rows["exclude"]


def test_auto_provider_prefers_an_installed_coding_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    installed = {"gemini", "codex"}
    monkeypatch.setattr(
        config_module.shutil, "which", lambda name: f"/bin/{name}" if name in installed else None
    )
    assert auto_provider() == "codex"  # Claude Code, Codex, Gemini CLI, in that order
    assert Config().provider == "auto"
    assert Config().provider_name == "codex"
    assert Config(provider="Anthropic").provider_name == "anthropic"
    assert dict(describe(Config()))["provider"] == "auto (codex)"
    installed.clear()
    assert Config().provider_name == "anthropic"
