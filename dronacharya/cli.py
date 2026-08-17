"""`dc` — the DronaCharya CLI."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .config import config_path, db_path, load_config, write_default_config

app = typer.Typer(help="DronaCharya — personal knowledge management with a local RAG.")
seed_app = typer.Typer(help="Seed knowledge kits — portable starter knowledge.")
app.add_typer(seed_app, name="seed")
tags_app = typer.Typer(help="Manage tags across the whole knowledge base.")
app.add_typer(tags_app, name="tags")
console = Console()


def _open_repo():
    from .storage import get_repo

    return get_repo(load_config())


def _remote_api(config, path: str, payload: dict, timeout: int = 90) -> dict | None:
    """POST to the configured home server; None when unreachable (client
    then falls back to fully-local operation — offline-first, by design)."""
    import json as json_mod
    import urllib.request

    if config.deployment.role != "client" or not config.server.remote_url:
        return None
    req = urllib.request.Request(
        config.server.remote_url.rstrip("/") + "/api/v1" + path,
        data=json_mod.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {config.server.token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json_mod.loads(resp.read())
    except Exception:  # noqa: BLE001 — offline / server down / auth mismatch
        return None


def _embedder(config):
    from .embeddings import get_embedder

    return get_embedder(config)


@app.command()
def init(
    preset: str = typer.Option("english", help="Embedding preset: english | multilingual"),
    force: bool = typer.Option(False, help="Overwrite an existing config"),
    download: bool = typer.Option(True, help="Pre-download the embedding model"),
):
    """Create the config file, the knowledge base, and (optionally) pre-download the model."""
    path = write_default_config(preset=preset, force=force)
    console.print(f"Config: [bold]{path}[/bold]")
    repo = _open_repo()
    console.print(f"Knowledge base: [bold]{db_path()}[/bold]")
    repo.close()
    if download:
        config = load_config()
        console.print(f"Downloading embedding model [bold]{config.embeddings.model_name}[/bold] "
                      "(first time only)...")
        _embedder(config).embed_passages(["warmup"])
        console.print("[green]Model ready.[/green]")
    console.print("\nNext: add note directories to the config, then `dc sync-notes`, "
                  "or save a page with `dc save <url>`.")


@app.command()
def save(
    url: str,
    tag: list[str] = typer.Option([], "--tag", "-t", help="Tag(s) for this page"),
    note: str = typer.Option(None, "--note", "-n", help="Your note about why you saved it"),
    force: bool = typer.Option(False, "--force", "-f",
                               help="Overwrite an existing save without reviewing changes"),
):
    """Save one web page's knowledge (only that page — links are never crawled)."""
    from .ingest.pipeline import save_web

    config = load_config()
    repo = _open_repo()
    try:
        outcome = save_web(repo, _embedder(config), config, url,
                           tags=tag or None, note=note, overwrite=force)
        if outcome.status == "needs_consent":
            console.print("[yellow]This page changed since you saved it.[/yellow]")
            console.print(f"  old: {outcome.old_summary or '—'}")
            console.print(f"  new: {outcome.new_summary or '—'}")
            if typer.confirm("Update your saved knowledge?"):
                outcome = save_web(repo, _embedder(config), config, url,
                                   tags=tag or None, note=note, overwrite=True)
            else:
                console.print("Kept the existing version.")
                return
        color = {"created": "green", "updated": "green",
                 "unchanged": "yellow", "blocked": "red"}.get(outcome.status, "white")
        console.print(f"[{color}]{outcome.status}[/{color}]: {outcome.message}")
    finally:
        repo.close()


def _web_origin(grounded: bool, confidence: str | None) -> str:
    """Honest provenance: 'the internet' only when the answer came FROM
    fetched web pages; a bare LLM has no web access — say so."""
    where = "the internet" if grounded else "model knowledge (not verified online)"
    return f"{where} · confidence {confidence or 'low'}"


def _no_verified_answer(question: str) -> None:
    console.print("[yellow]No verified answer.[/yellow] Not in your knowledge "
                  "base, and the web answer could not be verified against real "
                  "pages.")
    console.print(f'[dim]try[/dim] dc query "{question}" --deeper'
                  '   [dim]full model answer, clearly labeled[/dim]')
    console.print(f'[dim]or[/dim]  dc "{question}" --guess'
                  '          [dim]show the unverified quick answer[/dim]')


