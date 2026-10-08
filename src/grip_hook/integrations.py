"""Wire grip into the coding agents on this machine.

``grip agents`` looks for the agents a developer has installed (a binary on ``PATH`` or a
configuration directory under the home directory) and writes, inside the repository, the
file each one reads: a rule block in ``AGENTS.md`` for Codex and the other agents that
honour it, ``GEMINI.md`` for Gemini CLI, ``CLAUDE.md`` plus the plugin enablement in
``.claude/settings.json`` for Claude Code, and a rules file for Cursor, Windsurf, Cline and
Copilot. Every write is marked so the same command can update or remove it later. Nothing
is written outside the repository and nothing is sent anywhere.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from grip_hook.errors import GripError

START = "<!-- grip:start -->"
"""First line of a block grip manages inside a file the developer owns."""

END = "<!-- grip:end -->"
"""Last line of that block."""

RULES = """\
## grip

This repository uses [grip](https://guilyx.github.io/grip/): the developer must pass a
five-question quiz about their own diff before it goes upstream.

Before `git push`, run `grip check --unpushed`. If it exits 1, run `grip ask --unpushed`,
show the developer the questions verbatim, wait for their answers, then run
`grip grade --answers -` with the answers as a JSON list of five strings. Never answer
the questions yourself, never hint, and never open the diff to help them. Push only after
`grip check --unpushed` exits 0. If `grip` is missing, install it with
`curl -fsSL https://raw.githubusercontent.com/guilyx/grip/main/install.sh | sh`.
"""
"""The instructions every agent gets, as Markdown."""

MARKETPLACE: dict[str, Any] = {"source": {"source": "github", "repo": "guilyx/grip"}}
PLUGIN_ID = "grip@grip"


class Kind(StrEnum):
    """How grip writes to a target file."""

    BLOCK = "block"
    """A marked block appended to a file the developer owns (``AGENTS.md``)."""
    FILE = "file"
    """A whole file grip owns (``.cursor/rules/grip.mdc``)."""
    SETTINGS = "settings"
    """Keys merged into a JSON settings file (``.claude/settings.json``)."""


@dataclass(frozen=True, slots=True)
class Target:
    """One file an agent reads, relative to the repository root with ``/`` separators."""

    path: str
    kind: Kind
    frontmatter: str = ""
    """Prepended to whole files: the agent's own metadata header."""


@dataclass(frozen=True, slots=True)
class Agent:
    """A coding agent grip knows how to configure."""

    key: str
    """The name used on the command line (``--agent codex``)."""
    name: str
    binaries: tuple[str, ...]
    """Executables whose presence on ``PATH`` means the agent is installed."""
    home_dirs: tuple[str, ...]
    """Paths under the home directory (globs allowed) that mean the same."""
    targets: tuple[Target, ...]
    always: bool = False
    """Offered even when nothing is detected: the generic ``AGENTS.md``."""


_AGENTS_MD = Target("AGENTS.md", Kind.BLOCK)

AGENTS: tuple[Agent, ...] = (
    Agent(
        "agents-md",
        "AGENTS.md readers",
        (),
        (),
        (_AGENTS_MD,),
        always=True,
    ),
    Agent(
        "claude-code",
        "Claude Code",
        ("claude",),
        (".claude",),
        (Target("CLAUDE.md", Kind.BLOCK), Target(".claude/settings.json", Kind.SETTINGS)),
    ),
    Agent("codex", "Codex CLI", ("codex",), (".codex",), (_AGENTS_MD,)),
    Agent("gemini", "Gemini CLI", ("gemini",), (".gemini",), (Target("GEMINI.md", Kind.BLOCK),)),
    Agent(
        "cursor",
        "Cursor",
        ("cursor",),
        (".cursor",),
        (
            Target(
                ".cursor/rules/grip.mdc",
                Kind.FILE,
                "---\ndescription: grip quiz before push\nalwaysApply: true\n---\n",
            ),
        ),
    ),
    Agent(
        "windsurf",
        "Windsurf",
        ("windsurf",),
        (".codeium/windsurf",),
        (Target(".windsurf/rules/grip.md", Kind.FILE, "---\ntrigger: always_on\n---\n"),),
    ),
    Agent(
        "cline",
        "Cline",
        (),
        (".cline", ".vscode/extensions/saoudrizwan.claude-dev*"),
        (Target(".clinerules/grip.md", Kind.FILE),),
    ),
    Agent(
        "copilot",
        "GitHub Copilot",
        ("copilot",),
        (".copilot", ".vscode/extensions/github.copilot*"),
        (Target(".github/copilot-instructions.md", Kind.BLOCK),),
    ),
    Agent("opencode", "OpenCode", ("opencode",), (".config/opencode",), (_AGENTS_MD,)),
)
"""Every agent ``grip agents`` can configure, in display order."""

_BY_KEY = {agent.key: agent for agent in AGENTS}


def agent(key: str) -> Agent:
    """Look an agent up by its command-line key."""
    try:
        return _BY_KEY[key]
    except KeyError as exc:
        known = ", ".join(a.key for a in AGENTS)
        raise GripError(f"unknown agent {key!r}; known agents: {known}") from exc


def _home_match(home: Path, pattern: str) -> bool:
    if any(ch in pattern for ch in "*?["):
        return any(home.glob(pattern))
    return (home / pattern).exists()


def is_installed(
    target: Agent,
    home: Path | None = None,
    which: Callable[[str], str | None] | None = None,
) -> bool:
    """Whether ``target`` looks installed: a binary on ``PATH`` or a directory under home."""
    home = home or Path.home()
    which = which or shutil.which
    return any(which(b) for b in target.binaries) or any(
        _home_match(home, d) for d in target.home_dirs
    )


def detect(
    home: Path | None = None, which: Callable[[str], str | None] | None = None
) -> list[Agent]:
    """The agents that look installed, plus the always-on generic ``AGENTS.md``."""
    return [a for a in AGENTS if a.always or is_installed(a, home, which)]


class Action(StrEnum):
    """What happened to one target file."""

    WRITTEN = "written"
    UPDATED = "updated"
    UNCHANGED = "unchanged"
    REMOVED = "removed"
    ABSENT = "absent"
    """Nothing to remove."""


@dataclass(frozen=True, slots=True)
class Change:
    """The outcome for one target file."""

    path: Path
    agents: tuple[str, ...]
    """Display names of the agents that read this file."""
    action: Action


def block() -> str:
    """The marked rule block, as written into files the developer owns."""
    return f"{START}\n{RULES}{END}\n"


def _read(path: Path) -> str | None:
    try:
        return path.read_text("utf-8")
    except FileNotFoundError:
        return None


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, "utf-8")


