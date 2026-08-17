"""/api/v1 routes."""

from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from .. import __version__
from ..ingest.distill import _lead_sentences
from ..ingest.pipeline import preview_web, save_web
from .app import open_repo

router = APIRouter()


# ------------------------------------------------------------------ payloads
class SaveBody(BaseModel):
    url: str
    tags: list[str] | None = None
    note: str | None = None
    overwrite: bool = False


class SaveHtmlBody(SaveBody):
    title: str = ""
    html: str


class SearchBody(BaseModel):
    query: str
    k: int = 8
    tags: list[str] | None = None


class QueryBody(BaseModel):
    question: str
    mode: str = "kb"  # kb | deeper
    k: Optional[int] = None
    tags: list[str] | None = None


class AskBody(BaseModel):
    question: str


class AskSaveBody(BaseModel):
    question: str
    payload: dict
    provider: str = ""
    user_verified: bool = True


class PatchDocBody(BaseModel):
    title: str | None = None
    saved_note: str | None = None
    summary: str | None = None
    tags: list[str] | None = None


class TodoBody(BaseModel):
    text: str
    due: str | None = None      # ISO datetime, or null for no reminder


class TodoPatchBody(BaseModel):
    text: str | None = None
    due: str | None = None
    clear_due: bool = False
    done: bool | None = None


class MindmapBody(BaseModel):
    data: dict            # mind-elixir map JSON ({nodeData: {...}, ...})
    title: str | None = None


class UnitsBody(BaseModel):
    units: list[dict]     # [{text, kind?, heading_path?}] — full replacement list


class GraphBody(BaseModel):
    query: str
    k: int = 12
    tags: list[str] | None = None


class TagRenameBody(BaseModel):
    old: str
    new: str


class TagNameBody(BaseModel):
    name: str


# --------------------------------------------------------------------- save
import threading

_COMMIT_LOCK = threading.Lock()  # serialize background commits (single-user scale;
                                 # prevents two saves of the same URL racing the insert)


_SAVE_ERRORS: dict[str, str] = {}   # url -> last background save failure


def _background_commit(request: Request, url: str, html: str | None,
                       title_hint: str, tags, note) -> None:
    with _COMMIT_LOCK:
        repo = open_repo(request)
        try:
            save_web(repo, request.app.state.embedder, request.app.state.config, url,
                     html=html, title_hint=title_hint, tags=tags, note=note, overwrite=True)
            _SAVE_ERRORS.pop(url, None)
        except Exception as e:  # noqa: BLE001 — background failure must not crash the app
            _SAVE_ERRORS[url] = str(e)[:300]
            repo.log_event("save_background_error", {"url": url, "error": str(e)[:300]})
        finally:
            repo.close()


def _handle_save(request: Request, background: BackgroundTasks, url: str,
                 html: str | None, title_hint: str, body: SaveBody) -> JSONResponse:
    repo = open_repo(request)
    try:
        config = request.app.state.config
        status, extracted, existing, _ = preview_web(
            repo, config, url, html=html, title_hint=title_hint)
        if status == "blocked":
            return JSONResponse({"status": "blocked",
                                 "message": "could not extract, or blocked by guardrails"},
                                status_code=422)
        if status == "unchanged":
            return JSONResponse({
                "status": "unchanged", "document_id": existing.id,
                "message": f"already in your knowledge base (saved {existing.created_at[:10]})",
            })
        if status == "needs_consent" and not body.overwrite:
            return JSONResponse({
                "status": "needs_consent", "document_id": existing.id,
                "old_summary": existing.summary or "",
                "new_preview": _lead_sentences(extracted.text),
                "message": "page changed since it was saved — re-send with overwrite=true to update",
            }, status_code=409)
        # accepted (or consented update): distill+embed+index in the background
        _SAVE_ERRORS.pop(url, None)   # fresh attempt — drop any stale failure
        background.add_task(_background_commit, request, url, html, title_hint,
                            body.tags, body.note)
        return JSONResponse({"status": "accepted", "title": extracted.title,
                             "message": "saved — distilling in background"}, status_code=202)
    finally:
        repo.close()


@router.post("/save")
def save(request: Request, body: SaveBody, background: BackgroundTasks):
    return _handle_save(request, background, body.url, None, "", body)


@router.post("/save-html")
def save_html(request: Request, body: SaveHtmlBody, background: BackgroundTasks):
    return _handle_save(request, background, body.url, body.html, body.title, body)


# ------------------------------------------------------------------- search
def _result_json(r) -> dict:
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
    }


