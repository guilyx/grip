"""Install and remove native git hooks.

The generated hook is a tiny POSIX shell script that delegates to ``grip hook
<stage>``. It is marked with :data:`MARKER` so we never overwrite a hook we did not
write. Users of the ``pre-commit`` framework should use ``.pre-commit-hooks.yaml``
instead; see the documentation.

The hook first runs the grip that installed it, by absolute path: IDEs and GUI git
clients often run hooks with a ``PATH`` that does not have grip on it. ``PATH`` and
``python3 -m grip_hook`` are the fallbacks.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

from grip_hook.errors import GripError
from grip_hook.git import Git
from grip_hook.models import Stage

MARKER = "# managed-by: grip-hook"
INSTALLABLE_STAGES = (Stage.PRE_COMMIT, Stage.PRE_PUSH)
_CONSOLE_SCRIPTS = ("grip", "git-grip")
_GRIP_BIN = re.compile(r"^grip_bin=(.*)$", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class GripCommand:
    """How a hook starts the grip that installed it."""

    path: str
    """Absolute path with forward slashes, which git-bash on Windows understands too."""
    python: bool = False
    """``True`` when ``path`` is a Python interpreter to run ``-m grip_hook`` with."""


def running_grip(argv0: str | None = None, executable: str | None = None) -> GripCommand | None:
    """The grip running now: its console script, else this interpreter with ``-m``.

    ``None`` when neither can be found (an embedded interpreter, a frozen app).
    """
    argv0 = sys.argv[0] if argv0 is None else argv0
    script = Path(argv0)
    if script.stem.lower() in _CONSOLE_SCRIPTS:
        if not script.parent.parts:  # a bare name, as git passes for `git grip`
            found = shutil.which(argv0)
            script = Path(found) if found else script
        # Windows launchers may report the script without its ``.exe``.
        for candidate in (script, script.with_name(script.name + ".exe")):
            if candidate.is_file():
                return GripCommand(candidate.absolute().as_posix())
    executable = sys.executable if executable is None else executable
    if executable and Path(executable).is_file():
        return GripCommand(Path(executable).absolute().as_posix(), python=True)
    return None


_SCRIPT = """\
#!/bin/sh
{marker}
# Installed by `grip install`. Remove with `grip uninstall`.
# Docs: https://guilyx.github.io/grip/
# The grip that installed this hook comes first: IDEs and GUI git clients often run hooks
# with a PATH that does not have grip on it. `grip install` again updates the path.
grip_bin={grip_bin}
if {runnable}; then
  exec "$grip_bin" {launch}hook {stage} "$@"
fi
if command -v grip >/dev/null 2>&1; then
  exec grip hook {stage} "$@"
fi
if command -v python3 >/dev/null 2>&1 && python3 -c "import grip_hook" >/dev/null 2>&1; then
  exec python3 -m grip_hook hook {stage} "$@"
fi
{missing}
exit 0
"""

_APPEND_SNIPPET = """

{marker}
# Added by `grip install --append`. Remove this block with `grip uninstall`.
grip_bin={grip_bin}
if {runnable}; then
  "$grip_bin" {launch}hook {stage} "$@" || exit $?
elif command -v grip >/dev/null 2>&1; then
  grip hook {stage} "$@" || exit $?
else
{missing}
fi
"""

# Three ways to be missing, each with its own way out. Single quotes inside the double
# quotes stay literal, so no backticks get run as commands.
_MISSING = """\
if [ -n "$grip_bin" ] && [ ! -e "$grip_bin" ]; then
  echo "grip: $grip_bin no longer exists and grip is not on PATH, skipping the quiz. \
Reinstall grip, then run 'grip install' again in this repository." >&2
elif [ -n "$grip_bin" ]; then
  echo "grip: could not run grip with $grip_bin and grip is not on PATH, skipping the \
quiz. Run 'grip install' again in this repository." >&2
else
  echo "grip: not installed (pipx install grip-hook), skipping the quiz." >&2
