from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass
class ParsedSection:
    heading_path: str | None
    text: str


@dataclass
class ParsedFile:
    title: str
    source_type: str
    sections: list[ParsedSection] = field(default_factory=list)


class Parser(Protocol):
    def parse(self, path: Path) -> ParsedFile | None: ...
