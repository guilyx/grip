"""Tests for the providers that shell out to installed coding agents.

Each test drops a stub executable on PATH that records its argv and stdin, then answers
the way the real CLI would.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from grip_hook import QUESTION_COUNT
from grip_hook.config import Config
from grip_hook.errors import ProviderError
from grip_hook.git import Diff
from grip_hook.models import Answer, Difficulty, Question
from grip_hook.prompts import GRADING_SYSTEM_PROMPT, QUESTION_SYSTEM_PROMPT
from grip_hook.providers import get_provider
from grip_hook.providers.agents import (
    ClaudeCodeProvider,
    CodexProvider,
    GeminiProvider,
    extract_json,
)

DIFF = Diff("staged changes", " a.py | 1 +", "+print('hi')\n", ("a.py",))
QUESTIONS = [Question(question=f"q{i}", rubric=f"r{i}", focus="f") for i in range(QUESTION_COUNT)]
ANSWERS = [Answer(question_index=i, text=f"a{i}") for i in range(QUESTION_COUNT)]

QUESTION_SET = {
    "summary": "adds a print",
    "questions": [
        {"question": f"q{i}", "rubric": f"r{i}", "focus": "f"} for i in range(QUESTION_COUNT)
    ],
}
GRADE_SHEET = {
    "grades": [{"question_index": i, "score": 15, "feedback": "ok"} for i in range(QUESTION_COUNT)],
    "verdict": "fine",
}


def _stub(bin_dir: Path, name: str, body: str) -> Path:
    """Write a POSIX shell stub that saves argv and stdin, then runs ``body``."""
    script = bin_dir / name
    script.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\0" "$@" > "{bin_dir}/{name}.args"\n'
        f'cat > "{bin_dir}/{name}.stdin"\n' + body + "\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


@pytest.fixture
def bin_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "bin"
    directory.mkdir()
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ.get('PATH', '')}")
    return directory


def _args(bin_dir: Path, name: str) -> list[str]:
    return (bin_dir / f"{name}.args").read_text().split("\0")[:-1]


def _stdin(bin_dir: Path, name: str) -> str:
    return (bin_dir / f"{name}.stdin").read_text()


# -- claude code -------------------------------------------------------------------------


def test_claude_code_questions(bin_dir: Path) -> None:
    envelope = {"type": "result", "is_error": False, "structured_output": QUESTION_SET}
    _stub(bin_dir, "claude", f"cat <<'JSON'\n{json.dumps(envelope)}\nJSON")
    provider = ClaudeCodeProvider(Config(provider="claude-code", model="claude-sonnet-5"))
    result = provider.generate_questions(DIFF, Difficulty.NORMAL)
    assert result.summary == "adds a print"
    assert len(result.questions) == QUESTION_COUNT

    args = _args(bin_dir, "claude")
    assert args[:3] == ["-p", "--output-format", "json"]
    assert "--json-schema" in args
    assert args[args.index("--tools") + 1] == ""
    assert "--no-session-persistence" in args
    assert args[args.index("--model") + 1] == "claude-sonnet-5"
    assert args[args.index("--system-prompt") + 1] == QUESTION_SYSTEM_PROMPT.strip()
    schema = json.loads(args[args.index("--json-schema") + 1])
    assert schema["additionalProperties"] is False
    stdin = _stdin(bin_dir, "claude")
    assert "+print('hi')" in stdin
    assert QUESTION_SYSTEM_PROMPT.strip() not in stdin
    assert "JSON Schema" not in stdin


def test_claude_code_grades_without_model_flag(bin_dir: Path) -> None:
    envelope = {"is_error": False, "structured_output": GRADE_SHEET}
    _stub(bin_dir, "claude", f"cat <<'JSON'\n{json.dumps(envelope)}\nJSON")
    provider = ClaudeCodeProvider(Config(provider="claude-code"))
    sheet = provider.grade(DIFF, QUESTIONS, ANSWERS, Difficulty.HARD)
    assert [g.score for g in sheet.grades] == [15] * QUESTION_COUNT
    args = _args(bin_dir, "claude")
    assert "--model" not in args
    assert args[args.index("--system-prompt") + 1] == GRADING_SYSTEM_PROMPT.strip()


def test_claude_code_error_envelope(bin_dir: Path) -> None:
    envelope = {"is_error": True, "result": "Not logged in"}
    _stub(bin_dir, "claude", f"cat <<'JSON'\n{json.dumps(envelope)}\nJSON")
    provider = ClaudeCodeProvider(Config(provider="claude-code"))
    with pytest.raises(ProviderError, match="Not logged in"):
        provider.generate_questions(DIFF, Difficulty.NORMAL)


def test_claude_code_falls_back_to_result_text(bin_dir: Path) -> None:
    envelope = {"is_error": False, "result": f"Here you go:\n{json.dumps(QUESTION_SET)}"}
    _stub(bin_dir, "claude", f"cat <<'JSON'\n{json.dumps(envelope)}\nJSON")
    provider = ClaudeCodeProvider(Config(provider="claude-code"))
    assert provider.generate_questions(DIFF, Difficulty.NORMAL).summary == "adds a print"


# -- codex -------------------------------------------------------------------------------


def test_codex_uses_schema_and_last_message_files(bin_dir: Path) -> None:
    body = """
