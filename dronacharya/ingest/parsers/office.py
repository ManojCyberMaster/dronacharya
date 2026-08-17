"""Office documents: .docx / .xlsx / .pptx (and .xlsm).

OOXML files are zip archives of XML — parsed here with the stdlib only
(zipfile + ElementTree), no python-docx/openpyxl/python-pptx dependency.
Good-enough text extraction for knowledge, with honest limitations:
formulas yield their cached values, dates in Excel appear as serial numbers
unless stored as text, and legacy binary formats (.doc/.xls/.ppt) are not
supported — resave them in the modern format.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from ..chunking import chunk_text
from .base import ParsedFile, ParsedSection

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PR = "{http://schemas.openxmlformats.org/package/2006/relationships}"

MAX_SHEET_ROWS = 200          # per sheet; truncation is stated in the section


def _read_xml(zf: zipfile.ZipFile, name: str) -> ElementTree.Element | None:
    try:
        return ElementTree.fromstring(zf.read(name))
    except (KeyError, ElementTree.ParseError):
        return None


class DocxParser:
    """Paragraph text, grouped under Heading-style paragraphs."""

    def parse(self, path: Path) -> ParsedFile | None:
        try:
            with zipfile.ZipFile(path) as zf:
                root = _read_xml(zf, "word/document.xml")
        except (OSError, zipfile.BadZipFile):
            return None
        if root is None:
            return None
        heading: str | None = None
        buf: list[str] = []
        sections: list[ParsedSection] = []

        def flush():
            text = "\n".join(buf).strip()
            buf.clear()
            if text:
                for chunk in chunk_text(text):
                    sections.append(ParsedSection(heading, chunk.text))

        for para in root.iter(f"{W}p"):
            text = "".join(t.text or "" for t in para.iter(f"{W}t")).strip()
            if not text:
                continue
            style = para.find(f"{W}pPr/{W}pStyle")
            style_val = style.get(f"{W}val", "") if style is not None else ""
            if re.match(r"(?i)heading|title", style_val):
                flush()
                heading = text
            else:
                buf.append(text)
        flush()
        if not sections:
            return None
        return ParsedFile(title=path.stem, source_type="docx", sections=sections)


class PptxParser:
    """One section per slide; the slide's first line acts as its heading."""

    def parse(self, path: Path) -> ParsedFile | None:
        sections: list[ParsedSection] = []
        try:
            with zipfile.ZipFile(path) as zf:
                slides = sorted(
                    (n for n in zf.namelist()
                     if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                    key=lambda n: int(re.search(r"\d+", n).group()))
                for idx, name in enumerate(slides, start=1):
                    root = _read_xml(zf, name)
                    if root is None:
                        continue
                    paras = []
                    for p in root.iter(f"{A}p"):
                        line = "".join(t.text or "" for t in p.iter(f"{A}t")).strip()
                        if line:
                            paras.append(line)
                    if not paras:
                        continue
                    title = paras[0][:80]
                    text = "\n".join(paras)
                    for chunk in chunk_text(text):
                        sections.append(
                            ParsedSection(f"slide {idx}: {title}", chunk.text))
        except (OSError, zipfile.BadZipFile):
            return None
        if not sections:
            return None
        return ParsedFile(title=path.stem, source_type="pptx", sections=sections)


class XlsxParser:
    """One section per sheet: rows as tab-separated lines (first row is
    usually the header). Large sheets are truncated with a note."""

    def parse(self, path: Path) -> ParsedFile | None:
        try:
            with zipfile.ZipFile(path) as zf:
                shared = self._shared_strings(zf)
                sheets = self._sheet_files(zf)
                sections: list[ParsedSection] = []
                for sheet_name, xml_name in sheets:
                    root = _read_xml(zf, xml_name)
                    if root is None:
                        continue
                    lines, truncated = self._rows(root, shared)
                    if not lines:
                        continue
                    text = "\n".join(lines)
                    if truncated:
                        text += f"\n… (truncated at {MAX_SHEET_ROWS} rows)"
                    for chunk in chunk_text(text):
                        sections.append(
                            ParsedSection(f"sheet: {sheet_name}", chunk.text))
        except (OSError, zipfile.BadZipFile):
            return None
        if not sections:
            return None
        return ParsedFile(title=path.stem, source_type="xlsx", sections=sections)

    @staticmethod
    def _shared_strings(zf: zipfile.ZipFile) -> list[str]:
        root = _read_xml(zf, "xl/sharedStrings.xml")
        if root is None:
            return []
        return ["".join(t.text or "" for t in si.iter(f"{S}t"))
                for si in root.iter(f"{S}si")]

    @staticmethod
    def _sheet_files(zf: zipfile.ZipFile) -> list[tuple[str, str]]:
        """[(sheet display name, zip member)] in workbook order."""
        wb = _read_xml(zf, "xl/workbook.xml")
        rels = _read_xml(zf, "xl/_rels/workbook.xml.rels")
        rel_map = {}
        if rels is not None:
            for rel in rels.iter(f"{PR}Relationship"):
                rel_map[rel.get("Id")] = "xl/" + rel.get("Target", "").lstrip("/")
        out: list[tuple[str, str]] = []
        if wb is not None:
            for sh in wb.iter(f"{S}sheet"):
                target = rel_map.get(sh.get(f"{R}id"))
                if target and target in zf.namelist():
                    out.append((sh.get("name", "sheet"), target))
        if not out:  # fallback: positional
            out = [(f"sheet {i}", n) for i, n in enumerate(sorted(
                m for m in zf.namelist()
                if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", m)), start=1)]
        return out

    @staticmethod
    def _rows(root, shared: list[str]) -> tuple[list[str], bool]:
        lines: list[str] = []
        truncated = False
        for row in root.iter(f"{S}row"):
            if len(lines) >= MAX_SHEET_ROWS:
                truncated = True
                break
            cells: list[str] = []
            for c in row.iter(f"{S}c"):
                ctype = c.get("t", "")
                if ctype == "inlineStr":
                    val = "".join(t.text or "" for t in c.iter(f"{S}t"))
                else:
                    v = c.find(f"{S}v")
                    val = v.text if v is not None and v.text else ""
                    if ctype == "s" and val:
                        try:
                            val = shared[int(val)]
                        except (ValueError, IndexError):
                            pass
                cells.append(val)
            line = "\t".join(cells).rstrip()
            if line.strip():
                lines.append(line)
        return lines, truncated
