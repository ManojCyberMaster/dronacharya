"""Office documents: .docx / .xlsx / .pptx (and .xlsm).

OOXML files are zip archives of XML — parsed here with the stdlib only
(zipfile + ElementTree), no python-docx/openpyxl/python-pptx dependency.
Good-enough text extraction for knowledge, with honest limitations:
formulas yield their cached values, dates in Excel appear as serial numbers
unless stored as text, and legacy binary formats (.doc/.xls/.ppt) are not
supported — resave them in the modern format.
"""

from __future__ import annotations

import posixpath
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
            # localized Word writes localized styleIds ("berschrift1" in
            # German, "Ttulo1" in Spanish), so an English-only match collapsed
            # such a document into one unheaded blob. outlineLvl is the
            # language-independent signal and is present on real headings.
            outline = para.find(f"{W}pPr/{W}outlineLvl")
            if re.match(r"(?i)heading|title|berschrift|titre|ttulo|titolo|rubrik",
                        style_val) or outline is not None:
                flush()
                heading = text
            else:
                buf.append(text)
        flush()
        if not sections:
            return None
        return ParsedFile(title=path.stem, source_type="docx", sections=sections)


class PptxParser:
    """One section per slide; the slide's first line acts as its heading.

    Speaker notes are ingested too: on a content-light deck the actual
    knowledge usually lives in the presenter notes, and reading only the slide
    bodies silently reduced such a deck to a list of headings.
    """

    @staticmethod
    def _notes_for(zf: zipfile.ZipFile, slide_name: str, names: set[str]) -> str:
        """The notesSlide part for a slide, via its relationships. Falls back to
        matching numbers, which is right for decks where every slide has notes."""
        base = slide_name.rsplit("/", 1)[-1]
        rels = _read_xml(zf, f"ppt/slides/_rels/{base}.rels")
        if rels is not None:
            for rel in rels.iter(f"{PR}Relationship"):
                target = rel.get("Target", "")
                if "notesSlide" not in target:
                    continue
                if target.startswith("/"):
                    return target.lstrip("/")
                return posixpath.normpath(posixpath.join("ppt/slides", target))
        num = re.search(r"\d+", base)
        return f"ppt/notesSlides/notesSlide{num.group()}.xml" if num else ""

    @staticmethod
    def _lines(root) -> list[str]:
        out = []
        for p in root.iter(f"{A}p"):
            line = "".join(t.text or "" for t in p.iter(f"{A}t")).strip()
            if line:
                out.append(line)
        return out

    def parse(self, path: Path) -> ParsedFile | None:
        sections: list[ParsedSection] = []
        try:
            with zipfile.ZipFile(path) as zf:
                names = set(zf.namelist())
                slides = sorted(
                    (n for n in names
                     if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                    key=lambda n: int(re.search(r"\d+", n).group()))
                for idx, name in enumerate(slides, start=1):
                    root = _read_xml(zf, name)
                    paras = self._lines(root) if root is not None else []
                    # notesSlideN.xml is numbered independently of slideN.xml,
                    # so resolve through the slide's relationships when we can
                    notes_name = self._notes_for(zf, name, names)
                    notes_root = (_read_xml(zf, notes_name)
                                  if notes_name in names else None)
                    notes = self._lines(notes_root) if notes_root is not None else []
                    # the notes pane repeats the slide number as a lone line
                    notes = [ln for ln in notes if ln != str(idx)]
                    if not paras and not notes:
                        continue
                    title = (paras[0] if paras else notes[0])[:80]
                    body = list(paras)
                    if notes:
                        body.append("Speaker notes: " + "\n".join(notes))
                    for chunk in chunk_text("\n".join(body)):
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
                target = rel.get("Target", "")
                # a Target may be absolute ("/xl/worksheets/sheet2.xml"), which
                # is legal OOXML; prefixing "xl/" then produced "xl/xl/..." and
                # the sheet was skipped without a word
                rel_map[rel.get("Id")] = (target.lstrip("/") if target.startswith("/")
                                          else "xl/" + target)
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
    def _col_index(ref: str) -> int | None:
        """'C2' -> 2. Excel omits empty cells from the XML entirely, so cell
        POSITION must come from the reference, never from document order."""
        m = re.match(r"([A-Z]+)\d*$", ref or "")
        if not m:
            return None
        idx = 0
        for ch in m.group(1):
            idx = idx * 26 + ord(ch) - 64
        return idx - 1

    @classmethod
    def _rows(cls, root, shared: list[str]) -> tuple[list[str], bool]:
        """Rows as tab-separated lines, every row padded to the SHEET's column
        count. Trimming each row to its own last non-empty cell made column
        counts differ between rows, and both the grid editor and the markdown
        table conversion require a uniform width — so one row with an empty
        last cell silently degraded the whole sheet to unstructured tab soup."""
        rows: list[dict[int, str]] = []
        truncated = False
        for row in root.iter(f"{S}row"):
            if len(rows) >= MAX_SHEET_ROWS:
                truncated = True
                break
            cells: dict[int, str] = {}
            pos = 0
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
                col = cls._col_index(c.get("r", ""))
                if col is None:
                    col = pos
                # a cell can legitimately contain its own line breaks (Excel
                # supports multi-line cell text) — but rows are joined with
                # "\n" below, so an embedded newline would be indistinguishable
                # from a row boundary and corrupt every row after it. One
                # logical row must stay one line of text.
                cells[col] = " ".join(val.split())
                pos = col + 1
            if not cells or not any(v.strip() for v in cells.values()):
                continue
            rows.append(cells)
        if not rows:
            return [], truncated
        width = max(max(cells) for cells in rows) + 1
        lines = ["\t".join(cells.get(i, "") for i in range(width))
                 for cells in rows]
        return lines, truncated
