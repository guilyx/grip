"""Thin wrapper around the git CLI: repository discovery and diff collection."""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

from grip_hook.errors import GitError

ZERO_SHA = "0" * 40
LS_REMOTE_TIMEOUT = 15.0
"""Seconds to wait for ``git ls-remote`` before trusting the local remote-tracking refs."""
_TRUNCATION_NOTE = "\n\n[... diff truncated by grip: {omitted} bytes omitted ...]\n"
COMMITS_MAX_BYTES = 4096
"""Commit messages beyond this many bytes are cut before they reach the model."""
_COMMITS_NOTE = "\n[... older commit messages omitted by grip ...]"
_COMMIT_SEPARATOR = "\n\n---\n\n"


_HASH = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
EMPTY_TREES = frozenset(
    {
        "4b825dc642cb6eb9a060e54bf8d69288fbee4904",
        "6ef19b41225c5369f1c104d45d8d85efa9b057b53b14b4b9b939dd74decc5321",
    }
)
"""The empty tree in SHA-1 and SHA-256 repositories: the base for a root commit."""
SINCE_FIRST_COMMIT = "everything since the first commit"

NOT_A_REPOSITORY = "not inside a git repository (run grip from your project)"
NO_COMMITS = "this repository has no commits yet"
_UNKNOWN_REVISION = re.compile(
    r"(?:ambiguous argument|bad revision) '([^']+)'|(?:Not a valid object name|bad object) (\S+)"
)

_INDENT = re.compile(r"[ \t]*")


def _short(rev: str) -> str:
    """A full hash shortened to 12 characters; a branch name or other spelling as is."""
    return rev[:12] if _HASH.fullmatch(rev) else rev


def _ref_name(ref: str) -> str:
    """``refs/heads/feat/x`` as ``feat/x``, ``refs/tags/v1`` as ``tag v1``."""
    for prefix, label in (("refs/heads/", ""), ("refs/tags/", "tag ")):
        if ref.startswith(prefix):
            return label + ref.removeprefix(prefix)
    return _short(ref)


@dataclass(frozen=True, slots=True)
class PushedRef:
    """One line of the ``pre-push`` hook's stdin."""

    local_ref: str
    local_sha: str
    remote_ref: str
    remote_sha: str

    @classmethod
    def parse(cls, line: str) -> PushedRef | None:
        """Parse ``<local ref> <local sha> <remote ref> <remote sha>``; ``None`` if malformed."""
        parts = line.split()
        if len(parts) != 4:
            return None
        return cls(*parts)

    @property
    def is_delete(self) -> bool:
        """``True`` when the push deletes the remote ref."""
        return self.local_sha == ZERO_SHA

    @property
    def is_new(self) -> bool:
        """``True`` when the remote ref does not exist yet."""
        return self.remote_sha == ZERO_SHA


@dataclass(frozen=True, slots=True)
class DiffStats:
    """How many lines a diff changes, to tell a diff with nothing to explain from the rest."""

    added: int
    """Lines added, from ``git diff --numstat``."""

    removed: int
    """Lines removed, from ``git diff --numstat``."""

    significant: int
    """Added plus removed lines once whitespace-only edits are set aside (``git diff -w``).
    A line whose indentation changed still counts: in Python, YAML or a Makefile that
    changes what the code means."""

    binary: bool = False
    """A binary file changed. It has no line count, so such a diff is never trivial."""

    @property
    def changed(self) -> int:
        """Added plus removed lines."""
        return self.added + self.removed

    def __add__(self, other: DiffStats) -> DiffStats:
        return DiffStats(
            self.added + other.added,
            self.removed + other.removed,
            self.significant + other.significant,
            self.binary or other.binary,
        )


def _numstat(out: str) -> tuple[int, int, bool]:
    """``(added, removed, binary)`` summed over ``git diff --numstat`` output."""
    added = removed = 0
    binary = False
    for line in out.splitlines():
        parts = line.split("\t", 2)
        if len(parts) < 3:
            continue
        if parts[0] == "-" or parts[1] == "-":
            binary = True
            continue
        with contextlib.suppress(ValueError):
            added, removed = added + int(parts[0]), removed + int(parts[1])
    return added, removed, binary


