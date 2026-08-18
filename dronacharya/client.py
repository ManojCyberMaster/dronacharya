"""Transport facade: one call per command, answered by the home server when
reachable and locally otherwise — the CALLER never branches on transport.

Before this facade every CLI command hand-rolled the remote-then-local dance,
and the two render paths drifted (dict keys vs dataclass attrs; flags that
never reached the server). A future mobile/thin client is exactly the remote
half of this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import Config

_REMOTE_DOWN = False   # per-process: first unreachable check skips later tries


def remote_api(config: Config, path: str, payload: dict, timeout: int = 90,
               *, skip: bool = False) -> dict | None:
    """POST to the configured home server; None when unreachable (caller
    falls back to fully-local operation — offline-first, by design).
    A 3s TCP preflight keeps offline laptops instant: without it, a server
    that silently drops packets stalls every command for the full timeout."""
    global _REMOTE_DOWN
    import json as json_mod
    import socket
    import urllib.request
    from urllib.parse import urlparse

    if (skip or _REMOTE_DOWN or config.deployment.role != "client"
            or not config.server.remote_url):
        return None
    u = urlparse(config.server.remote_url)
    try:
        socket.create_connection(
            (u.hostname, u.port or (443 if u.scheme == "https" else 80)),
            timeout=3).close()
    except OSError:
        _REMOTE_DOWN = True
        return None
    req = urllib.request.Request(
        config.server.remote_url.rstrip("/") + "/api/v1" + path,
        data=json_mod.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {config.server.token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json_mod.loads(resp.read())
    except Exception:  # noqa: BLE001 — offline / server down / auth mismatch
        return None


@dataclass
class AskOutcome:
    """Normalized quick-answer result, identical for both transports."""

    origin: str                  # "server" | "local"
    mode: str                    # kb | web | declined | no_provider
    answer: str = ""
    source_urls: list[str] = field(default_factory=list)
    provider: str = ""
    confidence: str = ""
    grounded: bool = False
    saved: bool = False          # already auto-saved (grounded high, server side)
    save_payload: dict | None = None
    error: str = ""


def ask_remote(config: Config, question: str, *,
               no_save: bool = False) -> AskOutcome | None:
    """Server half; None when no server is configured/reachable."""
    remote = remote_api(config, "/ask",
                        {"question": question, "no_save": no_save})
    if remote is None:
        return None
    return AskOutcome(
        origin="server", mode=remote["mode"], answer=remote.get("answer", ""),
        source_urls=remote.get("source_urls")
        or ([remote["source_url"]] if remote.get("source_url") else []),
        provider=remote.get("provider", ""),
        confidence=remote.get("confidence", ""),
        grounded=remote.get("grounded", False),
        saved=remote.get("saved", False),
        save_payload=remote.get("save_payload"),
        error=remote.get("error", ""))


def ask_local(config: Config, repo, embedder, question: str, *,
              no_save: bool = False) -> AskOutcome:
    from .quick import quick_ask, save_quick_answer

    result = quick_ask(repo, embedder, config, question)
    saved = False
    if (result.mode == "web" and result.confidence == "high"
            and result.save_payload and not no_save):
        save_quick_answer(repo, embedder, config, question,
                          result.save_payload, result.provider)
        saved = True
    return AskOutcome(
        origin="local", mode=result.mode, answer=result.answer,
        source_urls=result.source_urls
        or ([result.source_url] if result.source_url else []),
        provider=result.provider, confidence=result.confidence,
        grounded=result.grounded, saved=saved,
        save_payload=None if saved else result.save_payload,
        error=result.error)


def save_vetted_answer(config: Config, question: str, payload: dict,
                       provider: str, *, origin: str,
                       repo=None, embedder=None) -> bool:
    """Persist a user-approved low-confidence answer on whichever side
    answered it."""
    if origin == "server":
        return remote_api(config, "/ask/save",
                          {"question": question, "payload": payload,
                           "provider": provider}) is not None
    from .quick import save_quick_answer

    save_quick_answer(repo, embedder, config, question, payload, provider,
                      user_verified=True)
    return True


def search_remote(config: Config, query: str, *, k: int = 8,
                  tags: list[str] | None = None,
                  show_all: bool = False) -> list[dict] | None:
    """Server half; the threshold was applied with the SERVER's tuned config.
    None when no server is configured/reachable."""
    remote = remote_api(config, "/search",
                        {"query": query, "k": k, "tags": tags,
                         "show_all": show_all}, timeout=30)
    return None if remote is None else remote.get("results", [])


def search_local(config: Config, repo, embedder, query: str, *, k: int = 8,
                 tags: list[str] | None = None,
                 show_all: bool = False) -> list[dict]:
    from .reranker import get_reranker
    from .search import confidence_gate, hybrid_search, result_to_json

    reranker = get_reranker(config)
    results = hybrid_search(repo, embedder, query, top_k=k,
                            candidates=config.retrieval.candidates,
                            reranker=reranker, tags=tags)
    if not show_all:
        results = [r for r in results
                   if r.score >= confidence_gate(config, reranker)]
    return [result_to_json(r) for r in results]
