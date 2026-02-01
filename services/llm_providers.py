"""LLM provider interface and registry for scoring workflows."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, TypeVar

from google.genai.types import ThinkingLevel
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models.anthropic import AnthropicModel, AnthropicModelSettings
from pydantic_ai.models.google import GoogleModel, GoogleModelSettings

OutputT = TypeVar("OutputT", bound=BaseModel)


class LLMProvider(Protocol):
    """Interface for LLM providers backed by pydantic-ai models."""

    name: str

    def matches(self, model_name: str) -> bool:
        """Return True if provider should handle the given model name."""

    def estimate_tokens(self, text: str, model_name: str) -> int:
        """Estimate token count for the given text and model."""

    def create_agent(self, model_name: str, output_type: type[OutputT]) -> Agent[None, OutputT]:
        """Create a configured pydantic-ai Agent for the model."""


@dataclass(frozen=True)
class ProviderConfig:
    """Provider definition for registry lookup."""

    name: str
    matcher: Callable[[str], bool]
    token_estimator: Callable[[str, str], int]
    agent_factory: Callable[[str, type[OutputT]], Agent[None, OutputT]]

    def matches(self, model_name: str) -> bool:
        return self.matcher(model_name)

    def estimate_tokens(self, text: str, model_name: str) -> int:
        return self.token_estimator(text, model_name)

    def create_agent(self, model_name: str, output_type: type[OutputT]) -> Agent[None, OutputT]:
        return self.agent_factory(model_name, output_type)


def _estimate_anthropic_tokens(text: str, _model_name: str) -> int:
    # Claude: ~3.5 chars per token (Anthropic documentation)
    return int(len(text) / 3.5)


def _estimate_google_tokens(text: str, _model_name: str) -> int:
    # Gemini: ~4 chars per token (Google documentation)
    return len(text) // 4


def _create_anthropic_agent(model_name: str, output_type: type[OutputT]) -> Agent[None, OutputT]:
    settings = AnthropicModelSettings(temperature=0.2, timeout=300.0)
    model = AnthropicModel(model_name)
    return Agent(model, output_type=output_type, model_settings=settings)


def _create_google_agent(model_name: str, output_type: type[OutputT]) -> Agent[None, OutputT]:
    settings = GoogleModelSettings(
        temperature=0.2,
        google_thinking_config={"thinking_level": ThinkingLevel.MEDIUM},
    )
    model = GoogleModel(model_name)
    return Agent(model, output_type=output_type, model_settings=settings)


_PROVIDERS: tuple[ProviderConfig, ...] = (
    ProviderConfig(
        name="anthropic",
        matcher=lambda model_name: model_name.startswith("claude-"),
        token_estimator=_estimate_anthropic_tokens,
        agent_factory=_create_anthropic_agent,
    ),
    ProviderConfig(
        name="google",
        matcher=lambda _model_name: True,
        token_estimator=_estimate_google_tokens,
        agent_factory=_create_google_agent,
    ),
)


def resolve_provider(model_name: str) -> LLMProvider:
    """Resolve a provider for the model name, defaulting to Google."""
    for provider in _PROVIDERS:
        if provider.matches(model_name):
            return provider
    return _PROVIDERS[-1]


def estimate_tokens(text: str, model_name: str) -> int:
    """Estimate token count using the resolved provider."""
    return resolve_provider(model_name).estimate_tokens(text, model_name)


def create_agent(model_name: str, output_type: type[OutputT]) -> Agent[None, OutputT]:
    """Create a provider-backed agent for the given model name."""
    return resolve_provider(model_name).create_agent(model_name, output_type)
