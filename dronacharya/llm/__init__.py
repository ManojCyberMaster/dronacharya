from __future__ import annotations

from ..config import Config
from .base import LLMProvider, ProviderRefusal, ProviderUnavailable  # noqa: F401


def get_provider_chain(config: Config) -> list[LLMProvider]:
    """Providers in the user's configured order; unavailable ones are skipped
    at call time (never at import time)."""
    from .anthropic_api import AnthropicProvider
    from .openai_api import OpenAIProvider
    from .openai_compat import OpenAICompatProvider

    chain: list[LLMProvider] = []
    for name in config.llm.provider_order:
        if name == "anthropic":
            chain.append(AnthropicProvider(config.llm.anthropic_model))
        elif name == "openai":
            chain.append(OpenAIProvider(config.llm.openai_model))
        elif name == "ollama" and config.llm.ollama_url:
            chain.append(OpenAICompatProvider("ollama", config.llm.ollama_url,
                                              config.llm.ollama_model))
        elif name == "vllm" and config.llm.vllm_url:
            chain.append(OpenAICompatProvider("vllm", config.llm.vllm_url,
                                              config.llm.vllm_model))
    return chain


def run_complete(chain: list[LLMProvider], system: str, user: str,
                 max_tokens: int = 2000) -> tuple[str, str]:
    """Try each provider in order; returns (text, provider_name)."""
    errors: list[str] = []
    for provider in chain:
        if not provider.available():
            continue
        try:
            return provider.complete(system, user, max_tokens=max_tokens), provider.name
        except (ProviderRefusal, ProviderUnavailable, Exception) as e:  # noqa: BLE001
            errors.append(f"{provider.name}: {e}")
    raise ProviderUnavailable(
        "no LLM provider available" + (f" ({'; '.join(errors[-3:])})" if errors else "")
    )
