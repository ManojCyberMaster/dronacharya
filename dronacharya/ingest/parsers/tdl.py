"""AbstractSpoon ToDoList (.tdl) parser.

.tdl files are XML: a <TODOLIST> root with nested <TASK> elements. Task text
lives in the TITLE attribute; free-text notes in the COMMENTS attribute or a
<COMMENTS> child element. Custom columns (anything added in TDL's column
editor — Password, Wifi Key, License, whatever) show up as EXTRA attributes
or child elements outside that fixed set; this parser used to read only
TITLE/COMMENTS, so any custom-column value — including credentials — was
silently dropped at ingest and never reached the knowledge base at all
(not a search problem: the data was never stored). We now keep every
attribute/child that isn't pure TDL bookkeeping, labeled by its
COLUMNDEFINITIONS title when the file recorded one, else its raw name.

"Bookkeeping" is judged two ways: a name list built from real TDL files
(dates, colors, positions, ids — see _BOOKKEEPING_ATTRS), PLUS a
value-shape check (_looks_internal) for internal fields no name list will
ever fully cover: GUIDs, raw OLE Automation date serials, style/color
codes, and CUSTOMCOMMENTS — confirmed against AbstractSpoon's own example
file (github.com/abstractspoon/ToDoList_Resources, Introduction.tdl) to be
a compressed/binary re-encoding of the SAME text already in COMMENTS, not
separate user content, despite the name.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .base import ParsedFile, ParsedSection

# TDL's own bookkeeping/formatting attributes — never user-entered knowledge.
# Everything else on a <TASK> is surfaced as "<label>: <value>" so custom
# columns (of any name) are never silently dropped again. This list was
# built from generic knowledge of the format and turned out to miss a lot
# against real files (CREATIONDATE vs CREATEDDATE, LASTMOD vs LASTMODDATE,
# REFID, COMMENTSTYPE, *STRING/*COLOR variants...) — _looks_internal() below
# catches the rest by what the VALUE looks like, since no name list is ever
# going to be complete.
_BOOKKEEPING_ATTRS = {
    "ID", "TITLE", "COMMENTS", "POS", "POSSTRING", "ICON", "COLOR", "VERSION",
    "LOCKED", "FLAG", "GOODASDONE", "CREATEDBY", "CREATEDDATE", "CREATIONDATE",
    "CREATIONDATESTRING", "LASTMODBY", "LASTMODDATE", "LASTMOD", "LASTMODSTRING",
    "DONEDATE", "DONEDATESTRING", "DUEDATE", "DUEDATESTRING", "DUEDATETIME",
    "STARTDATE", "STARTDATESTRING", "STARTDATETIME", "REMINDER", "PERCENTDONE",
    "RECURRENCE", "SUBTASKDONE", "TIMEESTUNITS", "TIMESPENTUNITS", "PARENTID",
    "REFID", "COMMENTSTYPE", "CUSTOMCOMMENTS", "PRIORITY", "RISK",
}

_GUID_RE = re.compile(r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
                      r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$")
# An OLE Automation date for any plausible real date is 5 digits before the
# point with a long fraction (45908.05605324). The old \d{3,6}\.\d+ also
# matched ordinary user values — a "Price" of 199.99 was silently dropped.
_OLE_DATE_RE = re.compile(r"^\d{5}\.\d{4,}$")


def _looks_internal(name: str, value: str, *, labelled: bool = False) -> bool:
    """Catches TDL bookkeeping this parser doesn't know the exact name of:
    style/formatting IDs (*COLOR, *WEBCOLOR), GUIDs (COMMENTSTYPE and
    friends), raw OLE Automation date serials (a STRING sibling already has
    the readable form), and long encoded blobs — CUSTOMCOMMENTS is a
    compressed/binary re-encoding of the SAME text already captured via
    COMMENTS, not new content, and despite the name it is not a custom
    column.

    `labelled` means the file's COLUMNDEFINITIONS give this attribute a human
    title, i.e. the user defined it as a column. Then it is user data by
    definition and NEVER dropped on the shape of its value — guessing from
    shape silently ate exactly the high-value fields (a licence key that
    happens to be a GUID, a price that looks like a date serial).
    """
    if labelled:
        return False
    upper = name.upper()
    if upper.endswith("COLOR") or upper.endswith("WEBCOLOR"):
        return True
    if _GUID_RE.match(value):
        return True
    if _OLE_DATE_RE.match(value):
        return True
    if len(value) > 120 and " " not in value and re.fullmatch(r"[A-Za-z0-9+/=]+", value):
        return True   # base64-shaped blob
    return False


def _column_labels(root: ET.Element) -> dict[str, str]:
    """Best-effort custom-column-id -> human title map, from whatever
    COLUMNDEFINITIONS-style block the file has (schema varies by TDL
    version). Absence isn't an error — callers fall back to the raw
    attribute/tag name."""
    labels: dict[str, str] = {}
    for defs in root.iter():
        if defs.tag.upper() not in ("COLUMNDEFINITIONS", "CUSTOMCOLUMNS", "CUSTOMATTRIBUTEDEFS"):
            continue
        for d in defs:
            attrib_id = d.get("ATTRIBID") or d.get("ID") or d.get("ATTRIB")
            label = d.get("TITLE") or d.get("LABEL") or d.get("NAME")
            if attrib_id and label:
                labels[attrib_id] = label
    return labels


def _task_fields(task: ET.Element, labels: dict[str, str]) -> str:
    parts = []
    if task.get("COMMENTS"):
        parts.append(task.get("COMMENTS", ""))
    for child in task:
        tag = child.tag.upper()
        text = (child.text or "").strip()
        if not text:
            continue
        if tag == "COMMENTS":
            parts.append(text)
        elif (tag != "TASK" and tag not in _BOOKKEEPING_ATTRS
              and not _looks_internal(tag, text, labelled=child.tag in labels)):
            parts.append(f"{labels.get(child.tag, child.tag)}: {text}")
    for name, value in task.attrib.items():
        value = value.strip()
        if (not value or name.upper() in _BOOKKEEPING_ATTRS
                or _looks_internal(name, value, labelled=name in labels)):
            continue
        parts.append(f"{labels.get(name, name)}: {value}")
    return "\n".join(p.strip() for p in parts if p.strip())


class TdlParser:
    def parse(self, path: Path) -> ParsedFile | None:
        try:
            tree = ET.parse(path)
        except ET.ParseError:
            return None
        root = tree.getroot()
        title = root.get("PROJECTNAME") or path.stem
        labels = _column_labels(root)
        sections: list[ParsedSection] = []

        def walk(task: ET.Element, ancestors: list[str]) -> None:
            task_title = (task.get("TITLE") or "").strip()
            crumb = [*ancestors, task_title] if task_title else ancestors
            comments = _task_fields(task, labels)
            children = [c for c in task if c.tag.upper() == "TASK"]
            if comments:
                sections.append(ParsedSection(" > ".join(crumb) or None, comments))
            elif task_title and not children:
                # leaf task with no notes — the title itself is the knowledge
                sections.append(ParsedSection(" > ".join(ancestors) or None, task_title))
            for child in children:
                walk(child, crumb)

        for task in (c for c in root if c.tag.upper() == "TASK"):
            walk(task, [])
        if not sections:
            return None
        return ParsedFile(title=title, source_type="tdl", sections=sections)
