"""Heading/paragraph-aware chunking for user-authored content and fallbacks."""

from __future__ import annotations

import re
from dataclasses import dataclass

TARGET_CHARS = 1400
OVERLAP_CHARS = 200
MIN_CHARS = 80

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


@dataclass
class Section:
    heading_path: str | None
    text: str


def split_markdown_sections(text: str) -> list[Section]:
    """Split on markdown headings, tracking the heading breadcrumb."""
    lines = text.splitlines()
    path: list[tuple[int, str]] = []
    sections: list[Section] = []
    buf: list[str] = []

    def flush() -> None:
        body = "\n".join(buf).strip()
        if body:
            crumb = " > ".join(h for _, h in path) or None
            sections.append(Section(crumb, body))
        buf.clear()

    for line in lines:
        m = _HEADING.match(line)
        if m:
            flush()
            level = len(m.group(1))
            path[:] = [(lv, h) for lv, h in path if lv < level]
            path.append((level, m.group(2).strip()))
        else:
            buf.append(line)
    flush()
    if not sections and text.strip():
        sections = [Section(None, text.strip())]
    return sections


def chunk_text(text: str, heading_path: str | None = None) -> list[Section]:
    """Paragraph-pack a section into ~TARGET_CHARS chunks with overlap."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[Section] = []
    buf = ""
    for p in paras:
        if buf and len(buf) + len(p) + 2 > TARGET_CHARS:
            chunks.append(Section(heading_path, buf.strip()))
            buf = buf[-OVERLAP_CHARS:] + "\n\n" + p if OVERLAP_CHARS else p
        else:
            buf = f"{buf}\n\n{p}" if buf else p
        while len(buf) > TARGET_CHARS * 2:  # single huge paragraph
            chunks.append(Section(heading_path, buf[: TARGET_CHARS * 2]))
            buf = buf[TARGET_CHARS * 2 - OVERLAP_CHARS:]
    if buf.strip():
        chunks.append(Section(heading_path, buf.strip()))
    # merge a trailing tiny chunk into its predecessor
    if len(chunks) >= 2 and len(chunks[-1].text) < MIN_CHARS:
        chunks[-2] = Section(heading_path, chunks[-2].text + "\n\n" + chunks[-1].text)
        chunks.pop()
    return chunks


def chunk_document(text: str) -> list[Section]:
    out: list[Section] = []
    for section in split_markdown_sections(text):
        out.extend(chunk_text(section.text, section.heading_path))
    return out
