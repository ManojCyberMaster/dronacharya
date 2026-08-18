"""Quick answers: `dc "how do I …"` → the shortest correct answer.

Command questions get the command line + one example; anything else gets at
most two sentences. Flow: KB retrieval first (terse prompt over the user's
saved knowledge). If the KB can't answer, fall back to the provider chain,
grounded on fetched SearxNG result pages when configured. Only grounded
answers can be "high" confidence and auto-embed into the KB; ungrounded model
memory is always low confidence and user-vetted (the CLI does the asking).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .config import Config
from .embeddings import Embedder
from .llm import get_provider_chain, run_complete
from .llm.base import ProviderUnavailable
from .llm.prompts import (QUICK_SEARX_SYSTEM, QUICK_SYSTEM, QUICK_WEB_SYSTEM,
                          RAG_USER, answer_language)
from .models import Document, KnowledgeUnit, UnitKind
from .search import hybrid_search

NOT_IN_KB = "NOT_IN_KB"
QUICK_TAG = "quick-ask"


@dataclass
class QuickResult:
    mode: str                 # kb | web | declined | no_provider
    answer: str = ""
    source_url: str = ""
    source_urls: list[str] = field(default_factory=list)  # all cited sources
    provider: str = ""
    confidence: str = ""      # web mode: high | low
    grounded: bool = False    # answered FROM fetched web pages (not model memory)
    save_payload: dict | None = None   # web mode: what install-into-KB would store
    error: str = ""


def _kb_context(sources) -> str:
    lines = []
    for i, r in enumerate(sources, 1):
        origin = r.document.url or r.document.file_path or ""
        lines.append(f"[{i}] {r.document.title} ({origin})\n{r.unit.text}")
    return "\n\n".join(lines)


def _parse_web_json(raw: str) -> dict | None:
    from .llm import loads_lenient

    payload = loads_lenient(raw)   # repairs the \* escapes shell answers love
    if payload is None:
        return None
    if "answer" not in payload and "command" not in payload:
        return None
    # "command" is the pre-2026-08 key; an empty answer is a valid refusal
    payload["answer"] = str(payload.get("answer", payload.get("command", ""))).strip()
    return payload


def _searx_pages(config: Config, question: str) -> list[tuple[str, str]]:
    """Search the user's own SearxNG and fetch the top result pages.
    Returns [(url, extracted_text)] — real, verified-by-fetch sources."""
    import urllib.parse
    import urllib.request

    from .ingest import extract as extract_mod
    from .ingest.fetch import allow_private_urls, fetch_page

    base = config.websearch.searx_url.rstrip("/")
    q = urllib.parse.urlencode({"q": question, "format": "json", "language": "en"})
    req = urllib.request.Request(f"{base}/search?{q}",
                                 headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        hits = json.loads(resp.read()).get("results", [])
    pages: list[tuple[str, str]] = []
    for hit in hits:
        if len(pages) >= config.websearch.max_pages:
            break
        url = hit.get("url", "")
        if not url:
            continue
        html, err = fetch_page(url, timeout=20,
                               allow_private=allow_private_urls(config))
        if err:
            continue
        extracted = extract_mod.from_html(html, url=url)
        if extracted and len(extracted.text) > 200:
            pages.append((url, extracted.text[:6000]))
    return pages


def _answer_supported(config, answer: str, pages: list[tuple[str, str]],
                      cited_url: str) -> bool | None:
    """Claim-to-passage verification: does the cited page actually SUPPORT the
    answer text? Scored by the cross-encoder (the same model that reranks
    search). None = can't verify (no reranker available) — caller keeps the
    weaker URL-membership grounding. Threshold is deliberately permissive
    (terse command answers vs prose pages score lower than QA pairs)."""
    import math

    from .reranker import get_reranker

    reranker = get_reranker(config)
    if reranker is None or not answer.strip():
        return None
    texts = [t for u, t in pages if u == cited_url] or [t for _, t in pages]
    if not texts:
        return None
    try:
        model = reranker._load()
        # best-supporting window of the cited page
        windows = [texts[0][i:i + 1500] for i in range(0, min(len(texts[0]), 6000), 1500)]
        scores = model.predict([(answer, w) for w in windows if w.strip()])
        support = max(1.0 / (1.0 + math.exp(-float(x))) for x in scores)
    except Exception:  # noqa: BLE001 — verification must never break answering
        return None
    return support >= 0.05


def quick_ask(
    repo, embedder: Embedder, config: Config, question: str, *,
    chain=None, web_chain=None, verify_fetch=None,
) -> QuickResult:
    from .reranker import get_reranker

    reranker = get_reranker(config)
    sources = hybrid_search(
        repo, embedder, question, top_k=6,
        candidates=config.retrieval.candidates, reranker=reranker,
    )
    from .search import confidence_gate
    confident = bool(sources) and sources[0].score >= confidence_gate(config, reranker)
    chain = chain if chain is not None else get_provider_chain(config, task="answer")

    if confident:
        try:
            text, provider = run_complete(
                chain, QUICK_SYSTEM.format(language=answer_language(question)),
                RAG_USER.format(context=_kb_context(sources), question=question),
                max_tokens=300,
            )
        except ProviderUnavailable as e:
            repo.log_event("quick_no_provider", {"error": str(e)})
            return QuickResult(mode="no_provider", error=str(e))
        if text.strip() != NOT_IN_KB:
            import re

            # a synthesized answer may draw on several KB items — attribute
            # every cited [n], not just the top-ranked document
            raw_answer = text.strip()
            cited = {int(m) for m in re.findall(r"\[(\d{1,2})\]", raw_answer)}
            used = [sources[i - 1] for i in sorted(cited)
                    if 1 <= i <= len(sources)] or sources[:1]
            urls: list[str] = []
            for r in used:
                u = r.document.url or r.document.file_path or ""
                if u and u not in urls:
                    urls.append(u)
            repo.log_event("quick_kb", {"q": question[:200]})
            return QuickResult(
                mode="kb",
                answer=re.sub(r"\s*\[\d{1,2}\]", "", raw_answer).strip(),
                provider=provider,
                source_url=urls[0] if urls else "", source_urls=urls)

    # --- KB can't answer: go to the internet (or general model knowledge) ----
    if web_chain is None:
        web_chain = list(chain)   # same providers the user configured

    # Grounded path: user's own SearxNG finds real pages, the LLM answers
    # FROM them — sources are verified by construction (we fetched them).
    searx_urls: set[str] = set()
    # these two prompts contain literal JSON braces, so no str.format on them
    lang_rule = f'\nWrite "answer" and "summary" in {answer_language(question)}.'
    system, user_msg = QUICK_WEB_SYSTEM + lang_rule, f"Question: {question}"
    if config.websearch.searx_url:
        try:
            pages = _searx_pages(config, question)
        except Exception as e:  # noqa: BLE001 — searx down: general fallback
            repo.log_event("quick_searx_error", {"error": str(e)[:200]})
            pages = []
        if pages:
            searx_urls = {u for u, _ in pages}
            context = "\n\n".join(f"=== PAGE: {u} ===\n{t}" for u, t in pages)
            system = QUICK_SEARX_SYSTEM + lang_rule
            user_msg = f"{context}\n\nQuestion: {question}"
    try:
        raw, provider = run_complete(web_chain, system, user_msg, max_tokens=1000)
    except ProviderUnavailable as e:
        repo.log_event("quick_no_provider", {"error": str(e)})
        return QuickResult(mode="no_provider", error=str(e))

    payload = _parse_web_json(raw)
    if payload is None:
        # unparseable — surface the text, but nothing trustworthy to save
        repo.log_event("quick_web_unparsed", {"q": question[:200], "provider": provider})
        return QuickResult(mode="web", answer=raw.strip()[:800], provider=provider,
                           confidence="low", grounded=bool(searx_urls))
    answer_text = payload["answer"]
    if not answer_text:
        # the model declined (harmful/nonsensical question) — nothing to save
        reason = str(payload.get("summary", "")).strip() or "No answer."
        repo.log_event("quick_declined", {"q": question[:200], "provider": provider})
        return QuickResult(mode="declined", answer=reason, provider=provider)
    example = str(payload.get("example", "")).strip()
    source_url = str(payload.get("source_url", "")).strip()
    confidence = payload.get("confidence", "low")
    grounded = bool(searx_urls) and source_url in searx_urls
    if grounded and confidence == "high":
        # grounding proves the page was FETCHED; verification asks whether it
        # actually supports the claim before "high" is allowed to stand
        supported = _answer_supported(config, answer_text, pages, source_url)
        if supported is False:
            repo.log_event("quick_unsupported_demoted",
                           {"q": question[:200], "url": source_url})
            confidence = "low"
    if source_url and not grounded:
        # Providers without web access invent plausible doc URLs — drop a
        # cited source that doesn't actually exist rather than show it.
        # (Searx-grounded sources were fetched already — no second fetch.)
        if verify_fetch is None:
            from .ingest.fetch import allow_private_urls, fetch_page

            _ap = allow_private_urls(config)

            def verify_fetch(u, timeout=15, _ap=_ap):
                return fetch_page(u, timeout=timeout, allow_private=_ap)
        _, fetch_err = verify_fetch(source_url, timeout=15)
        if fetch_err:
            repo.log_event("quick_source_unverified",
                           {"url": source_url, "error": fetch_err})
            source_url = ""
    if confidence == "high" and not grounded:
        # A reachable URL doesn't prove the ANSWER is right (the model never
        # read the page). Without grounded web pages, self-reported "high" is
        # just model memory — never auto-saved, always user-vetted.
        confidence = "low"
    answer = answer_text + (f"\ne.g. {example}" if example else "")
    repo.log_event("quick_web", {"q": question[:200], "provider": provider,
                                 "confidence": confidence, "grounded": grounded})
    return QuickResult(
        mode="web", answer=answer, provider=provider, source_url=source_url,
        source_urls=[source_url] if source_url else [],
        confidence=confidence, grounded=grounded,
        save_payload={"answer": answer_text, "example": example,
                      "source_url": source_url,
                      "summary": str(payload.get("summary", "")).strip()},
    )


def save_quick_answer(
    repo, embedder: Embedder, config: Config, question: str, payload: dict,
    provider: str, confidence: str = "high", user_verified: bool = False,
) -> str:
    """Embed a web-qualified quick answer into the KB as a howto unit.
    A user-vetted save is stored as high confidence — the user is the
    authority. Returns the new document id."""
    if user_verified:
        confidence = "high"
    source_url = payload.get("source_url") or None
    url = source_url
    if url and repo.get_document_by_url(url):
        url = None  # source page already saved — keep the Q&A, avoid URL clash
    text = payload.get("answer") or payload.get("command")  # "command": pre-2026-08 key
    if not str(text or "").strip():
        raise ValueError("quick-answer payload has no answer text")
    if payload.get("example"):
        text += f"\ne.g. {payload['example']}"
    if payload.get("summary"):
        text += f"\n{payload['summary']}"
    doc = Document(
        source_type="web" if url else "text",
        title=question[:200], url=url,
        summary=payload.get("summary") or text.split("\n", 1)[0],
        distilled=True, distill_tier=f"quick:{provider}", lang="en",
        meta={"quick_ask": True, "source_url": source_url or "",
              "confidence": confidence, "user_verified": user_verified},
    )
    units = [KnowledgeUnit(document_id=doc.id, seq=0, text=text,
                           kind=UnitKind.HOWTO, heading_path=question[:120])]
    repo.insert_document(doc, units, embedder.embed_passages([u.text for u in units]))
    repo.set_tags(doc.id, [QUICK_TAG])
    repo.log_event("quick_saved", {"document_id": doc.id, "source": source_url or ""})
    return doc.id
