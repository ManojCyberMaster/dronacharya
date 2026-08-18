"""Knowledge distillation.

We store distilled knowledge, never original page content (see ARCHITECTURE.md). The
distiller ladder picks the best available backend; Phase 1 ships the extractive
fallback (headings + lead content as 'excerpt' units, distilled=False) so saves
made with no LLM reachable are still searchable and get upgraded later
(`dc redistill` or the server sync upgrade pass). LLM distillers arrive in
Phase 2 behind this same interface.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

from .chunking import chunk_document

MAX_UNITS = 40


@dataclass
class DistilledUnit:
    kind: str  # fact | concept | howto | excerpt
    text: str
    heading_path: str | None = None


@dataclass
class Distillation:
    summary: str
    units: list[DistilledUnit] = field(default_factory=list)
    distilled: bool = False  # True only for real (LLM) distillation
    tier: str = "extractive"


class Distiller(Protocol):
    def distill(self, title: str, text: str, lang: str | None) -> Distillation: ...


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _lead_sentences(text: str, n: int = 3, max_chars: int = 500) -> str:
    sentences = _SENTENCE_END.split(text.strip())
    out = " ".join(sentences[:n]).strip()
    return out[:max_chars]


class ExtractiveDistiller:
    """No-LLM fallback: lead sentences as summary, chunked sections as excerpts."""

    tier = "extractive"

    def distill(self, title: str, text: str, lang: str | None) -> Distillation:
        summary = _lead_sentences(text)
        units = [
            DistilledUnit(kind="excerpt", text=s.text, heading_path=s.heading_path)
            for s in chunk_document(text)[:MAX_UNITS]
        ]
        return Distillation(summary=summary, units=units, distilled=False, tier=self.tier)


MAX_DISTILL_CHARS = 24_000
_ALLOWED_KINDS = {"fact", "concept", "howto"}


def _json_loads_lenient(s: str):
    """Back-compat shim — the shared repair lives in llm.loads_lenient."""
    from ..llm import loads_lenient

    payload = loads_lenient(s)
    if payload is None:
        raise ValueError("no parseable JSON object in model reply")
    return payload


class LLMDistiller:
    """Real distillation through the configured provider chain."""

    def __init__(self, chain):
        self.chain = chain

    def distill(self, title: str, text: str, lang: str | None) -> Distillation:

        from ..llm import run_complete
        from ..llm.prompts import DISTILL_SYSTEM, DISTILL_USER

        # 8000, not 3000: reasoning models (gpt-oss) think before emitting the
        # JSON, and a truncated object silently demotes the save to extractive
        raw, provider = run_complete(
            self.chain, DISTILL_SYSTEM,
            DISTILL_USER.format(title=title, text=text[:MAX_DISTILL_CHARS]),
            max_tokens=8000,
        )
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("distiller returned no JSON")
        payload = _json_loads_lenient(raw[start:end + 1])
        units = [
            DistilledUnit(
                kind=u.get("kind") if u.get("kind") in _ALLOWED_KINDS else "fact",
                text=str(u.get("text", "")).strip(),
                heading_path=(u.get("heading_path") or None),
            )
            for u in payload.get("units", [])
            if str(u.get("text", "")).strip()
        ][:MAX_UNITS]
        if not units:
            raise ValueError("distiller returned no units")
        return Distillation(
            summary=str(payload.get("summary", "")).strip() or _lead_sentences(text),
            units=units, distilled=True, tier=f"llm:{provider}",
        )


class DistillerLadder:
    """Best-available: LLM chain first, extractive fallback when nothing answers."""

    def __init__(self, distillers: list[Distiller]):
        self.distillers = distillers

    def distill(self, title: str, text: str, lang: str | None) -> Distillation:
        last_error: Exception | None = None
        for distiller in self.distillers:
            try:
                return distiller.distill(title, text, lang)
            except Exception as e:  # noqa: BLE001 — fall through the ladder
                last_error = e
        raise last_error or RuntimeError("no distiller available")


def get_distiller(config) -> Distiller:
    from ..llm import get_provider_chain

    chain = get_provider_chain(config, task="distill")
    return DistillerLadder([LLMDistiller(chain), ExtractiveDistiller()])
