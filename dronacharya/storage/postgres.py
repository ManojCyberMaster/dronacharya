"""Postgres implementation (home-server role): pgvector + tsvector FTS.

Same repository interface and sync semantics as SqliteRepo. Driver is pg8000
(BSD, pure Python — no copyleft obligations, no C toolchain). The commercial
service builds on this same schema (Supabase = Postgres + pgvector).
"""

from __future__ import annotations

import json
from typing import Iterable
from urllib.parse import urlparse

from ..config import EMBEDDING_DIM
from ..models import LOCAL_TENANT, Document, KnowledgeUnit, utcnow

_DOC_COLS = ("id, tenant_id, source_type, url, file_path, title, saved_note, summary,"
             " content_hash, distilled, distill_tier, lang, version, origin_device,"
             " meta, created_at, updated_at")
_UNIT_COLS = "rid, id, document_id, tenant_id, seq, text, kind, heading_path, lang"

_DDL = f"""
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS tenants (
  id TEXT PRIMARY KEY, name TEXT, created_at TEXT
);

CREATE TABLE IF NOT EXISTS devices (
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, name TEXT,
  last_push_seq BIGINT DEFAULT 0, last_pull_seq BIGINT DEFAULT 0, last_seen TEXT
);

CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  source_type TEXT NOT NULL,
  url TEXT, file_path TEXT, title TEXT, saved_note TEXT,
  summary TEXT,
  content_hash TEXT NOT NULL DEFAULT '',
  distilled BOOLEAN NOT NULL DEFAULT FALSE,
  distill_tier TEXT, lang TEXT,
  version INTEGER NOT NULL DEFAULT 1,
  origin_device TEXT, meta TEXT NOT NULL DEFAULT '{{}}',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_doc_url ON documents(tenant_id, url)
  WHERE url IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_doc_path ON documents(tenant_id, file_path)
  WHERE file_path IS NOT NULL;

CREATE TABLE IF NOT EXISTS knowledge_units (
  rid BIGSERIAL PRIMARY KEY,
  id TEXT NOT NULL UNIQUE,
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  tenant_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  text TEXT NOT NULL,
  kind TEXT NOT NULL,
  heading_path TEXT, lang TEXT,
  embedding vector({EMBEDDING_DIM}),
  tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED
);
CREATE INDEX IF NOT EXISTS idx_units_doc ON knowledge_units(document_id);
CREATE INDEX IF NOT EXISTS idx_units_tsv ON knowledge_units USING GIN (tsv);
CREATE INDEX IF NOT EXISTS idx_units_vec ON knowledge_units
  USING hnsw (embedding vector_l2_ops);

CREATE TABLE IF NOT EXISTS tags (
  id BIGSERIAL PRIMARY KEY, tenant_id TEXT NOT NULL, name TEXT NOT NULL,
  UNIQUE (tenant_id, name)
);
CREATE TABLE IF NOT EXISTS document_tags (
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  tag_id BIGINT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  PRIMARY KEY (document_id, tag_id)
);

CREATE TABLE IF NOT EXISTS events (
  id BIGSERIAL PRIMARY KEY, tenant_id TEXT NOT NULL,
  type TEXT NOT NULL, meta TEXT NOT NULL DEFAULT '{{}}', created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS oplog (
  seq BIGSERIAL PRIMARY KEY, tenant_id TEXT NOT NULL,
  entity TEXT NOT NULL, entity_id TEXT NOT NULL, op TEXT NOT NULL,
  origin TEXT NOT NULL DEFAULT 'local', updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deletions (
  tenant_id TEXT NOT NULL, entity TEXT NOT NULL, entity_id TEXT NOT NULL,
  deleted_at TEXT NOT NULL, PRIMARY KEY (tenant_id, entity, entity_id)
);

CREATE TABLE IF NOT EXISTS sync_conflicts (
  id BIGSERIAL PRIMARY KEY, tenant_id TEXT NOT NULL, document_id TEXT NOT NULL,
  losing_payload TEXT NOT NULL, rule TEXT NOT NULL, resolved_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_state (
  file_path TEXT PRIMARY KEY, mtime DOUBLE PRECISION NOT NULL,
  content_hash TEXT NOT NULL, last_synced TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kb_meta (
  key TEXT PRIMARY KEY, value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_tokens (
  id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE, scopes TEXT NOT NULL,
  created_at TEXT NOT NULL, last_used TEXT, revoked BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS jobs (
  id BIGSERIAL PRIMARY KEY, kind TEXT NOT NULL, key TEXT NOT NULL,
  payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
  error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_key ON jobs (kind, key, id);
"""


