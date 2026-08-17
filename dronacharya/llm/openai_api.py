from __future__ import annotations

import os
from typing import Iterator


class OpenAIProvider:
    name = "openai"

    def __init__(self, model: str):
        self.model = model
        self._client = None

    def available(self) -> bool:
        return bool(os.environ.get("OPENAI_API_KEY"))

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI()
        return self._client

    def complete(self, system: str, user: str, *, max_tokens: int = 2000) -> str:
        response = self._get_client().chat.completions.create(
            model=self.model,
            max_completion_tokens=max_tokens,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
        )
        return response.choices[0].message.content or ""

    def stream(self, system: str, user: str, *, max_tokens: int = 4000) -> Iterator[str]:
        stream = self._get_client().chat.completions.create(
            model=self.model,
            max_completion_tokens=max_tokens,
            stream=True,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                yield delta
