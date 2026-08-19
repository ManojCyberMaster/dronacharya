"""Direct notes — knowledge typed straight into DronaCharya (no page, no
file). Two formats, chosen by the user:

- markdown: sectioned by headings exactly like a synced .md file
- rich: the same formatting layer as mind-map node notes (whitelisted HTML,
  attributes stripped); indexed as plain text, original kept for re-editing

The note document is editor-owned (like mind maps / to-dos): its units are
derived, so edits go through update_note, never unit-level patches.
"""

from __future__ import annotations

from html.parser import HTMLParser

from .models import Document, KnowledgeUnit, SourceType, UnitKind, unit_index_text

# same whitelist as the mind-map note editor (attributes always stripped)
_KEEP = {"b", "i", "u", "s", "em", "strong", "p", "div", "br",
         "ul", "ol", "li", "h1", "h2", "h3", "span", "blockquote"}
_DROP_WITH_CONTENT = {"script", "style", "iframe", "object", "svg", "head"}


class _Sanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._dropping = 0

    def handle_starttag(self, tag, attrs):
        if tag in _DROP_WITH_CONTENT:
            self._dropping += 1
        elif not self._dropping and tag in _KEEP:
            self.out.append(f"<{tag}>")

    def handle_endtag(self, tag):
        if tag in _DROP_WITH_CONTENT:
            self._dropping = max(0, self._dropping - 1)
        elif not self._dropping and tag in _KEEP:
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if not self._dropping:
            self.out.append(data.replace("<", "&lt;").replace(">", "&gt;"))


def sanitize_note_html(html: str) -> str:
    s = _Sanitizer()
    s.feed(str(html or ""))
    return "".join(s.out).strip()


def _note_sections(content: str, fmt: str) -> list[tuple[str | None, str]]:
    """[(heading_path, text)] — markdown sections by heading; rich notes as
    plain-text chunks."""
    if fmt == "markdown":
        from .ingest.chunking import chunk_document

        return [(c.heading_path, c.text) for c in chunk_document(content)]
    from .ingest.chunking import chunk_text
    from .mindmap import note_text

    plain = note_text(content)
    return [(None, c.text) for c in chunk_text(plain)]


def _title_for(title: str, content: str, fmt: str) -> str:
    import re

    if title.strip():
        return title.strip()[:200]
    if fmt == "markdown":
        m = re.search(r"^#\s+(.+)$", content, flags=re.MULTILINE)
        if m:
            return m.group(1).strip()[:200]
        first = content.strip().split("\n", 1)[0]
    else:
        from .mindmap import note_text

        first = note_text(content).split("\n", 1)[0]
    return (first[:80] or "Untitled note").strip()


def _build(repo, embedder, doc: Document, title: str, content: str,
           fmt: str, tags: list[str] | None, *, is_new: bool) -> Document:
    from .ingest.extract import _detect_lang

    stored = sanitize_note_html(content) if fmt == "rich" else content
    sections = _note_sections(stored, fmt)
    if not sections:
        raise ValueError("note is empty")
    doc.title = _title_for(title, stored, fmt)
    doc.summary = sections[0][1][:300]
    doc.distilled = True                    # user content IS the knowledge
    doc.distill_tier = "user-content"
    doc.lang = _detect_lang(" ".join(t for _, t in sections))
    doc.meta = {**(doc.meta or {}), "note": True, "note_format": fmt,
                "note_source": stored,
                # remembers whether the title was typed or derived, so the
                # editor doesn't freeze a derived title as an explicit one
                "note_title_explicit": bool(title.strip())}
    units = [KnowledgeUnit(document_id=doc.id, seq=i, text=text,
                           kind=UnitKind.NOTE, heading_path=heading,
                           lang=doc.lang)
             for i, (heading, text) in enumerate(sections)]
    embeddings = embedder.embed_passages([unit_index_text(u) for u in units])
    if is_new:
        repo.insert_document(doc, units, embeddings)
    else:
        repo.replace_document(doc, units, embeddings)
    if tags is not None:
        repo.set_tags(doc.id, sorted(set(tags)))
    return doc


