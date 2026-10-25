"""The GitHub Action's comment renderer, scripts/action/comment.py."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "action" / "comment.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("grip_action_comment", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


comment = _load()

PAYLOAD = {
    "status": "questions",
    "summary": "Adds a rate limiter to POST /login.",
    "passing_score": 70,
    "max_score": 100,
    "diff": {"description": "abc..def", "files": ["api/auth.py", "api/ratelimit.py"]},
    "questions": [
        {"index": i + 1, "focus": focus, "question": f"Question {i + 1} about the limiter?"}
        for i, focus in enumerate(["behaviour", "motivation", "edge case", "risk", "testing"])
    ],
}


def test_render_lists_the_five_questions_under_the_marker() -> None:
    body = comment.render(PAYLOAD)
    assert body.startswith(comment.MARKER + "\n")
    assert "> Adds a rate limiter to POST /login." in body
    assert "(2 files)" in body
    for i, focus in enumerate(["behaviour", "motivation", "edge case", "risk", "testing"]):
        assert f"{i + 1}. **{focus}**: Question {i + 1} about the limiter?" in body
    assert "Nothing is graded here" in body
    assert "whiteboard" not in body.lower()
    assert "rubric" not in body.lower()


def test_render_links_the_board_and_notes_truncation() -> None:
    payload = {**PAYLOAD, "diff": {"files": ["a.py"], "truncated": True}}
    url = "https://github.com/o/r/actions/runs/1/artifacts/2"
    body = comment.render(payload, url)
    assert "(1 file)" in body
    assert f"[The whiteboard of this change]({url})" in body
    assert "cut before the model read it" in body


def test_model_text_cannot_mention_people_or_inject_html() -> None:
    hostile = {
        **PAYLOAD,
        "summary": "Ping @octocat and <img src=x onerror=alert(1)>",
        "questions": [
            {"index": 1, "focus": "risk", "question": f"Line one\n\n{comment.MARKER}\n@org/team?"}
        ],
    }
    body = comment.render(hostile)
    assert "@octocat" not in body and "@org/team" not in body
    assert "@<!-- -->octocat" in body
    assert "<img" not in body and "&lt;img" in body
    assert body.count(comment.MARKER) == 1  # only grip's own, on the first line
    assert "1. **risk**: Line one &lt;!-- grip-action --&gt; @<!-- -->org/team?" in body
    assert comment.defuse("a@b.com") == "a@<!-- -->b.com"
    assert comment.defuse("a & b") == "a &amp; b"


def test_main_status_marker_and_out(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    questions = tmp_path / "q.json"
    questions.write_text(json.dumps(PAYLOAD), "utf-8")
    assert comment.main(["--status", str(questions)]) == 0
    assert capsys.readouterr().out.strip() == "questions"
    assert comment.main(["--marker"]) == 0
    assert capsys.readouterr().out.strip() == comment.MARKER

    out = tmp_path / "body.md"
    unicode = {**PAYLOAD, "summary": "Défend the change — naïvely"}
    questions.write_text(json.dumps(unicode), "utf-8")
    assert comment.main([str(questions), "--out", str(out)]) == 0
    assert "Défend the change — naïvely" in out.read_text("utf-8")


def test_main_rejects_what_is_not_grip_ask_output(tmp_path: Path) -> None:
    questions = tmp_path / "q.json"
    questions.write_text("[1, 2]", "utf-8")
    with pytest.raises(SystemExit, match="expected the JSON object"):
        comment.main([str(questions)])
    with pytest.raises(SystemExit):
        comment.main([])


def test_action_inputs_are_documented() -> None:
    yaml = pytest.importorskip("yaml")  # installed with pre-commit, in the dev extra
    action = yaml.safe_load((ROOT / "action.yml").read_text("utf-8"))
    docs = (ROOT / "docs" / "github-action.md").read_text("utf-8")
    assert action["runs"]["using"] == "composite"
    inputs = set(action["inputs"])
    assert {"provider", "model", "api-key", "difficulty", "post-comment", "export-board"} <= inputs
    assert action["inputs"]["provider"]["default"] == "anthropic"
    for name in [*inputs, *action["outputs"]]:
        assert f"`{name}`" in docs, f"{name} is not in docs/github-action.md"
