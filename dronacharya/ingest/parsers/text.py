from __future__ import annotations

from pathlib import Path

from ..chunking import chunk_text
from .base import ParsedFile, ParsedSection


class TextParser:
    def parse(self, path: Path) -> ParsedFile | None:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        if not text.strip():
            return None
        sections = [ParsedSection(None, s.text) for s in chunk_text(text)]
        return ParsedFile(title=path.stem, source_type="text", sections=sections)
