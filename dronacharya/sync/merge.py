"""Sync merge & conflict resolution (single-user scope).

Rules (explicit, never silent):
  - deletions win on the LOGICAL clock (data-deletion guarantee across
    devices). A tombstone records the version it buried; only a peer that
    genuinely advanced the document past that version may bring it back. A
    peer's wall clock can never resurrect a deleted document — clocks disagree
    between devices (a WSL client that slept runs minutes ahead) and a delete
    must not be undone by skew. A refused copy is recorded in sync_conflicts,
    restorable — never dropped silently. Tombstones predating this rule carry
    version 0 and block unconditionally
  - a tombstone is cleared the moment the document exists again (insert /
    replace_document do it): a live document must never carry one, or every
    later op on it would be broadcast to peers as a delete
  - knowledge content: the higher per-document VERSION counter wins (a
    logical clock — immune to wall-clock skew between devices); equal
    versions fall back to updated_at with a tie guard. The losing version is
    recorded in sync_conflicts (restorable), never dropped silently
  - embeddings never travel: sync ships text + metadata only, the receiving
    node re-embeds with ITS model (peers may run different embedding models;
    payloads stay small enough for thin/mobile clients)
  - tags travel with the document version: the winning side's tag set is taken
    verbatim (so a REMOVED tag stays removed), and only a genuine version tie
    falls back to a union
  - near-tie (< 2s) clock guard: the preferred side wins (server prefers its
    local copy; a client applying server ops prefers the incoming/server copy)
  - two devices saving the same URL *or the same file path* offline (different
    uuids): resolved by the same LWW rule; the losing document is tombstoned
    and recorded. Both are UNIQUE in the schema — an unresolved collision
    raises on insert and wedges the whole sync batch permanently
  - one bad op never wedges the batch: ops are applied independently and a
    failure is rolled back, counted and reported, not retried forever

Applied ops are written to the local oplog with the sync source as origin, so
they are never echoed back to where they came from.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from ..models import Document, KnowledgeUnit, unit_index_text

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
        # A LIVE document is never broadcast as a delete, whatever its tombstone
        # history says. Trusting a stale tombstone here meant that a document
        # which legitimately came back (a newer peer edit, `dc conflicts
        # --restore`) had every subsequent edit pushed to every device as a
        # DELETE — silently destroying it everywhere, forever.
        bundle = None if kind == "delete" else repo.get_bundle(entity_id)
        if bundle is None:
            tombstone = repo.get_deletion(entity_id)
            if tombstone:
                ops.append({"op": "delete", "entity_id": entity_id,
                            "deleted_at": tombstone})
            # gone with no tombstone: nothing to say about it (never invent a
            # delete for a document that merely failed to load)
            continue
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
    summary = {"applied": 0, "skipped": 0, "conflicts": 0, "deleted": 0,
               "failed": 0}
    previous_origin = repo.sync_origin
    repo.sync_origin = origin
    try:
        for op in ops:
            try:
                _apply_one(repo, op, summary, prefer_local_on_tie=prefer_local_on_tie,
                           pending_ids=pending_ids, embedder=embedder)
            except Exception:  # noqa: BLE001 — one bad op must not wedge the batch
                # Without this a single unresolvable op (e.g. a UNIQUE collision)
                # failed the whole push forever: the cursor never advanced, so
                # the identical batch was retried on every future sync, silently.
                repo.rollback()
                summary["failed"] += 1
    finally:
        repo.sync_origin = previous_origin
    return summary


def _resolve_duplicate(repo, op: dict, incoming, summary: dict,
                       other, rule: str, *, prefer_incoming_on_tie: bool) -> bool:
    """Same knowledge saved independently on two devices under different uuids.
    Both `url` and `file_path` are UNIQUE per tenant, so an unresolved collision
    raises on insert. Returns True if the incoming op should be abandoned."""
    if other is None or other.id == incoming.id:
        return False
    if _newer(incoming.updated_at, other.updated_at, prefer_incoming_on_tie):
        repo.record_conflict(incoming.id, repo.get_bundle(other.id) or {}, rule)
        repo.delete_document(other.id)
        return False
    repo.record_conflict(other.id, op, rule)
    summary["conflicts"] += 1
    return True


def _apply_one(repo, op: dict, summary: dict, *, prefer_local_on_tie: bool,
               pending_ids: set[str] | None, embedder) -> None:
    if op["op"] == "delete":
        if repo.get_document(op["entity_id"]) is not None:
            # a delete landing on top of un-pushed local edits is a real
            # conflict: keep the losing copy, never drop it silently
            if pending_ids is not None and op["entity_id"] in pending_ids:
                repo.record_conflict(op["entity_id"],
                                     repo.get_bundle(op["entity_id"]) or {},
                                     "deleted_remotely")
                summary["conflicts"] += 1
            repo.delete_document(op["entity_id"])
            summary["deleted"] += 1
        else:
            summary["skipped"] += 1
        return

    incoming = _doc_from_payload(op["doc"])
    units, embeddings = _units_from_payload(op["units"])
    if units and not any(embeddings) and embedder is not None:
        # modern peers ship no vectors — derive them locally
        embeddings = embedder.embed_passages([unit_index_text(u) for u in units])
    incoming_tags = op.get("tags") or []

    # Deletions win on the LOGICAL clock. This used to compare the tombstone
    # against the peer's updated_at, so any clock skew (a suspended WSL client
    # is routinely minutes ahead) silently resurrected a deleted document with
    # its original id — a wall clock cannot be trusted to overrule a delete.
    # Only a peer that genuinely advanced the document PAST the version we
    # buried may bring it back; a peer merely echoing the version we deleted
    # cannot. Its copy is kept as a restorable conflict, never dropped silently.
    tomb = repo.get_deletion_record(incoming.id)
    if tomb is not None:
        _, buried_version = tomb
        if buried_version <= 0 or incoming.version <= buried_version:
            repo.record_conflict(incoming.id, op, "deleted_remotely")
            summary["conflicts"] += 1
            summary["skipped"] += 1
            return

    local = repo.get_document(incoming.id)
    prefer_incoming = not prefer_local_on_tie
    if local is None:
        # same knowledge, different uuids — resolve before insert, or the
        # UNIQUE(tenant_id, url) / UNIQUE(tenant_id, file_path) index raises
        if incoming.url and _resolve_duplicate(
                repo, op, incoming, summary, repo.get_document_by_url(incoming.url),
                "url_duplicate_lww", prefer_incoming_on_tie=prefer_incoming):
            return
        if incoming.file_path and _resolve_duplicate(
                repo, op, incoming, summary,
                repo.get_document_by_path(incoming.file_path),
                "path_duplicate_lww", prefer_incoming_on_tie=prefer_incoming):
            return
        repo.insert_document(incoming, units, embeddings)
        if incoming_tags:
            repo.set_tags(incoming.id, incoming_tags, bump_version=False)
        summary["applied"] += 1
        return

    local_tags = repo.get_tags(local.id)
    if local.version == incoming.version and local.updated_at == incoming.updated_at:
        # identical state: no logical clock to separate the tag sets, so union
        # is the only safe answer here
        merged = sorted(set(local_tags) | set(incoming_tags))
        if merged != local_tags:
            repo.set_tags(local.id, merged, bump_version=False)
        summary["skipped"] += 1
        return

    divergent = pending_ids is None or incoming.id in pending_ids
    if _doc_wins(incoming, local, prefer_incoming):
        if divergent:
            repo.record_conflict(local.id, repo.get_bundle(local.id) or {},
                                 "content_lww")
            summary["conflicts"] += 1
        repo.replace_document(incoming, units, embeddings, bump_version=False)
        # the winner's tag set wins too. Unioning here made tag REMOVAL
        # impossible: a tag deleted on one device was re-added by the first
        # merge with any peer still holding it, and the re-add propagated back.
        repo.set_tags(incoming.id, sorted(set(incoming_tags)), bump_version=False)
        summary["applied"] += 1
    else:
        if divergent:
            repo.record_conflict(local.id, op, "content_lww")
            summary["conflicts"] += 1
        summary["skipped"] += 1   # local won: its tag set already stands
