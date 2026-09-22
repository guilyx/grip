"""Anthropic provider built on the official ``anthropic`` SDK."""

from __future__ import annotations

import os
from typing import TypeVar

import anthropic
from pydantic import BaseModel

from grip_hook.config import DEFAULT_MODEL, Config
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

_EFFORT_LEVELS = {"low", "medium", "high", "xhigh", "max"}
_MAX_TOKENS = 16_000


class AnthropicProvider:
    """Ask Claude for questions and grades using structured outputs."""

    name = "anthropic"

    def __init__(self, cfg: Config, client: anthropic.Anthropic | None = None) -> None:
        self.model = cfg.model or DEFAULT_MODEL
        self._effort = cfg.effort.strip().lower()
        self._client = client or self._build_client(cfg)

    @staticmethod
    def _build_client(cfg: Config) -> anthropic.Anthropic:
        kwargs: dict[str, object] = {"timeout": cfg.timeout, "max_retries": 2}
        if cfg.api_key_env:
            key = os.environ.get(cfg.api_key_env)
            if not key:
                raise ProviderError(
                    f"api_key_env is set to {cfg.api_key_env!r} but that variable is empty"
                )
            kwargs["api_key"] = key
        if cfg.base_url:
            kwargs["base_url"] = cfg.base_url
        try:
            return anthropic.Anthropic(**kwargs)  # type: ignore[arg-type]
        except anthropic.AnthropicError as exc:
            raise ProviderError(
                "could not create the Anthropic client. Set ANTHROPIC_API_KEY or run "
                f"`ant auth login`. ({exc})"
            ) from exc

    def _output_config(self) -> dict[str, str] | None:
        # `effort` is accepted by Claude 4.5+ models; Haiku 4.5 and older reject it.
        if self._effort in {"", "none"} or self._effort not in _EFFORT_LEVELS:
            return None
        if self.model.startswith("claude-haiku"):
            return None
        return {"effort": self._effort}

    def _parse(self, system: str, user: str, schema: type[T]) -> T:
        kwargs: dict[str, object] = {}
        output_config = self._output_config()
        if output_config:
            kwargs["output_config"] = output_config
        try:
            response = self._client.messages.parse(
                model=self.model,
                max_tokens=_MAX_TOKENS,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=schema,
                **kwargs,  # type: ignore[arg-type]
            )
        except anthropic.AuthenticationError as exc:
            raise ProviderError(
                "Anthropic rejected the API key. Set ANTHROPIC_API_KEY or run `ant auth login`."
            ) from exc
        except anthropic.NotFoundError as exc:
            raise ProviderError(f"model {self.model!r} was not found: {exc.message}") from exc
        except anthropic.RateLimitError as exc:
            raise ProviderError(
                f"Anthropic rate limit hit, try again shortly: {exc.message}"
            ) from exc
        except anthropic.APIStatusError as exc:
            raise ProviderError(f"Anthropic API error ({exc.status_code}): {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError(f"could not reach the Anthropic API: {exc}") from exc

        if response.stop_reason == "refusal":
            detail = ""
            if response.stop_details is not None:
                detail = f" ({response.stop_details.category}: {response.stop_details.explanation})"
            raise ProviderError(f"the model declined to process this diff{detail}")
        if response.stop_reason == "max_tokens":
            raise ProviderError("the model's response was cut off; try a smaller diff")
        parsed = response.parsed_output
        if parsed is None:
            raise ProviderError("the model returned no structured output")
        return parsed

    def generate_questions(self, diff: Diff, difficulty: Difficulty) -> QuestionSet:
        """Write the question set for ``diff``."""
        return self._parse(QUESTION_SYSTEM_PROMPT, question_prompt(diff, difficulty), QuestionSet)

    def grade(
        self,
        diff: Diff,
        questions: list[Question],
        answers: list[Answer],
        difficulty: Difficulty,
    ) -> GradeSheet:
        """Grade every answer in one call."""
        return self._parse(
            GRADING_SYSTEM_PROMPT, grading_prompt(diff, questions, answers, difficulty), GradeSheet
        )


__all__ = ["AnthropicProvider"]