fi"""


def _quote(path: str) -> str:
    """``path`` in single quotes for ``sh``, always, so spaces and ``$`` stay literal."""
    return "'" + path.replace("'", "'\"'\"'") + "'"


def _render(template: str, stage: Stage, command: GripCommand | None) -> str:
    """Fill a hook template for ``stage``, pointed at ``command`` when there is one."""
    runnable = '[ -x "$grip_bin" ]'
    if command is not None and command.python:
        runnable += ' && "$grip_bin" -c "import grip_hook" >/dev/null 2>&1'
    missing = _MISSING
    if template is _APPEND_SNIPPET:
        missing = "\n".join("  " + line for line in _MISSING.splitlines())
    return template.format(
        marker=MARKER,
        stage=stage.value,
        grip_bin=_quote(command.path) if command else "''",
        runnable=runnable,
        launch="-m grip_hook " if command is not None and command.python else "",
        missing=missing,
    )


@dataclass(frozen=True, slots=True)
class HookStatus:
    """What is installed for one stage."""

    stage: Stage
    path: Path
    exists: bool
    managed: bool
    """``True`` when the file was written entirely by grip."""
    appended: bool
    """``True`` when grip's snippet was appended to a pre-existing hook."""
    grip_path: str | None = None
    """The grip (or Python) the hook runs first; ``None`` for hooks that only use PATH."""

    @property
    def active(self) -> bool:
        """``True`` when grip runs at this stage."""
        return self.managed or self.appended

    @property
    def grip_missing(self) -> bool:
        """``True`` when the hook names a grip that is no longer there."""
        return self.active and self.grip_path is not None and not Path(self.grip_path).exists()


def _is_executable(path: Path) -> bool:
    return os.access(path, os.X_OK)


def _make_executable(path: Path) -> None:
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _recorded_grip(content: str) -> str | None:
    """The path after ``grip_bin=`` in grip's part of a hook, unquoted."""
    _, _, ours = content.partition(MARKER)
    found = _GRIP_BIN.search(ours)
    if found is None:
        return None
    try:
        words = shlex.split(found.group(1))
    except ValueError:
        return None
    return words[0] if words and words[0] else None


def status(git: Git, stage: Stage) -> HookStatus:
    """Inspect the hook file for ``stage``."""
    path = git.hooks_dir() / stage.value
    if not path.exists():
        return HookStatus(stage, path, exists=False, managed=False, appended=False)
    content = path.read_text("utf-8", errors="replace")
    managed = MARKER in content and content.startswith("#!/bin/sh\n" + MARKER)
    appended = MARKER in content and not managed
    grip_path = _recorded_grip(content) if managed or appended else None
    return HookStatus(stage, path, True, managed, appended, grip_path)


def _strip_appended(content: str) -> str:
    """``content`` without grip's appended block, which is always the last thing in it."""
    head, _, _tail = content.partition(f"\n\n{MARKER}\n")
    return head.rstrip("\n") + "\n"


def install(
    git: Git,
    stage: Stage,
    *,
    force: bool = False,
    append: bool = False,
    command: GripCommand | None = None,
) -> HookStatus:
    """Write the hook for ``stage``.

    Args:
        git: Repository handle.
        stage: ``pre-commit`` or ``pre-push``.
        force: Overwrite a hook that grip did not write.
        append: Append grip's snippet to an existing foreign hook instead of failing.
        command: The grip the hook runs first; defaults to :func:`running_grip`.

    Raises:
        GripError: when a foreign hook exists and neither ``force`` nor ``append`` is set.
    """
    if stage not in INSTALLABLE_STAGES:
        raise GripError(f"cannot install a {stage.value} hook")
    command = command or running_grip()
    current = status(git, stage)
    path = current.path
    path.parent.mkdir(parents=True, exist_ok=True)

    if current.exists and not current.managed and not current.appended:
        if append:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(_render(_APPEND_SNIPPET, stage, command))
            _make_executable(path)
            return status(git, stage)
        if not force:
            raise GripError(
                f"{path} already exists and was not written by grip. Re-run with --append to "
                "chain grip after it, or --force to replace it."
            )
    if current.appended and not force:
        # Re-running install refreshes the block, so it points at the grip running now.
        head = _strip_appended(path.read_text("utf-8"))
        path.write_text(head.rstrip("\n") + _render(_APPEND_SNIPPET, stage, command), "utf-8")
        return status(git, stage)

    path.write_text(_render(_SCRIPT, stage, command), "utf-8")
    _make_executable(path)
    if not _is_executable(path):  # pragma: no cover - filesystem without exec bits
        raise GripError(f"could not make {path} executable")
    return status(git, stage)


def uninstall(git: Git, stage: Stage) -> HookStatus:
    """Remove grip from the hook for ``stage`` (deleting the file if grip owns it)."""
    current = status(git, stage)
    if not current.exists:
        return current
    if current.managed:
        current.path.unlink()
    elif current.appended:
        content = current.path.read_text("utf-8")
        current.path.write_text(_strip_appended(content), "utf-8")
    return status(git, stage)


__all__ = [
    "INSTALLABLE_STAGES",
    "MARKER",
    "GripCommand",
    "HookStatus",
    "install",
    "running_grip",
    "status",
    "uninstall",
]
