"""Connected-facts graph over search results (GraphRAG-lite).

Given a query, retrieve the top knowledge units and connect them into a graph:
  unit —in→ document          (a fact belongs to its source)
  document —tagged→ tag       (shared tags connect documents)
  unit —related→ unit         (embedding cosine similarity ≥ threshold)

This is computed on the fly from what the KB already stores — no schema
changes, no LLM calls, works offline. A persistent entity graph extracted at
ingest time (full GraphRAG) is a separate, reviewable proposal — see
docs/graphrag-proposal.md.
"""

from __future__ import annotations

from .config import Config
from .embeddings import Embedder
from .search import hybrid_search

# Nearest-neighbor linking beats a single global threshold: embedding-similarity
# scales differ by model, and "each fact connects to its closest related fact"
# stays readable at any scale. The floor kills noise links; STRONG adds extra
# edges only for near-duplicates/very tight relations.
SIM_FLOOR = 0.35
SIM_STRONG = 0.75
MAX_LABEL = 100


def _cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))  # embeddings are L2-normalized


def build_graph(repo, embedder: Embedder, config: Config, query: str, *,
                k: int = 12, tags: list[str] | None = None) -> dict:
    from .reranker import get_reranker

    results = hybrid_search(repo, embedder, query, top_k=k,
                            candidates=config.retrieval.candidates,
                            reranker=get_reranker(config), tags=tags)
    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    def add_node(node_id: str, **attrs) -> None:
        if node_id not in nodes:
            nodes[node_id] = {"id": node_id, **attrs}

    for r in results:
        doc, unit = r.document, r.unit
        add_node(f"d:{doc.id}", type="document", label=doc.title[:MAX_LABEL],
                 url=doc.url or doc.file_path or "", source_type=doc.source_type)
        add_node(f"u:{unit.id}", type="unit", label=unit.text[:MAX_LABEL],
                 full_text=unit.text, kind=unit.kind, score=round(r.score, 4),
                 document_id=doc.id)
        edges.append({"source": f"u:{unit.id}", "target": f"d:{doc.id}",
                      "kind": "in", "weight": 1.0})
        for tag in repo.get_tags(doc.id):
            add_node(f"t:{tag}", type="tag", label=tag)
            if not any(e for e in edges if e["source"] == f"d:{doc.id}"
                       and e["target"] == f"t:{tag}"):
                edges.append({"source": f"d:{doc.id}", "target": f"t:{tag}",
                              "kind": "tagged", "weight": 0.5})

    # relatedness between the retrieved facts themselves
    vecs = embedder.embed_passages([r.unit.text for r in results])
    linked: set[tuple[int, int]] = set()

    def link(i: int, j: int, sim: float) -> None:
        key = (min(i, j), max(i, j))
        if key in linked:
            return
        linked.add(key)
        edges.append({"source": f"u:{results[i].unit.id}",
                      "target": f"u:{results[j].unit.id}",
                      "kind": "related", "weight": round(sim, 3)})

    for i in range(len(results)):
        best_j, best_sim = -1, 0.0
        for j in range(len(results)):
            if i == j or results[i].unit.document_id == results[j].unit.document_id:
                continue  # same source — already connected through the doc
            sim = _cos(vecs[i], vecs[j])
            if sim > best_sim:
                best_j, best_sim = j, sim
            if sim >= SIM_STRONG:
                link(i, j, sim)
        if best_j >= 0 and best_sim >= SIM_FLOOR:
            link(i, best_j, best_sim)

    repo.log_event("graph", {"q": query[:200], "nodes": len(nodes),
                             "edges": len(edges)})
    return {"query": query, "nodes": list(nodes.values()), "edges": edges}
