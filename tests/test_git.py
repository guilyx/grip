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
    assert base == pushed
    assert git.range_diff(base, head).description == f"{pushed[:12]}..{head[:12]}"

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


def test_new_branch_is_quizzed_when_a_remote_tracking_ref_is_stale(
    git: Git, remote: Path, run_git: Callable[..., str], stage_change: Callable[[str, str], None]
) -> None:
    # Commits pushed on a branch that was later deleted upstream (a merged pull request),
    # while the local origin/old ref was never pruned.
    run_git("checkout", "-q", "-b", "old")
    stage_change("kidnap.py", "detect = True\n")
    run_git("commit", "-q", "-m", "kidnap detection")
    run_git("push", "-q", "origin", "old")
    Git(remote).run("branch", "-D", "old")
    run_git("checkout", "-q", "-b", "feat/new")
    head = run_git("rev-parse", "HEAD").strip()

    assert git.unpushed_base(head, "origin") is None  # the stale ref hides the commit
    refs = [PushedRef("refs/heads/feat/new", head, "refs/heads/feat/new", ZERO_SHA)]
    diff = git.push_diff(refs, "origin")
    assert diff.files == ("kidnap.py",)


def test_range_description_keeps_branch_names(
    git: Git, run_git: Callable[..., str], stage_change: Callable[[str, str], None]
) -> None:
    run_git("checkout", "-q", "-b", "feat/kidnap-detection")
    stage_change("k.py", "x = 1\n")
    run_git("commit", "-q", "-m", "k")
    assert git.range_diff("main", "feat/kidnap-detection").description == (
        "main..feat/kidnap-detection"
    )


def _fork(run_git: Callable[..., str], stage_change: Callable[[str, str], None]) -> None:
    """A ``feat`` branch with ``f.txt`` and, after it forked, ``m.txt`` on main."""
    run_git("checkout", "-q", "-b", "feat")
    stage_change("f.txt", "f\n")
    run_git("commit", "-q", "-m", "f")
    run_git("checkout", "-q", "main")
    stage_change("m.txt", "m\n")
    run_git("commit", "-q", "-m", "m")


def test_merge_base_diff_leaves_out_what_main_did_since(
    git: Git, run_git: Callable[..., str], stage_change: Callable[[str, str], None]
) -> None:
    _fork(run_git, stage_change)
    diff = git.merge_base_diff("main", "feat")
    assert diff.files == ("f.txt",)
    assert diff.description == "main...feat"
    # Two dots compare the tips, so main's newer file shows up as deleted.
    assert set(git.range_diff("main", "feat").files) == {"f.txt", "m.txt"}


def test_merge_base_diff_errors(
    git: Git, run_git: Callable[..., str], stage_change: Callable[[str, str], None]
) -> None:
    with pytest.raises(GitError, match="unknown revision 'nope'"):
        git.merge_base_diff("nope", "HEAD")
    run_git("checkout", "-q", "--orphan", "lonely")
    stage_change("l.txt", "l\n")
    run_git("commit", "-q", "-m", "l")
    with pytest.raises(GitError, match="share no history"):
        git.merge_base_diff("main", "lonely")


def test_live_check_falls_back_when_the_remote_is_unreachable(
    git: Git, run_git: Callable[..., str]
) -> None:
    head = run_git("rev-parse", "HEAD").strip()
    assert git.unpushed_base_live(head, "no-such-remote") is None


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
