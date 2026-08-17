from __future__ import annotations

import re
from pathlib import Path

from ..chunking import chunk_document
from .base import ParsedFile, ParsedSection


class MarkdownParser:
    def parse(self, path: Path) -> ParsedFile | None:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        if not text.strip():
            return None
        m = re.search(r"^#\s+(.+)$", text, flags=re.MULTILINE)
        title = m.group(1).strip() if m else path.stem
        sections = [ParsedSection(s.heading_path, s.text) for s in chunk_document(text)]
        return ParsedFile(title=title, source_type="markdown", sections=sections)