@app.command()
def ask(
    question: str,
    yes: bool = typer.Option(False, "--yes", "-y",
                             help="Add low-confidence web answers without asking"),
    no_save: bool = typer.Option(False, "--no-save",
                                 help="Never add the answer to your knowledge base"),
    guess: bool = typer.Option(False, "--guess",
                               help="Show low-confidence (unverified) web answers "
                                    "instead of refusing"),
):
    """Quick answer: the command line + one usage example, nothing else.
    Also the default — `dc "how do I …"` runs this command. Falls back to an
    internet search when your knowledge base doesn't know, and embeds the
    qualified answer back into the KB (low-confidence answers ask you first)."""
    from .quick import quick_ask, save_quick_answer

    config = load_config()

    # Fast path: a configured home server answers with warm models — no
    # local model load at all. Falls back to local when offline.
    remote = _remote_api(config, "/ask", {"question": question})
    if remote is not None:
        if remote["mode"] == "no_provider":
            console.print("[red]The server has no LLM provider available.[/red]")
            if remote.get("error"):
                console.print(f"[dim]{remote['error'][:300]}[/dim]")
            raise typer.Exit(1)
        if (remote["mode"] == "web" and remote.get("confidence") != "high"
                and not guess):
            _no_verified_answer(question)
            return
        console.print(remote["answer"])
        if remote["mode"] == "declined":
            return
        for u in remote.get("source_urls") or ([remote["source_url"]]
                                               if remote.get("source_url") else []):
            console.print(f"[italic cyan]{u}[/italic cyan]")
        where = ("your knowledge base" if remote["mode"] == "kb"
                 else _web_origin(remote.get("grounded", False),
                                  remote.get("confidence")))
        console.print(f"[dim]from {where} · {remote['provider']} · via server[/dim]")
        if remote.get("saved"):
            console.print("[green]✓ added to your knowledge base[/green]")
        elif remote.get("save_payload") and not no_save and (
                yes or typer.confirm("Low confidence — add to your knowledge base "
                                     "anyway?", default=False)):
            saved = _remote_api(config, "/ask/save",
                                {"question": question,
                                 "payload": remote["save_payload"],
                                 "provider": remote.get("provider", "")})
            if saved:
                console.print("[green]✓ added (verified by you)[/green]")
            else:
                console.print("[red]server unreachable — answer not saved[/red]")
        return

    repo = _open_repo()
    try:
        result = quick_ask(repo, _embedder(config), config, question)
        if result.mode == "no_provider":
            console.print("[red]No LLM provider is available.[/red] Configure one in "
                          f"[bold]{config_path()}[/bold] → [llm]. "
                          "Meanwhile `dc search` works fully offline.")
            raise typer.Exit(1)
        if result.mode == "web" and result.confidence != "high" and not guess:
            _no_verified_answer(question)
            return
        console.print(result.answer)
        if result.mode == "declined":
            return
        for u in result.source_urls or ([result.source_url]
                                        if result.source_url else []):
            console.print(f"[italic cyan]{u}[/italic cyan]")
        if result.mode == "kb":
            console.print(f"[dim]from your knowledge base · {result.provider}[/dim]")
            return
        console.print(f"[dim]from {_web_origin(result.grounded, result.confidence)} "
                      f"· {result.provider}[/dim]")
        if no_save or not result.save_payload:
            return
        if result.confidence == "high":
            save_quick_answer(repo, _embedder(config), config, question,
                              result.save_payload, result.provider)
            console.print("[green]✓ added to your knowledge base[/green]")
        elif yes or typer.confirm("Low confidence — add to your knowledge base anyway?",
                                  default=False):
            save_quick_answer(repo, _embedder(config), config, question,
                              result.save_payload, result.provider,
                              user_verified=True)
            console.print("[green]✓ added (verified by you)[/green]")
    finally:
        repo.close()


