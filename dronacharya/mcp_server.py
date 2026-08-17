"""MCP server (stdio) — search your knowledge base from any MCP client
Desktop. Reads the local store directly through the repository layer, so it
works even when `dc serve` isn't running.

Register (stdio command):  python -m dronacharya.mcp_server
Requires:  pip install "dronacharya[mcp]"
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .config import db_path, load_config

mcp = FastMCP("dronacharya")


def _repo():
    from .storage.sqlite import SqliteRepo

    return SqliteRepo(db_path())


def _embedder():
    from .embeddings import get_embedder

    return get_embedder(load_config())


@mcp.tool()
def search_knowledge(query: str, k: int = 8) -> list[dict]:
    """Search the user's personal knowledge base (saved web pages, notes, and
    documents). Returns knowledge units with their source links — always show
    the user the source link for anything you use."""
    from .search import hybrid_search

    repo = _repo()
    try:
        results = hybrid_search(repo, _embedder(), query, top_k=k)
        return [{
            "title": r.document.title,
            "source": r.document.url or r.document.file_path,
            "heading_path": r.unit.heading_path,
            "kind": r.unit.kind,
            "knowledge": r.unit.text,
            "document_id": r.document.id,
        } for r in results]
    finally:
        repo.close()


@mcp.tool()
def get_document(document_id: str) -> dict:
    """Fetch one saved document: its summary, tags, and all knowledge units."""
    repo = _repo()
    try:
        doc = repo.get_document(document_id)
        if doc is None:
            return {"error": "not found"}
        units = []
        for d, us in repo.iter_documents_with_units():
            if d.id == document_id:
                units = us
                break
        return {
            "title": doc.title,
            "source": doc.url or doc.file_path,
            "summary": doc.summary,
            "tags": repo.get_tags(doc.id),
            "units": [{"kind": u.kind, "heading_path": u.heading_path, "text": u.text}
                      for u in units],
        }
    finally:
        repo.close()


@mcp.tool()
def save_url(url: str, tags: list[str] | None = None, note: str | None = None) -> dict:
    """Save one web page's knowledge to the user's knowledge base (only that
    page is fetched — links are never crawled). Distillation may take a while
    if an LLM provider is configured."""
    from .ingest.pipeline import save_web

    config = load_config()
    repo = _repo()
    try:
        outcome = save_web(repo, _embedder(), config, url, tags=tags, note=note)
        return {"status": outcome.status, "message": outcome.message,
                "document_id": outcome.document_id}
    finally:
        repo.close()


if __name__ == "__main__":
    mcp.run()
