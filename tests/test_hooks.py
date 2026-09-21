import os
from pathlib import Path

import pytest

from grip_hook.errors import GripError
from grip_hook.git import Git
from grip_hook.hooks import MARKER, install, status, uninstall
from grip_hook.models import Stage


def test_install_status_uninstall(git: Git) -> None:
    before = status(git, Stage.PRE_PUSH)
    assert not before.exists and not before.active

    result = install(git, Stage.PRE_PUSH)
    assert result.managed and result.active
    assert result.path.name == "pre-push"
    content = result.path.read_text()
    assert content.startswith("#!/bin/sh\n" + MARKER)
    assert "grip hook pre-push" in content
    if os.name != "nt":
        assert os.access(result.path, os.X_OK)

    # Idempotent.
    again = install(git, Stage.PRE_PUSH)
    assert again.managed

    after = uninstall(git, Stage.PRE_PUSH)
    assert not after.exists


def test_foreign_hook_requires_flag(git: Git) -> None:
    path = git.hooks_dir() / "pre-commit"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\necho mine\n")

    with pytest.raises(GripError, match="--append"):
        install(git, Stage.PRE_COMMIT)
    assert status(git, Stage.PRE_COMMIT).exists
    assert not status(git, Stage.PRE_COMMIT).active


def test_append_and_uninstall_keeps_foreign_hook(git: Git) -> None:
    path = git.hooks_dir() / "pre-commit"
    path.parent.mkdir(parents=True, exist_ok=True)
    original = "#!/bin/sh\necho mine\n"
    path.write_text(original)

    result = install(git, Stage.PRE_COMMIT, append=True)
    assert result.appended and not result.managed
    text = path.read_text()
    assert text.startswith(original.rstrip("\n"))
    assert "grip hook pre-commit" in text

    # Appending twice is a no-op.
    install(git, Stage.PRE_COMMIT, append=True)
    assert path.read_text().count(MARKER) == 1

    after = uninstall(git, Stage.PRE_COMMIT)
    assert after.exists and not after.active
    assert path.read_text() == original


def test_force_replaces_foreign_hook(git: Git) -> None:
    path = git.hooks_dir() / "pre-push"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\necho mine\n")
    result = install(git, Stage.PRE_PUSH, force=True)
    assert result.managed


def test_uninstall_missing_is_noop(git: Git) -> None:
    result = uninstall(git, Stage.PRE_PUSH)
    assert not result.exists


def test_cannot_install_manual_stage(git: Git) -> None:
    with pytest.raises(GripError):
        install(git, Stage.MANUAL)


def test_hooks_path_is_honoured(git: Git, repo: Path) -> None:
    git.run("config", "core.hooksPath", "hooks")
    result = install(git, Stage.PRE_PUSH)
    assert result.path == repo.resolve() / "hooks" / "pre-push"