@seed_app.command("build")
def seed_build(
    manifest: Path = typer.Argument(..., help="Manifest TOML (see seedkits/)"),
    out: Path = typer.Option(None, "--out", "-o", help="Kit file to write/resume "
                             "(default: <manifest>.dckit.json)"),
    version: str = typer.Option("", "--version", help="Kit version stamp, e.g. 2026.08"),
    limit: int = typer.Option(None, "--limit", help="Build at most N sources"),
    include_all: bool = typer.Option(False, "--include-all",
                                     help="Also build sources whose license does not "
                                          "allow redistribution (personal use only — "
                                          "do not publish such a kit)"),
):
    """Fetch, distill, and package a seed kit from a curated URL manifest.
    Interrupted builds resume: already-built URLs are skipped. Run this where
    your best distillation model lives (e.g. the DGX with vLLM configured)."""
    from .seed import build_kit

    if not manifest.exists():
        console.print(f"[red]No such manifest:[/red] {manifest}")
        raise typer.Exit(1)
    out = out or manifest.with_suffix(".dckit.json")
    config = load_config()

    def progress(i, total, src):
        console.print(f"[dim]{i}/{total}[/dim] {src.topic} · {src.title[:60]}")

    report = build_kit(config, manifest, out, version=version, limit=limit,
                       include_all=include_all, progress=progress)
    console.print(f"\n[bold]Kit written to {out}[/bold] — built={report.built} "
                  f"resumed-skip={report.skipped_resume} "
                  f"license-excluded={report.excluded_license}")
    if report.failed:
        console.print(f"[red]{len(report.failed)} source(s) failed:[/red]")
        for url, reason in report.failed:
            console.print(f"  [red]✗[/red] {reason} — {url}")
        console.print("[dim]Re-run the same command to retry only the failures.[/dim]")


@seed_app.command("install")
def seed_install(
    kit: str = typer.Argument(..., help="Kit file path or https URL (*.dckit.json)"),
):
    """Install a seed kit: documents are embedded with THIS knowledge base's
    embedding model, so any kit works with any model choice. Your existing
    knowledge is never overwritten (already-saved URLs are skipped)."""
    from .seed import install_kit, load_kit

    config = load_config()
    try:
        data = load_kit(kit)
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]Could not load kit:[/red] {e}")
        raise typer.Exit(1)
    repo = _open_repo()
    try:
        console.print(f"Installing [bold]{data.get('name')}[/bold] "
                      f"{data.get('version', '')} — {len(data['documents'])} documents, "
                      "embedding locally…")
        report = install_kit(repo, _embedder(config), config, data)
        console.print(f"[green]installed={report.installed}[/green] "
                      f"units={report.units} already-present={report.skipped_existing}")
    finally:
        repo.close()


def _retag_documents(repo, transform) -> int:
    """Apply a tags-list transform to every document; returns changed count."""
    from .tagmap import retag_documents

    return retag_documents(repo, transform)


@tags_app.command("list")
def tags_list():
    """All tags with their knowledge-item counts."""
    repo = _open_repo()
    try:
        rows = sorted(repo.list_tags(), key=lambda t: (-t[1], t[0]))
        if not rows:
            console.print("No tags yet.")
            return
        for name, count in rows:
            console.print(f"{count:>5}  {name}")
    finally:
        repo.close()


@tags_app.command("rename")
def tags_rename(old: str, new: str):
    """Rename a tag everywhere (exact match)."""
    repo = _open_repo()
    try:
        n = _retag_documents(repo, lambda ts: [new if t == old else t for t in ts])
        repo.log_event("tags_rename", {"old": old, "new": new, "documents": n})
        console.print(f"renamed on [green]{n}[/green] documents")
    finally:
        repo.close()


@tags_app.command("remove")
def tags_remove(tag: str):
    """Remove a tag from every document (the documents stay)."""
    repo = _open_repo()
    try:
        n = _retag_documents(repo, lambda ts: [t for t in ts if t != tag])
        repo.log_event("tags_remove", {"tag": tag, "documents": n})
        console.print(f"removed from [green]{n}[/green] documents")
    finally:
        repo.close()


