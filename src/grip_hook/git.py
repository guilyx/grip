"""Thin wrapper around the git CLI: repository discovery and diff collection."""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from grip_hook.errors import GitError

ZERO_SHA = "0" * 40
_TRUNCATION_NOTE = "\n\n[... diff truncated by grip: {omitted} bytes omitted ...]\n"


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
        commits = out.split()
        if not commits:
            return None
        base = f"{commits[0]}^"
        return base if self.rev_exists(base) else self.empty_tree()

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
        truncated = False
        if len(patch.encode("utf-8")) > max_bytes:
            encoded = patch.encode("utf-8")
            keep = encoded[:max_bytes].decode("utf-8", "ignore")
            patch = keep + _TRUNCATION_NOTE.format(omitted=len(encoded) - max_bytes)
            truncated = True
        return Diff(description, stat, patch, files, truncated)

    def staged_diff(self, exclude: tuple[str, ...] = (), max_bytes: int = 200_000) -> Diff:
        """Changes staged for the next commit."""
        return self._collect("staged changes", ["--cached"], exclude, max_bytes)

    def range_diff(
        self, base: str, head: str, exclude: tuple[str, ...] = (), max_bytes: int = 200_000
    ) -> Diff:
        """Changes between two revisions (``base`` may be the empty tree)."""
        return self._collect(f"{base[:12]}..{head[:12]}", [base, head], exclude, max_bytes)

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
        return Diff(description, stat, patch, files, truncated)


__all__ = ["ZERO_SHA", "Diff", "Git", "PushedRef"]
