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
        for i, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception:
                continue
            for chunk in chunk_text(text):
                sections.append(ParsedSection(f"page {i}", chunk.text))
        if not sections:
            return None
        return ParsedFile(title=title, source_type="pdf", sections=sections)
