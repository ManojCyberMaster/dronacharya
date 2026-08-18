"""SQLite implementation: sqlite-vec (vectors) + FTS5 (BM25) + relational schema.

Used by the standalone role and as the laptop offline cache. Knowledge units get
an integer rowid (`rid`) that keys both index tables; the uuid `id` columns are
the sync-stable identities. All mutations write to the oplog (and `deletions`
tombstones) so Phase-5 sync works without schema changes.
"""

from __future__ import annotations

import json
import sqlite3
import struct
from pathlib import Path
from typing import Iterable

from ..config import EMBEDDING_DIM
from ..models import LOCAL_TENANT, Document, KnowledgeUnit, utcnow

SCHEMA_VERSION = 1

_DDL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS tenants (
  id TEXT PRIMARY KEY, name TEXT, created_at TEXT
);

CREATE TABLE IF NOT EXISTS devices (
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, name TEXT,
  last_push_seq INTEGER DEFAULT 0, last_pull_seq INTEGER DEFAULT 0, last_seen TEXT
);

CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  source_type TEXT NOT NULL,
  url TEXT, file_path TEXT, title TEXT, saved_note TEXT,
  summary TEXT,
  content_hash TEXT NOT NULL DEFAULT '',
  distilled INTEGER NOT NULL DEFAULT 0,
  distill_tier TEXT, lang TEXT,
  version INTEGER NOT NULL DEFAULT 1,
  origin_device TEXT, meta TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_doc_url  ON documents(tenant_id, url)  WHERE url IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_doc_path ON documents(tenant_id, file_path) WHERE file_path IS NOT NULL;

CREATE TABLE IF NOT EXISTS knowledge_units (
  rid INTEGER PRIMARY KEY,
  id TEXT NOT NULL UNIQUE,
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  tenant_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  text TEXT NOT NULL,
  kind TEXT NOT NULL,
  heading_path TEXT, lang TEXT
);
CREATE INDEX IF NOT EXISTS idx_units_doc ON knowledge_units(document_id);

CREATE VIRTUAL TABLE IF NOT EXISTS units_fts USING fts5(
  text, title, tokenize='porter unicode61'
);

