"""LLM providers.

A provider turns a diff into questions and answers into grades. Add a new one by
implementing :class:`grip_hook.providers.base.Provider` and registering it in
:data:`REGISTRY`.
"""

from __future__ import annotations

from collections.abc import Callable

from grip_hook.config import Config
from grip_hook.errors import ConfigError
from grip_hook.providers.base import Provider

ProviderFactory = Callable[[Config], Provider]


def _anthropic(cfg: Config) -> Provider:
    from grip_hook.providers.anthropic import AnthropicProvider  # noqa: PLC0415 - lazy: SDK import

    return AnthropicProvider(cfg)


def _openai(cfg: Config) -> Provider:
    from grip_hook.providers.openai_compat import OpenAICompatibleProvider  # noqa: PLC0415

    return OpenAICompatibleProvider(cfg)


def _fake(cfg: Config) -> Provider:
    from grip_hook.providers.fake import FakeProvider  # noqa: PLC0415

    return FakeProvider(cfg)


REGISTRY: dict[str, ProviderFactory] = {
    "anthropic": _anthropic,
    "openai": _openai,
    "ollama": _openai,
    "fake": _fake,
}
"""Provider name to factory. ``ollama`` is an alias of ``openai`` with a local default URL."""


def get_provider(cfg: Config) -> Provider:
    """Instantiate the provider named by ``cfg.provider``."""
    try:
        factory = REGISTRY[cfg.provider.lower()]
    except KeyError as exc:
        known = ", ".join(sorted(REGISTRY))
        raise ConfigError(f"unknown provider {cfg.provider!r}; known providers: {known}") from exc
    return factory(cfg)


__all__ = ["REGISTRY", "Provider", "ProviderFactory", "get_provider"]
