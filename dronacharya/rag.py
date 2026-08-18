"""RAG query pipeline: retrieve → confidence gate → streamed answer with citations."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator

from .config import Config
from .embeddings import Embedder
from .llm import get_provider_chain
from .llm.base import ProviderRefusal, ProviderUnavailable
from .llm.prompts import DEEPER_SYSTEM, RAG_SYSTEM, RAG_USER, answer_language
from .models import SearchResult
from .search import hybrid_search


@dataclass
class QueryResult:
    mode: str                      # kb | deeper | no_answer | no_provider
    sources: list[SearchResult] = field(default_factory=list)
    provider: str = ""
    chunks: Iterator[str] | None = None   # streamed answer text


def _context_block(sources: list[SearchResult]) -> str:
    lines = []
    for i, r in enumerate(sources, 1):
        origin = r.document.url or r.document.file_path or ""
        crumb = f" — {r.unit.heading_path}" if r.unit.heading_path else ""
        lines.append(f"[{i}] {r.document.title}{crumb} ({origin})\n{r.unit.text}")
    return "\n\n".join(lines)


def _stream_with_fallthrough(chain, system: str, user: str, result: QueryResult):
    """Try providers in order; fall through only before the first chunk arrives."""
    errors = []
    for provider in chain:
        if not provider.available():
            continue
        try:
            gen = provider.stream(system, user)
            first = next(gen, None)
            if first is None:
                continue
        except (ProviderRefusal, ProviderUnavailable, Exception) as e:  # noqa: BLE001
            errors.append(f"{provider.name}: {e}")
            continue
        result.provider = provider.name

        def _rest(first_chunk=first, generator=gen):
            yield first_chunk
            try:
                yield from generator
            except ProviderRefusal:
                yield "\n\n[answer stopped by provider safety policy]"

        return _rest()
    raise ProviderUnavailable("; ".join(errors) or "no provider configured/authenticated")


def query(
    repo, embedder: Embedder, config: Config, question: str, *,
    mode: str = "kb", top_k: int | None = None, tags: list[str] | None = None,
) -> QueryResult:
    from .reranker import get_reranker

    reranker = get_reranker(config)
    sources = hybrid_search(
        repo, embedder, question,
        top_k=top_k or config.retrieval.top_k,
        candidates=config.retrieval.candidates,
        reranker=reranker,
        tags=tags,
    )
    from .search import confidence_gate
    confident = bool(sources) and sources[0].score >= confidence_gate(config, reranker)
    chain = get_provider_chain(config)

    if mode == "kb" and not confident:
        repo.log_event("query_no_answer", {"q": question[:200]})
        return QueryResult(mode="no_answer", sources=sources)

    result = QueryResult(mode=mode, sources=sources if confident or mode == "kb" else sources)
    language = answer_language(question)
    if mode == "deeper":
        system = DEEPER_SYSTEM.format(web_hint="", language=language)
        context = _context_block(sources) if sources else "(no relevant saved knowledge)"
        user = RAG_USER.format(context=context, question=question)
    else:
        system = RAG_SYSTEM.format(language=language)
        user = RAG_USER.format(context=_context_block(sources), question=question)

    try:
        result.chunks = _stream_with_fallthrough(chain, system, user, result)
    except ProviderUnavailable as e:
        repo.log_event("query_no_provider", {"error": str(e)})
        return QueryResult(mode="no_provider", sources=sources)
    repo.log_event("query", {"mode": mode, "sources": len(sources)})
    return result


def cited_indices(answer_text: str) -> list[int]:
    return sorted({int(m) for m in re.findall(r"\[(\d{1,2})\]", answer_text)})