@router.post("/search")
def search(request: Request, body: SearchBody):
    from ..reranker import get_reranker
    from ..search import hybrid_search

    repo = open_repo(request)
    try:
        results = hybrid_search(repo, request.app.state.embedder, body.query,
                                top_k=body.k,
                                candidates=request.app.state.config.retrieval.candidates,
                                reranker=get_reranker(request.app.state.config),
                                tags=body.tags)
        return {"results": [_result_json(r) for r in results]}
    finally:
        repo.close()


@router.post("/ask")
def ask(request: Request, body: AskBody):
    """Quick answer with warm models — the fast path for thin CLI clients.
    High-confidence web answers are saved server-side (mirrors the CLI);
    low-confidence ones return their save_payload for client-side vetting."""
    from ..quick import quick_ask, save_quick_answer

    repo = open_repo(request)
    try:
        config = request.app.state.config
        result = quick_ask(repo, request.app.state.embedder, config, body.question)
        saved = False
        if (result.mode == "web" and result.confidence == "high"
                and result.save_payload):
            with _COMMIT_LOCK:
                save_quick_answer(repo, request.app.state.embedder, config,
                                  body.question, result.save_payload,
                                  result.provider, confidence="high")
            saved = True
        return {"mode": result.mode, "answer": result.answer,
                "error": result.error,
                "source_url": result.source_url,
                "source_urls": result.source_urls, "provider": result.provider,
                "confidence": result.confidence, "grounded": result.grounded,
                "saved": saved,
                "save_payload": None if saved else result.save_payload}
    finally:
        repo.close()


@router.post("/ask/save")
def ask_save(request: Request, body: AskSaveBody):
    """Persist a user-vetted quick answer (stored as high confidence)."""
    from ..quick import save_quick_answer

    repo = open_repo(request)
    try:
        with _COMMIT_LOCK:
            doc_id = save_quick_answer(
                repo, request.app.state.embedder, request.app.state.config,
                body.question, body.payload, body.provider,
                confidence="low", user_verified=body.user_verified)
        return {"status": "saved", "document_id": doc_id}
    finally:
        repo.close()


@router.post("/query")
def query(request: Request, body: QueryBody):
    from ..rag import query as rag_query

    def event_stream():
        repo = open_repo(request)
        try:
            result = rag_query(repo, request.app.state.embedder,
                               request.app.state.config, body.question,
                               mode=body.mode, top_k=body.k, tags=body.tags)
            yield ("event: sources\ndata: "
                   + json.dumps([_result_json(r) for r in result.sources]) + "\n\n")
            if result.mode in ("no_answer", "no_provider"):
                yield f"event: status\ndata: {result.mode}\n\n"
            else:
                for chunk in result.chunks or []:
                    yield "event: token\ndata: " + json.dumps(chunk) + "\n\n"
            yield ("event: done\ndata: "
                   + json.dumps({"provider": result.provider, "mode": result.mode}) + "\n\n")
        finally:
            repo.close()

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


# ----------------------------------------------------------------- mindmaps
@router.get("/mindmaps")
def list_mindmaps(request: Request):
    repo = open_repo(request)
    try:
        docs = repo.list_documents(source_type="mindmap", limit=200)
        # topics = first-level branches, cached client-side for autocomplete
        def _topics(d):
            root = (((d.meta or {}).get("mindmap") or {}).get("nodeData")) or {}
            return [str(c.get("topic", ""))[:80]
                    for c in (root.get("children") or [])[:12]
                    if isinstance(c, dict) and c.get("topic")]
        return {"mindmaps": [{"id": d.id, "title": d.title,
                              "updated_at": d.updated_at,
                              "topics": _topics(d)} for d in docs]}
    finally:
        repo.close()


@router.get("/mindmaps/{document_id}")
def get_mindmap(request: Request, document_id: str):
    repo = open_repo(request)
    try:
        doc = repo.get_document(document_id)
        if doc is None or doc.source_type != "mindmap":
            return JSONResponse({"detail": "not found"}, status_code=404)
        return {"id": doc.id, "title": doc.title,
                "data": (doc.meta or {}).get("mindmap") or {},
                "updated_at": doc.updated_at}
    finally:
        repo.close()


@router.post("/mindmaps")
def create_mindmap(request: Request, body: MindmapBody):
    from ..mindmap import save_mindmap

    repo = open_repo(request)
    try:
        with _COMMIT_LOCK:
            doc = save_mindmap(repo, request.app.state.embedder, body.data,
                               title=body.title)
        return {"id": doc.id, "title": doc.title}
    finally:
        repo.close()


