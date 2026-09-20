"""Configuration loading.

Precedence, highest first:

1. Command-line flags.
2. ``GRIP_*`` environment variables.
3. ``.grip.toml`` at the repository root.
4. ``[tool.grip]`` in ``pyproject.toml`` at the repository root.
5. Built-in defaults.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any

from grip_hook.errors import ConfigError
from grip_hook.models import Difficulty

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_PASSING_SCORE = 70
DEFAULT_EXCLUDES: tuple[str, ...] = (
    "*.lock",
    "*.lockb",
    "*.min.js",
    "*.min.css",
    "*.map",
    "*.snap",
    "*.svg",
    "*.pb.go",
    "*_pb2.py",
    "*.generated.*",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "Cargo.lock",
    "poetry.lock",
    "uv.lock",
    "go.sum",
    "Pipfile.lock",
    "composer.lock",
    "Gemfile.lock",
)

ENV_PREFIX = "GRIP_"


@dataclass(frozen=True, slots=True)
class Config:
    """Resolved grip configuration."""

    passing_score: int = DEFAULT_PASSING_SCORE
    """Minimum Grip Score (0-100) required to let the commit or push through."""

    provider: str = "anthropic"
    """Provider name: ``anthropic``, ``openai`` (any OpenAI-compatible API) or ``fake``."""

    model: str = DEFAULT_MODEL
    """Model identifier passed to the provider."""

    effort: str = "medium"
    """Reasoning effort for providers that support it. ``none`` disables the parameter."""

    api_key_env: str = ""
    """Environment variable holding the API key. Empty means the provider's default."""

    base_url: str = ""
    """Override the provider's API base URL (Ollama, proxies, OpenAI-compatible servers)."""

    difficulty: Difficulty = Difficulty.NORMAL
    """How demanding the questions and grading are."""

    max_diff_bytes: int = 200_000
    """Diffs larger than this are truncated before being sent to the provider."""

    exclude: tuple[str, ...] = DEFAULT_EXCLUDES
    """Path globs excluded from the diff (lockfiles, generated code)."""

    remember_passes_hours: float = 24.0
    """How long a passed diff stays valid without re-quizzing. ``0`` disables."""

    require_tty: bool = False
    """Fail instead of skipping when no interactive terminal is available."""

    fail_open: bool = False
    """Let the commit or push through when the provider errors out instead of blocking."""

    timeout: float = 120.0
    """Per-request timeout in seconds for provider calls."""

    def validate(self) -> Config:
        """Raise :class:`ConfigError` for out-of-range values and return ``self``."""
        if not 0 <= self.passing_score <= 100:
            raise ConfigError(f"passing_score must be between 0 and 100, got {self.passing_score}")
        if self.max_diff_bytes <= 0:
            raise ConfigError("max_diff_bytes must be positive")
        if self.remember_passes_hours < 0:
            raise ConfigError("remember_passes_hours must be zero or positive")
        if self.timeout <= 0:
            raise ConfigError("timeout must be positive")
        if not self.provider:
            raise ConfigError("provider must not be empty")
        return self


_FIELD_NAMES = {f.name for f in fields(Config)}


def _coerce(name: str, value: Any) -> Any:  # noqa: PLR0911 - one branch per field type
    """Convert a raw TOML/env value into the type the dataclass expects."""
    if name == "difficulty":
        try:
            return Difficulty(str(value).lower())
        except ValueError as exc:
            allowed = ", ".join(d.value for d in Difficulty)
            raise ConfigError(f"difficulty must be one of {allowed}, got {value!r}") from exc
    if name == "exclude":
        if isinstance(value, str):
            return tuple(p.strip() for p in value.split(",") if p.strip())
        if isinstance(value, list | tuple):
            return tuple(str(p) for p in value)
        raise ConfigError("exclude must be a list of globs")
    if name in {"passing_score", "max_diff_bytes"}:
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{name} must be an integer, got {value!r}") from exc
    if name in {"remember_passes_hours", "timeout"}:
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{name} must be a number, got {value!r}") from exc
    if name in {"require_tty", "fail_open"}:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
    return str(value)


def _apply(cfg: Config, raw: dict[str, Any], source: str) -> Config:
    updates: dict[str, Any] = {}
    for key, value in raw.items():
        name = key.replace("-", "_")
        if name not in _FIELD_NAMES:
            raise ConfigError(f"unknown option {key!r} in {source}")
        updates[name] = _coerce(name, value)
    return replace(cfg, **updates) if updates else cfg


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"could not parse {path}: {exc}") from exc


def load_config(
    root: Path | None,
    *,
    env: dict[str, str] | None = None,
    overrides: dict[str, Any] | None = None,
) -> Config:
    """Build the effective :class:`Config` for a repository.

    Args:
        root: Repository root (where ``.grip.toml`` / ``pyproject.toml`` live), or ``None``.
        env: Environment mapping. Defaults to :data:`os.environ`.
        overrides: Values from command-line flags. ``None`` entries are ignored.
    """
    cfg = Config()
    if root is not None:
        pyproject = root / "pyproject.toml"
        if pyproject.is_file():
            tool = _read_toml(pyproject).get("tool", {})
            if isinstance(tool, dict) and isinstance(tool.get("grip"), dict):
                cfg = _apply(cfg, tool["grip"], str(pyproject))
        grip_toml = root / ".grip.toml"
        if grip_toml.is_file():
            cfg = _apply(cfg, _read_toml(grip_toml), str(grip_toml))

    environ = os.environ if env is None else env
    env_raw = {
        key[len(ENV_PREFIX) :].lower(): value
        for key, value in environ.items()
        if key.startswith(ENV_PREFIX) and key[len(ENV_PREFIX) :].lower() in _FIELD_NAMES
    }
    cfg = _apply(cfg, env_raw, "environment")

    if overrides:
        cfg = _apply(cfg, {k: v for k, v in overrides.items() if v is not None}, "command line")
    return cfg.validate()


def describe(cfg: Config) -> list[tuple[str, str]]:
    """Return ``(name, value)`` pairs for display."""
    rows: list[tuple[str, str]] = []
    for f in fields(cfg):
        value = getattr(cfg, f.name)
        if isinstance(value, tuple):
            text = ", ".join(value) if value else "(none)"
        elif isinstance(value, Difficulty):
            text = value.value
        else:
            text = str(value) if value != "" else "(default)"
        rows.append((f.name, text))
    return rows


__all__ = [
    "DEFAULT_EXCLUDES",
    "DEFAULT_MODEL",
    "DEFAULT_PASSING_SCORE",
    "Config",
    "describe",
    "load_config",
]
