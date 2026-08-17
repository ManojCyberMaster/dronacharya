"""Seed knowledge kits — portable starter knowledge for a fresh KB.

A *seed kit* is a JSON file of distilled knowledge (summaries + knowledge
units + source links) built from a curated manifest of documentation URLs.
Kits deliberately contain NO embeddings: `dc seed install` embeds the units
locally with whatever embedding preset the user's KB was initialized with,
so one published kit works for every user — and answering uses whatever LLM
provider they configured (or none: `dc search` works provider-free).

Build (`dc seed build`) follows the same rules as every other ingest path:
each manifest URL is fetched exactly once (never crawled), PII-guarded, and
distilled — original page content is never stored. Sources whose license
does not permit redistribution of derivatives are excluded from kits by
default (they stay available for a personal `--include-all` build).

Manifest format (TOML):
    [[source]]
    topic = "wsl"                 # becomes tag "wsl"
    title = "Mount a Windows drive in WSL"
    url = "https://..."
    license = "CC BY 4.0"
    redistributable = true
"""

from __future__ import annotations

import json
import tomllib
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .embeddings import Embedder
from .guardrails.pii import apply_pii_policy
from .ingest import extract as extract_mod
from .ingest.distill import Distiller, get_distiller
from .ingest.pipeline import _hash
from .models import Document, KnowledgeUnit

KIT_FORMAT = "dronacharya-seedkit/1"
MAX_KIT_BYTES = 50 * 1024 * 1024
MIN_PAGE_CHARS = 300


@dataclass
class SeedSource:
    topic: str
    title: str
    url: str
    license: str = ""
    redistributable: bool = True


def load_manifest(path: Path) -> list[SeedSource]:
    with path.open("rb") as f:
        data = tomllib.load(f)
    sources = []
    seen: set[str] = set()
    for s in data.get("source", []):
        url = s.get("url", "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        sources.append(SeedSource(
            topic=s.get("topic", "misc"), title=s.get("title", url), url=url,
            license=s.get("license", ""),
            redistributable=bool(s.get("redistributable", True)),
        ))
    return sources


@dataclass
class BuildReport:
    built: int = 0
    skipped_resume: int = 0
    excluded_license: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)  # (url, reason)


def _load_kit_file(path: Path) -> dict:
    with path.open("rb") as f:
        return json.load(f)