@router.put("/mindmaps/{document_id}")
def update_mindmap(request: Request, document_id: str, body: MindmapBody):
    from ..mindmap import save_mindmap

    repo = open_repo(request)
    try:
        existing = repo.get_document(document_id)
        if existing is None or existing.source_type != "mindmap":
            return JSONResponse({"detail": "not found"}, status_code=404)
        with _COMMIT_LOCK:
            doc = save_mindmap(repo, request.app.state.embedder, body.data,
                               document_id=document_id, title=body.title)
        return {"id": doc.id, "title": doc.title}
    finally:
        repo.close()


# -------------------------------------------------------------------- graph
@router.post("/graph")
def graph(request: Request, body: GraphBody):
    from ..graph import build_graph

    repo = open_repo(request)
    try:
        return build_graph(repo, request.app.state.embedder,
                           request.app.state.config, body.query,
                           k=body.k, tags=body.tags)
    finally:
        repo.close()


# ------------------------------------------------------------------ library
@router.get("/documents")
def list_documents(request: Request, tag: str | None = None, type: str | None = None,
                   limit: int = 50, offset: int = 0):
    repo = open_repo(request)
    try:
        docs = repo.list_documents(source_type=type, tag=tag, limit=limit, offset=offset)
        return {"documents": [{
            "id": d.id, "title": d.title, "url": d.url, "file_path": d.file_path,
            "source_type": d.source_type, "summary": d.summary,
            "distilled": d.distilled, "lang": d.lang, "saved_note": d.saved_note,
            "created_at": d.created_at, "updated_at": d.updated_at,
            "tags": repo.get_tags(d.id),
            "tag_nodes": (d.meta or {}).get("tag_nodes") or None,
        } for d in docs]}
    finally:
        repo.close()


@router.get("/documents/lookup")
def lookup_document(request: Request, url: str):
    """Find the saved document for a URL — the extension uses this to show
    (and let the user edit) the distilled summary right after a save."""
    repo = open_repo(request)
    try:
        doc = repo.get_document_by_url(url)
        if doc is None:
            if url in _SAVE_ERRORS:
                # the background distill+commit died — tell the client instead
                # of letting it poll forever
                return JSONResponse({"detail": "background save failed",
                                     "error": _SAVE_ERRORS[url]}, status_code=502)
            return JSONResponse({"detail": "not found"}, status_code=404)
        return {"id": doc.id, "title": doc.title, "summary": doc.summary,
                "distilled": doc.distilled, "tags": repo.get_tags(doc.id)}
    finally:
        repo.close()


@router.get("/documents/{document_id}")
def get_document(request: Request, document_id: str):
    repo = open_repo(request)
    try:
        doc = repo.get_document(document_id)
        if doc is None:
            return JSONResponse({"detail": "not found"}, status_code=404)
        units = []
        for d, us in repo.iter_documents_with_units():
            if d.id == document_id:
                units = us
                break
        return {
            "id": doc.id, "title": doc.title, "url": doc.url,
            "file_path": doc.file_path, "source_type": doc.source_type,
            "summary": doc.summary, "saved_note": doc.saved_note,
            "distilled": doc.distilled, "tags": repo.get_tags(doc.id),
            "units": [{"seq": u.seq, "kind": u.kind,
                       "heading_path": u.heading_path, "text": u.text}
                      for u in units],
        }
    finally:
        repo.close()


@router.put("/documents/{document_id}/units")
def put_units(request: Request, document_id: str, body: UnitsBody):
    """Edit/delete individual knowledge units: the client sends the full new
    unit list; changed text is re-embedded and the document version bumps so
    the edit syncs like any other change."""
    from ..models import KnowledgeUnit, UnitKind

    kinds = {k.value for k in UnitKind}
    units_in = [u for u in body.units if str(u.get("text", "")).strip()]
    if not units_in:
        return JSONResponse(
            {"detail": "a document needs at least one knowledge unit — "
                       "delete the whole document instead"}, status_code=400)
    with _COMMIT_LOCK:
        repo = open_repo(request)
        try:
            doc = repo.get_document(document_id)
            if doc is None:
                return JSONResponse({"detail": "not found"}, status_code=404)
            if doc.source_type == "mindmap":
                return JSONResponse(
                    {"detail": "mind-map knowledge is edited in the mind map"},
                    status_code=400)
            units = [KnowledgeUnit(
                document_id=document_id, seq=i,
                text=str(u["text"]).strip(),
                kind=u.get("kind") if u.get("kind") in kinds else "note",
                heading_path=str(u.get("heading_path", "") or ""),
            ) for i, u in enumerate(units_in)]
            embeddings = request.app.state.embedder.embed_passages(
                [u.text for u in units])
            repo.replace_document(doc, units, embeddings)
            repo.log_event("units_edit", {"document_id": document_id,
                                          "units": len(units)})
            return {"status": "ok", "units": len(units), "version": doc.version}
        finally:
            repo.close()


