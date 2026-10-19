"""The release script, run against a copy of the real files it edits."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "release" / "bump.py"
FILES = [
    "src/grip_hook/__init__.py",
    "CHANGELOG.md",
    "CITATION.cff",
    ".claude-plugin/plugin.json",
    ".claude-plugin/marketplace.json",
    "docs/getting-started.md",
    "docs/index.md",
    ".pre-commit-hooks.yaml",
    *(str(p.relative_to(ROOT)) for p in (ROOT / "evals" / "snapshots").glob("*.json")),
]

sys.path.insert(0, str(SCRIPT.parent))
from bump import bumped  # noqa: E402


@pytest.fixture
def copy(tmp_path: Path) -> Path:
    for name in FILES:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / name, tmp_path / name)
    return tmp_path


def _run(root: Path, part: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), part, "--root", str(root), "--date", "2030-01-02"],
        capture_output=True,
        text=True,
        check=False,
    )


def test_bumped() -> None:
    assert bumped("0.2.1", "patch") == "0.2.2"
    assert bumped("0.2.1", "minor") == "0.3.0"
    assert bumped("0.2.1", "major") == "1.0.0"


def test_bump_rewrites_every_file(copy: Path) -> None:
    import grip_hook  # noqa: PLC0415

    old = grip_hook.__version__
    changelog = (copy / "CHANGELOG.md").read_text("utf-8")
    head = "## [Unreleased]\n"
    start = changelog.index(head) + len(head)
    if not changelog[start : changelog.index("\n## [", start)].strip():
        # Nothing to release right now: give the script an entry to date.
        changelog = changelog.replace(head, head + "\n### Fixed\n\n- Something.\n", 1)
        (copy / "CHANGELOG.md").write_text(changelog, "utf-8")

    result = _run(copy, "minor")
    assert result.returncode == 0, result.stderr
    new = result.stdout.strip()
    assert new == bumped(old, "minor")
    for name in FILES:
        text = (copy / name).read_text("utf-8")
        assert new in text, name
        if name != "CHANGELOG.md":
            assert old not in text, name
    text = (copy / "CHANGELOG.md").read_text("utf-8")
    assert f"## [Unreleased]\n\n## [{new}] - 2030-01-02\n" in text
    assert f"[{new}]: https://github.com/guilyx/grip/compare/v{old}...v{new}" in text


def test_bump_refuses_an_empty_unreleased_section(copy: Path) -> None:
    changelog = copy / "CHANGELOG.md"
    text = changelog.read_text("utf-8")
    head = "## [Unreleased]\n"
    start = text.index(head) + len(head)
    end = text.index("\n## [", start)
    changelog.write_text(text[:start] + text[end:], "utf-8")
    result = _run(copy, "patch")
    assert result.returncode != 0
    assert "nothing to release" in result.stderr
