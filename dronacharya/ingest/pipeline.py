"""The single write path: extract → guardrails → dedup/consent → distill → embed → index.

Save semantics for an already-saved URL (see ARCHITECTURE.md):
  unchanged page  -> status "unchanged", nothing written
  changed page    -> status "needs_consent" with old/new abstracts, unless
                     overwrite=True (user's right to overwrite without reading)
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..config import Config
from ..embeddings import Embedder
from ..guardrails.pii import apply_pii_policy
from ..guardrails.policy import get_policy
from ..models import Document, KnowledgeUnit, SaveOutcome, UnitKind, unit_index_text
from . import extract as extract_mod
from .fetch import allow_private_urls
from .distill import get_distiller
from .parsers import get_parser


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _guard(repo, config: Config, text: str, title: str) -> tuple[str, str | None]:
    """Apply PII + policy guardrails. Returns (clean_text, block_reason|None)."""
    text, findings, blocked = apply_pii_policy(text, config.guardrails.pii_mode)
    if findings:
        repo.log_event("pii_detected", {"kinds": sorted({f.kind for f in findings}),
                                        "count": len(findings), "blocked": blocked})
    if blocked:
        return text, "contains sensitive data (PII/secrets) and pii_mode=block"
    decision = get_policy(config.guardrails.policy).check(text, title=title)
    if decision.action != "allow":
        repo.log_event("policy_" + decision.action, {"title": title, "reason": decision.reason})
    if decision.action == "block":
        return text, "blocked by content policy"
    return text, None


def _units_from_distillation(doc: Document, dist) -> list[KnowledgeUnit]:
    return [
        KnowledgeUnit(
            document_id=doc.id, seq=i, text=u.text, kind=u.kind,
            heading_path=u.heading_path, lang=doc.lang,
        )
        for i, u in enumerate(dist.units)
    ]


def preview_web(
    repo, config: Config, url: str, *, html: str | None = None, title_hint: str = "",
) -> tuple[str, "extract_mod.Extracted | None", Document | None, str]:
    """Fast pre-flight for async servers: extract + guard + dedup decision,
    WITHOUT the (slow) distillation. Returns (status, extracted, existing, hash)
    where status is one of accepted|unchanged|needs_consent|blocked."""
    if html is not None:
        extracted = extract_mod.from_html(html, url=url, title_hint=title_hint)
    elif config.extraction.primary == "firecrawl" and config.extraction.firecrawl_api_key:
        extracted = extract_mod.from_firecrawl(url, config.extraction.firecrawl_api_key)
    else:
        extracted = extract_mod.from_url(url, allow_private=allow_private_urls(config))
    if extracted is None:
        return "blocked", None, None, ""
    text, block_reason = _guard(repo, config, extracted.text, extracted.title)
    if block_reason:
        return "blocked", None, None, ""
    extracted.text = text
    content_hash = _hash(text)
    existing = repo.get_document_by_url(url)
    if existing and existing.content_hash == content_hash:
        return "unchanged", extracted, existing, content_hash
    if existing:
        return "needs_consent", extracted, existing, content_hash
    return "accepted", extracted, None, content_hash


def save_web(
    repo, embedder: Embedder, config: Config, url: str, *,
    html: str | None = None, title_hint: str = "",
    tags: list[str] | None = None, note: str | None = None,
    overwrite: bool = False, distiller=None,
) -> SaveOutcome:
    # --- extract exactly one page (never follows links) -------------------
    if html is not None:
        extracted = extract_mod.from_html(html, url=url, title_hint=title_hint)
    elif config.extraction.primary == "firecrawl" and config.extraction.firecrawl_api_key:
        extracted = extract_mod.from_firecrawl(url, config.extraction.firecrawl_api_key)
    else:
        extracted = extract_mod.from_url(url, allow_private=allow_private_urls(config))
    if extracted is None:
        return SaveOutcome(status="blocked", message="could not extract readable content")

    text, block_reason = _guard(repo, config, extracted.text, extracted.title)
    if block_reason:
        return SaveOutcome(status="blocked", message=block_reason)

    content_hash = _hash(text)
    existing = repo.get_document_by_url(url)
    if existing and existing.content_hash == content_hash:
        repo.log_event("save_unchanged", {"url": url})
        return SaveOutcome(
            status="unchanged", document_id=existing.id,
            message=f"already in your knowledge base (saved {existing.created_at[:10]})",
        )

    dist = (distiller or get_distiller(config)).distill(extracted.title, text, extracted.lang)

    if existing and not overwrite:
        repo.log_event("save_needs_consent", {"url": url})
        return SaveOutcome(
            status="needs_consent", document_id=existing.id,
            message="page content changed since it was saved — confirm update",
            old_summary=existing.summary, new_summary=dist.summary,
        )

    if existing:
        doc = existing
        doc.title = extracted.title
        doc.summary = dist.summary
        doc.content_hash = content_hash
        doc.distilled = dist.distilled
        doc.distill_tier = dist.tier
        doc.lang = extracted.lang
        if note:
            doc.saved_note = note
        units = _units_from_distillation(doc, dist)
        repo.replace_document(doc, units, embedder.embed_passages([unit_index_text(u) for u in units]))
        status = "updated"
    else:
        doc = Document(
            source_type="web", title=extracted.title, url=url, saved_note=note,
            summary=dist.summary, content_hash=content_hash,
            distilled=dist.distilled, distill_tier=dist.tier, lang=extracted.lang,
        )
        units = _units_from_distillation(doc, dist)
        repo.insert_document(doc, units, embedder.embed_passages([unit_index_text(u) for u in units]))
        status = "created"

    if tags:
        repo.set_tags(doc.id, tags)
    repo.log_event("save_" + status, {"url": url, "units": len(units)})
    return SaveOutcome(status=status, document_id=doc.id, message=doc.title)


def redistill_document(repo, embedder: Embedder, config: Config, document_id: str) -> bool:
    """Upgrade a fallback-distilled document to real distilled knowledge.
    Used by `dc redistill` and the server's sync upgrade pass. Returns True on upgrade."""
    from .distill import get_distiller

    doc = repo.get_document(document_id)
    if doc is None or doc.distilled:
        return False
    units = []
    for d, us in repo.iter_documents_with_units():
        if d.id == document_id:
            units = us
            break
    text = None
    if doc.url:
        extracted = extract_mod.from_url(doc.url, allow_private=allow_private_urls(config))
        if extracted:
            text = extracted.text
    if text is None:
        text = "\n\n".join(u.text for u in units)
    try:
        dist = get_distiller(config).distill(doc.title, text, doc.lang)
    except Exception:  # noqa: BLE001
        return False
    if not dist.distilled:
        return False
    doc.summary = dist.summary
    doc.distilled = True
    doc.distill_tier = dist.tier
    new_units = _units_from_distillation(doc, dist)
    repo.replace_document(doc, new_units,
                          embedder.embed_passages([unit_index_text(u) for u in new_units]))
    repo.log_event("redistill", {"document_id": document_id, "tier": dist.tier})
    return True


