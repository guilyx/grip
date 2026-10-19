"""Cut a release: bump the version everywhere it is written and date the changelog.

    python scripts/release/bump.py patch|minor|major [--date YYYY-MM-DD] [--root DIR]

Prints the new version. Run by the release workflow when a pull request labelled
``release:patch``, ``release:minor`` or ``release:major`` merges; it refuses to release
when ``## [Unreleased]`` in CHANGELOG.md has nothing under it.

What it changes, all from the current version to the new one:

* ``__version__`` in ``src/grip_hook/__init__.py``;
* CHANGELOG.md: the ``[Unreleased]`` entries become ``## [X.Y.Z] - date``, under a fresh
  empty ``[Unreleased]``, and the compare links at the bottom follow;
* ``version`` in CITATION.cff and in the Claude Code plugin manifests;
* ``rev: vX.Y.Z`` in the pre-commit examples;
* ``grip_version`` in the committed eval snapshots.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

PARTS = ("major", "minor", "patch")
REPO_URL = "https://github.com/guilyx/grip"


def bumped(version: str, part: str) -> str:
    """``version`` with ``part`` incremented and the parts after it reset."""
    major, minor, patch = (int(x) for x in version.split("."))
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def _sub(path: Path, pattern: str, replacement: str, *, count: int = 0) -> None:
    text = path.read_text("utf-8")
    new, n = re.subn(pattern, replacement, text, count=count, flags=re.M)
    if n == 0:
        raise SystemExit(f"{path}: nothing matched {pattern!r}")
    path.write_text(new, "utf-8")


def date_changelog(path: Path, old: str, new: str, date: str) -> None:
    """Turn ``[Unreleased]`` into ``[new] - date`` and keep an empty one above it."""
    text = path.read_text("utf-8")
    head = "## [Unreleased]\n"
    start = text.index(head) + len(head)
    end = text.index("\n## [", start)
    if not text[start:end].strip():
        raise SystemExit("CHANGELOG.md has nothing under [Unreleased]; nothing to release")
    text = text[:start] + f"\n## [{new}] - {date}\n" + text[start:]
    text = text.replace(
        f"[Unreleased]: {REPO_URL}/compare/v{old}...HEAD\n",
        f"[Unreleased]: {REPO_URL}/compare/v{new}...HEAD\n"
        f"[{new}]: {REPO_URL}/compare/v{old}...v{new}\n",
    )
    path.write_text(text, "utf-8")


def main() -> int:
    """Bump, print the new version."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("part", choices=PARTS)
    parser.add_argument("--date", default=dt.datetime.now(dt.UTC).date().isoformat())
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    root: Path = args.root

    init = root / "src" / "grip_hook" / "__init__.py"
    match = re.search(r'^__version__ = "(\d+\.\d+\.\d+)"', init.read_text("utf-8"), re.M)
    if not match:
        raise SystemExit(f"{init}: no __version__")
    old = match.group(1)
    new = bumped(old, args.part)
    v_old, v_new = re.escape(old), new

    date_changelog(root / "CHANGELOG.md", old, new, args.date)
    _sub(init, rf'^__version__ = "{v_old}"', f'__version__ = "{v_new}"')
    _sub(root / "CITATION.cff", rf"^version: {v_old}$", f"version: {v_new}")
    for manifest in (".claude-plugin/plugin.json", ".claude-plugin/marketplace.json"):
        _sub(root / manifest, rf'"version": "{v_old}"', f'"version": "{v_new}"')
    for doc in ("docs/getting-started.md", "docs/index.md", ".pre-commit-hooks.yaml"):
        _sub(root / doc, rf"rev: v{v_old}\b", f"rev: v{v_new}")
    for snapshot in sorted((root / "evals" / "snapshots").glob("*.json")):
        _sub(snapshot, rf'"grip_version": "{v_old}"', f'"grip_version": "{v_new}"')
    print(new)
    return 0


if __name__ == "__main__":
    sys.exit(main())
