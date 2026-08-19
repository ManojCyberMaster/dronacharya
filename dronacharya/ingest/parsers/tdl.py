"""AbstractSpoon ToDoList (.tdl) parser.

.tdl files are XML: a <TODOLIST> root with nested <TASK> elements. Task text
lives in the TITLE attribute; free-text notes in the COMMENTS attribute or a
<COMMENTS> child element. Custom columns (anything added in TDL's column
editor — Password, Wifi Key, License, whatever) show up as EXTRA attributes
or child elements outside that fixed set; this parser used to read only
TITLE/COMMENTS, so any custom-column value — including credentials — was
silently dropped at ingest and never reached the knowledge base at all
(not a search problem: the data was never stored). We now keep every
attribute/child that isn't pure TDL bookkeeping (ids, positions, colors,
timestamps), labeled by its COLUMNDEFINITIONS title when the file recorded
one, else its raw attribute name.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from .base import ParsedFile, ParsedSection

# TDL's own bookkeeping/formatting attributes — never user-entered knowledge.
# Everything else on a <TASK> is surfaced as "<label>: <value>" so custom
# columns (of any name) are never silently dropped again.
_BOOKKEEPING_ATTRS = {
    "ID", "TITLE", "COMMENTS", "POS", "ICON", "COLOR", "VERSION", "LOCKED",
    "FLAG", "GOODASDONE", "CREATEDBY", "CREATEDDATE", "LASTMODBY",
    "LASTMODDATE", "DONEDATE", "DUEDATE", "DUEDATETIME", "STARTDATE",
    "STARTDATETIME", "REMINDER", "PERCENTDONE", "RECURRENCE",
    "SUBTASKDONE", "TIMEESTUNITS", "TIMESPENTUNITS", "PARENTID",
}


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
        elif tag != "TASK":
            parts.append(f"{labels.get(child.tag, child.tag)}: {text}")
    for name, value in task.attrib.items():
        if name.upper() in _BOOKKEEPING_ATTRS or not value.strip():
            continue
        parts.append(f"{labels.get(name, name)}: {value.strip()}")
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