def save_note_file(repo, embedder: Embedder, config: Config, path: Path) -> SaveOutcome:
    """Ingest a user-authored file (notes/PDF). The user's own content is stored
    as-is (chunked, kind='note') — the no-original-content rule applies only to
    web pages."""
    parser = get_parser(path)
    if parser is None:
        return SaveOutcome(status="blocked", message=f"no parser for {path.suffix}")
    parsed = parser.parse(path)
    if parsed is None:
        return SaveOutcome(status="blocked", message="file empty or unparseable")

    full_text = "\n\n".join(s.text for s in parsed.sections)
    clean_text, block_reason = _guard(repo, config, full_text, parsed.title)
    if block_reason:
        return SaveOutcome(status="blocked", message=block_reason)
    redactions = clean_text != full_text

    content_hash = _hash(full_text)
    file_key = str(path.resolve())
    existing = repo.get_document_by_path(file_key)
    if existing and existing.content_hash == content_hash:
        return SaveOutcome(status="unchanged", document_id=existing.id)

    lang = extract_mod._detect_lang(full_text)
    doc = existing or Document(
        source_type=parsed.source_type, title=parsed.title, file_path=file_key,
    )
    doc.title = parsed.title
    doc.content_hash = content_hash
    doc.lang = lang
    doc.distilled = True  # user content isn't distilled; it IS the knowledge
    doc.distill_tier = "user-content"
    doc.summary = parsed.sections[0].text[:300] if parsed.sections else None

    units: list[KnowledgeUnit] = []
    for i, section in enumerate(parsed.sections):
        text = section.text
        if redactions:
            text, _, _ = apply_pii_policy(text, config.guardrails.pii_mode)
        units.append(
            KnowledgeUnit(
                document_id=doc.id, seq=i, text=text, kind=UnitKind.NOTE,
                heading_path=section.heading_path, lang=lang,
            )
        )
    embeddings = embedder.embed_passages([unit_index_text(u) for u in units])
    if existing:
        repo.replace_document(doc, units, embeddings)
        status = "updated"
    else:
        repo.insert_document(doc, units, embeddings)
        status = "created"
    repo.log_event("notes_" + status, {"path": file_key, "units": len(units)})
    return SaveOutcome(status=status, document_id=doc.id, message=doc.title)
