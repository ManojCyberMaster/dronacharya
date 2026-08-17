from __future__ import annotations

from typing import Iterator, Protocol


class ProviderUnavailable(RuntimeError):
    """Provider can't serve right now (no auth, endpoint down) — try the next one."""


class ProviderRefusal(RuntimeError):
    """Provider declined the request — try the next one."""


class LLMProvider(Protocol):
    name: str

    def available(self) -> bool: ...

    def complete(self, system: str, user: str, *, max_tokens: int = 2000) -> str:
        """Non-streaming completion (used for distillation)."""
        ...

    def stream(self, system: str, user: str, *, max_tokens: int = 4000) -> Iterator[str]:
        """Streaming completion (used for answers)."""
        ...
