"""Install and remove native git hooks.

The generated hook is a tiny POSIX shell script that delegates to ``grip hook
<stage>``. It is marked with :data:`MARKER` so we never overwrite a hook we did not
write. Users of the ``pre-commit`` framework should use ``.pre-commit-hooks.yaml``
instead; see the documentation.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from grip_hook.errors import GripError
from grip_hook.git import Git
from grip_hook.models import Stage

MARKER = "# managed-by: grip-hook"
INSTALLABLE_STAGES = (Stage.PRE_COMMIT, Stage.PRE_PUSH)

_SCRIPT = """\
#!/bin/sh
{marker}
# Installed by `grip install`. Remove with `grip uninstall`.
# Docs: https://guilyx.github.io/grip/
if command -v grip >/dev/null 2>&1; then
  exec grip hook {stage} "$@"
fi
if command -v python3 >/dev/null 2>&1 && python3 -c "import grip_hook" >/dev/null 2>&1; then
  exec python3 -m grip_hook hook {stage} "$@"
fi
echo "grip: not installed (pipx install grip-hook), skipping quiz" >&2
exit 0
"""

_APPEND_SNIPPET = """

{marker}
# Added by `grip install --append`. Remove this block with `grip uninstall`.
if command -v grip >/dev/null 2>&1; then
  grip hook {stage} "$@" || exit $?
fi
"""


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

    @property
    def active(self) -> bool:
        """``True`` when grip runs at this stage."""
        return self.managed or self.appended


def _is_executable(path: Path) -> bool:
    return os.access(path, os.X_OK)


def _make_executable(path: Path) -> None:
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def status(git: Git, stage: Stage) -> HookStatus:
    """Inspect the hook file for ``stage``."""
    path = git.hooks_dir() / stage.value
    if not path.exists():
        return HookStatus(stage, path, exists=False, managed=False, appended=False)
    content = path.read_text("utf-8", errors="replace")
    managed = MARKER in content and content.startswith("#!/bin/sh\n" + MARKER)
    appended = MARKER in content and not managed
    return HookStatus(stage, path, exists=True, managed=managed, appended=appended)


def install(git: Git, stage: Stage, *, force: bool = False, append: bool = False) -> HookStatus:
    """Write the hook for ``stage``.

    Args:
        git: Repository handle.
        stage: ``pre-commit`` or ``pre-push``.
        force: Overwrite a hook that grip did not write.
        append: Append grip's snippet to an existing foreign hook instead of failing.

    Raises:
        GripError: when a foreign hook exists and neither ``force`` nor ``append`` is set.
    """
    if stage not in INSTALLABLE_STAGES:
        raise GripError(f"cannot install a {stage.value} hook")
    current = status(git, stage)
    path = current.path
    path.parent.mkdir(parents=True, exist_ok=True)

    if current.exists and not current.managed and not current.appended:
        if append:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(_APPEND_SNIPPET.format(marker=MARKER, stage=stage.value))
            _make_executable(path)
            return status(git, stage)
        if not force:
            raise GripError(
                f"{path} already exists and was not written by grip. Re-run with --append to "
                "chain grip after it, or --force to replace it."
            )
    if current.appended and not force:
        return current

    path.write_text(_SCRIPT.format(marker=MARKER, stage=stage.value), "utf-8")
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
        head, _, _tail = content.partition(f"\n\n{MARKER}\n")
        # The appended block is always last, so dropping everything after the marker is safe.
        current.path.write_text(head.rstrip("\n") + "\n", "utf-8")
    return status(git, stage)


__all__ = ["INSTALLABLE_STAGES", "MARKER", "HookStatus", "install", "status", "uninstall"]
