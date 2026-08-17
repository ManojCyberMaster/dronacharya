"""Parser registry: file extension → parser."""

from __future__ import annotations

from pathlib import Path

from .base import ParsedFile, Parser
from .markdown import MarkdownParser
from .tdl import TdlParser
from .text import TextParser


def _pdf_parser() -> Parser:
    from .pdf import PdfParser

    return PdfParser()


_REGISTRY: dict[str, Parser | None] = {
    ".tdl": TdlParser(),
    ".md": MarkdownParser(),
    ".markdown": MarkdownParser(),
    ".txt": TextParser(),
}


def get_parser(path: Path) -> Parser | None:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return _pdf_parser()
    if ext in (".docx", ".xlsx", ".xlsm", ".pptx"):
        from . import office

        return {".docx": office.DocxParser(), ".pptx": office.PptxParser()}.get(
            ext, office.XlsxParser())
    return _REGISTRY.get(ext)


__all__ = ["ParsedFile", "Parser", "get_parser"]
