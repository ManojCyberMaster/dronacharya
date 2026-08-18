"""Sync merge & conflict resolution (single-user scope).

Rules (explicit, never silent):
  - deletions always win (data-deletion guarantee across devices)
  - knowledge content: the higher per-document VERSION counter wins (a
    logical clock — immune to wall-clock skew between devices); equal
    versions fall back to updated_at with a tie guard. The losing version is
    recorded in sync_conflicts (restorable), never dropped silently
  - embeddings never travel: sync ships text + metadata only, the receiving
    node re-embeds with ITS model (peers may run different embedding models;
    payloads stay small enough for thin/mobile clients)
  - tags: union of both sides
  - near-tie (< 2s) clock guard: the preferred side wins (server prefers its
    local copy; a client applying server ops prefers the incoming/server copy)
  - two devices saving the same URL offline (different uuids): resolved by the
    same LWW rule; the losing document is tombstoned and recorded

Applied ops are written to the local oplog with the sync source as origin, so
they are never echoed back to where they came from.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from ..models import Document, KnowledgeUnit

TIE_SECONDS = 2.0


def _ts(value: str) -> float:
    try:
        return datetime.fromisoformat(value).timestamp()
    except (ValueError, TypeError):
        return 0.0


def _newer(a: str, b: str, prefer_first_on_tie: bool) -> bool:
    """True if a is the winner over b."""
    delta = _ts(a) - _ts(b)
    if abs(delta) < TIE_SECONDS:
        return prefer_first_on_tie
    return delta > 0


def _doc_wins(incoming, local, prefer_incoming_on_tie: bool) -> bool:
    """Version counter first (logical clock, clock-skew immune), wall time
    only to break version ties."""
    if incoming.version != local.version:
        return incoming.version > local.version
    return _newer(incoming.updated_at, local.updated_at, prefer_incoming_on_tie)


def _doc_from_payload(data: dict) -> Document:
    fields = {k: v for k, v in data.items() if k in Document.__dataclass_fields__}
    return Document(**fields)


def _units_from_payload(units: list[dict]) -> tuple[list[KnowledgeUnit], list[list[float]]]:
    import math

    from ..config import EMBEDDING_DIM

    out_units, out_embs = [], []
    for u in units:
        fields = {k: v for k, v in u.items()
                  if k in KnowledgeUnit.__dataclass_fields__}
        out_units.append(KnowledgeUnit(**fields))
        emb = u.get("embedding") or []
        # never index a malformed vector from a sync peer: wrong dimension or
        # non-finite floats corrupt nearest-neighbor search for everything
        if emb and (len(emb) != EMBEDDING_DIM
                    or not all(isinstance(x, (int, float)) and math.isfinite(x)
                               for x in emb)):
            emb = []
        out_embs.append(emb)
    return out_units, out_embs


def collect_ops(repo, since_seq: int, *, local_only: bool = False,
                exclude_origin: str | None = None) -> tuple[list[dict], int]:
    """Assemble sync ops from the oplog: one op per document, latest state wins.
    Returns (ops, latest_seq_covered). Embeddings are STRIPPED from the
    payload — they are derived artifacts, rebuilt by the receiver."""
    rows = repo.oplog_since(since_seq, local_only=local_only,
                            exclude_origin=exclude_origin)
    latest = since_seq
    per_doc: dict[str, str] = {}  # entity_id -> last op kind seen
    for seq, entity, entity_id, op in rows:
        latest = max(latest, seq)
        if entity in ("document", "tags"):
            per_doc[entity_id] = "delete" if op == "delete" else "upsert"
        elif entity == "tenant" and op == "wipe":
            pass  # per-document deletes were logged alongside
    ops: list[dict] = []
    for entity_id, kind in per_doc.items():
        if kind == "delete" or repo.get_deletion(entity_id):
            ops.append({"op": "delete", "entity_id": entity_id,
                        "deleted_at": repo.get_deletion(entity_id) or ""})
        else:
            bundle = repo.get_bundle(entity_id)
            if bundle is not None:
                for u in bundle.get("units", []):
                    u.pop("embedding", None)
                ops.append({"op": "upsert", **bundle})
    return ops, latest


def apply_ops(repo, ops: Iterable[dict], *, origin: str,
              prefer_local_on_tie: bool,
              pending_ids: set[str] | None = None,
              embedder=None) -> dict:
    """Apply remote ops to a repo. Returns summary counts.

    pending_ids: document ids with local changes not yet pushed. When given
    (client pulling), only those count as genuine conflicts — everything else
    is a normal fast-forward (e.g. the server's distillation upgrade) and is
    applied without a conflict record. When None (server applying a push),
    any divergence is real divergence between devices.
    """
    summary = {"applied": 0, "skipped": 0, "conflicts": 0, "deleted": 0}
    previous_origin = repo.sync_origin
    repo.sync_origin = origin
    try:
        for op in ops:
            if op["op"] == "delete":
                if repo.get_document(op["entity_id"]) is not None:
                    repo.delete_document(op["entity_id"])
                    summary["deleted"] += 1
                else:
                    summary["skipped"] += 1
                continue

            incoming = _doc_from_payload(op["doc"])
            units, embeddings = _units_from_payload(op["units"])
            if units and not any(embeddings) and embedder is not None:
                # modern peers ship no vectors — derive them locally
                embeddings = embedder.embed_passages([u.text for u in units])
            incoming_tags = op.get("tags") or []

            # deletions always win
            tombstone = repo.get_deletion(incoming.id)
            if tombstone and _ts(tombstone) >= _ts(incoming.updated_at) - TIE_SECONDS:
                summary["skipped"] += 1
                continue

            local = repo.get_document(incoming.id)
            if local is None and incoming.url:
                # same URL saved independently on two devices → same knowledge,
                # different uuids; resolve as a content conflict
                other = repo.get_document_by_url(incoming.url)
                if other is not None:
                    if _newer(incoming.updated_at, other.updated_at, not prefer_local_on_tie):
                        repo.record_conflict(incoming.id, repo.get_bundle(other.id) or {},
                                             "url_duplicate_lww")
                        repo.delete_document(other.id)
                    else:
                        repo.record_conflict(other.id, op, "url_duplicate_lww")
                        summary["conflicts"] += 1
                        continue

            if local is None:
                repo.insert_document(incoming, units, embeddings)
                if incoming_tags:
                    repo.set_tags(incoming.id, incoming_tags)
                summary["applied"] += 1
                continue

            merged_tags = sorted(set(repo.get_tags(local.id)) | set(incoming_tags))
            if local.version == incoming.version and local.updated_at == incoming.updated_at:
                # same state already — just make sure tags converge
                if merged_tags != repo.get_tags(local.id):
                    repo.set_tags(local.id, merged_tags)
                summary["skipped"] += 1
                continue

            divergent = pending_ids is None or incoming.id in pending_ids
            if _doc_wins(incoming, local, not prefer_local_on_tie):
                if divergent:
                    repo.record_conflict(local.id, repo.get_bundle(local.id) or {},
                                         "content_lww")
                    summary["conflicts"] += 1
                repo.replace_document(incoming, units, embeddings, bump_version=False)
                repo.set_tags(incoming.id, merged_tags)
                summary["applied"] += 1
            else:
                if divergent:
                    repo.record_conflict(local.id, op, "content_lww")
                    summary["conflicts"] += 1
                repo.set_tags(local.id, merged_tags)
                summary["skipped"] += 1
    finally:
        repo.sync_origin = previous_origin
    return summary
