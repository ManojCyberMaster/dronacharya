"""Hybrid retrieval: FTS5/BM25 + vector KNN fused with Reciprocal Rank Fusion."""

from __future__ import annotations

from .embeddings import Embedder
from .models import SearchResult

RRF_K = 60
MAX_UNITS_PER_DOC = 2


RERANK_POOL = 30


def result_to_json(r) -> dict:
    """One wire/display shape for a search hit — used identically by the
    HTTP API and the local CLI path so clients render one format."""
    from .models import doc_capabilities

    return {
        "document_id": r.document.id,
        "title": r.document.title,
        "source_type": r.document.source_type,
        "url": r.document.url,
        "file_path": r.document.file_path,
        "heading_path": r.unit.heading_path,
        "kind": r.unit.kind,
        "text": r.unit.text,
        "score": r.score,
        "capabilities": doc_capabilities(r.document.source_type),
    }


def confidence_gate(config, reranker) -> float:
    """The threshold matching the score scale actually in use (see
    RetrievalConfig: reranked probabilities vs raw RRF sums)."""
    return (config.retrieval.min_relevance if reranker is not None
            else config.retrieval.min_confidence)


def hybrid_search(
    repo, embedder: Embedder, query: str, *, top_k: int = 8, candidates: int = 50,
    reranker=None, tags: list[str] | None = None,
) -> list[SearchResult]:
    allowed_ids = repo.document_ids_for_tags(tags) if tags else None
    if allowed_ids is not None and not allowed_ids:
        return []
    if reranker is not None:
        fused = _fused_results(repo, embedder, query,
                               top_k=max(RERANK_POOL, top_k), candidates=candidates,
                               allowed_ids=allowed_ids)
        return reranker.rerank(query, fused, top_k)
    return _fused_results(repo, embedder, query, top_k=top_k, candidates=candidates,
                          allowed_ids=allowed_ids)


def _fused_results(
    repo, embedder: Embedder, query: str, *, top_k: int, candidates: int,
    allowed_ids: set[str] | None = None,
) -> list[SearchResult]:
    fts = repo.fts_candidates(query, candidates)
    vec = repo.vec_candidates(embedder.embed_query(query), candidates)

    scores: dict[int, float] = {}
    for rank, (rid, _) in enumerate(fts):
        scores[rid] = scores.get(rid, 0.0) + 1.0 / (RRF_K + rank + 1)
    for rank, (rid, _) in enumerate(vec):
        scores[rid] = scores.get(rid, 0.0) + 1.0 / (RRF_K + rank + 1)
    if not scores:
        return []

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    fetched = {rid: (unit, doc) for rid, unit, doc in repo.fetch_units([r for r, _ in ranked])}

    results: list[SearchResult] = []
    per_doc: dict[str, int] = {}
    for rid, score in ranked:
        if rid not in fetched:
            continue
        unit, doc = fetched[rid]
        if allowed_ids is not None and doc.id not in allowed_ids:
            continue
        if per_doc.get(doc.id, 0) >= MAX_UNITS_PER_DOC:
            continue
        per_doc[doc.id] = per_doc.get(doc.id, 0) + 1
        results.append(SearchResult(unit=unit, document=doc, score=score))
        if len(results) >= top_k:
            break
    return results


def near_duplicate(
    repo, embedder: Embedder, text: str, *, exclude_document_id: str | None = None,
    threshold: float = 0.90,
) -> tuple[str, float] | None:
    """Return (document_id, cosine_sim) of the most similar existing knowledge,
    if above threshold. Vectors are L2-normalized, so cos = 1 - dist^2/2."""
    probe = embedder.embed_query(text[:1000])
    for rid, dist in repo.vec_candidates(probe, 5):
        cos = 1.0 - (dist * dist) / 2.0
        if cos < threshold:
            continue
        fetched = repo.fetch_units([rid])
        if not fetched:
            continue
        _, _, doc = fetched[0]
        if doc.id != exclude_document_id:
            return doc.id, cos
    return None