schema=""; out=""
while [ $# -gt 0 ]; do
  case "$1" in
    --output-schema) schema="$2"; shift ;;
    --output-last-message) out="$2"; shift ;;
  esac
  shift
done
test -s "$schema" || exit 3
printf '%s' '__PAYLOAD__' > "$out"
echo "some progress chatter on stdout"
""".replace("__PAYLOAD__", json.dumps(QUESTION_SET))
    _stub(bin_dir, "codex", body)
    provider = CodexProvider(Config(provider="codex", model="o4"))
    assert provider.generate_questions(DIFF, Difficulty.EASY).summary == "adds a print"
    args = _args(bin_dir, "codex")
    assert args[:2] == ["exec", "-"]
    assert "--skip-git-repo-check" in args
    assert args[args.index("--sandbox") + 1] == "read-only"
    assert args[args.index("--model") + 1] == "o4"
    stdin = _stdin(bin_dir, "codex")
    assert QUESTION_SYSTEM_PROMPT.strip() in stdin
    assert "JSON Schema" not in stdin


def test_codex_falls_back_to_stdout(bin_dir: Path) -> None:
    _stub(bin_dir, "codex", f"cat <<'JSON'\n```json\n{json.dumps(GRADE_SHEET)}\n```\nJSON")
    provider = CodexProvider(Config(provider="codex"))
    sheet = provider.grade(DIFF, QUESTIONS, ANSWERS, Difficulty.NORMAL)
    assert sheet.verdict == "fine"


# -- gemini ------------------------------------------------------------------------------


def test_gemini_asks_for_json_and_unwraps_response(bin_dir: Path) -> None:
    reply = f"Sure!\n```json\n{json.dumps(QUESTION_SET)}\n```"
    envelope = {"response": reply, "stats": {}}
    _stub(bin_dir, "gemini", f"cat <<'JSON'\n{json.dumps(envelope)}\nJSON")
    provider = GeminiProvider(Config(provider="gemini", model="gemini-2.5-pro"))
    assert provider.generate_questions(DIFF, Difficulty.NORMAL).summary == "adds a print"
    args = _args(bin_dir, "gemini")
    assert args[:2] == ["--output-format", "json"]
    assert args[args.index("--model") + 1] == "gemini-2.5-pro"
    stdin = _stdin(bin_dir, "gemini")
    assert QUESTION_SYSTEM_PROMPT.strip() in stdin
    assert "JSON Schema" in stdin
    assert '"additionalProperties": false' in stdin


def test_gemini_error_envelope(bin_dir: Path) -> None:
    envelope = {"error": {"type": "AuthError", "message": "please sign in", "code": 1}}
    _stub(bin_dir, "gemini", f"cat <<'JSON'\n{json.dumps(envelope)}\nJSON")
    provider = GeminiProvider(Config(provider="gemini"))
    with pytest.raises(ProviderError, match="please sign in"):
        provider.generate_questions(DIFF, Difficulty.NORMAL)


def test_gemini_bare_json_without_envelope(bin_dir: Path) -> None:
    _stub(bin_dir, "gemini", f"cat <<'JSON'\n{json.dumps(GRADE_SHEET)}\nJSON")
    provider = GeminiProvider(Config(provider="gemini"))
    assert provider.grade(DIFF, QUESTIONS, ANSWERS, Difficulty.NORMAL).verdict == "fine"


# -- failure modes shared by every agent -------------------------------------------------


def test_missing_binary(bin_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(bin_dir))
    provider = ClaudeCodeProvider(Config(provider="claude-code"))
    with pytest.raises(ProviderError, match=r"not found on PATH.*npm install"):
        provider.generate_questions(DIFF, Difficulty.NORMAL)


def test_non_zero_exit_reports_stderr(bin_dir: Path) -> None:
    _stub(bin_dir, "gemini", "echo 'quota exceeded' >&2; exit 2")
    provider = GeminiProvider(Config(provider="gemini"))
    with pytest.raises(ProviderError, match="exited with 2: quota exceeded"):
        provider.generate_questions(DIFF, Difficulty.NORMAL)


def test_unparseable_output(bin_dir: Path) -> None:
    _stub(bin_dir, "claude", "echo 'not json at all'")
    provider = ClaudeCodeProvider(Config(provider="claude-code"))
    with pytest.raises(ProviderError, match="unreadable envelope"):
        provider.generate_questions(DIFF, Difficulty.NORMAL)
    _stub(bin_dir, "gemini", "echo 'not json at all'")
    with pytest.raises(ProviderError, match="did not return a JSON object"):
        GeminiProvider(Config(provider="gemini")).generate_questions(DIFF, Difficulty.NORMAL)


def test_schema_violation(bin_dir: Path) -> None:
    envelope = {"structured_output": {"summary": "x", "questions": []}}
    _stub(bin_dir, "claude", f"cat <<'JSON'\n{json.dumps(envelope)}\nJSON")
    provider = ClaudeCodeProvider(Config(provider="claude-code"))
    with pytest.raises(ProviderError, match="does not match the schema"):
        provider.generate_questions(DIFF, Difficulty.NORMAL)


def test_timeout(bin_dir: Path) -> None:
    _stub(bin_dir, "codex", "sleep 5")
    provider = CodexProvider(Config(provider="codex", timeout=0.3))
    with pytest.raises(ProviderError, match="did not answer within"):
        provider.generate_questions(DIFF, Difficulty.NORMAL)


def test_runs_in_scratch_directory(bin_dir: Path) -> None:
    _stub(bin_dir, "gemini", f"pwd > '{bin_dir}/cwd'; echo '{json.dumps(GRADE_SHEET)}'")
    GeminiProvider(Config(provider="gemini")).grade(DIFF, QUESTIONS, ANSWERS, Difficulty.NORMAL)
    cwd = (bin_dir / "cwd").read_text().strip()
    assert cwd != os.getcwd()
    assert "grip-agent-" in cwd
    assert not Path(cwd).exists(), "scratch directory is removed afterwards"


# -- registry and helpers ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "cls"),
    [
        ("claude-code", ClaudeCodeProvider),
        ("claude", ClaudeCodeProvider),
        ("CODEX", CodexProvider),
        ("gemini", GeminiProvider),
    ],
)
def test_registry(name: str, cls: type) -> None:
    assert isinstance(get_provider(Config(provider=name)), cls)


@pytest.mark.parametrize(
    "text",
    [
        '{"a": 1}',
        '```json\n{"a": 1}\n```',
        '```\n{"a": 1}\n```',
        'Here is the answer:\n{"a": 1}\nHope this helps!',
    ],
)
def test_extract_json(text: str) -> None:
    assert extract_json(text) == {"a": 1}


def test_extract_json_errors() -> None:
    with pytest.raises(ProviderError, match="did not return a JSON object"):
        extract_json("nothing here")
    with pytest.raises(ProviderError, match="invalid JSON"):
        extract_json('{"a": }')