def _reindents(patch: str) -> bool:
    """Whether a patch that ``git diff -w`` calls empty still changes some indentation.

    When every change is whitespace, each run of removed lines in a hunk pairs up, in
    order, with the run of added lines that replaces it; a pair whose leading whitespace
    differs is a re-indent. Runs that do not pair up count as one too, so a patch this
    cannot read is never called whitespace-only.
    """
    removed: list[str] = []
    added: list[str] = []

    def moved() -> bool:
        if len(removed) != len(added):
            return True
        indent = [_INDENT.match(line) for line in (*removed, *added)]
        lead = [m.group() if m else "" for m in indent]
        return lead[: len(removed)] != lead[len(removed) :]

    in_hunk = False
    for line in patch.splitlines():
        if line.startswith(("diff --git ", "@@")):
            in_hunk = line.startswith("@@")
        elif in_hunk and line.startswith("-"):
            removed.append(line[1:])
            continue
        elif in_hunk and line.startswith("+"):
            added.append(line[1:])
            continue
        elif not in_hunk or not line.startswith(" "):
            continue
        # A hunk header, a file header or a context line ends a run of changes.
        if moved():
            return True
        removed.clear()
        added.clear()
    return moved()


@dataclass(frozen=True, slots=True)
class Diff:
    """A collected diff, ready to be sent to a provider."""

    description: str
    """Human-readable description of what is being diffed, e.g. ``staged changes``."""

    stat: str
    """Output of ``git diff --stat``."""

    patch: str
    """The unified diff, possibly truncated."""

    files: tuple[str, ...]
    """Changed paths after exclusions."""

    truncated: bool = False

    context: str = ""
    """Optional text about the task the diff solves, shown to the model as data."""

    commits: str = ""
    """The messages of the commits being quizzed, newest first and capped at
    :data:`COMMITS_MAX_BYTES`, shown to the model as data. Not part of :attr:`digest`:
    rewording a commit does not change what passed."""

    stats: DiffStats | None = None
    """Line counts, when git measured them. ``None`` is unknown, and never trivial."""

    binary_only: bool = False
    """``True`` when every changed file is binary: the patch has no lines to ask about."""

    size: int = 0
    """Bytes in the whole patch before truncation; ``0`` when not known."""

    @property
    def is_empty(self) -> bool:
        """``True`` when there is nothing to quiz about."""
        return not self.patch.strip()

    @property
    def digest(self) -> str:
        """Stable fingerprint of the patch content, used to remember passed quizzes."""
        return hashlib.sha256(self.patch.encode("utf-8", "replace")).hexdigest()