def _vec(embedding: list[float]) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in embedding) + "]"


def _parse_vec(value) -> list[float]:
    if value is None:
        return []          # vector-less unit (awaiting reembed)
    if isinstance(value, (list, tuple)):
        return list(value)
    return [float(x) for x in str(value).strip("[]").split(",") if x]


def _tsquery(query: str) -> str:
    terms = ["".join(c for c in t if c.isalnum()) for t in query.split()]
    return " | ".join(t for t in terms if t)


_MIGRATED_DSNS: set[str] = set()   # migrate once per process, not per request
_POOL: dict[str, list] = {}        # dsn -> idle connections (per process)
_POOL_MAX = 8
_POOL_LOCK = __import__("threading").Lock()


def _pool_get(dsn: str):
    with _POOL_LOCK:
        idle = _POOL.get(dsn) or []
        while idle:
            conn = idle.pop()
            try:
                cur = conn.cursor()
                cur.execute("SELECT 1")
                cur.fetchone()
                return conn
            except Exception:  # noqa: BLE001 — stale connection: discard
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
    return None


def _pool_put(dsn: str, conn) -> bool:
    with _POOL_LOCK:
        idle = _POOL.setdefault(dsn, [])
        if len(idle) < _POOL_MAX:
            idle.append(conn)
            return True
    return False


