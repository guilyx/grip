"""Thin wrapper around the git CLI: repository discovery and diff collection."""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from grip_hook.errors import GitError

ZERO_SHA = "0" * 40
LS_REMOTE_TIMEOUT = 15.0
"""Seconds to wait for ``git ls-remote`` before trusting the local remote-tracking refs."""
_TRUNCATION_NOTE = "\n\n[... diff truncated by grip: {omitted} bytes omitted ...]\n"


_HASH = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_INDENT = re.compile(r"[ \t]*")


def _short(rev: str) -> str:
    """A full hash shortened to 12 characters; a branch name or other spelling as is."""
    return rev[:12] if _HASH.fullmatch(rev) else rev


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

    stats: DiffStats | None = None
    """Line counts, when git measured them. ``None`` is unknown, and never trivial."""

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
            raise GitError(f"git {' '.join(args)} failed: {detail}")
        return proc.stdout

    def ok(self, *args: str) -> bool:
        """Return ``True`` when ``git <args>`` exits successfully."""
        proc = subprocess.run(["git", *args], cwd=self.cwd, capture_output=True, check=False)
        return proc.returncode == 0

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
        patch = self.run(*common, "--unified=3", "--diff-filter=ACDMRT", *spec)
        stats = self._stats(common, spec, patch)
        truncated = False
        if len(patch.encode("utf-8")) > max_bytes:
            encoded = patch.encode("utf-8")
            keep = encoded[:max_bytes].decode("utf-8", "ignore")
            patch = keep + _TRUNCATION_NOTE.format(omitted=len(encoded) - max_bytes)
            truncated = True
        return Diff(description, stat, patch, files, truncated, stats=stats)

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
    ) -> Diff:
        """Changes between two revisions (``base`` may be the empty tree)."""
        description = description or f"{_short(base)}..{_short(head)}"
        return self._collect(description, [base, head], exclude, max_bytes)

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
            part = self.range_diff(base, ref.local_sha, exclude, max_bytes)
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
        description = "push (" + ", ".join(p.description for p in parts) + ")"
        measured = [p.stats for p in parts if p.stats is not None]
        stats = sum(measured[1:], measured[0]) if len(measured) == len(parts) else None
        return Diff(description, stat, patch, files, truncated, stats=stats)


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


__all__ = ["ZERO_SHA", "Diff", "DiffStats", "Git", "PushedRef", "matches_path"]