def _write_kit_file(path: Path, kit: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(kit, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def build_kit(
    config: Config, manifest_path: Path, out_path: Path, *,
    name: str | None = None, version: str = "", description: str = "",
    limit: int | None = None, include_all: bool = False,
    fetch=None, distiller: Distiller | None = None, progress=None,
) -> BuildReport:
    """Build (or resume building) a seed kit from a manifest.

    The kit file is rewritten after every distilled page, so an interrupted
    build resumes where it stopped — already-built URLs are never re-fetched.
    """
    from .ingest.fetch import fetch_page

    fetch = fetch or fetch_page
    distiller = distiller or get_distiller(config)
    sources = load_manifest(manifest_path)
    if limit:
        sources = sources[:limit]

    report = BuildReport()
    if out_path.exists():
        kit = _load_kit_file(out_path)
    else:
        kit = {
            "format": KIT_FORMAT,
            "name": name or manifest_path.stem,
            "version": version,
            "description": description,
            "documents": [],
        }
    done_urls = {d["url"] for d in kit["documents"]}

    for i, src in enumerate(sources, 1):
        if progress:
            progress(i, len(sources), src)
        if src.url in done_urls:
            report.skipped_resume += 1
            continue
        if not src.redistributable and not include_all:
            report.excluded_license += 1
            continue

        html, err = fetch(src.url)
        if err:
            report.failed.append((src.url, err))
            continue
        extracted = extract_mod.from_html(html, url=src.url, title_hint=src.title)
        if extracted is None:
            report.failed.append((src.url, "no readable content"))
            continue
        clean, _, blocked = apply_pii_policy(extracted.text, config.guardrails.pii_mode)
        if blocked:
            report.failed.append((src.url, "blocked by PII policy"))
            continue
        if len(clean) < MIN_PAGE_CHARS:
            # "topic does not exist" stubs, empty shells, residual challenge
            # pages — junk knowledge is worse than a reported failure
            report.failed.append((src.url, f"page too thin ({len(clean)} chars) — "
                                           "moved, empty, or bot-gated"))
            continue
        try:
            dist = distiller.distill(extracted.title, clean, extracted.lang)
        except Exception as e:  # noqa: BLE001 — report, keep building
            report.failed.append((src.url, f"distillation failed ({e})"))
            continue

        kit["documents"].append({
            "url": src.url,
            "title": extracted.title or src.title,
            "summary": dist.summary,
            "lang": extracted.lang,
            "tags": [src.topic],
            "content_hash": _hash(clean),
            "distilled": dist.distilled,
            "distill_tier": dist.tier,
            "license": src.license,
            "units": [
                {"kind": u.kind, "heading_path": u.heading_path, "text": u.text}
                for u in dist.units
            ],
        })
        done_urls.add(src.url)
        report.built += 1
        _write_kit_file(out_path, kit)

    if kit["documents"] and not out_path.exists():
        _write_kit_file(out_path, kit)
    return report


@dataclass
class InstallReport:
    installed: int = 0
    skipped_existing: int = 0
    units: int = 0
    kit_name: str = ""
    kit_version: str = ""


def load_kit(path_or_url: str) -> dict:
    """Load a kit from a local file or an https URL (the kit file itself —
    this is not a page fetch and nothing in it is crawled)."""
    if path_or_url.startswith(("http://", "https://")):
        with urllib.request.urlopen(path_or_url, timeout=120) as resp:
            kit = json.loads(resp.read(MAX_KIT_BYTES))
    else:
        kit = _load_kit_file(Path(path_or_url))
    if kit.get("format") != KIT_FORMAT:
        raise ValueError(f"not a DronaCharya seed kit (format={kit.get('format')!r})")
    return kit


def install_kit(repo, embedder: Embedder, config: Config, kit: dict) -> InstallReport:
    """Insert a kit's documents and embed them with THIS KB's canonical model.
    Already-saved URLs are skipped — a kit never overwrites user knowledge."""
    report = InstallReport(kit_name=kit.get("name", ""), kit_version=kit.get("version", ""))
    for d in kit["documents"]:
        if repo.get_document_by_url(d["url"]):
            report.skipped_existing += 1
            continue
        doc = Document(
            source_type="web", title=d["title"], url=d["url"],
            summary=d.get("summary"), content_hash=d.get("content_hash", ""),
            distilled=bool(d.get("distilled")), distill_tier=d.get("distill_tier"),
            lang=d.get("lang"),
            meta={"seed_kit": report.kit_name, "seed_version": report.kit_version,
                  "license": d.get("license", "")},
        )
        units = [
            KnowledgeUnit(
                document_id=doc.id, seq=i, text=u["text"], kind=u.get("kind", "fact"),
                heading_path=u.get("heading_path"), lang=d.get("lang"),
            )
            for i, u in enumerate(d["units"])
        ]
        repo.insert_document(doc, units, embedder.embed_passages([u.text for u in units]))
        if d.get("tags"):
            # legacy kits carried a "seed/" tag prefix; the topic alone is
            # what means something to the user
            tags = [t.removeprefix("seed/") for t in d["tags"] if t != "seed"]
            repo.set_tags(doc.id, [t for t in tags if t])
        report.installed += 1
        report.units += len(units)
    repo.log_event("seed_install", {
        "kit": report.kit_name, "version": report.kit_version,
        "installed": report.installed, "skipped": report.skipped_existing,
    })
    return report