class PostgresRepo:
    sync_origin: str = "local"

    def __init__(self, dsn: str):
        import pg8000.dbapi

        self._dsn = dsn
        pooled = _pool_get(dsn)
        if pooled is not None:
            self.conn = pooled
        else:
            parsed = urlparse(dsn)
            self.conn = pg8000.dbapi.connect(
                user=parsed.username or "dronacharya",
                password=parsed.password or "",
                host=parsed.hostname or "localhost",
                port=parsed.port or 5432,
                database=(parsed.path or "/dronacharya").lstrip("/"),
            )
        if dsn not in _MIGRATED_DSNS:
            self._migrate()
            _MIGRATED_DSNS.add(dsn)

    def _migrate(self) -> None:
        cur = self.conn.cursor()
        cur.execute(_DDL)
        cur.execute("SELECT version FROM schema_version")
        if cur.fetchone() is None:
            cur.execute("INSERT INTO schema_version (version) VALUES (1)")
        cur.execute(
            "INSERT INTO tenants (id, name, created_at) VALUES (%s,%s,%s)"
            " ON CONFLICT (id) DO NOTHING",
            (LOCAL_TENANT, "Local", utcnow()),
        )
        self.conn.commit()

    # ------------------------------------------------------------------ util
    def _oplog(self, cur, entity: str, entity_id: str, op: str) -> None:
        cur.execute(
            "INSERT INTO oplog (tenant_id, entity, entity_id, op, origin, updated_at)"
            " VALUES (%s,%s,%s,%s,%s,%s)",
            (LOCAL_TENANT, entity, entity_id, op, self.sync_origin, utcnow()),
        )

    @staticmethod
    def _tuple_to_doc(row) -> Document:
        (doc_id, tenant_id, source_type, url, file_path, title, saved_note, summary,
         content_hash, distilled, distill_tier, lang, version, origin_device,
         meta, created_at, updated_at) = row
        return Document(
            id=doc_id, tenant_id=tenant_id, source_type=source_type, url=url,
            file_path=file_path, title=title or "", saved_note=saved_note,
            summary=summary, content_hash=content_hash, distilled=bool(distilled),
            distill_tier=distill_tier, lang=lang, version=version,
            origin_device=origin_device, meta=json.loads(meta or "{}"),
            created_at=created_at, updated_at=updated_at,
        )

    @staticmethod
    def _tuple_to_unit(row) -> tuple[int, KnowledgeUnit]:
        (rid, unit_id, document_id, tenant_id, seq, text, kind, heading_path, lang) = row
        return rid, KnowledgeUnit(
            id=unit_id, document_id=document_id, tenant_id=tenant_id, seq=seq,
            text=text, kind=kind, heading_path=heading_path, lang=lang,
        )

    def _fetch_doc(self, where: str, params: tuple) -> Document | None:
        cur = self.conn.cursor()
        cur.execute(f"SELECT {_DOC_COLS} FROM documents WHERE {where}", params)
        row = cur.fetchone()
        return self._tuple_to_doc(row) if row else None

    # ------------------------------------------------------------- documents
    def get_document(self, document_id: str) -> Document | None:
        return self._fetch_doc("id = %s", (document_id,))

    def get_document_by_url(self, url: str) -> Document | None:
        return self._fetch_doc("tenant_id = %s AND url = %s", (LOCAL_TENANT, url))

    def get_document_by_path(self, file_path: str) -> Document | None:
        return self._fetch_doc("tenant_id = %s AND file_path = %s",
                               (LOCAL_TENANT, file_path))

    def _insert_units(self, cur, doc: Document, units: list[KnowledgeUnit],
                      embeddings: list[list[float]]) -> None:
        for unit, emb in zip(units, embeddings, strict=True):
            cur.execute(
                "INSERT INTO knowledge_units (id, document_id, tenant_id, seq, text,"
                " kind, heading_path, lang, embedding)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,CAST(%s AS vector))",
                (unit.id, doc.id, unit.tenant_id, unit.seq, unit.text, unit.kind,
                 unit.heading_path, unit.lang,
                 _vec(emb) if emb else None),   # vector-less until reembed
            )

    def insert_document(self, doc: Document, units: list[KnowledgeUnit],
                        embeddings: list[list[float]]) -> None:
        cur = self.conn.cursor()
        cur.execute(
            f"INSERT INTO documents ({_DOC_COLS})"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (doc.id, doc.tenant_id, doc.source_type, doc.url, doc.file_path, doc.title,
             doc.saved_note, doc.summary, doc.content_hash, doc.distilled,
             doc.distill_tier, doc.lang, doc.version, doc.origin_device,
             json.dumps(doc.meta), doc.created_at, doc.updated_at),
        )
        self._insert_units(cur, doc, units, embeddings)
        self._oplog(cur, "document", doc.id, "upsert")
        self.conn.commit()

    def replace_document(self, doc: Document, units: list[KnowledgeUnit],
                         embeddings: list[list[float]], *, bump_version: bool = True) -> None:
        cur = self.conn.cursor()
        if bump_version:
            doc.version += 1
            doc.updated_at = utcnow()
        cur.execute(
            "UPDATE documents SET title=%s, saved_note=%s, summary=%s, content_hash=%s,"
            " distilled=%s, distill_tier=%s, lang=%s, version=%s, meta=%s, updated_at=%s"
            " WHERE id=%s",
            (doc.title, doc.saved_note, doc.summary, doc.content_hash, doc.distilled,
             doc.distill_tier, doc.lang, doc.version, json.dumps(doc.meta),
             doc.updated_at, doc.id),
        )
        cur.execute("DELETE FROM knowledge_units WHERE document_id = %s", (doc.id,))
        self._insert_units(cur, doc, units, embeddings)
        self._oplog(cur, "document", doc.id, "upsert")
        self.conn.commit()

    def update_document_meta(self, document_id: str, *, title: str | None = None,
                             saved_note: str | None = None,
                             summary: str | None = None) -> None:
        cur = self.conn.cursor()
        if title is not None:
            cur.execute("UPDATE documents SET title=%s WHERE id=%s", (title, document_id))
        if saved_note is not None:
            cur.execute("UPDATE documents SET saved_note=%s WHERE id=%s",
                        (saved_note, document_id))
        if summary is not None:
            cur.execute("UPDATE documents SET summary=%s WHERE id=%s",
                        (summary, document_id))
        cur.execute("UPDATE documents SET version = version + 1, updated_at = %s"
                    " WHERE id = %s", (utcnow(), document_id))
        self._oplog(cur, "document", document_id, "upsert")
        self.conn.commit()

    def delete_document(self, document_id: str) -> bool:
        cur = self.conn.cursor()
        cur.execute("SELECT 1 FROM documents WHERE id=%s", (document_id,))
        if cur.fetchone() is None:
            return False
        cur.execute("DELETE FROM documents WHERE id=%s", (document_id,))
        cur.execute(
            "INSERT INTO deletions (tenant_id, entity, entity_id, deleted_at)"
            " VALUES (%s,%s,%s,%s) ON CONFLICT (tenant_id, entity, entity_id)"
            " DO UPDATE SET deleted_at = EXCLUDED.deleted_at",
            (LOCAL_TENANT, "document", document_id, utcnow()),
        )
        self._oplog(cur, "document", document_id, "delete")
        self.conn.commit()
        return True

    def list_documents(self, *, source_type: str | None = None, tag: str | None = None,
                       limit: int = 50, offset: int = 0) -> list[Document]:
        cur = self.conn.cursor()
        sql = f"SELECT DISTINCT {', '.join('d.' + c.strip() for c in _DOC_COLS.split(','))}" \
              " FROM documents d"
        where = ["d.tenant_id = %s"]
        params: list = [LOCAL_TENANT]
        if tag:
            sql += (" JOIN document_tags dt ON dt.document_id = d.id"
                    " JOIN tags t ON t.id = dt.tag_id")
            where.append("(LOWER(t.name) = LOWER(%s)"
                         " OR LOWER(t.name) LIKE LOWER(%s) || '/%%')")
            params += [tag, tag]
        if source_type:
            where.append("d.source_type = %s")
            params.append(source_type)
        sql += (" WHERE " + " AND ".join(where)
                + " ORDER BY d.updated_at DESC LIMIT %s OFFSET %s")
        params += [limit, offset]
        cur.execute(sql, params)
        return [self._tuple_to_doc(r) for r in cur.fetchall()]

    def iter_documents_with_units(self) -> Iterable[tuple[Document, list[KnowledgeUnit]]]:
        cur = self.conn.cursor()
        cur.execute(f"SELECT {_DOC_COLS} FROM documents WHERE tenant_id = %s"
                    " ORDER BY created_at", (LOCAL_TENANT,))
        docs = [self._tuple_to_doc(r) for r in cur.fetchall()]
        for doc in docs:
            cur.execute(f"SELECT {_UNIT_COLS} FROM knowledge_units"
                        " WHERE document_id = %s ORDER BY seq", (doc.id,))
            yield doc, [self._tuple_to_unit(r)[1] for r in cur.fetchall()]

    # ------------------------------------------------------ search primitives
    def fts_candidates(self, query: str, limit: int) -> list[tuple[int, float]]:
        q = _tsquery(query)
        if not q:
            return []
        cur = self.conn.cursor()
        cur.execute(
            "SELECT rid, ts_rank(tsv, to_tsquery('simple', %s)) AS r"
            " FROM knowledge_units WHERE tsv @@ to_tsquery('simple', %s)"
            " ORDER BY r DESC LIMIT %s",
            (q, q, limit),
        )
        # convention: lower is better (bm25-style) — negate the rank
        return [(r[0], -float(r[1])) for r in cur.fetchall()]

    def vec_candidates(self, embedding: list[float], limit: int) -> list[tuple[int, float]]:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT rid, embedding <-> CAST(%s AS vector) AS d FROM knowledge_units"
            " ORDER BY d LIMIT %s",
            (_vec(embedding), limit),
        )
        return [(r[0], float(r[1])) for r in cur.fetchall()]

    def fetch_units(self, rids: list[int]) -> list[tuple[int, KnowledgeUnit, Document]]:
        if not rids:
            return []
        cur = self.conn.cursor()
        placeholders = ",".join(["%s"] * len(rids))
        cur.execute(f"SELECT {_UNIT_COLS} FROM knowledge_units"
                    f" WHERE rid IN ({placeholders})", rids)
        rows = [self._tuple_to_unit(r) for r in cur.fetchall()]
        docs: dict[str, Document] = {}
        out = []
        for rid, unit in rows:
            if unit.document_id not in docs:
                docs[unit.document_id] = self.get_document(unit.document_id)  # type: ignore
            out.append((rid, unit, docs[unit.document_id]))
        return out

    # ------------------------------------------------------------------- tags
    def set_tags(self, document_id: str, tags: list[str]) -> None:
        cur = self.conn.cursor()
        cur.execute("DELETE FROM document_tags WHERE document_id = %s", (document_id,))
        for name in {t.strip() for t in tags if t.strip()}:
            cur.execute(
                "INSERT INTO tags (tenant_id, name) VALUES (%s,%s)"
                " ON CONFLICT (tenant_id, name) DO NOTHING",
                (LOCAL_TENANT, name),
            )
            cur.execute("SELECT id FROM tags WHERE tenant_id=%s AND LOWER(name)=LOWER(%s)",
                        (LOCAL_TENANT, name))
            tag_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO document_tags (document_id, tag_id) VALUES (%s,%s)"
                " ON CONFLICT DO NOTHING",
                (document_id, tag_id),
            )
        self._oplog(cur, "tags", document_id, "upsert")
        self.conn.commit()

    def get_tags(self, document_id: str) -> list[str]:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT t.name FROM tags t JOIN document_tags dt ON dt.tag_id = t.id"
            " WHERE dt.document_id = %s ORDER BY t.name",
            (document_id,),
        )
        return [r[0] for r in cur.fetchall()]

    def document_ids_for_tags(self, tags: list[str]) -> set[str]:
        out: set[str] = set()
        cur = self.conn.cursor()
        for tag in tags:
            cur.execute(
                "SELECT dt.document_id FROM document_tags dt JOIN tags t ON t.id = dt.tag_id"
                " WHERE LOWER(t.name) = LOWER(%s) OR LOWER(t.name) LIKE LOWER(%s) || '/%%'",
                (tag, tag),
            )
            out.update(r[0] for r in cur.fetchall())
        return out

    def list_tags(self) -> list[tuple[str, int]]:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT t.name, COUNT(dt.document_id) FROM tags t"
            " JOIN document_tags dt ON dt.tag_id = t.id"
            " WHERE t.tenant_id = %s GROUP BY t.id, t.name"
            " ORDER BY COUNT(dt.document_id) DESC, t.name",
            (LOCAL_TENANT,),
        )
        return [(r[0], int(r[1])) for r in cur.fetchall()]

    # ------------------------------------------------------- notes bookkeeping
    def get_sync_state(self, file_path: str) -> tuple[float, str] | None:
        cur = self.conn.cursor()
        cur.execute("SELECT mtime, content_hash FROM sync_state WHERE file_path = %s",
                    (file_path,))
        row = cur.fetchone()
        return (row[0], row[1]) if row else None

    def set_sync_state(self, file_path: str, mtime: float, content_hash: str) -> None:
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO sync_state (file_path, mtime, content_hash, last_synced)"
            " VALUES (%s,%s,%s,%s) ON CONFLICT (file_path) DO UPDATE SET"
            " mtime=EXCLUDED.mtime, content_hash=EXCLUDED.content_hash,"
            " last_synced=EXCLUDED.last_synced",
            (file_path, mtime, content_hash, utcnow()),
        )
        self.conn.commit()

    # ------------------------------------------------------------------ audit
    def log_event(self, type_: str, meta: dict) -> None:
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO events (tenant_id, type, meta, created_at) VALUES (%s,%s,%s,%s)",
            (LOCAL_TENANT, type_, json.dumps(meta), utcnow()),
        )
        self.conn.commit()

    # -------------------------------------------------------------- sync ops
    def latest_seq(self) -> int:
        cur = self.conn.cursor()
        cur.execute("SELECT COALESCE(MAX(seq), 0) FROM oplog")
        return int(cur.fetchone()[0])

    def oplog_since(self, seq: int, *, exclude_origin: str | None = None,
                    local_only: bool = False) -> list[tuple[int, str, str, str]]:
        sql = "SELECT seq, entity, entity_id, op FROM oplog WHERE seq > %s"
        params: list = [seq]
        if local_only:
            sql += " AND origin = 'local'"
        if exclude_origin:
            sql += " AND origin != %s"
            params.append(exclude_origin)
        sql += " ORDER BY seq"
        cur = self.conn.cursor()
        cur.execute(sql, params)
        return [(int(r[0]), r[1], r[2], r[3]) for r in cur.fetchall()]

    def get_unit_embeddings(self, document_id: str) -> dict[str, list[float]]:
        cur = self.conn.cursor()
        cur.execute("SELECT id, embedding FROM knowledge_units WHERE document_id = %s",
                    (document_id,))
        return {r[0]: _parse_vec(r[1]) for r in cur.fetchall()}

    def get_bundle(self, document_id: str) -> dict | None:
        from dataclasses import asdict

        doc = self.get_document(document_id)
        if doc is None:
            return None
        embeddings = self.get_unit_embeddings(document_id)
        cur = self.conn.cursor()
        cur.execute(f"SELECT {_UNIT_COLS} FROM knowledge_units"
                    " WHERE document_id = %s ORDER BY seq", (document_id,))
        units = []
        for row in cur.fetchall():
            _, unit = self._tuple_to_unit(row)
            record = asdict(unit)
            record["embedding"] = embeddings.get(unit.id, [])
            units.append(record)
        return {"doc": asdict(doc), "units": units, "tags": self.get_tags(document_id)}

    def get_deletion(self, entity_id: str) -> str | None:
        cur = self.conn.cursor()
        cur.execute("SELECT deleted_at FROM deletions WHERE tenant_id=%s"
                    " AND entity='document' AND entity_id=%s", (LOCAL_TENANT, entity_id))
        row = cur.fetchone()
        return row[0] if row else None

    def record_conflict(self, document_id: str, losing_payload: dict, rule: str) -> None:
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO sync_conflicts (tenant_id, document_id, losing_payload, rule,"
            " resolved_at) VALUES (%s,%s,%s,%s,%s)",
            (LOCAL_TENANT, document_id, json.dumps(losing_payload), rule, utcnow()),
        )
        self.conn.commit()

    def list_conflicts(self) -> list[dict]:
        cur = self.conn.cursor()
        cur.execute("SELECT id, document_id, rule, resolved_at, losing_payload"
                    " FROM sync_conflicts ORDER BY id DESC LIMIT 100")
        return [{"id": int(r[0]), "document_id": r[1], "rule": r[2],
                 "resolved_at": r[3], "losing_payload": json.loads(r[4])}
                for r in cur.fetchall()]

    def device_state(self, device_id: str) -> tuple[int, int]:
        cur = self.conn.cursor()
        cur.execute("SELECT last_push_seq, last_pull_seq FROM devices WHERE id = %s",
                    (device_id,))
        row = cur.fetchone()
        return (int(row[0]), int(row[1])) if row else (0, 0)

    def device_update(self, device_id: str, *, name: str = "",
                      push_seq: int | None = None, pull_seq: int | None = None) -> None:
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO devices (id, tenant_id, name, last_seen) VALUES (%s,%s,%s,%s)"
            " ON CONFLICT (id) DO UPDATE SET last_seen = EXCLUDED.last_seen",
            (device_id, LOCAL_TENANT, name, utcnow()),
        )
        if push_seq is not None:
            cur.execute("UPDATE devices SET last_push_seq=%s WHERE id=%s",
                        (push_seq, device_id))
        if pull_seq is not None:
            cur.execute("UPDATE devices SET last_pull_seq=%s WHERE id=%s",
                        (pull_seq, device_id))
        self.conn.commit()

    # ------------------------------------------------------------ data rights


    # ----------------------------------------------------------------- kb meta
    def get_meta(self, key: str) -> str | None:
        cur = self.conn.cursor()
        cur.execute("SELECT value FROM kb_meta WHERE key=%s", (key,))
        row = cur.fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO kb_meta (key, value) VALUES (%s,%s)"
            " ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (key, value))
        self.conn.commit()

    # -------------------------------------------------------------- api tokens
    def create_token(self, name: str, scopes: list[str]) -> tuple[int, str]:
        import hashlib
        import secrets

        plaintext = secrets.token_urlsafe(32)
        digest = hashlib.sha256(plaintext.encode()).hexdigest()
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO api_tokens (name, token_hash, scopes, created_at)"
            " VALUES (%s,%s,%s,%s) RETURNING id",
            (name, digest, ",".join(sorted(set(scopes))), utcnow()))
        token_id = int(cur.fetchone()[0])
        self.conn.commit()
        return token_id, plaintext

    def list_tokens(self) -> list[dict]:
        cur = self.conn.cursor()
        cur.execute("SELECT id, name, scopes, created_at, last_used, revoked"
                    " FROM api_tokens ORDER BY id")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]

    def revoke_token(self, token_id: int) -> bool:
        cur = self.conn.cursor()
        cur.execute("UPDATE api_tokens SET revoked=TRUE WHERE id=%s", (token_id,))
        n = cur.rowcount
        self.conn.commit()
        return n > 0

    def verify_token(self, plaintext: str) -> list[str] | None:
        import hashlib

        digest = hashlib.sha256(plaintext.encode()).hexdigest()
        cur = self.conn.cursor()
        cur.execute("SELECT id, scopes FROM api_tokens"
                    " WHERE token_hash=%s AND NOT revoked", (digest,))
        row = cur.fetchone()
        if row is None:
            return None
        cur.execute("UPDATE api_tokens SET last_used=%s WHERE id=%s",
                    (utcnow(), row[0]))
        self.conn.commit()
        return row[1].split(",") if row[1] else []

    # ------------------------------------------------------------ durable jobs
    def enqueue_job(self, kind: str, key: str, payload: dict) -> int:
        import json as json_mod

        cur = self.conn.cursor()
        now = utcnow()
        cur.execute(
            "INSERT INTO jobs (kind, key, payload, status, created_at, updated_at)"
            " VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
            (kind, key, json_mod.dumps(payload), "queued", now, now))
        job_id = int(cur.fetchone()[0])
        self.conn.commit()
        return job_id

    def claim_next_job(self) -> dict | None:
        import json as json_mod

        cur = self.conn.cursor()
        cur.execute(
            "UPDATE jobs SET status='running', updated_at=%s WHERE id = ("
            "  SELECT id FROM jobs WHERE status='queued' ORDER BY id"
            "  LIMIT 1 FOR UPDATE SKIP LOCKED)"
            " RETURNING id, kind, key, payload", (utcnow(),))
        row = cur.fetchone()
        self.conn.commit()
        if row is None:
            return None
        return {"id": int(row[0]), "kind": row[1], "key": row[2],
                "payload": json_mod.loads(row[3])}

    def finish_job(self, job_id: int, error: str | None = None) -> None:
        cur = self.conn.cursor()
        cur.execute("UPDATE jobs SET status=%s, error=%s, updated_at=%s WHERE id=%s",
                    ("error" if error else "done", error, utcnow(), job_id))
        self.conn.commit()

    def requeue_stale_jobs(self) -> int:
        cur = self.conn.cursor()
        cur.execute("UPDATE jobs SET status='queued', updated_at=%s"
                    " WHERE status='running'", (utcnow(),))
        n = cur.rowcount
        self.conn.commit()
        return n

    def job_state_for_key(self, kind: str, key: str) -> tuple[str | None, str | None]:
        cur = self.conn.cursor()
        cur.execute("SELECT status, error FROM jobs WHERE kind=%s AND key=%s"
                    " ORDER BY id DESC LIMIT 1", (kind, key))
        row = cur.fetchone()
        if row is None or row[0] == "done":
            return None, None
        if row[0] == "error":
            return "error", row[1]
        return "pending", None

    def wipe(self, factory: bool = False) -> int:
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM documents WHERE tenant_id = %s", (LOCAL_TENANT,))
        n = int(cur.fetchone()[0])
        cur.execute("SELECT id FROM documents WHERE tenant_id = %s", (LOCAL_TENANT,))
        for (doc_id,) in cur.fetchall():
            cur.execute(
                "INSERT INTO deletions (tenant_id, entity, entity_id, deleted_at)"
                " VALUES (%s,%s,%s,%s) ON CONFLICT (tenant_id, entity, entity_id)"
                " DO UPDATE SET deleted_at = EXCLUDED.deleted_at",
                (LOCAL_TENANT, "document", doc_id, utcnow()),
            )
            self._oplog(cur, "document", doc_id, "delete")
        cur.execute("DELETE FROM documents WHERE tenant_id = %s", (LOCAL_TENANT,))
        cur.execute("DELETE FROM tags WHERE tenant_id = %s", (LOCAL_TENANT,))
        cur.execute("DELETE FROM sync_state")
        if factory:
            for table in ("events", "oplog", "deletions", "sync_conflicts",
                          "devices"):
                cur.execute(f"DELETE FROM {table}")   # noqa: S608 — fixed names
        else:
            self._oplog(cur, "tenant", LOCAL_TENANT, "wipe")
        self.conn.commit()
        return n

    def dump_operational(self) -> dict:
        out: dict = {}
        cur = self.conn.cursor()
        for table in ("events", "sync_conflicts", "deletions"):
            cur.execute(f"SELECT * FROM {table}")   # noqa: S608 — fixed names
            cols = [d[0] for d in cur.description]
            out[table] = [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]
        return out

    def counts(self) -> dict:
        cur = self.conn.cursor()
        out = {}
        for key, table in (("documents", "documents"), ("knowledge_units", "knowledge_units"),
                           ("tags", "tags"), ("events", "events")):
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            out[key] = int(cur.fetchone()[0])
        return out

    def close(self) -> None:
        # rollback any half-open transaction, then return to the pool
        try:
            self.conn.rollback()
        except Exception:  # noqa: BLE001 — dead connection: just drop it
            try:
                self.conn.close()
            except Exception:  # noqa: BLE001
                pass
            return
        if not _pool_put(self._dsn, self.conn):
            self.conn.close()