def create_note(repo, embedder, *, title: str = "", content: str,
                fmt: str = "markdown", tags: list[str] | None = None,
                owner: str | None = None) -> Document:
    from .models import LOCAL_TENANT

    doc = Document(source_type=SourceType.NOTE,
                   tenant_id=owner or LOCAL_TENANT, title="")
    doc = _build(repo, embedder, doc, title, content, fmt, tags, is_new=True)
    repo.log_event("note_created", {"document_id": doc.id, "format": fmt})
    return doc


def update_note(repo, embedder, doc: Document, *, title: str = "",
                content: str, fmt: str = "markdown",
                tags: list[str] | None = None) -> Document:
    doc = _build(repo, embedder, doc, title, content, fmt, tags, is_new=False)
    repo.log_event("note_updated", {"document_id": doc.id})
    return doc


# Files only — a document has file_path set only when it came from `dc add`
# or the web upload endpoint (TDL/PDF/office files). Saved web pages have a
# url instead, never a file_path, and are deliberately NOT eligible: their
# summary/units are already editable in place (same as the extension's
# review step), converting the whole document to a note is the wrong fix.


def _reconstruct_markdown(units) -> str:
    """Rebuild a markdown document from stored heading_path/text — the
    inverse of chunk_document(). Headings are only emitted where the path
    actually changes between consecutive units, so re-chunking the result
    reproduces the same heading hierarchy the document already has instead
    of a flat wall of repeated headings.

    One representational gap: markdown headings only ever narrow scope
    (there's no way to say "back to no heading at all" once inside one),
    so a unit that returns all the way to an empty heading_path right
    after nested ones ends up nested under the last real heading instead
    of top-level. The text itself is never lost — only that one case's
    heading attribution can drift, which the user can fix by hand same as
    anything else in a freshly-converted note."""
    lines: list[str] = []
    prev_path: list[str] = []
    for u in sorted(units, key=lambda x: x.seq):
        path = [p for p in (u.heading_path or "").split(" > ") if p]
        i = 0
        while i < len(path) and i < len(prev_path) and path[i] == prev_path[i]:
            i += 1
        if path and i == len(path) and i < len(prev_path):
            # path is a strict prefix of prev_path (moving to a SHALLOWER,
            # already-visited heading) — re-emit its own last segment so
            # split_markdown_sections() actually truncates back up to it,
            # instead of silently keeping the deeper heading active.
            i -= 1
        for level, name in enumerate(path[i:], start=i + 1):
            lines.append(f"{'#' * min(level, 6)} {name}")
        prev_path = path
        if u.text and u.text.strip():
            lines.append(u.text.strip())
    return "\n\n".join(lines)


def convert_to_note(repo, embedder, document_id: str) -> Document:
    """In-place: a TDL/PDF/office-file document becomes an editable note —
    same document, same id. The content stops being a frozen ingest
    snapshot (re-upload to change) and becomes something the user can
    actually edit, same as any note they typed directly. Files only —
    see the module-level note above on why saved web pages are excluded."""
    doc = repo.get_document(document_id)
    if doc is None:
        raise ValueError("document not found")
    if not doc.file_path:
        raise ValueError("only uploaded files can be converted to a note")
    units = repo.get_document_units(document_id)
    markdown = _reconstruct_markdown(units)
    if not markdown.strip():
        raise ValueError("nothing to convert — document has no content")
    original_type = doc.source_type
    doc.source_type = SourceType.NOTE
    doc.file_path = None   # stop claiming the original path — a future
    doc.url = None          # re-upload/re-save must not collide with this note
    doc.meta = {**(doc.meta or {}), "converted_from": original_type}
    doc = _build(repo, embedder, doc, title=doc.title or "", content=markdown,
                fmt="markdown", tags=repo.get_tags(document_id), is_new=False)
    repo.log_event("note_converted", {"document_id": doc.id, "from": original_type})
    return doc
