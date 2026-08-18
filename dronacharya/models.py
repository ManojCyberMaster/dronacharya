"""Core domain models. The OSS build is single-tenant: tenant_id is always LOCAL_TENANT."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum

LOCAL_TENANT = "local"


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SourceType(StrEnum):
    WEB = "web"
    TDL = "tdl"
    MARKDOWN = "markdown"
    TEXT = "text"
    PDF = "pdf"
    MINDMAP = "mindmap"  # web-UI-authored mind map; nodes are corpus units
    TODO = "todo"        # to-do reminder (extension / future mobile); syncs + searchable
    NOTE = "note"        # direct note (web/extension/CLI); editor-owned like mindmaps


class UnitKind(StrEnum):
    FACT = "fact"
    CONCEPT = "concept"
    HOWTO = "howto"
    NOTE = "note"        # user-authored content (notes, PDFs) — stored as-is, chunked
    EXCERPT = "excerpt"  # extractive fallback until real distillation upgrades it


@dataclass
class Document:
    source_type: str
    title: str
    id: str = field(default_factory=new_id)
    tenant_id: str = LOCAL_TENANT
    url: str | None = None
    file_path: str | None = None
    saved_note: str | None = None
    summary: str | None = None
    content_hash: str = ""
    distilled: bool = False
    distill_tier: str | None = None
    lang: str | None = None
    version: int = 1
    origin_device: str | None = None
    meta: dict = field(default_factory=dict)
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)


@dataclass
class KnowledgeUnit:
    document_id: str
    seq: int
    text: str
    kind: str = UnitKind.NOTE
    id: str = field(default_factory=new_id)
    tenant_id: str = LOCAL_TENANT
    heading_path: str | None = None
    lang: str | None = None


def unit_index_text(unit) -> str:
    """What gets EMBEDDED and FTS-INDEXED for a unit. Heading context makes
    'second step' findable as part of 'Project > Deploy > second step' —
    without it, TDL task hierarchies, docx section titles, and sheet names
    were invisible to retrieval. Stored/displayed text stays verbatim."""
    if unit.heading_path:
        return f"{unit.heading_path} — {unit.text}"
    return unit.text


def doc_capabilities(source_type: str) -> dict:
    """Server-declared per-document behavior so no client hardcodes
    source_type checks: whether units are editable in place, and which
    dedicated editor (if any) owns the document."""
    editor = source_type if source_type in ("mindmap", "todo", "note") else None
    return {"editable_units": editor is None, "editor": editor}


@dataclass
class SearchResult:
    unit: KnowledgeUnit
    document: Document
    score: float


@dataclass
class SaveOutcome:
    """Result of a save attempt (dedup/consent semantics — see ARCHITECTURE.md)."""

    status: str  # created | updated | unchanged | needs_consent | blocked
    document_id: str | None = None
    message: str = ""
    old_summary: str | None = None
    new_summary: str | None = None