@tags_app.command("strip-prefix")
def tags_strip_prefix(prefix: str = typer.Argument(
        ..., help="e.g. 'seed' turns seed/wsl into wsl and drops bare 'seed'")):
    """Strip a hierarchy prefix from all tags."""
    pfx = prefix.rstrip("/") + "/"
    bare = prefix.rstrip("/")
    repo = _open_repo()
    try:
        n = _retag_documents(
            repo, lambda ts: [t.removeprefix(pfx) for t in ts if t != bare])
        repo.log_event("tags_strip_prefix", {"prefix": bare, "documents": n})
        console.print(f"updated [green]{n}[/green] documents")
    finally:
        repo.close()


@app.command("sync-notes")
def sync_notes():
    """Scan configured note directories and ingest new/changed files."""
    from .notes_sync import scan_notes

    config = load_config()
    if not config.notes.directories:
        console.print("[yellow]No note directories configured.[/yellow] "
                      f"Edit [bold]{config_path()}[/bold] → [notes].directories")
        raise typer.Exit(1)
    repo = _open_repo()
    try:
        report = scan_notes(repo, _embedder(config), config)
        console.print(f"created={report.created} updated={report.updated} "
                      f"unchanged={report.unchanged}")
        for line in report.skipped:
            console.print(f"[yellow]skipped[/yellow] {line}")
    finally:
        repo.close()


@app.command()
def search(
    query: str,
    k: int = typer.Option(8, "--top", "-k"),
    tag: list[str] = typer.Option([], "--tag", "-t",
                                  help="Filter by tag(s); 'Research' also matches 'Research/RAG'"),
    show_all: bool = typer.Option(False, "--all",
                                  help="Also show weak matches below the "
                                       "confidence threshold"),
):
    """Search your knowledge base (hybrid keyword + semantic)."""
    from .search import hybrid_search

    config = load_config()

    def _no_coverage() -> None:
        console.print("[yellow]No.[/yellow] Your knowledge base doesn't cover "
                      "this.")
        console.print(f'[dim]try[/dim] dc "{query}"   [dim]quick answer with '
                      "web fallback[/dim]  [dim]· or --all for weak matches[/dim]")

    remote = _remote_api(config, "/search",
                         {"query": query, "k": k, "tags": tag or None}, timeout=30)
    if remote is not None:
        results = remote.get("results", [])
        if not show_all:
            results = [r for r in results
                       if r["score"] >= config.retrieval.min_confidence]
        if not results:
            _no_coverage()
            return
        for i, r in enumerate(results, 1):
            crumb = f" · {r['heading_path']}" if r.get("heading_path") else ""
            console.print(f"[bold cyan]{i}. {r['title']}[/bold cyan]{crumb}  "
                          f"[dim](score {r['score']:.4f})[/dim]")
            console.print(f"   {r['text'][:240].replace(chr(10), ' ')}")
        console.print("\n[bold]Sources[/bold]")
        for i, r in enumerate(results, 1):
            source = r.get("url") or r.get("file_path") or ""
            if source:
                console.print(f"  [{i}] [italic cyan underline]{source}[/italic cyan underline]")
        return

    repo = _open_repo()
    try:
        from .reranker import get_reranker

        results = hybrid_search(repo, _embedder(config), query,
                                top_k=k, candidates=config.retrieval.candidates,
                                reranker=get_reranker(config),
                                tags=tag or None)
        if not show_all:
            results = [r for r in results
                       if r.score >= config.retrieval.min_confidence]
        if not results:
            _no_coverage()
            return
        for i, r in enumerate(results, 1):
            crumb = f" · {r.unit.heading_path}" if r.unit.heading_path else ""
            console.print(f"[bold cyan]{i}. {r.document.title}[/bold cyan]{crumb}  "
                          f"[dim](score {r.score:.4f})[/dim]")
            console.print(f"   {r.unit.text[:240].replace(chr(10), ' ')}")
        console.print("\n[bold]Sources[/bold]")
        for i, r in enumerate(results, 1):
            source = r.document.url or r.document.file_path or ""
            if source:
                console.print(f"  [{i}] [italic cyan underline]{source}[/italic cyan underline]")
    finally:
        repo.close()


