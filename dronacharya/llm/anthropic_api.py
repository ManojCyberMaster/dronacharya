"""Anthropic API provider (API key or `ant auth login` profile).

claude-opus-5 notes: adaptive thinking is the default (omit `thinking`);
`budget_tokens`/`temperature`/`top_p` are rejected — never send them. A safety
refusal arrives as HTTP 200 + stop_reason "refusal": raise ProviderRefusal so
the chain falls through.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

from .base import ProviderRefusal


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str):
        self.model = model
        self._client = None

    def available(self) -> bool:
        if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            return True
        # `ant auth login` profile on disk also works with a bare client
        cfg = os.environ.get("ANTHROPIC_CONFIG_DIR")
        base = Path(cfg) if cfg else Path.home() / ".config" / "anthropic"
        return (base / "credentials").exists() or any(base.glob("credentials/*.json")) \
            if base.exists() else False

    def _get_client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        return self._client

    def complete(self, system: str, user: str, *, max_tokens: int = 2000) -> str:
        response = self._get_client().messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=[{"type": "text", "text": system,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
        )
        if response.stop_reason == "refusal":
            raise ProviderRefusal("anthropic safety refusal")
        return "".join(b.text for b in response.content if b.type == "text")

    def stream(self, system: str, user: str, *, max_tokens: int = 4000) -> Iterator[str]:
        with self._get_client().messages.stream(
            model=self.model,
            max_tokens=max_tokens,
            system=[{"type": "text", "text": system,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
        ) as stream_ctx:
            yield from stream_ctx.text_stream
            final = stream_ctx.get_final_message()
            if final.stop_reason == "refusal":
                raise ProviderRefusal("anthropic safety refusal")
