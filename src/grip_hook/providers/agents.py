"""Providers that reuse a coding agent CLI already installed and signed in.

No API key is needed: each provider shells out to the agent in non-interactive
mode, sends the same prompts the API providers use, and asks for JSON back.

* ``claude-code``: ``claude -p`` with ``--json-schema`` (schema-enforced output).
* ``codex``: ``codex exec`` with ``--output-schema`` (schema-enforced output).
* ``gemini``: ``gemini --output-format json``; the JSON is requested in the prompt and
  extracted from the reply.

Every agent runs with tools disabled where the CLI allows it, in an empty scratch
directory, so it cannot read the repository, run commands, or pick up project-level
instructions and hooks. It only ever sees the diff and the answers.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, ClassVar, TypeVar

from pydantic import BaseModel, ValidationError

from grip_hook.config import Config
from grip_hook.errors import ProviderError
from grip_hook.git import Diff
from grip_hook.models import Answer, Difficulty, GradeSheet, Question, QuestionSet
from grip_hook.prompts import (
    GRADING_SYSTEM_PROMPT,
    QUESTION_SYSTEM_PROMPT,
    grading_prompt,
    question_prompt,
)

T = TypeVar("T", bound=BaseModel)

_JSON_INSTRUCTION = (
    "Respond with a single JSON object and nothing else: no prose, no code fences. "
    "It must match this JSON Schema exactly:\n"
)


def _schema(model: type[BaseModel]) -> dict[str, Any]:
    """JSON Schema for ``model`` with ``additionalProperties: false`` everywhere."""
    schema = model.model_json_schema()

    def tighten(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and "additionalProperties" not in node:
                node["additionalProperties"] = False
            for value in node.values():
                tighten(value)
        elif isinstance(node, list):
            for value in node:
                tighten(value)

    tighten(schema)
    return schema


def extract_json(text: str) -> Any:
    """Parse the first JSON object in ``text``, tolerating code fences and chatter."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ProviderError("the agent did not return a JSON object")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ProviderError(f"the agent returned invalid JSON: {exc}") from exc


class AgentCLIProvider:
    """Common machinery: build a command, run it, validate the JSON it returns."""

    name: str = "agent"
    executable: ClassVar[str] = ""
    install_hint: ClassVar[str] = ""
    enforces_schema: ClassVar[bool] = False
    """Whether the CLI constrains its output to the schema; otherwise it is asked for."""
    system_flag: ClassVar[str] = ""
    """Flag that takes the system prompt; empty means prepend it to the user prompt."""

    def __init__(self, cfg: Config) -> None:
        self.model = cfg.model
        self._timeout = cfg.timeout
        self._binary = shutil.which(self.executable)

    # -- to implement per agent -------------------------------------------------------

    def command(self, schema: dict[str, Any], workdir: Path) -> list[str]:
        """The argv to run. The prompt is written to stdin."""
        raise NotImplementedError

    def parse_output(self, stdout: str, workdir: Path) -> Any:
        """Turn the process output into the JSON payload (a dict)."""
        raise NotImplementedError

    # -- shared -----------------------------------------------------------------------

    def _prompt(self, system: str, user: str, schema: dict[str, Any]) -> str:
        parts = [user.strip()] if self.system_flag else [system.strip(), user.strip()]
        if not self.enforces_schema:
            parts.append(_JSON_INSTRUCTION + json.dumps(schema))
        return "\n\n".join(parts) + "\n"

    def _run(self, system: str, user: str, output: type[T]) -> T:
        if self._binary is None:
            raise ProviderError(
                f"{self.executable!r} was not found on PATH. {self.install_hint} "
                "Or pick another provider in .grip.toml."
            )
        schema = _schema(output)
        prompt = self._prompt(system, user, schema)
        with tempfile.TemporaryDirectory(prefix="grip-agent-") as tmp:
            workdir = Path(tmp)
            cmd = [self._binary, *self.command(schema, workdir)]
            if self.system_flag:
                cmd += [self.system_flag, system.strip()]
            try:
                proc = subprocess.run(
                    cmd,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self._timeout,
                    cwd=workdir,
                    env=os.environ.copy(),
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise ProviderError(
                    f"{self.executable} did not answer within {self._timeout:.0f}s "
                    "(raise `timeout` in .grip.toml)"
                ) from exc
            except OSError as exc:
                raise ProviderError(f"could not run {self.executable}: {exc}") from exc
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout).strip()[-600:]
                raise ProviderError(f"{self.executable} exited with {proc.returncode}: {detail}")
            payload = self.parse_output(proc.stdout, workdir)
        try:
            return output.model_validate(payload)
        except ValidationError as exc:
            raise ProviderError(
                f"{self.executable} returned output that does not match the schema: {exc}"
            ) from exc

    def generate_questions(self, diff: Diff, difficulty: Difficulty) -> QuestionSet:
        """Write the question set for ``diff``."""
        return self._run(QUESTION_SYSTEM_PROMPT, question_prompt(diff, difficulty), QuestionSet)

    def grade(
        self,
        diff: Diff,
        questions: list[Question],
        answers: list[Answer],
        difficulty: Difficulty,
    ) -> GradeSheet:
        """Grade every answer in one call."""
        return self._run(
            GRADING_SYSTEM_PROMPT, grading_prompt(diff, questions, answers, difficulty), GradeSheet
        )


