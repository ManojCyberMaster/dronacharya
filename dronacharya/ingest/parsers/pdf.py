from __future__ import annotations

from pathlib import Path

from ..chunking import chunk_text
from .base import ParsedFile, ParsedSection


class PdfParser:
    def parse(self, path: Path) -> ParsedFile | None:
        from pypdf import PdfReader

        try:
            reader = PdfReader(str(path))
        except Exception:
            return None
        title = path.stem
        try:
            if reader.metadata and reader.metadata.title:
                title = str(reader.metadata.title)
        except Exception:
            pass
        sections: list[ParsedSection] = []
        skipped = 0
        try:
            # An encrypted PDF raises on PAGE ACCESS, not in the constructor, so
            # iterating reader.pages outside a try surfaced as an unhandled 500
            # on upload instead of the normal "unparseable file" answer.
            pages = list(reader.pages)
        except Exception:
            return None
        for i, page in enumerate(pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception:
                skipped += 1     # one unreadable page must not lose the rest
                continue
            for chunk in chunk_text(text):
                sections.append(ParsedSection(f"page {i}", chunk.text))
        if not sections:
            return None
        if skipped:
            # state the loss in the content itself, the way the sheet-row
            # truncation does — a silently short document reads as complete
            sections.append(ParsedSection(
                None, f"… ({skipped} page(s) of this PDF could not be read — "
                      f"they may be scanned images, which need OCR)"))
        return ParsedFile(title=title, source_type="pdf", sections=sections)
