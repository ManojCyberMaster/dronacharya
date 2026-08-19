"""Mind maps — user-authored maps whose every node lands in the knowledge corpus.

A mind map is stored as a normal document (source_type="mindmap") with the
full mind-elixir map JSON in `meta["mindmap"]`. Each node becomes one
knowledge unit whose text is the node's full path ("Homelab > Proxmox >
DGX Spark: ..."), so map knowledge is searchable, syncs to every device,
and follows the same export/wipe/data-rights machinery as everything else.
User-authored content is stored as-is (kind='note') — the distillation /
no-original-content rules apply only to web captures.

Tags are first-class: node tags and the optional map-level tag(s)
(`data["dcTag"]`) become the document's tags — the SAME tag namespace the
whole app uses, so the Tags page, search filters and the library all see
them. `meta["tag_nodes"]` records which node paths carry which tag, so tag
browsing can show "MindMap:<Name> > <node path>". Per-node notes
(`node["dcNote"]`, rich-text HTML) are stripped to plain text and included
in the node's knowledge unit — notes are searchable too.
"""

from __future__ import annotations

import hashlib
import json
import re

from .models import Document, KnowledgeUnit, SourceType, UnitKind, unit_index_text

MAX_NODES = 2000
NOTE_CHARS = 800          # how much of a node note enters the searchable unit

_HTML_TAG = re.compile(r"<[^>]+>")
_ENTITIES = {"&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">",
             "&quot;": '"', "&#39;": "'"}


def note_text(html: str) -> str:
    """Rich-text note HTML → plain text (notes are text-only by design).
    Tags are stripped FIRST, then entities decoded exactly once with the
    stdlib — a hand-rolled entity loop double-decoded &amp;lt; into <."""
    import html as html_mod

    text = _HTML_TAG.sub(" ", str(html or ""))
    return " ".join(html_mod.unescape(text).split())


def _walk(node: dict, path: list[str], out: list[tuple[str, str]],
          tag_nodes: dict[str, list[str]]) -> None:
    if not isinstance(node, dict) or len(out) >= MAX_NODES:
        return
    topic = str(node.get("topic", "")).strip()
    here = path + ([topic] if topic else [])
    if topic:
        full_path = " > ".join(here)
        text = full_path
        link = str(node.get("hyperLink", "") or "").strip()
        if link:
            text += f" ({link})"
        tags = [str(t).strip() for t in (node.get("tags") or []) if str(t).strip()]
        if tags:
            text += " [" + ", ".join(tags) + "]"
            for t in tags:
                tag_nodes.setdefault(t, []).append(full_path)
        note = note_text(node.get("dcNote", ""))
        if note:
            text += f" — note: {note[:NOTE_CHARS]}"
        out.append((text, " > ".join(path) if path else ""))
    for child in node.get("children") or []:
        _walk(child, here, out, tag_nodes)


def map_tag_nodes(data: dict) -> tuple[list[tuple[str, str]], dict[str, list[str]]]:
    """(unit text, heading) pairs + {tag: [node paths]} for the whole map."""
    pairs: list[tuple[str, str]] = []
    tag_nodes: dict[str, list[str]] = {}
    _walk(data.get("nodeData") or {}, [], pairs, tag_nodes)
    return pairs, tag_nodes


def map_level_tags(data: dict) -> list[str]:
    """Optional map-level tag(s): `data["dcTag"]`, comma-separated."""
    return [t.strip() for t in str(data.get("dcTag") or "").split(",") if t.strip()]


def units_from_map(document_id: str, data: dict) -> list[KnowledgeUnit]:
    """One unit per node; text = full node path for self-contained retrieval."""
    pairs, _ = map_tag_nodes(data)
    return [
        KnowledgeUnit(document_id=document_id, seq=i, text=text,
                      kind=UnitKind.NOTE, heading_path=heading or None)
        for i, (text, heading) in enumerate(pairs)
    ]


def map_title(data: dict, fallback: str = "Untitled map") -> str:
    return str((data.get("nodeData") or {}).get("topic") or fallback).strip()[:200]


def save_mindmap(repo, embedder, data: dict, *, document_id: str | None = None,
                 title: str | None = None) -> Document:
    """Create or update a mind-map document (nodes re-derived + re-embedded)."""
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False)
    content_hash = hashlib.sha256(canonical.encode()).hexdigest()

    existing = repo.get_document(document_id) if document_id else None
    doc = existing or Document(source_type=SourceType.MINDMAP,
                               title=title or map_title(data))
    doc.title = title or map_title(data, fallback=doc.title)
    doc.content_hash = content_hash
    doc.distilled = True                 # user content IS the knowledge
    doc.distill_tier = "user-content"
    pairs, tag_nodes = map_tag_nodes(data)
    doc.meta = {**(doc.meta or {}), "mindmap": data,
                "tag_nodes": {t: paths[:20] for t, paths in tag_nodes.items()}}
    units = [
        KnowledgeUnit(document_id=doc.id, seq=i, text=text,
                      kind=UnitKind.NOTE, heading_path=heading or None)
        for i, (text, heading) in enumerate(pairs)
    ]
    doc.summary = units[0].text[:300] if units else None

    embeddings = embedder.embed_passages([unit_index_text(u) for u in units])
    if existing:
        repo.replace_document(doc, units, embeddings)
    else:
        repo.insert_document(doc, units, embeddings)
    # node tags + optional map-level tag → the app-wide tag namespace
    tags = map_level_tags(data)
    tags += [t for t in tag_nodes if t not in tags]
    # part of this save: the insert/replace above already ticked the version
    repo.set_tags(doc.id, tags, bump_version=False)   # no-ops when unchanged
    repo.log_event("mindmap_saved", {"document_id": doc.id, "nodes": len(units)})
    return doc
