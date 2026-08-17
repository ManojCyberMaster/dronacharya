"""One provider class for every OpenAI-compatible endpoint the user points at:
local Ollama, vLLM on a home server, or a rented remote GPU service."""

from __future__ import annotations

from typing import Iterator


class OpenAICompatProvider:
    def __init__(self, name: str, base_url: str, model: str, api_key: str = ""):
        self.name = name
        self.base_url = base_url
        self.model = model
        self.api_key = api_key or "not-needed"
        self._client = None

    def available(self) -> bool:
        return bool(self.base_url and self.model)

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(base_url=self.base_url, api_key=self.api_key)
        return self._client

    def _sampling(self, *, extraction: bool) -> dict:
        """Deterministic-ish factual decoding, per model family.

        Dense instruct models: temperature 0 + fixed seed — greedy decoding
        curbs the language drift and hallucination small models show at their
        default temperature. gpt-oss reasoning models are the exception:
        greedy decoding makes them loop (vendor recommends temperature 1.0),
        and on extraction calls we cap the hidden reasoning at "low" — that,
        not model size, is what makes distillation fast."""
        if "gpt-oss" in self.model.lower():
            params: dict = {"temperature": 1.0, "seed": 7}
            if extraction:
                params["extra_body"] = {"reasoning_effort": "low"}
            return params
        return {"temperature": 0.0, "seed": 7}

    def _resolve_model(self) -> str | None:
        """Configured model id not served — fix it ourselves when unambiguous.
        Home vLLM/Ollama servers typically serve one model, and ids drift
        ('gpt-oss-120b' vs 'openai/gpt-oss-120b'); a suffix match or a
        single-model server resolves without bothering the user."""
        try:
            served = [m.id for m in self._get_client().models.list()]
        except Exception:  # noqa: BLE001 — can't list: nothing to resolve
            return None
        matches = [m for m in served if m == self.model
                   or m.endswith("/" + self.model)]
        if len(matches) == 1:
            return matches[0]
        return served[0] if len(served) == 1 else None

    def _create(self, kwargs: dict):
        try:
            return self._get_client().chat.completions.create(**kwargs)
        except Exception as e:
            not_found = getattr(e, "status_code", None) == 404 or \
                "does not exist" in str(e).lower()
            fixed = self._resolve_model() if not_found else None
            if not fixed or fixed == self.model:
                raise
            self.model = fixed
            kwargs["model"] = fixed
            return self._get_client().chat.completions.create(**kwargs)

    # complete() serves extraction jobs (distillation, quick answers);
    # stream() serves user-facing RAG/--deeper answers (full reasoning).
    def complete(self, system: str, user: str, *, max_tokens: int = 2000) -> str:
        response = self._create(dict(
            model=self.model,
            max_tokens=max_tokens,
            **self._sampling(extraction=True),
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
        ))
        return response.choices[0].message.content or ""

    def stream(self, system: str, user: str, *, max_tokens: int = 4000) -> Iterator[str]:
        stream = self._create(dict(
            model=self.model,
            max_tokens=max_tokens,
            **self._sampling(extraction=False),
            stream=True,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
        ))
        for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                yield delta