CREATE TABLE IF NOT EXISTS tags (
  id INTEGER PRIMARY KEY, tenant_id TEXT NOT NULL,
  name TEXT NOT NULL COLLATE NOCASE, UNIQUE(tenant_id, name)
);
CREATE TABLE IF NOT EXISTS document_tags (
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  PRIMARY KEY (document_id, tag_id)
);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY, tenant_id TEXT NOT NULL,
  type TEXT NOT NULL, meta TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS oplog (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL,
  entity TEXT NOT NULL, entity_id TEXT NOT NULL, op TEXT NOT NULL,
  origin TEXT NOT NULL DEFAULT 'local', updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deletions (
  tenant_id TEXT NOT NULL, entity TEXT NOT NULL, entity_id TEXT NOT NULL,
  deleted_at TEXT NOT NULL, PRIMARY KEY (tenant_id, entity, entity_id)
);

CREATE TABLE IF NOT EXISTS sync_conflicts (
  id INTEGER PRIMARY KEY, tenant_id TEXT NOT NULL, document_id TEXT NOT NULL,
  losing_payload TEXT NOT NULL, rule TEXT NOT NULL, resolved_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_state (
  file_path TEXT PRIMARY KEY, mtime REAL NOT NULL,
  content_hash TEXT NOT NULL, last_synced TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kb_meta (
  key TEXT PRIMARY KEY, value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_tokens (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE, scopes TEXT NOT NULL,
  created_at TEXT NOT NULL, last_used TEXT, revoked INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, key TEXT NOT NULL,
  payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
  error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_key ON jobs (kind, key, id);
"""


def _dt_ago_hours(hours: int) -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _serialize_f32(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _fts_escape(query: str) -> str:
    """Quote each term (so user input can't break FTS5 syntax) and OR them —
    natural-language questions should rank by overlap, not require every word."""
    terms = [t.replace('"', '""') for t in query.split()]
    return " OR ".join(f'"{t}"' for t in terms if t)


class SqliteRepo:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self._load_vec_extension()
        self._migrate()

    def _load_vec_extension(self) -> None:
        try:
            import sqlite_vec

            self.conn.enable_load_extension(True)
            sqlite_vec.load(self.conn)
            self.conn.enable_load_extension(False)
        except Exception as e:  # pragma: no cover
            raise RuntimeError(
                "Could not load the sqlite-vec extension. Ensure the 'sqlite-vec' "
                "package is installed and your Python build allows loadable "
                f"extensions. Underlying error: {e}"
            ) from e

    def _migrate(self) -> None:
        cur = self.conn.cursor()
        cur.executescript(_DDL)
        cur.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS units_vec USING vec0("
            f"embedding float[{EMBEDDING_DIM}])"
        )
        row = cur.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            cur.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
        # dev-era additive migration: origin column on oplog
        cols = {r[1] for r in cur.execute("PRAGMA table_info(oplog)").fetchall()}
        if "origin" not in cols:
            cur.execute("ALTER TABLE oplog ADD COLUMN origin TEXT NOT NULL DEFAULT 'local'")
        cur.execute(
            "INSERT OR IGNORE INTO tenants (id, name, created_at) VALUES (?, ?, ?)",
            (LOCAL_TENANT, "Local", utcnow()),
        )
        self.conn.commit()

    # ------------------------------------------------------------------ util
    # sync_origin marks where mutations came from: 'local' ops get pushed to a
    # server; ops applied *from* a sync source are tagged with it so they are
    # never echoed back (see sync/merge.py).
    sync_origin: str = "local"

    def _oplog(self, cur: sqlite3.Cursor, entity: str, entity_id: str, op: str) -> None:
        cur.execute(
            "INSERT INTO oplog (tenant_id, entity, entity_id, op, origin, updated_at)"
            " VALUES (?,?,?,?,?,?)",
            (LOCAL_TENANT, entity, entity_id, op, self.sync_origin, utcnow()),
        )

    @staticmethod
    def _row_to_doc(row: sqlite3.Row) -> Document:
        return Document(
            id=row["id"], tenant_id=row["tenant_id"], source_type=row["source_type"],
            url=row["url"], file_path=row["file_path"], title=row["title"] or "",
            saved_note=row["saved_note"], summary=row["summary"],
            content_hash=row["content_hash"], distilled=bool(row["distilled"]),
            distill_tier=row["distill_tier"], lang=row["lang"], version=row["version"],
            origin_device=row["origin_device"], meta=json.loads(row["meta"] or "{}"),
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_unit(row: sqlite3.Row) -> KnowledgeUnit:
        return KnowledgeUnit(
            id=row["id"], document_id=row["document_id"], tenant_id=row["tenant_id"],
            seq=row["seq"], text=row["text"], kind=row["kind"],
            heading_path=row["heading_path"], lang=row["lang"],
        )

    # ------------------------------------------------------------- documents
    def get_document(self, document_id: str) -> Document | None:
        row = self.conn.execute(
            "SELECT * FROM documents WHERE id = ?", (document_id,)
        ).fetchone()
        return self._row_to_doc(row) if row else None

    def get_document_by_url(self, url: str) -> Document | None:
        row = self.conn.execute(
            "SELECT * FROM documents WHERE tenant_id = ? AND url = ?", (LOCAL_TENANT, url)
        ).fetchone()
        return self._row_to_doc(row) if row else None

    def get_document_by_path(self, file_path: str) -> Document | None:
        row = self.conn.execute(
            "SELECT * FROM documents WHERE tenant_id = ? AND file_path = ?",
            (LOCAL_TENANT, file_path),
        ).fetchone()
        return self._row_to_doc(row) if row else None

    def _insert_units(
        self, cur: sqlite3.Cursor, doc: Document,
        units: list[KnowledgeUnit], embeddings: list[list[float]],
    ) -> None:
        for unit, emb in zip(units, embeddings, strict=True):
            cur.execute(
                "INSERT INTO knowledge_units (id, document_id, tenant_id, seq, text, kind,"
                " heading_path, lang) VALUES (?,?,?,?,?,?,?,?)",
                (unit.id, doc.id, unit.tenant_id, unit.seq, unit.text, unit.kind,
                 unit.heading_path, unit.lang),
            )
            rid = cur.lastrowid
            cur.execute(
                "INSERT INTO units_fts (rowid, text, title) VALUES (?,?,?)",
                (rid, unit.text, doc.title),
            )
            if emb:   # vector-less unit (peer shipped none, no embedder yet):
                      # FTS still finds it; `dc reembed` adds the vector later
                cur.execute(
                    "INSERT INTO units_vec (rowid, embedding) VALUES (?,?)",
                    (rid, _serialize_f32(emb)),
                )

    def _delete_units(self, cur: sqlite3.Cursor, document_id: str) -> None:
        rids = [r[0] for r in cur.execute(
            "SELECT rid FROM knowledge_units WHERE document_id = ?", (document_id,)
        ).fetchall()]
        for rid in rids:
            cur.execute("DELETE FROM units_fts WHERE rowid = ?", (rid,))
            cur.execute("DELETE FROM units_vec WHERE rowid = ?", (rid,))
        cur.execute("DELETE FROM knowledge_units WHERE document_id = ?", (document_id,))

    def insert_document(
        self, doc: Document, units: list[KnowledgeUnit], embeddings: list[list[float]]
    ) -> None:
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO documents (id, tenant_id, source_type, url, file_path, title,"
            " saved_note, summary, content_hash, distilled, distill_tier, lang, version,"
            " origin_device, meta, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (doc.id, doc.tenant_id, doc.source_type, doc.url, doc.file_path, doc.title,
             doc.saved_note, doc.summary, doc.content_hash, int(doc.distilled),
             doc.distill_tier, doc.lang, doc.version, doc.origin_device,
             json.dumps(doc.meta), doc.created_at, doc.updated_at),
        )
        self._insert_units(cur, doc, units, embeddings)
        self._oplog(cur, "document", doc.id, "upsert")
        self.conn.commit()

    def replace_document(
        self, doc: Document, units: list[KnowledgeUnit], embeddings: list[list[float]],
        *, bump_version: bool = True,
    ) -> None:
        cur = self.conn.cursor()
        if bump_version:
            doc.version += 1
            doc.updated_at = utcnow()
        cur.execute(
            "UPDATE documents SET title=?, saved_note=?, summary=?, content_hash=?,"
            " distilled=?, distill_tier=?, lang=?, version=?, meta=?, updated_at=?"
            " WHERE id=?",
            (doc.title, doc.saved_note, doc.summary, doc.content_hash, int(doc.distilled),
             doc.distill_tier, doc.lang, doc.version, json.dumps(doc.meta),
             doc.updated_at, doc.id),
        )
        self._delete_units(cur, doc.id)
        self._insert_units(cur, doc, units, embeddings)
        self._oplog(cur, "document", doc.id, "upsert")
        self.conn.commit()

    def update_document_meta(
        self, document_id: str, *, title: str | None = None,
        saved_note: str | None = None, summary: str | None = None,
    ) -> None:
        cur = self.conn.cursor()
        if title is not None:
            cur.execute("UPDATE documents SET title=? WHERE id=?", (title, document_id))
        if saved_note is not None:
            cur.execute("UPDATE documents SET saved_note=? WHERE id=?", (saved_note, document_id))
        if summary is not None:
            cur.execute("UPDATE documents SET summary=? WHERE id=?", (summary, document_id))
        cur.execute(
            "UPDATE documents SET version = version + 1, updated_at = ? WHERE id = ?",
            (utcnow(), document_id),
        )
        self._oplog(cur, "document", document_id, "upsert")
        self.conn.commit()

    def delete_document(self, document_id: str) -> bool:
        cur = self.conn.cursor()
        if cur.execute("SELECT 1 FROM documents WHERE id=?", (document_id,)).fetchone() is None:
            return False
        self._delete_units(cur, document_id)
        cur.execute("DELETE FROM documents WHERE id=?", (document_id,))
        cur.execute(
            "INSERT OR REPLACE INTO deletions (tenant_id, entity, entity_id, deleted_at)"
            " VALUES (?,?,?,?)",
            (LOCAL_TENANT, "document", document_id, utcnow()),
        )
        self._oplog(cur, "document", document_id, "delete")
        self.conn.commit()
        return True

    def list_documents(
        self, *, source_type: str | None = None, tag: str | None = None,
        limit: int = 50, offset: int = 0,
    ) -> list[Document]:
        sql = "SELECT DISTINCT d.* FROM documents d"
        params: list = []
        where = ["d.tenant_id = ?"]
        params.append(LOCAL_TENANT)
        if tag:
            sql += (" JOIN document_tags dt ON dt.document_id = d.id"
                    " JOIN tags t ON t.id = dt.tag_id")
            where.append("(t.name = ? COLLATE NOCASE"
                         " OR LOWER(t.name) LIKE LOWER(?) || '/%')")
            params += [tag, tag]
        if source_type:
            where.append("d.source_type = ?")
            params.append(source_type)
        sql += " WHERE " + " AND ".join(where) + " ORDER BY d.updated_at DESC LIMIT ? OFFSET ?"
        params += [limit, offset]
        return [self._row_to_doc(r) for r in self.conn.execute(sql, params).fetchall()]

    def iter_documents_with_units(self) -> Iterable[tuple[Document, list[KnowledgeUnit]]]:
        for row in self.conn.execute(
            "SELECT * FROM documents WHERE tenant_id = ? ORDER BY created_at", (LOCAL_TENANT,)
        ).fetchall():
            doc = self._row_to_doc(row)
            units = [
                self._row_to_unit(u) for u in self.conn.execute(
                    "SELECT * FROM knowledge_units WHERE document_id = ? ORDER BY seq",
                    (doc.id,),
                ).fetchall()
            ]
            yield doc, units

    # ------------------------------------------------------ search primitives
    def fts_candidates(self, query: str, limit: int) -> list[tuple[int, float]]:
        q = _fts_escape(query)
        if not q:
            return []
        rows = self.conn.execute(
            "SELECT rowid, bm25(units_fts) AS s FROM units_fts WHERE units_fts MATCH ?"
            " ORDER BY s LIMIT ?",
            (q, limit),
        ).fetchall()
        return [(r["rowid"], r["s"]) for r in rows]

    def vec_candidates(self, embedding: list[float], limit: int) -> list[tuple[int, float]]:
        rows = self.conn.execute(
            "SELECT rowid, distance FROM units_vec WHERE embedding MATCH ? AND k = ?"
            " ORDER BY distance",
            (_serialize_f32(embedding), limit),
        ).fetchall()
        return [(r["rowid"], r["distance"]) for r in rows]

    def fetch_units(self, rids: list[int]) -> list[tuple[int, KnowledgeUnit, Document]]:
        if not rids:
            return []
        placeholders = ",".join("?" * len(rids))
        rows = self.conn.execute(
            f"SELECT ku.rid AS rid, ku.*, d.id AS d_id FROM knowledge_units ku"
            f" JOIN documents d ON d.id = ku.document_id WHERE ku.rid IN ({placeholders})",
            rids,
        ).fetchall()
        docs: dict[str, Document] = {}
        out = []
        for r in rows:
            unit = self._row_to_unit(r)
            if unit.document_id not in docs:
                docs[unit.document_id] = self.get_document(unit.document_id)  # type: ignore
            out.append((r["rid"], unit, docs[unit.document_id]))
        return out

    # ------------------------------------------------------------------- tags
    def set_tags(self, document_id: str, tags: list[str]) -> None:
        cur = self.conn.cursor()
        cur.execute("DELETE FROM document_tags WHERE document_id = ?", (document_id,))
        for name in {t.strip() for t in tags if t.strip()}:
            cur.execute(
                "INSERT OR IGNORE INTO tags (tenant_id, name) VALUES (?,?)",
                (LOCAL_TENANT, name),
            )
            tag_id = cur.execute(
                "SELECT id FROM tags WHERE tenant_id=? AND name=? COLLATE NOCASE",
                (LOCAL_TENANT, name),
            ).fetchone()[0]
            cur.execute(
                "INSERT OR IGNORE INTO document_tags (document_id, tag_id) VALUES (?,?)",
                (document_id, tag_id),
            )
        self._oplog(cur, "tags", document_id, "upsert")
        self.conn.commit()

    def get_tags(self, document_id: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT t.name FROM tags t JOIN document_tags dt ON dt.tag_id = t.id"
            " WHERE dt.document_id = ? ORDER BY t.name",
            (document_id,),
        ).fetchall()
        return [r["name"] for r in rows]

    def document_ids_for_tags(self, tags: list[str]) -> set[str]:
        """Documents carrying ANY of the given tags. Hierarchical prefix match:
        filtering by "Research" also matches "Research/RAG" (tags are flat
        strings — "/" is only a naming convention, no tree logic anywhere)."""
        out: set[str] = set()
        for tag in tags:
            rows = self.conn.execute(
                "SELECT dt.document_id FROM document_tags dt JOIN tags t ON t.id = dt.tag_id"
                " WHERE t.name = ? COLLATE NOCASE OR LOWER(t.name) LIKE LOWER(?) || '/%'",
                (tag, tag),
            ).fetchall()
            out.update(r["document_id"] for r in rows)
        return out

    def list_tags(self) -> list[tuple[str, int]]:
        # INNER join: a tag row that no document carries anymore (after retag
        # or document deletion) must not surface as a ghost 0-count tag
        rows = self.conn.execute(
            "SELECT t.name, COUNT(dt.document_id) AS n FROM tags t"
            " JOIN document_tags dt ON dt.tag_id = t.id"
            " WHERE t.tenant_id = ? GROUP BY t.id ORDER BY n DESC, t.name",
            (LOCAL_TENANT,),
        ).fetchall()
        return [(r["name"], r["n"]) for r in rows]

    # ------------------------------------------------------- notes bookkeeping
    def get_sync_state(self, file_path: str) -> tuple[float, str] | None:
        row = self.conn.execute(
            "SELECT mtime, content_hash FROM sync_state WHERE file_path = ?", (file_path,)
        ).fetchone()
        return (row["mtime"], row["content_hash"]) if row else None

    def set_sync_state(self, file_path: str, mtime: float, content_hash: str) -> None:
        self.conn.execute(
            "INSERT INTO sync_state (file_path, mtime, content_hash, last_synced)"
            " VALUES (?,?,?,?) ON CONFLICT(file_path) DO UPDATE SET"
            " mtime=excluded.mtime, content_hash=excluded.content_hash,"
            " last_synced=excluded.last_synced",
            (file_path, mtime, content_hash, utcnow()),
        )
        self.conn.commit()

    # ------------------------------------------------------------------ audit
    def log_event(self, type_: str, meta: dict) -> None:
        self.conn.execute(
            "INSERT INTO events (tenant_id, type, meta, created_at) VALUES (?,?,?,?)",
            (LOCAL_TENANT, type_, json.dumps(meta), utcnow()),
        )
        self.conn.commit()



    # ----------------------------------------------------------------- kb meta
    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM kb_meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO kb_meta (key, value) VALUES (?,?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        self.conn.commit()

    # -------------------------------------------------------------- api tokens
    def create_token(self, name: str, scopes: list[str]) -> tuple[int, str]:
        """Returns (id, plaintext). Only the sha256 hash is stored — the
        plaintext is shown exactly once."""
        import hashlib
        import secrets

        plaintext = secrets.token_urlsafe(32)
        digest = hashlib.sha256(plaintext.encode()).hexdigest()
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO api_tokens (name, token_hash, scopes, created_at)"
            " VALUES (?,?,?,?)",
            (name, digest, ",".join(sorted(set(scopes))), utcnow()))
        self.conn.commit()
        return cur.lastrowid, plaintext

    def list_tokens(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, name, scopes, created_at, last_used, revoked"
            " FROM api_tokens ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def revoke_token(self, token_id: int) -> bool:
        cur = self.conn.execute(
            "UPDATE api_tokens SET revoked=1 WHERE id=?", (token_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def verify_token(self, plaintext: str) -> list[str] | None:
        import hashlib

        digest = hashlib.sha256(plaintext.encode()).hexdigest()
        row = self.conn.execute(
            "SELECT id, scopes FROM api_tokens WHERE token_hash=? AND revoked=0",
            (digest,)).fetchone()
        if row is None:
            return None
        self.conn.execute("UPDATE api_tokens SET last_used=? WHERE id=?",
                          (utcnow(), row["id"]))
        self.conn.commit()
        return row["scopes"].split(",") if row["scopes"] else []

    # ------------------------------------------------------------ durable jobs
    def enqueue_job(self, kind: str, key: str, payload: dict) -> int:
        import json as json_mod

        cur = self.conn.cursor()
        now = utcnow()
        cur.execute("DELETE FROM jobs WHERE status = 'done' AND updated_at < ?",
                    ((_dt_ago_hours(24)),))
        cur.execute(
            "INSERT INTO jobs (kind, key, payload, status, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?)",
            (kind, key, json_mod.dumps(payload), "queued", now, now))
        self.conn.commit()
        return cur.lastrowid

    def claim_next_job(self) -> dict | None:
        import json as json_mod

        cur = self.conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        row = cur.execute(
            "SELECT id, kind, key, payload FROM jobs WHERE status='queued'"
            " ORDER BY id LIMIT 1").fetchone()
        if row is None:
            self.conn.commit()
            return None
        cur.execute("UPDATE jobs SET status='running', updated_at=? WHERE id=?",
                    (utcnow(), row["id"]))
        self.conn.commit()
        return {"id": row["id"], "kind": row["kind"], "key": row["key"],
                "payload": json_mod.loads(row["payload"])}

    def finish_job(self, job_id: int, error: str | None = None) -> None:
        self.conn.execute(
            "UPDATE jobs SET status=?, error=?, updated_at=? WHERE id=?",
            ("error" if error else "done", error, utcnow(), job_id))
        self.conn.commit()

    def requeue_stale_jobs(self) -> int:
        cur = self.conn.execute(
            "UPDATE jobs SET status='queued', updated_at=? WHERE status='running'",
            (utcnow(),))
        self.conn.commit()
        return cur.rowcount

    def job_state_for_key(self, kind: str, key: str) -> tuple[str | None, str | None]:
        """State of the MOST RECENT job for this key: 'pending' while queued or
        running, 'error' with detail after a failure, None once done."""
        row = self.conn.execute(
            "SELECT status, error FROM jobs WHERE kind=? AND key=?"
            " ORDER BY id DESC LIMIT 1", (kind, key)).fetchone()
        if row is None or row["status"] == "done":
            return None, None
        if row["status"] == "error":
            return "error", row["error"]
        return "pending", None

    # ------------------------------------------------------------ data rights
    def wipe(self, factory: bool = False) -> int:
        cur = self.conn.cursor()
        n = cur.execute(
            "SELECT COUNT(*) FROM documents WHERE tenant_id = ?", (LOCAL_TENANT,)
        ).fetchone()[0]
        for row in cur.execute(
            "SELECT id FROM documents WHERE tenant_id = ?", (LOCAL_TENANT,)
        ).fetchall():
            cur.execute(
                "INSERT OR REPLACE INTO deletions (tenant_id, entity, entity_id, deleted_at)"
                " VALUES (?,?,?,?)",
                (LOCAL_TENANT, "document", row["id"], utcnow()),
            )
            self._oplog(cur, "document", row["id"], "delete")
        cur.execute("DELETE FROM units_fts")
        cur.execute("DELETE FROM units_vec")
        cur.execute("DELETE FROM knowledge_units")
        cur.execute("DELETE FROM document_tags")
        cur.execute("DELETE FROM tags")
        cur.execute("DELETE FROM documents")
        cur.execute("DELETE FROM sync_state")
        if factory:
            # factory reset: operational data too — events, oplog, tombstones,
            # conflict payloads, device registrations. Local-only by design:
            # without an oplog there is nothing left to propagate.
            for table in ("events", "oplog", "deletions", "sync_conflicts",
                          "devices"):
                cur.execute(f"DELETE FROM {table}")
        else:
            self._oplog(cur, "tenant", LOCAL_TENANT, "wipe")
        self.conn.commit()
        return n

    def dump_operational(self) -> dict:
        """Everything OUTSIDE the knowledge tables that still holds user data —
        exported so 'full export' means full (events log questions and URLs)."""
        out: dict = {}
        for table in ("events", "sync_conflicts", "deletions"):
            rows = self.conn.execute(f"SELECT * FROM {table}").fetchall()
            out[table] = [dict(r) for r in rows]
        return out

    # -------------------------------------------------------------- sync ops
    def latest_seq(self) -> int:
        row = self.conn.execute("SELECT MAX(seq) FROM oplog").fetchone()
        return row[0] or 0

    def oplog_since(self, seq: int, *, exclude_origin: str | None = None,
                    local_only: bool = False) -> list[tuple[int, str, str, str]]:
        sql = "SELECT seq, entity, entity_id, op FROM oplog WHERE seq > ?"
        params: list = [seq]
        if local_only:
            sql += " AND origin = 'local'"
        if exclude_origin:
            sql += " AND origin != ?"
            params.append(exclude_origin)
        sql += " ORDER BY seq"
        return [(r["seq"], r["entity"], r["entity_id"], r["op"])
                for r in self.conn.execute(sql, params).fetchall()]

    def get_unit_embeddings(self, document_id: str) -> dict[str, list[float]]:
        """unit uuid -> embedding, read back from the vector index."""
        out: dict[str, list[float]] = {}
        rows = self.conn.execute(
            "SELECT ku.id AS uid, uv.embedding AS emb FROM knowledge_units ku"
            " JOIN units_vec uv ON uv.rowid = ku.rid WHERE ku.document_id = ?",
            (document_id,),
        ).fetchall()
        for r in rows:
            blob = r["emb"]
            out[r["uid"]] = list(struct.unpack(f"{len(blob) // 4}f", blob))
        return out

    def get_bundle(self, document_id: str) -> dict | None:
        """Full sync payload for one document: doc + units (with vectors) + tags."""
        from dataclasses import asdict

        doc = self.get_document(document_id)
        if doc is None:
            return None
        embeddings = self.get_unit_embeddings(document_id)
        units = []
        for r in self.conn.execute(
            "SELECT * FROM knowledge_units WHERE document_id = ? ORDER BY seq",
            (document_id,),
        ).fetchall():
            unit = self._row_to_unit(r)
            record = asdict(unit)
            record["embedding"] = embeddings.get(unit.id, [])
            units.append(record)
        return {"doc": asdict(doc), "units": units, "tags": self.get_tags(document_id)}

    def get_deletion(self, entity_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT deleted_at FROM deletions WHERE tenant_id=? AND entity='document'"
            " AND entity_id=?",
            (LOCAL_TENANT, entity_id),
        ).fetchone()
        return row["deleted_at"] if row else None

    def record_conflict(self, document_id: str, losing_payload: dict, rule: str) -> None:
        self.conn.execute(
            "INSERT INTO sync_conflicts (tenant_id, document_id, losing_payload, rule,"
            " resolved_at) VALUES (?,?,?,?,?)",
            (LOCAL_TENANT, document_id, json.dumps(losing_payload), rule, utcnow()),
        )
        self.conn.commit()

    def list_conflicts(self) -> list[dict]:
        return [{
            "id": r["id"], "document_id": r["document_id"], "rule": r["rule"],
            "resolved_at": r["resolved_at"],
            "losing_payload": json.loads(r["losing_payload"]),
        } for r in self.conn.execute(
            "SELECT * FROM sync_conflicts ORDER BY id DESC LIMIT 100").fetchall()]

    def device_state(self, device_id: str) -> tuple[int, int]:
        row = self.conn.execute(
            "SELECT last_push_seq, last_pull_seq FROM devices WHERE id = ?", (device_id,)
        ).fetchone()
        return (row["last_push_seq"], row["last_pull_seq"]) if row else (0, 0)

    def device_update(self, device_id: str, *, name: str = "",
                      push_seq: int | None = None, pull_seq: int | None = None) -> None:
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO devices (id, tenant_id, name, last_seen) VALUES (?,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen",
            (device_id, LOCAL_TENANT, name, utcnow()),
        )
        if push_seq is not None:
            cur.execute("UPDATE devices SET last_push_seq=? WHERE id=?", (push_seq, device_id))
        if pull_seq is not None:
            cur.execute("UPDATE devices SET last_pull_seq=? WHERE id=?", (pull_seq, device_id))
        self.conn.commit()

    def counts(self) -> dict:
        c = self.conn.execute
        return {
            "documents": c("SELECT COUNT(*) FROM documents").fetchone()[0],
            "knowledge_units": c("SELECT COUNT(*) FROM knowledge_units").fetchone()[0],
            "tags": c("SELECT COUNT(*) FROM tags").fetchone()[0],
            "events": c("SELECT COUNT(*) FROM events").fetchone()[0],
        }

    def close(self) -> None:
        self.conn.close()
