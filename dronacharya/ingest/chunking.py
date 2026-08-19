"""Heading/paragraph-aware chunking for user-authored content and fallbacks."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Trim only line breaks and spaces, never TABS: a spreadsheet row whose last
# cell is empty ends in a tab, and stripping it made that row one column
# narrower than the rest — which is exactly what disables the grid editor and
# the markdown-table conversion for the whole sheet.
_TRIM = "\n\r "

TARGET_CHARS = 1400
OVERLAP_CHARS = 200
MIN_CHARS = 80

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_FENCE = re.compile(r"^\s*(```|~~~)")


@dataclass
class Section:
    heading_path: str | None
    text: str


def _unescape_headings(text: str) -> str:
    r"""Turn an escaped body line (`\# note`) back into its literal text.

    This is the inverse of the escaping notes._escape_body applies when it
    rebuilds a document, so a convert → edit → convert cycle neither loses the
    line (it would become a phantom heading) nor accumulates backslashes.
    Plain markdown semantics anyway: `\#` renders as a literal `#`.
    """
    out = []
    for line in text.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith("\\#") and _HEADING.match(stripped[1:]):
            indent = line[:len(line) - len(stripped)]
            out.append(indent + stripped[1:])
        else:
            out.append(line)
    return "\n".join(out)


def split_markdown_sections(text: str) -> list[Section]:
    """Split on markdown headings, tracking the heading breadcrumb.

    Two things this must NOT do, because both silently lose user content:
      - treat a `#` comment inside a fenced code block as a heading (it tore
        the fence across two sections and turned the comment into a crumb)
      - drop a heading that has neither body text nor sub-headings; its words
        would then exist in no unit at all and be unsearchable, so it is
        emitted with its own title as the body instead.
    """
    lines = text.splitlines()
    # (level, heading, produced_any_text)
    path: list[list] = []
    sections: list[Section] = []
    buf: list[str] = []
    in_fence = False
    fence_marker = ""

    def crumb(upto: int | None = None) -> str | None:
        return " > ".join(h for _, h, _ in path[:upto]) or None

    def flush() -> None:
        body = _unescape_headings("\n".join(buf)).strip(_TRIM)
        if body:
            sections.append(Section(crumb(), body))
            for entry in path:
                entry[2] = True      # every ancestor now has real content
        buf.clear()

    def retire(dropped: list[list], depth: int) -> None:
        """Headings leaving scope. Any that never carried text keeps its own
        wording as content (idempotent: re-chunking the result is stable)."""
        for i, (_, heading, produced) in enumerate(dropped):
            if not produced:
                trail = [h for _, h, _ in path[:depth]] + \
                        [h for _, h, _ in dropped[:i + 1]]
                sections.append(Section(" > ".join(trail) or None, heading))

    for line in lines:
        fence = _FENCE.match(line)
        if fence:
            marker = fence.group(1)
            if not in_fence:
                in_fence, fence_marker = True, marker
            elif marker == fence_marker:
                in_fence = False
            buf.append(line)
            continue
        m = None if in_fence else _HEADING.match(line)
        if m:
            flush()
            level = len(m.group(1))
            keep = [e for e in path if e[0] < level]
            retire([e for e in path if e[0] >= level], len(keep))
            path[:] = keep
            path.append([level, m.group(2).strip(), False])
        else:
            buf.append(line)
    flush()
    retire(list(path), 0)
    if not sections and text.strip():
        sections = [Section(None, text.strip())]
    return sections


def _split_at_boundary(buf: str, limit: int) -> tuple[str, str]:
    """Cut `buf` at or before `limit`, preferring a line break and falling back
    to a word break. Cutting at an arbitrary character sliced tab-separated
    spreadsheet rows mid-cell, which broke the grid editor and the markdown
    table conversion for every chunk of a sheet over ~70 rows."""
    window = buf[:limit]
    for sep in ("\n", " "):
        cut = window.rfind(sep)
        if cut > limit // 2:          # only if it isn't a pathological cut
            return buf[:cut], buf[cut + 1:]
    return window, buf[limit:]


def _overlap_tail(buf: str) -> str:
    """The trailing context repeated into the next chunk, snapped to a line or
    word boundary so the repeat never starts mid-word or mid-row."""
    tail = buf[-OVERLAP_CHARS:]
    for sep in ("\n", " "):
        cut = tail.find(sep)
        if cut != -1:
            return tail[cut + 1:]
    return tail


def chunk_text(text: str, heading_path: str | None = None) -> list[Section]:
    """Paragraph-pack a section into ~TARGET_CHARS chunks with overlap."""
    paras = [p.strip(_TRIM) for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[Section] = []
    buf = ""
    for p in paras:
        if buf and len(buf) + len(p) + 2 > TARGET_CHARS:
            chunks.append(Section(heading_path, buf.strip(_TRIM)))
            buf = _overlap_tail(buf) + "\n\n" + p if OVERLAP_CHARS else p
        else:
            buf = f"{buf}\n\n{p}" if buf else p
        while len(buf) > TARGET_CHARS * 2:  # single huge paragraph
            head, buf = _split_at_boundary(buf, TARGET_CHARS * 2)
            chunks.append(Section(heading_path, head.strip(_TRIM)))
            buf = _overlap_tail(head) + "\n" + buf
    if buf.strip():
        chunks.append(Section(heading_path, buf.strip(_TRIM)))
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
