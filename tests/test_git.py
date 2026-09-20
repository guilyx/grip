from collections.abc import Callable
from pathlib import Path

import pytest

from grip_hook.errors import GitError
from grip_hook.git import ZERO_SHA, Diff, Git, PushedRef


def test_root_and_git_dir(git: Git, repo: Path) -> None:
    assert git.root() == repo.resolve()
    assert git.git_dir() == (repo / ".git").resolve()
    assert git.hooks_dir() == (repo / ".git" / "hooks").resolve()


def test_hooks_path_config(git: Git, repo: Path, run_git: Callable[..., str]) -> None:
    run_git("config", "core.hooksPath", ".githooks")
    assert git.hooks_dir() == repo.resolve() / ".githooks"


def test_run_failure_raises(git: Git) -> None:
    with pytest.raises(GitError, match="rev-parse"):
        git.run("rev-parse", "--verify", "does-not-exist")


def test_not_a_repo(tmp_path: Path) -> None:
    with pytest.raises(GitError):
        Git(tmp_path).root()


def test_staged_diff_empty(git: Git) -> None:
    diff = git.staged_diff()
    assert diff.is_empty
    assert diff.files == ()


def test_staged_diff(git: Git, stage_change: Callable[[str, str], None]) -> None:
    stage_change("app.py", "def f():\n    return 1\n")
    diff = git.staged_diff()
    assert not diff.is_empty
    assert diff.files == ("app.py",)
    assert "+def f():" in diff.patch
    assert "app.py" in diff.stat
    assert diff.description == "staged changes"
    assert len(diff.digest) == 64


def test_exclude_globs(git: Git, stage_change: Callable[[str, str], None]) -> None:
    stage_change("app.py", "x = 1\n")
    stage_change("poetry.lock", "lock\n")
    stage_change("vendor/big.min.js", "min\n")
    diff = git.staged_diff(exclude=("*.lock", "*.min.js"))
    assert diff.files == ("app.py",)
    assert "poetry.lock" not in diff.patch

    only_excluded = git.staged_diff(exclude=("*.py", "*.lock", "*.min.js"))
    assert only_excluded.is_empty


def test_truncation(git: Git, stage_change: Callable[[str, str], None]) -> None:
    stage_change("big.txt", "line\n" * 5000)
    diff = git.staged_diff(max_bytes=500)
    assert diff.truncated
    assert "truncated by grip" in diff.patch
    assert len(diff.patch.encode()) < 700


def test_range_diff(
    git: Git, run_git: Callable[..., str], stage_change: Callable[[str, str], None]
) -> None:
    base = run_git("rev-parse", "HEAD").strip()
    stage_change("a.txt", "a\n")
    run_git("commit", "-q", "-m", "a")
    head = run_git("rev-parse", "HEAD").strip()
    diff = git.range_diff(base, head)
    assert diff.files == ("a.txt",)
    assert diff.description == f"{base[:12]}..{head[:12]}"


def test_pushed_ref_parsing() -> None:
    line = f"refs/heads/main {'a' * 40} refs/heads/main {ZERO_SHA}\n"
    ref = PushedRef.parse(line)
    assert ref is not None
    assert ref.is_new
    assert not ref.is_delete
    assert PushedRef.parse("garbage") is None
    delete = PushedRef.parse(f"(delete) {ZERO_SHA} refs/heads/x {'b' * 40}")
    assert delete is not None and delete.is_delete


@pytest.fixture
def remote(repo: Path, tmp_path: Path, run_git: Callable[..., str]) -> Path:
    bare = tmp_path / "remote.git"
    bare.mkdir()
    Git(bare).run("init", "-q", "--bare")
    run_git("remote", "add", "origin", str(bare))
    run_git("push", "-q", "-u", "origin", "main")
    return bare


def test_unpushed_base_and_push_diff(
    git: Git, remote: Path, run_git: Callable[..., str], stage_change: Callable[[str, str], None]
) -> None:
    pushed = run_git("rev-parse", "HEAD").strip()
    assert git.unpushed_base(pushed) is None

    stage_change("one.txt", "1\n")
    run_git("commit", "-q", "-m", "one")
    stage_change("two.txt", "2\n")
    run_git("commit", "-q", "-m", "two")
    head = run_git("rev-parse", "HEAD").strip()

    base = git.unpushed_base(head, "origin")
    assert base is not None
    assert git.run("rev-parse", base).strip() == pushed

    # Existing remote branch: diff against the remote sha.
    refs = [PushedRef("refs/heads/main", head, "refs/heads/main", pushed)]
    diff = git.push_diff(refs, "origin")
    assert set(diff.files) == {"one.txt", "two.txt"}

    # New remote branch: fall back to "everything not on a remote".
    refs = [PushedRef("refs/heads/feature", head, "refs/heads/feature", ZERO_SHA)]
    diff = git.push_diff(refs, "origin")
    assert set(diff.files) == {"one.txt", "two.txt"}

    # Deletions are ignored.
    assert git.push_diff([PushedRef("(delete)", ZERO_SHA, "refs/heads/x", pushed)]).is_empty

    # Two refs are merged into one diff.
    refs = [
        PushedRef("refs/heads/main", head, "refs/heads/main", pushed),
        PushedRef("refs/heads/feature", head, "refs/heads/feature", ZERO_SHA),
    ]
    merged = git.push_diff(refs, "origin")
    assert merged.description.startswith("push (")
    assert set(merged.files) == {"one.txt", "two.txt"}


def test_unpushed_base_without_remote_uses_empty_tree(
    git: Git, run_git: Callable[..., str], stage_change: Callable[[str, str], None]
) -> None:
    stage_change("a.txt", "a\n")
    run_git("commit", "-q", "-m", "a")
    head = run_git("rev-parse", "HEAD").strip()
    base = git.unpushed_base(head)
    assert base == git.empty_tree()
    diff = git.range_diff(base, head)
    assert set(diff.files) == {"README.md", "a.txt"}
    new_branch = [PushedRef("refs/heads/main", head, "refs/heads/main", ZERO_SHA)]
    assert set(git.push_diff(new_branch).files) == {"README.md", "a.txt"}


def test_diff_properties() -> None:
    empty = Diff("x", "", "   \n", ())
    assert empty.is_empty
    assert Diff("x", "", "+a", ("a",)).digest != Diff("x", "", "+b", ("a",)).digest