@router.patch("/documents/{document_id}")
def patch_document(request: Request, document_id: str, body: PatchDocBody):
    repo = open_repo(request)
    try:
        if repo.get_document(document_id) is None:
            return JSONResponse({"detail": "not found"}, status_code=404)
        if (body.title is not None or body.saved_note is not None
                or body.summary is not None):
            repo.update_document_meta(document_id, title=body.title,
                                      saved_note=body.saved_note,
                                      summary=body.summary)
        if body.tags is not None:
            repo.set_tags(document_id, body.tags)
        return {"status": "ok"}
    finally:
        repo.close()


@router.delete("/documents/{document_id}")
def delete_document(request: Request, document_id: str):
    repo = open_repo(request)
    try:
        if not repo.delete_document(document_id):
            return JSONResponse({"detail": "not found"}, status_code=404)
        repo.log_event("delete", {"document_id": document_id})
        return {"status": "deleted"}
    finally:
        repo.close()


@router.get("/tags")
def tags(request: Request):
    repo = open_repo(request)
    try:
        return {"tags": [{"name": n, "count": c} for n, c in repo.list_tags()]}
    finally:
        repo.close()


@router.post("/tags/rename")
def tags_rename(request: Request, body: TagRenameBody):
    """Rename a tag on every document that carries it (exact match)."""
    from ..tagmap import retag_documents

    old, new = body.old.strip(), body.new.strip()
    if not old or not new:
        return JSONResponse({"detail": "old and new tag names required"},
                            status_code=400)
    repo = open_repo(request)
    try:
        n = retag_documents(repo, lambda ts: [new if t == old else t for t in ts])
        repo.log_event("tags_rename", {"old": old, "new": new, "documents": n})
        return {"status": "ok", "documents": n}
    finally:
        repo.close()


@router.post("/tags/delete")
def tags_delete(request: Request, body: TagNameBody):
    """Remove a tag from every document (the documents themselves stay)."""
    from ..tagmap import retag_documents

    name = body.name.strip()
    if not name:
        return JSONResponse({"detail": "tag name required"}, status_code=400)
    repo = open_repo(request)
    try:
        n = retag_documents(repo, lambda ts: [t for t in ts if t != name])
        repo.log_event("tags_remove", {"tag": name, "documents": n})
        return {"status": "ok", "documents": n}
    finally:
        repo.close()


@router.get("/tags/map")
def tags_map(request: Request):
    """Word-map data: every tag with count, 2D semantic position, cluster."""
    from ..tagmap import build_tag_map

    repo = open_repo(request)
    try:
        return build_tag_map(repo, request.app.state.embedder)
    finally:
        repo.close()


# -------------------------------------------------------------------- todos
# To-do reminders (browser extension / future mobile). Each to-do is a tiny
# document (source_type="todo") with one embedded unit — so it syncs across
# devices, is searchable, and follows export/wipe like everything else.
# meta["todo"] = {"done": bool, "due": iso-datetime | None}.
def _todo_json(repo, d):
    todo = (d.meta or {}).get("todo") or {}
    return {"id": d.id, "text": d.title, "done": bool(todo.get("done")),
            "due": todo.get("due"), "created_at": d.created_at,
            "updated_at": d.updated_at}


def _write_todo(repo, embedder, doc, text, done, due):
    from ..models import KnowledgeUnit

    doc.title = text[:300]
    doc.summary = ("✓ done — " if done else "to-do — ") + text[:200]
    doc.distilled = True
    doc.distill_tier = "user-content"
    doc.meta = {**(doc.meta or {}), "todo": {"done": done, "due": due}}
    units = [KnowledgeUnit(document_id=doc.id, seq=0, kind="note",
                           text=f"To-do: {text}")]
    embeddings = embedder.embed_passages([units[0].text])
    if repo.get_document(doc.id) is None:
        repo.insert_document(doc, units, embeddings)
    else:
        repo.replace_document(doc, units, embeddings)
    return doc


@router.get("/todos")
def list_todos(request: Request, include_done: bool = False):
    repo = open_repo(request)
    try:
        docs = repo.list_documents(source_type="todo", limit=500)
        todos = [_todo_json(repo, d) for d in docs]
        if not include_done:
            todos = [t for t in todos if not t["done"]]
        todos.sort(key=lambda t: (t["due"] is None, t["due"] or "", t["created_at"]))
        return {"todos": todos}
    finally:
        repo.close()


