"""FastAPI app — /api/v1, stateless JSON + SSE.

Auth: static bearer token from config (single-user by design).
GET /api/v1/status and the static UI are exempt so the extension's
status dot and the pages themselves work before a token is entered.
"""

from __future__ import annotations

import threading
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..config import Config, db_path, load_config
from ..embeddings import get_embedder

STATIC_DIR = Path(__file__).resolve().parent.parent / "web" / "static"

# sentence-transformers encode isn't guaranteed thread-safe; serialize embeds.
EMBED_LOCK = threading.Lock()


class LockedEmbedder:
    def __init__(self, inner):
        self._inner = inner

    def embed_passages(self, texts):
        with EMBED_LOCK:
            return self._inner.embed_passages(texts)

    def embed_query(self, text):
        with EMBED_LOCK:
            return self._inner.embed_query(text)


def create_app(config: Config | None = None) -> FastAPI:
    config = config or load_config()
    app = FastAPI(title="DronaCharya", docs_url="/api/v1/docs", redoc_url=None,
                  openapi_url="/api/v1/openapi.json")
    app.state.config = config
    app.state.embedder = LockedEmbedder(get_embedder(config))
    app.state.db_path = db_path()

    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"chrome-extension://.*|moz-extension://.*",
        allow_methods=["*"],
        allow_headers=["*"],
    )

    MAX_BODY = 12 * 1024 * 1024   # rendered pages fit; nothing needs more

    @app.middleware("http")
    async def hardening(request: Request, call_next):
        try:
            if int(request.headers.get("content-length") or 0) > MAX_BODY:
                return JSONResponse({"detail": "request body too large"},
                                    status_code=413)
        except ValueError:
            return JSONResponse({"detail": "bad content-length"}, status_code=400)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response

    # POST endpoints that only READ; everything else non-GET needs "write"
    READ_POSTS = {"/api/v1/search", "/api/v1/query", "/api/v1/ask",
                  "/api/v1/graph"}
    # destructive / whole-KB endpoints need "admin"
    ADMIN_PREFIXES = ("/api/v1/export", "/api/v1/data", "/api/v1/tokens")

    @app.middleware("http")
    async def auth(request: Request, call_next):
        path = request.url.path
        exempt = (
            not path.startswith("/api/")
            or path in ("/api/v1/status", "/api/v1/docs", "/api/v1/openapi.json")
            or request.method == "OPTIONS"
        )
        token = config.server.token
        if not exempt and token:
            header = request.headers.get("authorization", "")
            presented = header.removeprefix("Bearer ").strip()
            if presented == token:
                scopes = ["read", "write", "admin"]   # config token = admin
            else:
                repo = open_repo(app)
                try:
                    scopes = repo.verify_token(presented)
                finally:
                    repo.close()
                if scopes is None:
                    return JSONResponse({"detail": "invalid or missing token"},
                                        status_code=401)
            request.state.scopes = scopes
            needs = "read"
            if path.startswith(ADMIN_PREFIXES):
                needs = "admin"
            elif request.method != "GET" and path not in READ_POSTS:
                needs = "write"
            if needs not in scopes:
                return JSONResponse(
                    {"detail": f"token lacks the '{needs}' scope"},
                    status_code=403)
        return await call_next(request)

    from .routes import router

    app.include_router(router, prefix="/api/v1")
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/library")
    async def library():
        return FileResponse(STATIC_DIR / "library.html")

    @app.get("/tags")
    async def tags_page():
        return FileResponse(STATIC_DIR / "tags.html")

    @app.get("/mindmap")
    async def mindmap():
        return FileResponse(STATIC_DIR / "mindmap.html")

    @app.get("/graph")
    async def graph():
        return FileResponse(STATIC_DIR / "graph.html")

    @app.get("/todos")
    async def todos():
        return FileResponse(STATIC_DIR / "todos.html")

    return app


def open_repo(app_or_request):
    """A fresh connection per request — connections are not shared across
    FastAPI's worker threads. Backend comes from config (sqlite or postgres)."""
    from ..storage import get_repo

    state = getattr(app_or_request, "app", app_or_request).state
    return get_repo(state.config)
