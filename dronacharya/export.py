"""Data download (guardrails requirement): full export as a zip of JSON + markdown."""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import asdict
from pathlib import Path

from .models import utcnow


def export_zip(repo) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        manifest = {"exported_at": utcnow(), "counts": repo.counts()}
        docs_json = []
        for doc, units in repo.iter_documents_with_units():
            record = asdict(doc)
            record["tags"] = repo.get_tags(doc.id)
            record["knowledge_units"] = [asdict(u) for u in units]
            docs_json.append(record)

            lines = [f"# {doc.title}", ""]
            if doc.url:
                lines.append(f"Source: {doc.url}")
            if doc.file_path:
                lines.append(f"File: {doc.file_path}")
            if record["tags"]:
                lines.append("Tags: " + ", ".join(record["tags"]))
            if doc.summary:
                lines += ["", f"> {doc.summary}"]
            for u in units:
                lines.append("")
                if u.heading_path:
                    lines.append(f"## {u.heading_path}")
                lines.append(u.text)
            safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in doc.title)[:80]
            zf.writestr(f"markdown/{safe or doc.id}.md", "\n".join(lines))

        zf.writestr("documents.json", json.dumps(docs_json, indent=2, ensure_ascii=False))
        # operational data holds user traces too (questions, URLs, conflict
        # payloads) — "full export" without it would be a false claim
        zf.writestr("operational.json",
                    json.dumps(repo.dump_operational(), indent=2,
                               ensure_ascii=False, default=str))
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
    return buf.getvalue()


def export_to_file(repo, path: Path) -> Path:
    path.write_bytes(export_zip(repo))
    return path


def export_markdown_dir(repo, out_dir: Path) -> int:
    """Obsidian-ready vault export: one Markdown file per document with YAML
    frontmatter (tags, source, dates). Point Obsidian at the folder or merge
    it into an existing vault."""
    out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for doc, units in repo.iter_documents_with_units():
        tags = repo.get_tags(doc.id)
        fm = ["---", f'title: "{(doc.title or "").replace(chr(34), chr(39))}"']
        if tags:
            fm.append("tags:")
            fm += [f"  - {t.replace(' ', '_')}" for t in tags]
        if doc.url:
            fm.append(f"source: {doc.url}")
        if doc.file_path:
            fm.append(f'file: "{doc.file_path}"')
        fm += [f"created: {doc.created_at[:10]}", "---", ""]
        lines = fm
        if doc.summary:
            lines += [f"> {doc.summary}", ""]
        for u in units:
            if u.heading_path:
                lines.append(f"## {u.heading_path}")
            lines += [u.text, ""]
        if doc.url:
            lines.append(f"[source]({doc.url})")
        safe = "".join(c if c.isalnum() or c in "-_ " else "_"
                       for c in (doc.title or doc.id))[:80].strip() or doc.id
        (out_dir / f"{safe}.md").write_text("\n".join(lines), encoding="utf-8")
        n += 1
    return n