@router.post("/todos")
def create_todo(request: Request, body: TodoBody):
    from ..models import Document, SourceType

    text = body.text.strip()
    if not text:
        return JSONResponse({"detail": "text required"}, status_code=400)
    with _COMMIT_LOCK:
        repo = open_repo(request)
        try:
            doc = Document(source_type=SourceType.TODO, title=text[:300])
            _write_todo(repo, request.app.state.embedder, doc, text, False, body.due)
            repo.log_event("todo_add", {"document_id": doc.id})
            return _todo_json(repo, repo.get_document(doc.id))
        finally:
            repo.close()


@router.patch("/todos/{document_id}")
def patch_todo(request: Request, document_id: str, body: TodoPatchBody):
    with _COMMIT_LOCK:
        repo = open_repo(request)
        try:
            doc = repo.get_document(document_id)
            if doc is None or doc.source_type != "todo":
                return JSONResponse({"detail": "not found"}, status_code=404)
            todo = (doc.meta or {}).get("todo") or {}
            text = body.text.strip() if body.text is not None else doc.title
            done = body.done if body.done is not None else bool(todo.get("done"))
            due = None if body.clear_due else (
                body.due if body.due is not None else todo.get("due"))
            _write_todo(repo, request.app.state.embedder, doc, text, done, due)
            return _todo_json(repo, repo.get_document(document_id))
        finally:
            repo.close()


# -------------------------------------------------------------- data rights
@router.get("/export")
def export(request: Request):
    from ..export import export_zip

    repo = open_repo(request)
    try:
        blob = export_zip(repo)
        repo.log_event("export", {"via": "api"})
        return Response(blob, media_type="application/zip", headers={
            "Content-Disposition": "attachment; filename=dronacharya-export.zip"})
    finally:
        repo.close()


@router.delete("/data")
def wipe(request: Request):
    repo = open_repo(request)
    try:
        n = repo.wipe()
        repo.log_event("wipe", {"via": "api", "documents": n})
        return {"status": "wiped", "documents": n}
    finally:
        repo.close()


# --------------------------------------------------------------------- sync
class PushBody(BaseModel):
    device_id: str
    ops: list[dict]


def _upgrade_pass(request: Request, document_ids: list[str]) -> None:
    """Server-side re-distillation of weakly-distilled pushed documents."""
    from ..ingest.pipeline import redistill_document

    with _COMMIT_LOCK:
        repo = open_repo(request)
        try:
            for document_id in document_ids:
                try:
                    redistill_document(repo, request.app.state.embedder,
                                       request.app.state.config, document_id)
                except Exception as e:  # noqa: BLE001
                    repo.log_event("upgrade_pass_error",
                                   {"document_id": document_id, "error": str(e)[:200]})
        finally:
            repo.close()


@router.post("/sync/push")
def sync_push(request: Request, body: PushBody, background: BackgroundTasks):
    from ..sync.merge import apply_ops

    repo = open_repo(request)
    try:
        summary = apply_ops(repo, body.ops, origin=f"remote:{body.device_id}",
                            prefer_local_on_tie=True)
        repo.device_update(body.device_id, name=f"device:{body.device_id[:8]}")
        undistilled = [op["doc"]["id"] for op in body.ops
                       if op.get("op") == "upsert" and not op["doc"].get("distilled")]
        if undistilled:
            background.add_task(_upgrade_pass, request, undistilled)
        repo.log_event("sync_push", {"device": body.device_id, **summary})
        return {"summary": summary, "latest_seq": repo.latest_seq(),
                "upgrading": len(undistilled)}
    finally:
        repo.close()


@router.get("/sync/pull")
def sync_pull(request: Request, device_id: str, since: int = 0):
    from ..sync.merge import collect_ops

    repo = open_repo(request)
    try:
        ops, latest = collect_ops(repo, since,
                                  exclude_origin=f"remote:{device_id}")
        return {"ops": ops, "latest_seq": latest}
    finally:
        repo.close()


# ------------------------------------------------------------------- misc
@router.post("/sync-notes")
def sync_notes(request: Request):
    from ..notes_sync import scan_notes

    repo = open_repo(request)
    try:
        report = scan_notes(repo, request.app.state.embedder, request.app.state.config)
        return {"created": report.created, "updated": report.updated,
                "unchanged": report.unchanged, "skipped": report.skipped}
    finally:
        repo.close()


@router.get("/status")
def status(request: Request):
    repo = open_repo(request)
    try:
        return {"app": "dronacharya", "version": __version__, "counts": repo.counts()}
    finally:
        repo.close()