@app.command()
def query(
    question: str,
    deeper: bool = typer.Option(False, "--deeper",
                                help="Answer beyond your knowledge base (clearly labeled)"),
    k: int = typer.Option(None, "--top", "-k"),
    tag: list[str] = typer.Option([], "--tag", "-t",
                                  help="Filter sources by tag(s); 'Research' also matches 'Research/RAG'"),
):
    """Ask your knowledge base a question (streamed answer with cited sources)."""
    from .rag import cited_indices
    from .rag import query as rag_query

    config = load_config()
    repo = _open_repo()
    try:
        result = rag_query(repo, _embedder(config), config, question,
                           mode="deeper" if deeper else "kb", top_k=k,
                           tags=tag or None)
        if result.mode == "no_answer":
            console.print("[yellow]Your knowledge base doesn't cover this.[/yellow] "
                          "Try [bold]dc query --deeper[/bold] to answer beyond it.")
            return
        if result.mode == "no_provider":
            console.print("[red]No LLM provider is available.[/red] Configure one in "
                          f"[bold]{config_path()}[/bold] → [llm] (API key or your "
                          "own Ollama/vLLM endpoint). "
                          "Meanwhile `dc search` works fully offline.")
            return
        if deeper:
            console.print("[yellow]⚠ outside your knowledge base[/yellow]\n")
        answer_text = ""
        for chunk in result.chunks or []:
            answer_text += chunk
            console.print(chunk, end="", soft_wrap=True)
        console.print()
        # Show only sources the answer actually cited ([n]); in deeper mode an
        # uncited retrieval candidate was just optional context the model
        # rejected — listing it would misattribute the answer.
        cited = set(cited_indices(answer_text))
        shown = [(i, r) for i, r in enumerate(result.sources or [], 1)
                 if i in cited or (not deeper and not cited)]
        if shown:
            console.print("\n[bold]Sources[/bold]")
            for i, r in shown:
                origin = r.document.url or r.document.file_path or ""
                console.print(f"  [{i}] {r.document.title} — [italic cyan underline]{origin}[/italic cyan underline]")
        console.print(f"[dim]answered by {result.provider}[/dim]")
    finally:
        repo.close()


@app.command()
def add(
    paths: list[Path] = typer.Argument(..., help="Files or directories "
                                       "(pdf/docx/xlsx/pptx/md/txt/tdl)"),
    tag: list[str] = typer.Option([], "--tag", "-t", help="Tag(s) for these files"),
):
    """Add local documents to your knowledge base — searchable, with answers
    referencing the file path. One-off companion to the [notes] directories
    (which re-scan automatically via `dc sync-notes`)."""
    from .ingest.parsers import get_parser
    from .ingest.pipeline import save_note_file

    config = load_config()
    repo = _open_repo()
    embedder = _embedder(config)
    files: list[Path] = []
    for p in paths:
        p = p.expanduser()
        if p.is_dir():
            files += [f for f in sorted(p.rglob("*"))
                      if f.is_file() and get_parser(f) is not None]
        elif p.is_file():
            files.append(p)
        else:
            console.print(f"[yellow]skipped[/yellow] {p}: not found")
    try:
        for f in files:
            outcome = save_note_file(repo, embedder, config, f)
            color = {"created": "green", "updated": "green",
                     "unchanged": "yellow"}.get(outcome.status, "red")
            console.print(f"[{color}]{outcome.status}[/{color}] {f}"
                          + (f" — {outcome.message}" if outcome.status == "blocked" else ""))
            if tag and outcome.document_id and outcome.status != "blocked":
                existing = set(repo.get_tags(outcome.document_id))
                repo.set_tags(outcome.document_id, sorted(existing | set(tag)))
        if not files:
            console.print("Nothing to add — no supported files found.")
    finally:
        repo.close()


@app.command()
def redistill():
    """Upgrade saves that only have fallback excerpts into distilled knowledge."""
    from .ingest.pipeline import redistill_document

    config = load_config()
    repo = _open_repo()
    embedder = _embedder(config)
    upgraded = pending = 0
    try:
        targets = [d.id for d, _ in repo.iter_documents_with_units() if not d.distilled]
        if not targets:
            console.print("Nothing to redistill.")
            return
        for document_id in targets:
            if redistill_document(repo, embedder, config, document_id):
                doc = repo.get_document(document_id)
                upgraded += 1
                console.print(f"[green]upgraded[/green] {doc.title} ({doc.distill_tier})")
            else:
                pending += 1
        console.print(f"\nupgraded={upgraded} pending={pending}")
    finally:
        repo.close()