class Git:
    """Run git commands inside one repository."""

    def __init__(self, cwd: Path | None = None) -> None:
        self.cwd = Path(cwd) if cwd else Path.cwd()

    def run(self, *args: str, check: bool = True, input_text: str | None = None) -> str:
        """Run ``git <args>`` and return stdout, raising :class:`GitError` on failure."""
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=self.cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                input=input_text,
                check=False,
            )
        except FileNotFoundError as exc:
            raise GitError("git executable not found on PATH") from exc
        if check and proc.returncode != 0:
            detail = proc.stderr.strip() or proc.stdout.strip() or f"exit code {proc.returncode}"
            raise GitError(self._plain(args, detail), f"$ git {' '.join(args)}\n{detail}")
        return proc.stdout

    def _plain(self, args: tuple[str, ...], detail: str) -> str:
        """One line for a failed ``git <args>``, instead of its command line and stderr.

        The common failures (outside a repository, no commits yet, a revision that does
        not exist) get a sentence of their own; anything else keeps git's message but not
        the command line, whose pathspecs alone run to hundreds of bytes.
        """
        if "dubious ownership" in detail:
            # git's own text names the safe.directory command that fixes it.
            return f"git {args[0]} failed: {detail}"
        if "not a git repository" in detail or not self.ok("rev-parse", "--git-dir"):
            return NOT_A_REPOSITORY
        if found := _UNKNOWN_REVISION.search(detail):
            rev = found.group(1) or found.group(2)
            if rev.startswith("HEAD") and not self.has_commits():
                return NO_COMMITS
            return f"unknown revision '{rev}'"
        if "HEAD" in args and not self.has_commits():
            return NO_COMMITS
        return f"git {args[0]} failed: {detail}"

    def ok(self, *args: str) -> bool:
        """Return ``True`` when ``git <args>`` exits successfully."""
        proc = subprocess.run(["git", *args], cwd=self.cwd, capture_output=True, check=False)
        return proc.returncode == 0

    def has_commits(self) -> bool:
        """``False`` in a repository whose current branch has no commit yet."""
        return self.ok("rev-parse", "--verify", "--quiet", "HEAD^{commit}")

    def root(self) -> Path:
        """Top-level directory of the working tree."""
        return Path(self.run("rev-parse", "--show-toplevel").strip())

    def git_dir(self) -> Path:
        """The ``.git`` directory (absolute), also correct for worktrees."""
        return Path(self.run("rev-parse", "--absolute-git-dir").strip())

    def hooks_dir(self) -> Path:
        """Where hooks live, honouring ``core.hooksPath``."""
        configured = self.run("config", "--get", "core.hooksPath", check=False).strip()
        if configured:
            path = Path(os.path.expanduser(configured))
            return path if path.is_absolute() else (self.root() / path)
        return self.git_dir() / "hooks"

    def rev_exists(self, rev: str) -> bool:
        """``True`` when ``rev`` resolves to a commit in this repository."""
        return self.ok("cat-file", "-e", f"{rev}^{{commit}}")

    def empty_tree(self) -> str:
        """The hash of the empty tree, usable as a diff base for root commits."""
        return self.run("hash-object", "-t", "tree", os.devnull).strip()

    def unpushed_base(self, local_sha: str, remote: str | None = None) -> str | None:
        """Find a base to diff ``local_sha`` against that covers every unpushed commit.

        That is the parent of the oldest commit reachable from ``local_sha`` but not from
        any remote (or from ``remote`` only, when given). When that oldest commit is a root
        commit the empty tree is returned so the whole history is covered. Returns ``None``
        when every commit is already on a remote.
        """
        not_remotes = f"--remotes={remote}" if remote else "--remotes"
        out = self.run("rev-list", "--reverse", local_sha, "--not", not_remotes, check=False)
        return self._base_before(out.split())

    def unpushed_base_live(self, local_sha: str, remote: str) -> str | None:
        """:meth:`unpushed_base` against the refs ``remote`` has now, not the local copies.

        Remote-tracking refs go stale: a branch deleted upstream after its pull request
        merged stays in ``refs/remotes`` until ``git fetch --prune``, so its commits look
        pushed when the remote no longer has them. This asks the remote with
        ``git ls-remote``. Returns ``None`` when every commit is on the remote, or when the
        remote cannot be reached (then the local refs are all there is to go on).
        """
        try:
            listed = subprocess.run(
                ["git", "ls-remote", "--heads", "--tags", remote],
                cwd=self.cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
                timeout=LS_REMOTE_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if listed.returncode != 0:
            return None
        shas = {line.split()[0] for line in listed.stdout.splitlines() if line.strip()}
        # Only commits this repository has can be excluded; peeled tags arrive as commits.
        checked = self.run(
            "cat-file", "--batch-check", check=False, input_text="".join(f"{s}\n" for s in shas)
        )
        pushed = [
            parts[0]
            for parts in (line.split() for line in checked.splitlines())
            if len(parts) >= 2 and parts[1] == "commit"
        ]
        out = self.run(
            "rev-list",
            "--reverse",
            "--stdin",
            local_sha,
            check=False,
            input_text="".join(f"^{sha}\n" for sha in pushed),
        )
        return self._base_before(out.split())

    def default_branch(self) -> str | None:
        """The branch pull requests here merge into, as a revision (``origin/main``).

        That is what ``<remote>/HEAD`` points at, for the current branch's remote (else
        ``origin``, else the first remote), then ``<remote>/main`` or ``<remote>/master``,
        then a local ``main`` or ``master``. ``None`` when none of them exists.
        """
        remotes = self.run("remote", check=False).split()
        upstream = self.run(
            "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}", check=False
        ).strip()
        remote = next(
            (r for r in remotes if upstream.startswith(f"{r}/")),
            "origin" if "origin" in remotes else (remotes[0] if remotes else ""),
        )
        candidates: list[str] = []
        if remote:
            head = self.run(
                "symbolic-ref", "--quiet", "--short", f"refs/remotes/{remote}/HEAD", check=False
            ).strip()
            candidates += [head, f"{remote}/main", f"{remote}/master"]
        candidates += ["main", "master"]
        return next((c for c in candidates if c and self.rev_exists(c)), None)

    def branch_base(self, head: str = "HEAD") -> tuple[str, str] | None:
        """``(default branch, merge base)`` for ``head``: where its pull request starts.

        ``None`` when there is no default branch or it shares no history with ``head``.
        """
        default = self.default_branch()
        if default is None:
            return None
        base = self.run("merge-base", default, head, check=False).strip()
        return (default, base) if base else None

    def _base_before(self, commits: list[str]) -> str | None:
        """The parent of the oldest of ``commits`` (the empty tree for a root commit)."""
        if not commits:
            return None
        # Resolved to a hash: a ``sha^`` spelling would lose its ``^`` when shortened for
        # the description and read as ``sha..sha``.
        parent = self.run("rev-parse", "--verify", "--quiet", f"{commits[0]}^", check=False)
        return parent.strip() or self.empty_tree()

    def commit_messages(self, base: str, head: str, max_bytes: int = COMMITS_MAX_BYTES) -> str:
        """The messages of the commits in ``base..head``, newest first, merges left out.

        ``base`` may be the empty tree, for a range that starts at the root commit. The
        text is cut at ``max_bytes``, keeping the newest commits. Empty when git cannot
        list the range.
        """
        spec = f"{base}..{head}" if self.rev_exists(base) else head
        out = self.run("log", "--no-merges", "--format=%s%n%n%b%x00", spec, "--", check=False)
        messages = [m.strip() for m in out.split("\0")]
        return _cap(_COMMIT_SEPARATOR.join(m for m in messages if m), max_bytes)

    def current_branch(self) -> str | None:
        """The checked-out branch's short name; ``None`` on a detached HEAD."""
        name = self.run("rev-parse", "--abbrev-ref", "HEAD", check=False).strip()
        return name if name and name != "HEAD" else None

    def describe_commits(
        self,
        base: str,
        head: str,
        *,
        branch: str | None = None,
        remote: str | None = None,
        unpushed: bool = False,
    ) -> str:
        """``3 commits on feat/x → origin``: what ``base..head`` holds, said for people.

        When ``base`` is the empty tree (a root commit) the count is the whole history and
        the text says so instead of showing the tree's hash.
        """
        root = base in EMPTY_TREES or not self.rev_exists(base)
        counted = self.run("rev-list", "--count", head if root else f"{base}..{head}", check=False)
        count = int(counted.strip()) if counted.strip().isdigit() else 0
        text = f"{count} {'unpushed ' if unpushed else ''}commit{'' if count == 1 else 's'}"
        if branch:
            text += f" on {branch}"
        if remote:
            text += f" → {remote}"
        if root:
            text += f", {SINCE_FIRST_COMMIT}"
        return text

    # -- diff collection ------------------------------------------------------------------

    @staticmethod
    def _pathspec(exclude: tuple[str, ...]) -> list[str]:
        """Turn gitignore-style globs into pathspecs. Patterns without ``/`` match anywhere."""
        if not exclude:
            return []
        specs = []
        for pattern in exclude:
            anywhere = "/" not in pattern.rstrip("/")
            specs.append(f":(exclude,glob){'**/' if anywhere else ''}{pattern.lstrip('/')}")
        return ["--", ".", *specs]

    def _collect(
        self,
        description: str,
        range_args: list[str],
        exclude: tuple[str, ...],
        max_bytes: int,
    ) -> Diff:
        common = ["diff", "--no-color", "--no-ext-diff", "--find-renames", *range_args]
        spec = self._pathspec(exclude)
        names = self.run(*common, "--name-only", *spec).split("\n")
        files = tuple(n for n in names if n)
        if not files:
            return Diff(description, "", "", ())
        stat = self.run(*common, "--stat=100", *spec).rstrip()
        # ``--numstat`` counts a binary file's lines as ``-\t-``.
        counts = self.run(*common, "--numstat", *spec).splitlines()
        binary_only = bool(counts) and all(line.startswith("-\t-\t") for line in counts)
        patch = self.run(*common, "--unified=3", "--diff-filter=ACDMRT", *spec)
        encoded = patch.encode("utf-8")
        stats = self._stats(common, spec, patch)
        truncated = False
        if len(encoded) > max_bytes:
            keep = encoded[:max_bytes].decode("utf-8", "ignore")
            patch = keep + _TRUNCATION_NOTE.format(omitted=len(encoded) - max_bytes)
            truncated = True
        return Diff(
            description,
            stat,
            patch,
            files,
            truncated,
            binary_only=binary_only,
            size=len(encoded),
            stats=stats,
        )

    def _stats(self, common: list[str], spec: list[str], patch: str) -> DiffStats:
        """Line counts for the diff that ``common`` selects; ``patch`` is its full text."""
        numstat = [*common, "--numstat", "--diff-filter=ACDMRT"]
        added, removed, binary = _numstat(self.run(*numstat, *spec))
        significant = added + removed
        if significant and not binary:
            blind_added, blind_removed, _ = _numstat(self.run(*numstat, "-w", *spec))
            if blind_added + blind_removed == 0 and not _reindents(patch):
                significant = 0
        return DiffStats(added, removed, significant, binary)

    def _names(self, args: list[str], exclude: tuple[str, ...]) -> tuple[str, ...]:
        out = self.run(*args, *self._pathspec(exclude), check=False)
        return tuple(n for n in out.split("\n") if n)

    def staged_files(self, exclude: tuple[str, ...] = ()) -> tuple[str, ...]:
        """Paths with changes staged for the next commit."""
        return self._names(["diff", "--cached", "--name-only", "--no-renames"], exclude)

    def unstaged_files(self, exclude: tuple[str, ...] = ()) -> tuple[str, ...]:
        """Tracked paths changed in the working tree but not staged."""
        return self._names(["diff", "--name-only", "--no-renames"], exclude)

    def untracked_files(self, exclude: tuple[str, ...] = ()) -> tuple[str, ...]:
        """Paths git does not track yet, leaving out what ``.gitignore`` ignores."""
        return self._names(["ls-files", "--others", "--exclude-standard"], exclude)

    def count_commits(self, *revs: str) -> int:
        """``git rev-list --count <revs>``; 0 when a revision does not resolve (no commits)."""
        out = self.run("rev-list", "--count", *revs, check=False).strip()
        return int(out) if out.isdigit() else 0

    def staged_diff(self, exclude: tuple[str, ...] = (), max_bytes: int = 200_000) -> Diff:
        """Changes staged for the next commit."""
        return self._collect("staged changes", ["--cached"], exclude, max_bytes)

    def range_diff(
        self,
        base: str,
        head: str,
        exclude: tuple[str, ...] = (),
        max_bytes: int = 200_000,
        description: str | None = None,
        *,
        commits: bool = False,
    ) -> Diff:
        """Changes between two revisions (``base`` may be the empty tree).

        With ``commits``, the messages of the commits in the range come along
        (:meth:`commit_messages`).
        """
        if description is None:
            description = (
                f"{SINCE_FIRST_COMMIT}, up to {_short(head)}"
                if base in EMPTY_TREES
                else f"{_short(base)}..{_short(head)}"
            )
        diff = self._collect(description, [base, head], exclude, max_bytes)
        if commits and not diff.is_empty:
            diff = replace(diff, commits=self.commit_messages(base, head))
        return diff

    def merge_base_diff(
        self,
        base: str,
        head: str,
        exclude: tuple[str, ...] = (),
        max_bytes: int = 200_000,
        *,
        commits: bool = False,
    ) -> Diff:
        """Changes on ``head`` since it forked from ``base``: what git means by ``base...head``.

        With ``commits``, the messages of the commits since the fork come along. Raises
        :class:`GitError` when a revision is unknown or the two share no history.
        """
        for rev in (base, head):
            if not self.rev_exists(rev):
                raise GitError(f"unknown revision '{rev}'")
        fork = self.run("merge-base", base, head, check=False).strip()
        if not fork:
            raise GitError(
                f"{base} and {head} share no history, so {base}...{head} has no merge base; "
                f"use {base}..{head}"
            )
        description = f"{_short(base)}...{_short(head)}"
        return self.range_diff(
            fork, head, exclude, max_bytes, description=description, commits=commits
        )

    def worktree_diff(
        self, base: str, exclude: tuple[str, ...] = (), max_bytes: int = 200_000
    ) -> Diff:
        """Everything since ``base``: commits plus staged and unstaged changes."""
        self.run("add", "--intent-to-add", "--all", ".")
        return self._collect(f"{base[:12]}..worktree", [base], exclude, max_bytes)

    def push_diff(
        self,
        refs: list[PushedRef],
        remote: str | None = None,
        exclude: tuple[str, ...] = (),
        max_bytes: int = 200_000,
        *,
        commits: bool = False,
    ) -> Diff:
        """Combined diff of everything a ``git push`` would send upstream."""
        parts: list[Diff] = []
        for ref in refs:
            if ref.is_delete:
                continue
            base: str | None
            if not ref.is_new and self.rev_exists(ref.remote_sha):
                base = ref.remote_sha
            else:
                base = self.unpushed_base(ref.local_sha, remote)
                if base is None and ref.is_new and remote:
                    # A new branch that looks fully pushed may only look so through stale
                    # remote-tracking refs; git would not be sending it otherwise.
                    base = self.unpushed_base_live(ref.local_sha, remote)
            if base is None:
                continue
            branch = _ref_name(ref.remote_ref if ref.local_ref == "HEAD" else ref.local_ref)
            description = self.describe_commits(base, ref.local_sha, branch=branch, remote=remote)
            part = self.range_diff(
                base, ref.local_sha, exclude, max_bytes, description, commits=commits
            )
            if not part.is_empty:
                parts.append(part)
        if not parts:
            return Diff("push", "", "", ())
        if len(parts) == 1:
            return parts[0]
        patch = "\n".join(p.patch for p in parts)
        truncated = any(p.truncated for p in parts)
        if len(patch.encode("utf-8")) > max_bytes:
            patch = patch.encode("utf-8")[:max_bytes].decode("utf-8", "ignore")
            truncated = True
        files = tuple(dict.fromkeys(f for p in parts for f in p.files))
        stat = "\n\n".join(p.stat for p in parts)
        description = "; ".join(p.description for p in parts)
        binary_only = all(p.binary_only for p in parts)
        size = sum(p.size for p in parts)
        measured = [p.stats for p in parts if p.stats is not None]
        stats = sum(measured[1:], measured[0]) if len(measured) == len(parts) else None
        messages = _cap(_COMMIT_SEPARATOR.join(p.commits for p in parts if p.commits))
        return Diff(
            description,
            stat,
            patch,
            files,
            truncated,
            commits=messages,
            binary_only=binary_only,
            size=size,
            stats=stats,
        )


def _cap(text: str, max_bytes: int = COMMITS_MAX_BYTES) -> str:
    """``text`` cut to ``max_bytes`` of UTF-8, with a note saying so when it was."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", "ignore").rstrip() + _COMMITS_NOTE


def matches_path(path: str, patterns: tuple[str, ...]) -> bool:
    """Whether the repository path ``path`` matches one of the globs in ``patterns``.

    Read the way ``exclude`` is: a pattern without ``/`` matches at any depth (``*.md``),
    one with ``/`` from the repository root (``docs/**``). ``*`` and ``?`` stay within one
    directory, ``**`` spans several, and a pattern that matches a directory (``docs``,
    ``docs/``) matches everything inside it.
    """
    parts = path.split("/")
    for raw in patterns:
        anywhere = "/" not in raw.strip().rstrip("/")
        pattern = raw.strip().strip("/")
        if not pattern:
            continue
        regex = re.compile(_glob_regex(pattern))
        # Without a slash the pattern may start at any segment; with one, at the root.
        starts = range(len(parts)) if anywhere else range(1)
        for start in starts:
            for end in range(start + 1, len(parts) + 1):
                if regex.fullmatch("/".join(parts[start:end])):
                    return True
    return False


def _glob_regex(pattern: str) -> str:
    """A regex for ``pattern``: ``*`` and ``?`` stay in one segment, ``**`` crosses them."""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return "".join(out)


__all__ = [
    "COMMITS_MAX_BYTES",
    "EMPTY_TREES",
    "NOT_A_REPOSITORY",
    "NO_COMMITS",
    "SINCE_FIRST_COMMIT",
    "ZERO_SHA",
    "Diff",
    "DiffStats",
    "Git",
    "PushedRef",
    "matches_path",
]