def _split_block(path: Path, text: str) -> tuple[str, str] | None:
    """``(before, after)`` around the managed block, or ``None`` when there is none."""
    if START not in text:
        return None
    head, _, rest = text.partition(START)
    inner, found, tail = rest.partition(END)
    if not found:
        raise GripError(f"{path} has a {START} line but no {END} line; fix it by hand")
    del inner
    return head, tail


def _upsert_block(path: Path, dry_run: bool) -> Action:
    current = _read(path)
    if current is None:
        new = block()
    elif (parts := _split_block(path, current)) is not None:
        head, tail = parts
        new = head + block().rstrip("\n") + tail
    elif current.strip():
        new = current.rstrip("\n") + "\n\n" + block()
    else:
        new = block()
    if new == current:
        return Action.UNCHANGED
    if not dry_run:
        _write_text(path, new)
    return Action.WRITTEN if current is None else Action.UPDATED


def _remove_block(path: Path, dry_run: bool) -> Action:
    current = _read(path)
    if current is None or (parts := _split_block(path, current)) is None:
        return Action.ABSENT
    head, tail = parts
    remainder = (head.rstrip("\n") + "\n" + tail.lstrip("\n")).strip("\n")
    if not dry_run:
        if remainder:
            _write_text(path, remainder + "\n")
        else:
            path.unlink()
    return Action.REMOVED