@app.command("import-bookmarks")
def import_bookmarks_cmd(
    file: Path = typer.Argument(..., help="bookmarks.html exported from your browser"),
    refresh: bool = typer.Option(False, "--refresh",
                                 help="Re-fetch already-imported bookmarks and refresh changed pages"),
    llm_distill: bool = typer.Option(False, "--llm-distill",
                                     help="Fully distill during import (slow/costly for many "
                                          "bookmarks; default is fast mode + upgrade later)"),
    limit: int = typer.Option(None, "--limit", help="Import at most N bookmarks"),
):
    """Import your browser bookmarks (folders become hierarchical tags like
    'Research/RAG'). Re-run anytime — already-imported pages are skipped
    instantly; use --refresh to pick up changed pages. Dead pages are reported
    at the end."""
    from .importers.bookmarks_html import import_bookmarks

    if not file.exists():
        console.print(f"[red]No such file:[/red] {file}")
        raise typer.Exit(1)
    config = load_config()
    repo = _open_repo()
    try:
        def progress(i, total, bm):
            console.print(f"[dim]{i}/{total}[/dim] {bm.title[:70]}")

        report = import_bookmarks(repo, _embedder(config), config, file,
                                  refresh=refresh, llm_distill=llm_distill,
                                  limit=limit, progress=progress)
        console.print(f"\n[bold]Import done[/bold] — created={report.created} "
                      f"updated={report.updated} unchanged={report.unchanged} "
                      f"already-imported={report.skipped_existing}")
        if report.dead:
            console.print(f"\n[red]{len(report.dead)} page(s) are dead or unreadable:[/red]")
            for url, title, reason in report.dead:
                console.print(f"  [red]✗[/red] {title[:60]} — {reason}\n    {url}")
            console.print("[dim]These bookmarks were NOT added. If a page moved, "
                          "save its new address with `dc save <url>`.[/dim]")
        if not llm_distill and (report.created or report.updated):
            console.print("\n[dim]Imported in fast mode — run `dc redistill` (or `dc sync` "
                          "to your home server) to upgrade them to fully distilled "
                          "knowledge.[/dim]")
    finally:
        repo.close()


@app.command()
def reembed(
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation"),
):
    """Re-embed every knowledge unit with the currently configured embedding
    model. Needed after changing [embeddings].preset/model. Run it on every
    synced device (the embedding model must match across devices)."""
    config = load_config()
    if not yes and not typer.confirm(
        f"Re-embed everything with {config.embeddings.model_name}? This can take a while."
    ):
        raise typer.Exit(1)
    repo = _open_repo()
    embedder = _embedder(config)
    done = 0
    try:
        for doc, units in list(repo.iter_documents_with_units()):
            if not units:
                continue
            embeddings = embedder.embed_passages([u.text for u in units])
            # local re-index only: same ids, no version bump, nothing to sync
            repo.replace_document(doc, units, embeddings, bump_version=False)
            done += 1
        repo.log_event("reembed", {"documents": done, "model": config.embeddings.model_name})
        console.print(f"[green]Re-embedded {done} documents.[/green]")
    finally:
        repo.close()


@app.command()
def sync():
    """Sync this device's knowledge with your home server (push, then pull)."""
    from .sync.client import SyncError, sync_once

    config = load_config()
    repo = _open_repo()
    try:
        report = sync_once(repo, config)
        console.print(f"pushed={report.pushed} pulled={report.pulled} "
                      f"deleted={report.deleted} conflicts={report.conflicts}")
        if report.conflicts:
            console.print("[yellow]Conflicts were auto-resolved — review with"
                          " `dc conflicts`.[/yellow]")
    except SyncError as e:
        console.print(f"[red]sync failed:[/red] {e}")
        raise typer.Exit(1)
    finally:
        repo.close()