class ClaudeCodeProvider(AgentCLIProvider):
    """Claude Code (``claude``) in print mode with schema-enforced structured output."""

    name = "claude-code"
    executable = "claude"
    install_hint = "Install it with `npm install -g @anthropic-ai/claude-code` and sign in."
    enforces_schema = True
    system_flag = "--system-prompt"

    def command(self, schema: dict[str, Any], workdir: Path) -> list[str]:
        """``claude -p`` with no tools, no session persistence, JSON envelope output."""
        cmd = [
            "-p",
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(schema),
            "--tools",
            "",
            "--no-session-persistence",
        ]
        if self.model:
            cmd += ["--model", self.model]
        return cmd

    def parse_output(self, stdout: str, workdir: Path) -> Any:
        """Read ``structured_output`` from the JSON envelope."""
        try:
            envelope = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise ProviderError("claude returned an unreadable envelope") from exc
        if envelope.get("is_error"):
            raise ProviderError(f"claude reported an error: {envelope.get('result', '')}")
        structured = envelope.get("structured_output")
        if structured is not None:
            return structured
        return extract_json(str(envelope.get("result", "")))


class CodexProvider(AgentCLIProvider):
    """OpenAI Codex CLI (``codex exec``) with ``--output-schema``."""

    name = "codex"
    executable = "codex"
    install_hint = "Install it with `npm install -g @openai/codex` and run `codex login`."
    enforces_schema = True

    def command(self, schema: dict[str, Any], workdir: Path) -> list[str]:
        """Non-interactive, read-only sandbox, last message written to a file."""
        schema_file = workdir / "schema.json"
        schema_file.write_text(json.dumps(schema), "utf-8")
        cmd = [
            "exec",
            "-",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--output-schema",
            str(schema_file),
            "--output-last-message",
            str(workdir / "last-message.txt"),
        ]
        if self.model:
            cmd += ["--model", self.model]
        return cmd

    def parse_output(self, stdout: str, workdir: Path) -> Any:
        """Prefer the last-message file; fall back to scanning stdout."""
        last = workdir / "last-message.txt"
        text = last.read_text("utf-8") if last.exists() else stdout
        return extract_json(text)


class GeminiProvider(AgentCLIProvider):
    """Gemini CLI (``gemini``) in non-interactive JSON mode."""

    name = "gemini"
    executable = "gemini"
    install_hint = "Install it with `npm install -g @google/gemini-cli` and sign in."
    enforces_schema = False

    def command(self, schema: dict[str, Any], workdir: Path) -> list[str]:
        """Prompt comes from stdin; the reply is a JSON envelope with a ``response`` key."""
        cmd = ["--output-format", "json"]
        if self.model:
            cmd += ["--model", self.model]
        return cmd

    def parse_output(self, stdout: str, workdir: Path) -> Any:
        """Unwrap the envelope, then extract the JSON object from the reply text."""
        try:
            envelope = json.loads(stdout)
        except json.JSONDecodeError:
            return extract_json(stdout)
        if isinstance(envelope, dict):
            if "error" in envelope:
                err = envelope["error"]
                message = err.get("message", err) if isinstance(err, dict) else err
                raise ProviderError(f"gemini reported an error: {message}")
            if "response" in envelope:
                return extract_json(str(envelope["response"]))
        return envelope


__all__ = [
    "AgentCLIProvider",
    "ClaudeCodeProvider",
    "CodexProvider",
    "GeminiProvider",
    "extract_json",
]
