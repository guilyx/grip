import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from grip_hook import hooks
from grip_hook.cli import cli
from grip_hook.errors import GripError
from grip_hook.git import Git
from grip_hook.hooks import MARKER, GripCommand, install, running_grip, status, uninstall
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


# -- the absolute path of the installing grip --------------------------------------------

SH = shutil.which("sh")
needs_sh = pytest.mark.skipif(os.name == "nt" or SH is None, reason="runs the hook with sh")


def _fake_grip(directory: Path, name: str = "grip") -> Path:
    """An executable that says how it was called, standing in for grip."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text('#!/bin/sh\necho "ran $0 $*"\n')
    path.chmod(0o755)
    return path


def _run_hook(
    hook: Path, cwd: Path, path: list[Path], **env: str
) -> subprocess.CompletedProcess[str]:
    """Run ``hook`` the way git does, with only ``path`` on PATH (an IDE's PATH, say)."""
    assert SH is not None
    return subprocess.run(
        [SH, str(hook), "origin", "https://example.com/repo.git"],
        cwd=cwd,
        env={**os.environ, **env, "PATH": os.pathsep.join(str(p) for p in path)},
        input="",
        capture_output=True,
        text=True,
        check=False,
    )


def test_hook_records_the_grip_that_installed_it(git: Git, tmp_path: Path) -> None:
    grip = _fake_grip(tmp_path / "my tools" / "it's here")
    result = install(git, Stage.PRE_PUSH, command=GripCommand(grip.as_posix()))
    content = result.path.read_text()
    # Quoted for sh, whatever the path holds.
    assert "grip_bin='" in content and "my tools" in content
    assert result.grip_path == grip.as_posix()
    assert not result.grip_missing
    # The PATH fallbacks are still there.
    assert 'exec grip hook pre-push "$@"' in content
    assert "python3 -m grip_hook hook pre-push" in content


@needs_sh
def test_hook_runs_the_recorded_grip_without_it_on_path(
    git: Git, repo: Path, tmp_path: Path
) -> None:
    grip = _fake_grip(tmp_path / "my tools" / "it's here")
    hook = install(git, Stage.PRE_PUSH, command=GripCommand(grip.as_posix())).path
    empty = tmp_path / "empty-path"
    empty.mkdir()
    ran = _run_hook(hook, repo, [empty])
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.strip() == f"ran {grip} hook pre-push origin https://example.com/repo.git"


@needs_sh
def test_hook_falls_back_to_path_then_says_the_recorded_grip_is_gone(
    git: Git, repo: Path, tmp_path: Path
) -> None:
    gone = (tmp_path / "old-venv" / "bin" / "grip").as_posix()
    result = install(git, Stage.PRE_PUSH, command=GripCommand(gone))
    assert result.grip_path == gone and result.grip_missing

    on_path = _fake_grip(tmp_path / "bin")
    ran = _run_hook(result.path, repo, [on_path.parent])
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.startswith("ran ") and "hook pre-push origin" in ran.stdout

    empty = tmp_path / "empty-path"
    empty.mkdir()
    ran = _run_hook(result.path, repo, [empty])
    assert ran.returncode == 0
    assert ran.stdout == ""
    assert f"grip: {gone} no longer exists and grip is not on PATH" in ran.stderr
    assert "'grip install' again" in ran.stderr


@needs_sh
def test_hook_runs_python_m_grip_hook_when_installed_that_way(
    git: Git, repo: Path, tmp_path: Path
) -> None:
    command = GripCommand(Path(sys.executable).as_posix(), python=True)
    hook = install(git, Stage.PRE_PUSH, command=command).path
    assert "-m grip_hook hook pre-push" in hook.read_text()
    empty = tmp_path / "empty-path"
    empty.mkdir()
    # The real grip, by absolute path: it reports GRIP_SKIP and lets the push through.
    ran = _run_hook(hook, repo, [empty], GRIP_SKIP="1")
    assert ran.returncode == 0, ran.stderr
    assert "GRIP_SKIP is set" in ran.stderr


@needs_sh
def test_hook_without_a_known_grip_says_how_to_install_it(
    git: Git, repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hooks, "running_grip", lambda: None)
    result = install(git, Stage.PRE_PUSH)
    assert result.grip_path is None and not result.grip_missing
    empty = tmp_path / "empty-path"
    empty.mkdir()
    ran = _run_hook(result.path, repo, [empty])
    assert ran.returncode == 0
    assert "grip: not installed (pipx install grip-hook), skipping the quiz." in ran.stderr


def test_appended_block_records_the_path_and_install_refreshes_it(git: Git, tmp_path: Path) -> None:
    path = git.hooks_dir() / "pre-push"
    path.parent.mkdir(parents=True, exist_ok=True)
    original = "#!/bin/sh\necho mine\n"
    path.write_text(original)
    old = (tmp_path / "old" / "grip").as_posix()
    new = _fake_grip(tmp_path / "new")

    result = install(git, Stage.PRE_PUSH, append=True, command=GripCommand(old))
    assert result.appended and result.grip_path == old and result.grip_missing
    result = install(git, Stage.PRE_PUSH, append=True, command=GripCommand(new.as_posix()))
    assert result.appended and result.grip_path == new.as_posix() and not result.grip_missing
    text = path.read_text()
    assert text.count(MARKER) == 1 and old not in text
    assert text.startswith(original)

    assert uninstall(git, Stage.PRE_PUSH).exists
    assert path.read_text() == original


@needs_sh
def test_appended_block_runs_after_the_foreign_hook(git: Git, repo: Path, tmp_path: Path) -> None:
    path = git.hooks_dir() / "pre-push"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\necho mine\n")
    grip = _fake_grip(tmp_path / "bin dir")
    install(git, Stage.PRE_PUSH, append=True, command=GripCommand(grip.as_posix()))
    empty = tmp_path / "empty-path"
    empty.mkdir()
    ran = _run_hook(path, repo, [empty])
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.splitlines() == [
        "mine",
        f"ran {grip} hook pre-push origin https://example.com/repo.git",
    ]


def test_hook_from_an_older_grip_has_no_recorded_path(git: Git) -> None:
    path = git.hooks_dir() / "pre-push"
    path.parent.mkdir(parents=True, exist_ok=True)
    old = 'if command -v grip >/dev/null 2>&1; then\n  exec grip hook pre-push "$@"\nfi\n'
    path.write_text(f"#!/bin/sh\n{MARKER}\n{old}exit 0\n")
    found = status(git, Stage.PRE_PUSH)
    assert found.managed and found.grip_path is None and not found.grip_missing


def test_running_grip_prefers_the_console_script(tmp_path: Path) -> None:
    script = _fake_grip(tmp_path / "venv" / "bin")
    assert running_grip(str(script), sys.executable) == GripCommand(script.as_posix())
    # A Windows launcher can report the script without its .exe.
    exe = _fake_grip(tmp_path / "Scripts", "grip.exe")
    found = running_grip(str(tmp_path / "Scripts" / "grip"), sys.executable)
    assert found == GripCommand(exe.as_posix())
    git_grip = _fake_grip(tmp_path / "other", "git-grip")
    assert running_grip(str(git_grip), sys.executable) == GripCommand(git_grip.as_posix())


def test_running_grip_falls_back_to_this_interpreter(tmp_path: Path) -> None:
    main = tmp_path / "grip_hook" / "__main__.py"  # python -m grip_hook
    found = running_grip(str(main), sys.executable)
    assert found == GripCommand(Path(sys.executable).as_posix(), python=True)
    assert "\\" not in found.path
    assert running_grip(str(main), "") is None
    assert running_grip(str(tmp_path / "missing" / "grip"), "") is None


def test_status_flags_a_hook_whose_grip_is_gone(
    git: Git, repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gone = (tmp_path / "old-venv" / "bin" / "grip").as_posix()
    install(git, Stage.PRE_PUSH, command=GripCommand(gone))
    monkeypatch.chdir(repo)
    (repo / ".grip.toml").write_text('provider = "fake"\n')
    result = CliRunner().invoke(cli, ["status"])
    assert result.exit_code == 0, result.output
    assert "grip missing" in result.output
    out = " ".join(result.output.split())
    assert "no longer exists" in out and "run `grip install` again" in out
