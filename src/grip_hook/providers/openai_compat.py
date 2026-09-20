"""Provider for any OpenAI-compatible chat completions API (OpenAI, Ollama, vLLM, ...).

Implemented with the standard library so grip carries no extra dependency for it.
JSON output is requested through ``response_format`` with a JSON schema, which
Ollama and most OpenAI-compatible servers honour; the reply is validated with the
same pydantic models the Anthropic provider uses.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, TypeVar

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

OPENAI_URL = "https://api.openai.com/v1"
OLLAMA_URL = "http://localhost:11434/v1"


class OpenAICompatibleProvider:
    """Talk to ``{base_url}/chat/completions``."""

    name = "openai"

    def __init__(self, cfg: Config, opener: Any = None) -> None:
        self.model = cfg.model
        self.name = cfg.provider.lower()
        default_url = OLLAMA_URL if self.name == "ollama" else OPENAI_URL
        self._base_url = (cfg.base_url or default_url).rstrip("/")
        default_key_env = "OLLAMA_API_KEY" if self.name == "ollama" else "OPENAI_API_KEY"
        key_env = cfg.api_key_env or default_key_env
        self._api_key = os.environ.get(key_env, "")
        if not self._api_key and self.name != "ollama":
            raise ProviderError(f"{key_env} is not set; the {self.name} provider needs an API key")
        self._timeout = cfg.timeout
        self._opener = opener or urllib.request.urlopen

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json", "User-Agent": "grip-hook"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        request = urllib.request.Request(
            f"{self._base_url}/chat/completions", data=body, headers=headers, method="POST"
        )
        try:
            with self._opener(request, timeout=self._timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise ProviderError(f"{self.name} API error ({exc.code}): {detail}") from exc
        except urllib.error.URLError as exc:
            raise ProviderError(f"could not reach {self._base_url}: {exc.reason}") from exc
        try:
            data: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"{self.name} returned invalid JSON") from exc
        return data

    def _complete(self, system: str, user: str, schema: type[T]) -> T:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "schema": schema.model_json_schema(),
                },
            },
        }
        data = self._post(payload)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(
                f"unexpected response shape from {self.name}: {data!r}"[:500]
            ) from exc
        try:
            return schema.model_validate_json(content)
        except ValidationError as exc:
            raise ProviderError(
                f"{self.name} returned output that does not match the schema: {exc}"
            ) from exc

    def generate_questions(self, diff: Diff, difficulty: Difficulty) -> QuestionSet:
        """Write the question set for ``diff``."""
        return self._complete(
            QUESTION_SYSTEM_PROMPT, question_prompt(diff, difficulty), QuestionSet
        )

    def grade(
        self,
        diff: Diff,
        questions: list[Question],
        answers: list[Answer],
        difficulty: Difficulty,
    ) -> GradeSheet:
        """Grade every answer in one call."""
        return self._complete(
            GRADING_SYSTEM_PROMPT, grading_prompt(diff, questions, answers, difficulty), GradeSheet
        )


__all__ = ["OLLAMA_URL", "OPENAI_URL", "OpenAICompatibleProvider"]
