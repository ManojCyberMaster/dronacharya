"""Polling scan of configured note directories (cross-platform by design:
polling works on every OS, network drives, and WSL /mnt/c where inotify
doesn't fire for Windows-side writes)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .embeddings import Embedder
from .ingest.pipeline import save_note_file


@dataclass
class ScanReport:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: list[str] = field(default_factory=list)

    @property
    def changed(self) -> int:
        return self.created + self.updated


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


def scan_notes(repo, embedder: Embedder, config: Config) -> ScanReport:
    report = ScanReport()
    exts = {e.lower() for e in config.notes.extensions}
    for raw_dir in config.notes.directories:
        base = Path(raw_dir).expanduser()
        if not base.is_dir():
            report.skipped.append(f"{raw_dir}: not a directory")
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in exts:
                continue
            key = str(path.resolve())
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            state = repo.get_sync_state(key)
            if state and state[0] == mtime:
                report.unchanged += 1
                continue
            file_hash = _file_hash(path)
            if state and state[1] == file_hash:
                repo.set_sync_state(key, mtime, file_hash)  # touched but same bytes
                report.unchanged += 1
                continue
            outcome = save_note_file(repo, embedder, config, path)
            if outcome.status == "created":
                report.created += 1
            elif outcome.status == "updated":
                report.updated += 1
            elif outcome.status == "unchanged":
                report.unchanged += 1
            else:
                report.skipped.append(f"{key}: {outcome.message}")
                continue
            repo.set_sync_state(key, mtime, file_hash)
    return report