@app.command()
def conflicts(
    restore: int = typer.Option(None, "--restore",
                                help="Restore a losing version by conflict id"),
):
    """Review sync conflicts; every auto-resolved conflict keeps the losing
    version here so nothing is silently lost."""
    from .sync.merge import _doc_from_payload, _units_from_payload

    repo = _open_repo()
    try:
        if restore is not None:
            match = [c for c in repo.list_conflicts() if c["id"] == restore]
            if not match:
                console.print("[red]No such conflict id.[/red]")
                raise typer.Exit(1)
            payload = match[0]["losing_payload"]
            if "doc" not in payload:
                console.print("[red]This conflict has no restorable payload.[/red]")
                raise typer.Exit(1)
            doc = _doc_from_payload(payload["doc"])
            units, embeddings = _units_from_payload(payload["units"])
            if any(not e for e in embeddings):
                embeddings = _embedder(load_config()).embed_passages([u.text for u in units])
            if repo.get_document(doc.id):
                repo.replace_document(doc, units, embeddings)  # bump → wins next sync
            else:
                repo.insert_document(doc, units, embeddings)
            if payload.get("tags"):
                repo.set_tags(doc.id, payload["tags"])
            console.print(f"[green]restored[/green] {doc.title}")
            return
        items = repo.list_conflicts()
        if not items:
            console.print("No sync conflicts.")
            return
        for c in items:
            title = (c["losing_payload"].get("doc") or {}).get("title", c["document_id"])
            console.print(f"[bold]#{c['id']}[/bold] {c['resolved_at']} rule={c['rule']} "
                          f"losing side: {title}")
        console.print("\nRestore a losing version with: dc conflicts --restore <id>")
    finally:
        repo.close()


@app.command()
def status():
    """Knowledge base health and counts."""
    config = load_config()
    repo = _open_repo()
    try:
        counts = repo.counts()
        table = Table(title=f"DronaCharya v{__version__}")
        table.add_column("Item")
        table.add_column("Value", justify="right")
        table.add_row("Config", str(config_path()))
        table.add_row("Knowledge base", str(db_path()))
        table.add_row("Embedding preset", config.embeddings.preset)
        for key, value in counts.items():
            table.add_row(key, str(value))
        console.print(table)
    finally:
        repo.close()


@app.command()
def serve(
    host: str = typer.Option(None, help="Override bind host"),
    port: int = typer.Option(None, help="Override port"),
):
    """Run the local server (Web UI + API for the browser extension)."""
    try:
        import uvicorn
    except ImportError:
        console.print('[red]Server deps missing.[/red] Install with: pip install -e ".[server]"')
        raise typer.Exit(1)
    from .server.app import create_app

    config = load_config()
    bind_host = host or config.server.host
    bind_port = port or config.server.port
    console.print(f"DronaCharya at [bold]http://{bind_host}:{bind_port}[/bold] "
                  f"(token in {config_path()})")
    uvicorn.run(create_app(config), host=bind_host, port=bind_port, log_level="warning")


@app.command()
def export(
    out: Path = typer.Option(Path("dronacharya-export.zip"), "--out", "-o"),
):
    """Download all your data (JSON + markdown, zipped)."""
    from .export import export_to_file

    repo = _open_repo()
    try:
        path = export_to_file(repo, out)
        repo.log_event("export", {"path": str(path)})
        console.print(f"[green]Exported to {path}[/green]")
    finally:
        repo.close()


@app.command()
def wipe(
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation"),
):
    """Delete ALL knowledge from this device (tombstones propagate to synced devices)."""
    if not yes and not typer.confirm("Delete your entire knowledge base?"):
        raise typer.Exit(1)
    repo = _open_repo()
    try:
        n = repo.wipe()
        repo.log_event("wipe", {"documents": n})
        console.print(f"[red]Deleted {n} documents.[/red]")
    finally:
        repo.close()


def _command_names() -> set[str]:
    names = {(c.name or c.callback.__name__).replace("_", "-")
             for c in app.registered_commands}
    names |= {g.name for g in app.registered_groups if g.name}
    return names


def main() -> None:
    """Console entry point. Anything that isn't a known subcommand is a
    question: `dc "how do I …"` == `dc ask "how do I …"`."""
    import os
    import sys

    # model loading must be invisible: no HF progress bars, pings, or warnings
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

    if len(sys.argv) > 1 and not sys.argv[1].startswith("-") \
            and sys.argv[1] not in _command_names():
        sys.argv.insert(1, "ask")
    app()


if __name__ == "__main__":
    main()
