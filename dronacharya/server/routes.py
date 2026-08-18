"""/api/v1 routes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from fastapi import (APIRouter, BackgroundTasks, File, Form, Request,
                     Response, UploadFile)
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from .. import __version__
from ..ingest.distill import _lead_sentences
from ..ingest.pipeline import preview_web
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
    query: str = Field(max_length=2000)
    k: int = Field(8, ge=1, le=50)
    tags: list[str] | None = None
    show_all: bool = False   # include below-threshold weak matches


class QueryBody(BaseModel):
    question: str = Field(max_length=4000)
    mode: str = "kb"  # kb | deeper
    k: Optional[int] = Field(None, ge=1, le=50)
    tags: list[str] | None = None


class AskBody(BaseModel):
    question: str = Field(max_length=4000)
    no_save: bool = False    # never store the answer, even high-confidence


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
    text: str = Field(max_length=2000)
    due: str | None = None      # ISO datetime, or null for no reminder


class TodoPatchBody(BaseModel):
    text: str | None = None
    due: str | None = None
    clear_due: bool = False
    done: bool | None = None


class MindmapBody(BaseModel):
    data: dict            # mind-elixir map JSON ({nodeData: {...}, ...})
    title: str | None = None


class NoteBody(BaseModel):
    content: str = Field(min_length=1, max_length=200_000)
    title: str = Field("", max_length=200)
    format: str = "markdown"          # markdown | rich
    tags: list[str] | None = None


class UnitsBody(BaseModel):
    # full replacement list [{text, kind?, heading_path?}]
    units: list[dict] = Field(max_length=500)


class GraphBody(BaseModel):
    query: str = Field(max_length=2000)
    k: int = Field(12, ge=1, le=50)
    tags: list[str] | None = None


class TagRenameBody(BaseModel):
    old: str
    new: str


class TagNameBody(BaseModel):
    name: str


# --------------------------------------------------------------------- save
import threading  # noqa: E402 — section-local by design

_COMMIT_LOCK = threading.Lock()  # serializes quick-answer saves (jobs.py has
                                 # its own work lock for queued save jobs)


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
        # accepted (or consented update): distill+embed+index via the durable
        # job queue — state survives restarts, execution starts immediately
        from .jobs import run_pending_jobs

        repo.enqueue_job("save", url, {
            "url": url, "html": html, "title_hint": title_hint,
            "tags": body.tags, "note": body.note})
        background.add_task(run_pending_jobs, request.app)
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
from ..models import doc_capabilities as _doc_capabilities  # noqa: E402, unit_index_text
from ..search import result_to_json as _result_json  # noqa: E402


@router.post("/search")
def search(request: Request, body: SearchBody):
    from ..reranker import get_reranker
    from ..search import hybrid_search

    repo = open_repo(request)
    try:
        from ..search import confidence_gate

        config = request.app.state.config
        reranker = get_reranker(config)
        results = hybrid_search(repo, request.app.state.embedder, body.query,
                                top_k=body.k,
                                candidates=config.retrieval.candidates,
                                reranker=reranker,
                                tags=body.tags)
        if not body.show_all:
            # retrieval policy lives HERE, with the server's tuned config —
            # not re-applied by each client against its own defaults
            results = [r for r in results
                       if r.score >= confidence_gate(config, reranker)]
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
                and result.save_payload and not body.no_save):
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

    if not str(body.payload.get("answer") or body.payload.get("command") or "").strip():
        return JSONResponse({"detail": "payload needs a non-empty 'answer'"},
                            status_code=400)
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
            answer_parts: list[str] = []
            error = ""
            if result.mode in ("no_answer", "no_provider"):
                yield f"event: status\ndata: {result.mode}\n\n"
            else:
                try:
                    for chunk in result.chunks or []:
                        answer_parts.append(chunk)
                        yield "event: token\ndata: " + json.dumps(chunk) + "\n\n"
                except Exception as e:  # noqa: BLE001 — provider died mid-stream
                    error = str(e)[:200]
            from ..rag import cited_indices
            yield ("event: done\ndata: "
                   + json.dumps({"provider": result.provider, "mode": result.mode,
                                 "cited": cited_indices("".join(answer_parts)),
                                 "error": error}) + "\n\n")
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


# ------------------------------------------------------------------- upload
@router.post("/upload")
def upload_files(request: Request, files: list[UploadFile] = File(...),
                 tags: str = Form("")):
    """Ingest user files (tdl/md/txt/pdf/docx/xlsx/pptx) through the web UI.
    The file is kept under DRONACHARYA_HOME/uploads/ so its path remains a
    stable reference (re-uploading the same name updates the document)."""
    from ..config import home_dir
    from ..ingest.parsers import get_parser
    from ..ingest.pipeline import save_note_file

    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    uploads_dir = home_dir() / "uploads"
    try:
        uploads_dir.mkdir(parents=True, exist_ok=True)
        probe = uploads_dir / ".writable"
        probe.touch()
        probe.unlink()
    except OSError as e:
        # bind-mount ownership mismatch is the classic cause — say so
        return JSONResponse(
            {"detail": f"server cannot write {uploads_dir}: {e}. If running "
                       "in docker, the app user must own the data volume "
                       "(compose sets user: to the host uid — rebuild with "
                       "docker compose up -d --build app)"}, status_code=507)
    results = []
    repo = open_repo(request)
    try:
        for f in files:
            name = Path(f.filename or "upload").name   # strip any client path
            if get_parser(Path(name)) is None:
                results.append({"file": name, "status": "unsupported",
                                "message": "no parser for this file type"})
                continue
            target = uploads_dir / name
            target.write_bytes(f.file.read())
            with _COMMIT_LOCK:
                outcome = save_note_file(repo, request.app.state.embedder,
                                         request.app.state.config, target)
                if (tag_list and outcome.document_id
                        and outcome.status != "blocked"):
                    merged = sorted(set(repo.get_tags(outcome.document_id))
                                    | set(tag_list))
                    repo.set_tags(outcome.document_id, merged)
            results.append({"file": name, "status": outcome.status,
                            "document_id": outcome.document_id,
                            "message": outcome.message or ""})
        return {"results": results}
    finally:
        repo.close()


# ------------------------------------------------------------------ library
@router.get("/documents")
def list_documents(request: Request, tag: str | None = None, type: str | None = None,
                   limit: int = 50, offset: int = 0):
    limit, offset = min(max(limit, 1), 500), max(offset, 0)
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
            "capabilities": _doc_capabilities(d.source_type),
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
        state, error = repo.job_state_for_key("save", url)
        if doc is None:
            if state == "error":
                # the background distill+commit died — tell the client instead
                # of letting it poll forever
                return JSONResponse({"detail": "background save failed",
                                     "error": error}, status_code=502)
            if state == "pending":
                return JSONResponse({"status": "distilling"}, status_code=202)
            return JSONResponse({"detail": "not found"}, status_code=404)
        # pending=True while an overwrite re-distill is still running: the doc
        # on disk is the STALE version — clients must not present it as fresh
        return {"id": doc.id, "title": doc.title, "summary": doc.summary,
                "distilled": doc.distilled, "pending": state == "pending",
                "tags": repo.get_tags(doc.id)}
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
            "capabilities": _doc_capabilities(doc.source_type),
            "note_format": (doc.meta or {}).get("note_format"),
            "note_title_explicit": (doc.meta or {}).get("note_title_explicit"),
            "note_source": (doc.meta or {}).get("note_source"),
            "units": [{"seq": u.seq, "kind": u.kind,
                       "heading_path": u.heading_path, "text": u.text}
                      for u in units],
        }
    finally:
        repo.close()


@router.post("/notes")
def create_note_route(request: Request, body: NoteBody):
    """Direct note — typed knowledge, no page or file behind it."""
    from ..notes import create_note

    if body.format not in ("markdown", "rich"):
        return JSONResponse({"detail": "format must be markdown|rich"},
                            status_code=400)
    with _COMMIT_LOCK:
        repo = open_repo(request)
        try:
            try:
                doc = create_note(repo, request.app.state.embedder,
                                  title=body.title, content=body.content,
                                  fmt=body.format, tags=body.tags)
            except ValueError as e:
                return JSONResponse({"detail": str(e)}, status_code=400)
            return {"id": doc.id, "title": doc.title, "format": body.format}
        finally:
            repo.close()


@router.put("/notes/{document_id}")
def update_note_route(request: Request, document_id: str, body: NoteBody):
    from ..notes import update_note

    if body.format not in ("markdown", "rich"):
        return JSONResponse({"detail": "format must be markdown|rich"},
                            status_code=400)
    with _COMMIT_LOCK:
        repo = open_repo(request)
        try:
            doc = repo.get_document(document_id)
            if doc is None:
                return JSONResponse({"detail": "not found"}, status_code=404)
            if doc.source_type != "note":
                return JSONResponse({"detail": "not a note"}, status_code=400)
            try:
                doc = update_note(repo, request.app.state.embedder, doc,
                                  title=body.title, content=body.content,
                                  fmt=body.format, tags=body.tags)
            except ValueError as e:
                return JSONResponse({"detail": str(e)}, status_code=400)
            return {"id": doc.id, "title": doc.title}
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
            caps = _doc_capabilities(doc.source_type)
            if not caps["editable_units"]:
                return JSONResponse(
                    {"detail": f"this knowledge is edited in its own editor "
                               f"({caps['editor']})"}, status_code=400)
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
    embeddings = embedder.embed_passages([unit_index_text(units[0])])
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


class TokenCreateBody(BaseModel):
    name: str = Field(max_length=80)
    scopes: list[str] = Field(default_factory=lambda: ["read", "write"])


@router.post("/tokens")
def create_token(request: Request, body: TokenCreateBody):
    """Mint a scoped per-device token (admin only — enforced in middleware).
    The plaintext appears in THIS response only; the server stores a hash."""
    allowed = {"read", "write", "admin"}
    scopes = [sc for sc in body.scopes if sc in allowed]
    if not scopes:
        return JSONResponse({"detail": "scopes must be a subset of "
                             "read/write/admin"}, status_code=400)
    repo = open_repo(request)
    try:
        token_id, plaintext = repo.create_token(body.name, scopes)
        return {"id": token_id, "name": body.name, "scopes": scopes,
                "token": plaintext}
    finally:
        repo.close()


@router.get("/tokens")
def list_tokens(request: Request):
    repo = open_repo(request)
    try:
        return {"tokens": repo.list_tokens()}
    finally:
        repo.close()


@router.delete("/tokens/{token_id}")
def revoke_token(request: Request, token_id: int):
    repo = open_repo(request)
    try:
        if not repo.revoke_token(token_id):
            return JSONResponse({"detail": "not found"}, status_code=404)
        return {"status": "revoked", "id": token_id}
    finally:
        repo.close()


@router.delete("/data")
def wipe(request: Request, factory: bool = False):
    repo = open_repo(request)
    try:
        n = repo.wipe(factory=factory)
        if not factory:
            repo.log_event("wipe", {"via": "api", "documents": n})
        return {"status": "wiped", "documents": n, "factory": factory}
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
                            prefer_local_on_tie=True,
                            embedder=request.app.state.embedder)
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
        config = request.app.state.config
        return {"app": "dronacharya", "version": __version__,
                "counts": repo.counts(),
                # capability flags (booleans only — no config values leak):
                # clients use these so e.g. `dc doctor` can say "grounding
                # happens on the server" instead of a misleading local warning
                "features": {
                    "searxng": bool(config.websearch.searx_url),
                    "llm": bool(config.llm.provider_order),
                }}
    finally:
        repo.close()
