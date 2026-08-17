"""Browser-bookmarks importer (Netscape bookmarks.html — the export format of
Chrome, Edge, Brave, and Firefox).

- Bookmark folders become hierarchical tags: folder nesting joins with "/"
  ("Research/RAG"). Root container folders ("Bookmarks bar", "Other bookmarks",
  …) are dropped as noise.
- Dedup: already-imported URLs are skipped without fetching (fast re-runs);
  --refresh re-fetches and updates changed pages.
- Every fetch failure is classified and reported at the end: dead pages are
  told to the user, never silently dropped.
- Per the crawl restriction, only each bookmarked page itself is fetched —
  never anything it links to.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from ..config import Config
from ..embeddings import Embedder
from ..ingest.distill import ExtractiveDistiller
from ..ingest.fetch import fetch_page
from ..ingest.pipeline import save_web

_ROOT_CONTAINERS = {"bookmarks bar", "bookmarks menu", "other bookmarks",
                    "mobile bookmarks", "favorites bar", "favorites",
                    "imported", "bookmarks"}


@dataclass
class Bookmark:
    url: str
    title: str
    folder_tag: str | None  # "parent/child" or None


class _NetscapeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.bookmarks: list[Bookmark] = []
        self._stack: list[str | None] = []
        self._pending_folder: str | None = None
        self._collect_h3 = False
        self._h3_text = ""
        self._current_href: str | None = None
        self._a_text = ""

    def handle_starttag(self, tag, attrs):
        if tag == "h3":
            self._collect_h3 = True
            self._h3_text = ""
        elif tag == "a":
            self._current_href = dict(attrs).get("href")
            self._a_text = ""
        elif tag == "dl":
            self._stack.append(self._pending_folder)
            self._pending_folder = None

    def handle_data(self, data):
        if self._collect_h3:
            self._h3_text += data
        elif self._current_href is not None:
            self._a_text += data

    def handle_endtag(self, tag):
        if tag == "h3":
            self._collect_h3 = False
            self._pending_folder = self._h3_text.strip()
        elif tag == "dl":
            if self._stack:
                self._stack.pop()
        elif tag == "a" and self._current_href:
            url = self._current_href.strip()
            if url.startswith(("http://", "https://")):
                parts = [f.replace("/", "-") for f in self._stack if f]
                if parts and parts[0].lower() in _ROOT_CONTAINERS:
                    parts = parts[1:]
                self.bookmarks.append(Bookmark(
                    url=url,
                    title=self._a_text.strip() or url,
                    folder_tag="/".join(parts) or None,
                ))
            self._current_href = None


def parse_bookmarks_html(path: Path) -> list[Bookmark]:
    parser = _NetscapeParser()
    parser.feed(path.read_text(encoding="utf-8", errors="replace"))
    seen: set[str] = set()
    out = []
    for bm in parser.bookmarks:
        if bm.url not in seen:
            seen.add(bm.url)
            out.append(bm)
    return out


@dataclass
class ImportReport:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped_existing: int = 0
    blocked: list[tuple[str, str]] = field(default_factory=list)   # (url, reason)
    dead: list[tuple[str, str, str]] = field(default_factory=list)  # (url, title, reason)

    @property
    def total_processed(self) -> int:
        return (self.created + self.updated + self.unchanged
                + self.skipped_existing + len(self.blocked) + len(self.dead))


def import_bookmarks(
    repo, embedder: Embedder, config: Config, path: Path, *,
    refresh: bool = False, llm_distill: bool = False, limit: int | None = None,
    fetch=fetch_page, progress=None,
) -> ImportReport:
    """Import (or refresh) bookmarks into the knowledge base.

    Default distillation is the fast extractive mode so bulk imports stay
    quick and cheap; the saved pages are marked for upgrade and get properly
    distilled later by `dc redistill` or the home server's sync upgrade pass.
    Pass llm_distill=True to distill fully during import.
    """
    report = ImportReport()
    bookmarks = parse_bookmarks_html(path)
    if limit:
        bookmarks = bookmarks[:limit]
    distiller = None if llm_distill else ExtractiveDistiller()

    for i, bm in enumerate(bookmarks, 1):
        if progress:
            progress(i, len(bookmarks), bm)
        existing = repo.get_document_by_url(bm.url)
        if existing and not refresh:
            # no fetch — but still merge the folder tag in
            if bm.folder_tag:
                merged = sorted(set(repo.get_tags(existing.id)) | {bm.folder_tag})
                if merged != repo.get_tags(existing.id):
                    repo.set_tags(existing.id, merged)
            report.skipped_existing += 1
            continue

        html, err = fetch(bm.url)
        if err:
            report.dead.append((bm.url, bm.title, err))
            continue

        tags = None
        if bm.folder_tag:
            base = set(repo.get_tags(existing.id)) if existing else set()
            tags = sorted(base | {bm.folder_tag})
        outcome = save_web(repo, embedder, config, bm.url, html=html,
                           title_hint=bm.title, tags=tags,
                           overwrite=True, distiller=distiller)
        if outcome.status == "created":
            report.created += 1
        elif outcome.status == "updated":
            report.updated += 1
        elif outcome.status == "unchanged":
            report.unchanged += 1
        else:  # blocked: guardrails or no readable content
            report.dead.append((bm.url, bm.title, outcome.message or "no readable content"))

    repo.log_event("import_bookmarks", {
        "file": str(path), "created": report.created, "updated": report.updated,
        "skipped": report.skipped_existing, "dead": len(report.dead),
    })
    return report