def _upsert_file(path: Path, target: Target, dry_run: bool) -> Action:
    new = target.frontmatter + block()
    current = _read(path)
    if current == new:
        return Action.UNCHANGED
    if not dry_run:
        _write_text(path, new)
    return Action.WRITTEN if current is None else Action.UPDATED


def _remove_file(path: Path, dry_run: bool) -> Action:
    if not path.exists():
        return Action.ABSENT
    if not dry_run:
        path.unlink()
    return Action.REMOVED


def _load_settings(path: Path) -> dict[str, Any]:
    text = _read(path)
    if text is None or not text.strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GripError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise GripError(f"{path} must contain a JSON object")
    return data


def _mapping(data: dict[str, Any], key: str, path: Path) -> dict[str, Any]:
    value = data.setdefault(key, {})
    if not isinstance(value, dict):
        raise GripError(f"{path}: {key!r} must be a JSON object")
    return value


def _dump(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2) + "\n"


def _upsert_settings(path: Path, dry_run: bool) -> Action:
    current = _load_settings(path)
    new = json.loads(json.dumps(current))
    _mapping(new, "extraKnownMarketplaces", path)["grip"] = MARKETPLACE
    _mapping(new, "enabledPlugins", path)[PLUGIN_ID] = True
    if new == current:
        return Action.UNCHANGED
    if not dry_run:
        _write_text(path, _dump(new))
    return Action.WRITTEN if not path.exists() else Action.UPDATED


def _remove_settings(path: Path, dry_run: bool) -> Action:
    if not path.exists():
        return Action.ABSENT
    current = _load_settings(path)
    new = json.loads(json.dumps(current))
    touched = False
    for key, entry in (("extraKnownMarketplaces", "grip"), ("enabledPlugins", PLUGIN_ID)):
        section = new.get(key)
        if isinstance(section, dict) and entry in section:
            del section[entry]
            touched = True
            if not section:
                del new[key]
    if not touched:
        return Action.ABSENT
    if not dry_run:
        if new:
            _write_text(path, _dump(new))
        else:
            path.unlink()
    return Action.REMOVED


def _apply_one(path: Path, target: Target, *, remove: bool, dry_run: bool) -> Action:
    if target.kind is Kind.BLOCK:
        return _remove_block(path, dry_run) if remove else _upsert_block(path, dry_run)
    if target.kind is Kind.FILE:
        return _remove_file(path, dry_run) if remove else _upsert_file(path, target, dry_run)
    return _remove_settings(path, dry_run) if remove else _upsert_settings(path, dry_run)


def apply(
    root: Path, agents: Iterable[Agent], *, remove: bool = False, dry_run: bool = False
) -> list[Change]:
    """Write (or remove) every target of ``agents`` under ``root``.

    A file shared by several agents, like ``AGENTS.md``, is written once. With ``dry_run``
    nothing is touched and the actions say what would happen.
    """
    readers: dict[str, tuple[Target, list[str]]] = {}
    for a in agents:
        for target in a.targets:
            readers.setdefault(target.path, (target, []))[1].append(a.name)
    changes: list[Change] = []
    for rel, (target, names) in readers.items():
        path = root.joinpath(*rel.split("/"))
        action = _apply_one(path, target, remove=remove, dry_run=dry_run)
        changes.append(Change(path, tuple(names), action))
    return changes


__all__ = [
    "AGENTS",
    "END",
    "MARKETPLACE",
    "PLUGIN_ID",
    "RULES",
    "START",
    "Action",
    "Agent",
    "Change",
    "Kind",
    "Target",
    "agent",
    "apply",
    "block",
    "detect",
    "is_installed",
]
