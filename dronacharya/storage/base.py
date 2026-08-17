"""Repository protocol — every surface (CLI, API, MCP, sync) goes through this.

Two implementations: SqliteRepo (standalone + offline cache) and PostgresRepo
(home-server role). The build is single-tenant by design and always uses
tenant_id='local'.
"""

from __future__ import annotations

from typing import Iterable, Protocol

from ..models import Document, KnowledgeUnit


class Repository(Protocol):
    # --- documents -------------------------------------------------------
    def get_document(self, document_id: str) -> Document | None: ...
    def get_document_by_url(self, url: str) -> Document | None: ...
    def get_document_by_path(self, file_path: str) -> Document | None: ...
    def insert_document(
        self,
        doc: Document,
        units: list[KnowledgeUnit],
        embeddings: list[list[float]],
    ) -> None: ...
    def replace_document(
        self,
        doc: Document,
        units: list[KnowledgeUnit],
        embeddings: list[list[float]],
    ) -> None:
        """Update doc row and replace all its units/index rows (version bump)."""
        ...
    def update_document_meta(
        self, document_id: str, *, title: str | None = None,
        saved_note: str | None = None, summary: str | None = None,
    ) -> None: ...
    def delete_document(self, document_id: str) -> bool: ...
    def list_documents(
        self, *, source_type: str | None = None, tag: str | None = None,
        limit: int = 50, offset: int = 0,
    ) -> list[Document]: ...
    def iter_documents_with_units(self) -> Iterable[tuple[Document, list[KnowledgeUnit]]]: ...

    # --- search primitives (fused in search.py) --------------------------
    def fts_candidates(self, query: str, limit: int) -> list[tuple[int, float]]:
        """Return (rid, bm25_score) — lower is better."""
        ...
    def vec_candidates(self, embedding: list[float], limit: int) -> list[tuple[int, float]]:
        """Return (rid, distance) — lower is better."""
        ...
    def fetch_units(self, rids: list[int]) -> list[tuple[int, KnowledgeUnit, Document]]: ...

    # --- tags ------------------------------------------------------------
    def set_tags(self, document_id: str, tags: list[str]) -> None: ...
    def get_tags(self, document_id: str) -> list[str]: ...
    def list_tags(self) -> list[tuple[str, int]]: ...
    def document_ids_for_tags(self, tags: list[str]) -> set[str]:
        """ANY-of match; hierarchical prefix: 'a' matches 'a/b'."""
        ...

    # --- notes-file sync bookkeeping -------------------------------------
    def get_sync_state(self, file_path: str) -> tuple[float, str] | None: ...
    def set_sync_state(self, file_path: str, mtime: float, content_hash: str) -> None: ...

    # --- guardrails / audit ----------------------------------------------
    def log_event(self, type_: str, meta: dict) -> None: ...

    # --- data rights ------------------------------------------------------
    def wipe(self) -> int: ...
    def counts(self) -> dict: ...
