"""Exception hierarchy for grip."""

from __future__ import annotations


class GripError(Exception):
    """Base class for all grip errors. The message is shown to the user as-is."""

    exit_code: int = 2


class ConfigError(GripError):
    """The configuration is invalid."""


class GitError(GripError):
    """A git command failed or we are not inside a repository."""


class ProviderError(GripError):
    """The LLM provider could not produce a usable result."""


class NoTerminalError(GripError):
    """No interactive terminal is available to ask questions."""


class QuizFailed(GripError):  # noqa: N818 - reads better as a verdict than an "Error"
    """The developer did not reach the passing score."""

    exit_code = 1
