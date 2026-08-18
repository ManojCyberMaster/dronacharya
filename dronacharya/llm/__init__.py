from __future__ import annotations

from ..config import Config
from .base import LLMProvider, ProviderRefusal, ProviderUnavailable  # noqa: F401


LOCAL_PROVIDERS = {"ollama", "vllm"}   # endpoints the user hosts themselves


def get_provider_chain(config: Config, task: str = "answer") -> list[LLMProvider]:
    """Providers for one TASK, in the user's configured order.

    Task routing (privacy + cost): [llm].distill_providers can pin cheap
    local models to distillation while answers use the full chain, and the
    [privacy] policy per task ("local-only") removes cloud providers from
    the chain entirely — which makes silent local→cloud fallthrough
    structurally impossible, not merely discouraged."""
    from .anthropic_api import AnthropicProvider
    from .openai_api import OpenAIProvider
    from .openai_compat import OpenAICompatProvider

    order = list(config.llm.provider_order)
    if task == "distill" and config.llm.distill_providers:
        order = list(config.llm.distill_providers)
    policy = getattr(config.privacy, task, "any")
    if policy == "local-only":
        order = [n for n in order if n in LOCAL_PROVIDERS]

    chain: list[LLMProvider] = []
    for name in order:
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


def loads_lenient(raw: str):
    """Parse the JSON object embedded in an LLM reply. Models writing
    shell/regex examples emit invalid \\ escapes (\\*, \\e, ...); repair them
    instead of failing the whole call. Shared by every LLM-JSON call site."""
    import json
    import re

    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None
    span = raw[start:end + 1]
    try:
        return json.loads(span)
    except json.JSONDecodeError:
        try:
            return json.loads(re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", span))
        except json.JSONDecodeError:
            return None


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
