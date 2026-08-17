"""AbstractSpoon ToDoList (.tdl) parser.

.tdl files are XML: a <TODOLIST> root with nested <TASK> elements. Task text
lives in the TITLE attribute; free-text notes in the COMMENTS attribute or a
<COMMENTS> child element. We emit one section per task that carries comments
(or leaf tasks with meaningful titles), with the task hierarchy as the
heading path.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from .base import ParsedFile, ParsedSection


def _task_comments(task: ET.Element) -> str:
    parts = []
    if task.get("COMMENTS"):
        parts.append(task.get("COMMENTS", ""))
    for child in task:
        if child.tag.upper() == "COMMENTS" and (child.text or "").strip():
            parts.append(child.text or "")
    return "\n".join(p.strip() for p in parts if p.strip())


class TdlParser:
    def parse(self, path: Path) -> ParsedFile | None:
        try:
            tree = ET.parse(path)
        except ET.ParseError:
            return None
        root = tree.getroot()
        title = root.get("PROJECTNAME") or path.stem
        sections: list[ParsedSection] = []

        def walk(task: ET.Element, ancestors: list[str]) -> None:
            task_title = (task.get("TITLE") or "").strip()
            crumb = [*ancestors, task_title] if task_title else ancestors
            comments = _task_comments(task)
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
