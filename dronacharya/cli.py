"""`dc` — the DronaCharya CLI."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .config import config_path, db_path, load_config, write_default_config
from .models import unit_index_text

app = typer.Typer(help="DronaCharya — personal knowledge management with a local RAG.")
seed_app = typer.Typer(help="Seed knowledge kits — portable starter knowledge.")
app.add_typer(seed_app, name="seed")
tags_app = typer.Typer(help="Manage tags across the whole knowledge base.")
app.add_typer(tags_app, name="tags")
token_app = typer.Typer(help="Scoped API tokens for devices/clients "
                             "(the config token stays the admin key).")
app.add_typer(token_app, name="token")
console = Console()


def _open_repo():
    from .embeddings import ensure_embedding_compat
    from .storage import get_repo

    config = load_config()
    repo = get_repo(config)
    try:
        ensure_embedding_compat(repo, config)
    except RuntimeError as e:
        repo.close()
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1) from None
    return repo


def _embedder(config):
    from .embeddings import get_embedder

    return get_embedder(config)


def _auto_sync(config, repo) -> None:
    """[sync] auto: reconcile with the home server after KB-touching work."""
    from .sync.client import maybe_auto_sync

    report = maybe_auto_sync(
        repo, config, quiet=False,
        on_start=lambda: console.print(
            "[dim]auto-sync with your server (pulled changes re-embed "
            "locally — can take a while after big imports)…[/dim]"),
        # a sync that keeps failing used to be completely invisible
        on_error=lambda exc: console.print(
            f"[yellow]auto-sync failed:[/yellow] {exc} [dim](retrying later; "
            f"`dc sync` for detail)[/dim]"))
    if report and (report.pushed or report.pulled or report.deleted):
        console.print(f"[dim]auto-synced: +{report.pulled} from server, "
                      f"{report.pushed} pushed[/dim]")
    if report and report.failed:
        console.print(f"[yellow]{report.failed} change(s) from the server could "
                      f"not be applied[/yellow] [dim]— see `dc conflicts`[/dim]")


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
        _auto_sync(config, repo)
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
    local: bool = typer.Option(False, "--local",
                               help="Answer from this machine only — skip the "
                                    "home server"),
):
    """Quick answer: the command line + one usage example, nothing else.
    Also the default — `dc "how do I …"` runs this command, after routing:
    a question that reads as "find a known document", "explain/compare",
    or "list every X" is dispatched to `search`/`query`/`find` instead —
    you should rarely need to type those explicitly. Falls back to an
    internet search when your knowledge base doesn't know, and embeds the
    qualified answer back into the KB (low-confidence answers ask you first)."""
    from .client import ask_local, ask_remote, save_vetted_answer
    from .router import route_question

    config = load_config()
    route = route_question(config, question)
    if route != "ask":
        console.print(f"[dim]→ routed to: {route}[/dim]")
        if route == "search":
            return search(question, 8, [], False, local)
        if route == "query":
            return query(question, False, None, [])
        if route == "find":
            return find(question, False)
    repo = embedder = None
    try:
        # ONE render path for both transports — the facade normalizes them.
        out = None if local else ask_remote(config, question, no_save=no_save)
        if out is None:
            repo = _open_repo()
            embedder = _embedder(config)
            out = ask_local(config, repo, embedder, question, no_save=no_save)
        if out.mode == "no_provider":
            where = ("The server has no" if out.origin == "server"
                     else "No") + " LLM provider available."
            console.print(f"[red]{where}[/red] Configure one in "
                          f"[bold]{config_path()}[/bold] → [llm]. "
                          "Meanwhile `dc search` works fully offline.")
            if out.error:
                console.print(f"[dim]{out.error[:300]}[/dim]")
            raise typer.Exit(1)
        if out.mode == "web" and out.confidence != "high" and not guess:
            _no_verified_answer(question)
            return
        console.print(out.answer)
        if out.mode == "declined":
            return
        for u in out.source_urls:
            console.print(f"[italic cyan]{u}[/italic cyan]")
        origin_note = " · via server" if out.origin == "server" else ""
        where = ("your knowledge base" if out.mode == "kb"
                 else _web_origin(out.grounded, out.confidence))
        console.print(f"[dim]from {where} · {out.provider}{origin_note}[/dim]")
        if out.mode == "kb":
            return
        if out.saved:
            console.print("[green]✓ added to your knowledge base[/green]")
        elif out.save_payload and not no_save and (
                yes or typer.confirm("Low confidence — add to your knowledge "
                                     "base anyway?", default=False)):
            if repo is None and out.origin == "local":
                repo = _open_repo()
                embedder = _embedder(config)
            ok = save_vetted_answer(config, question, out.save_payload,
                                    out.provider, origin=out.origin,
                                    repo=repo, embedder=embedder)
            console.print("[green]✓ added (verified by you)[/green]" if ok
                          else "[red]server unreachable — answer not saved[/red]")
    finally:
        if repo is not None:
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
        raise typer.Exit(1) from None
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
        if report.changed:
            _auto_sync(config, repo)
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
    local: bool = typer.Option(False, "--local",
                               help="Search this machine's knowledge base only"),
):
    """Search your knowledge base (hybrid keyword + semantic)."""
    from .client import search_local, search_remote

    config = load_config()
    results = None if local else search_remote(config, query, k=k,
                                               tags=tag or None,
                                               show_all=show_all)
    if results is None:
        repo = _open_repo()
        try:
            results = search_local(config, repo, _embedder(config), query,
                                   k=k, tags=tag or None, show_all=show_all)
        finally:
            repo.close()

    # ONE render path — both transports return the shared result shape
    if not results:
        console.print("[yellow]No.[/yellow] Your knowledge base doesn't cover "
                      "this.")
        console.print(f'[dim]try[/dim] dc "{query}"   [dim]quick answer with '
                      "web fallback[/dim]  [dim]· or --all for weak matches[/dim]")
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
        # Show only sources the answer actually cited ([n]). An uncited
        # retrieval candidate was context the model saw but didn't use (or
        # explicitly declined to answer from) — listing it anyway would
        # misattribute a "your knowledge base doesn't cover this" reply to
        # 8 unrelated documents, which is worse than showing no sources.
        cited = set(cited_indices(answer_text))
        shown = [(i, r) for i, r in enumerate(result.sources or [], 1) if i in cited]
        if shown:
            console.print("\n[bold]Sources[/bold]")
            for i, r in shown:
                origin = r.document.url or r.document.file_path or ""
                console.print(f"  [{i}] {r.document.title} — [italic cyan underline]{origin}[/italic cyan underline]")
        console.print(f"[dim]answered by {result.provider}[/dim]")
    finally:
        repo.close()


@app.command()
def find(
    question: str,
    show_code: bool = typer.Option(False, "--show-code",
                                   help="Print the generated search function before running it"),
):
    """Find EVERY item matching a request across your whole knowledge base
    ("list all my passwords", "every item on my wish lists") — an exhaustive
    scan, not top-k retrieval. One LLM call writes a small search function
    from your question alone (it never sees your notes), then that function
    runs locally over every stored unit in a sandboxed, no-network process."""
    from rich.markup import escape

    from .search_codegen import find_all

    config = load_config()
    repo = _open_repo()
    try:
        try:
            result = find_all(repo, _embedder(config), config, question)
        except Exception as e:  # noqa: BLE001 — surface generation/sandbox failures plainly
            console.print(str(e), style="red", markup=False)
            raise typer.Exit(1) from None
        if show_code:
            # markup=False: the code is raw Python and may itself contain
            # "[...]" (list literals) — Rich would otherwise parse those as
            # markup tags and silently swallow them from the preview.
            console.print(result.code, style="dim", markup=False)
            console.print()
        console.print(f"[dim]scanned {result.scanned} unit(s), "
                      f"answered by {result.provider}[/dim]\n")
        if not result.items:
            console.print("[yellow]No matches.[/yellow]")
            return
        console.print(f"[bold]Found {len(result.items)} match(es)[/bold]\n")
        for item in result.items:
            # every field here comes from the user's own stored content —
            # escape before interpolating into markup, or a title/value
            # containing "[...]" gets silently mangled the same way the
            # generated code did above.
            where = f" — {escape(item['where'])}" if item["where"] else ""
            src = (f"  [italic cyan underline]{escape(item['source'])}[/italic cyan underline]"
                  if item["source"] else "")
            console.print(f"[bold]{escape(item['document'])}[/bold]{where}{src}")
            console.print(f"    {escape(item['text'])}\n")
    finally:
        repo.close()


@app.command()
def note(
    text: str = typer.Argument(None, help="Note text (markdown). Omit to "
                               "open $EDITOR"),
    title: str = typer.Option("", "--title", help="Title (default: first "
                              "heading or line)"),
    tag: list[str] = typer.Option([], "--tag", "-t"),
    file: Path = typer.Option(None, "--file", "-f",
                              help="Read the note from a markdown file"),
):
    """Jot a note straight into your knowledge base (markdown).
    `dc note "remember: fsck the git image from the host"` — or run with no
    text to write a longer note in your $EDITOR."""
    import os
    import subprocess
    import tempfile

    from .notes import create_note

    if file is not None:
        content = file.expanduser().read_text(encoding="utf-8")
    elif text:
        content = text
    else:
        editor = os.environ.get("EDITOR", "nano")
        with tempfile.NamedTemporaryFile("w+", suffix=".md",
                                         delete=False) as tf:
            tf.write("# \n\n")
            path = tf.name
        subprocess.run([editor, path], check=False)
        content = Path(path).read_text(encoding="utf-8")
        Path(path).unlink(missing_ok=True)
        if not content.strip() or content.strip() == "#":
            console.print("[yellow]Empty note — nothing saved.[/yellow]")
            return
    config = load_config()
    # client role: the home server has WARM models — a note lands in ~a
    # second instead of paying local embedder warm-up + sync on every jot
    from .client import RemoteRejected, remote_api

    try:
        remote = remote_api(config, "/notes",
                            {"title": title, "content": content,
                             "format": "markdown", "tags": tag or None})
    except RemoteRejected as e:
        # the server REFUSED it (e.g. too long). Writing it locally anyway would
        # print "noted" for a note the server will never accept — and sync would
        # carry it there regardless, where the editor could never save it again.
        console.print(f"[red]Server rejected this note:[/red] {e.detail}")
        raise typer.Exit(1) from e
    if remote is not None:
        console.print(f"[green]noted[/green]: {remote.get('title', '')} "
                      "[dim](via server — reaches this device on next sync)[/dim]")
        return
    repo = _open_repo()
    try:
        doc = create_note(repo, _embedder(config), title=title,
                          content=content, fmt="markdown",
                          tags=tag or None)
        console.print(f"[green]noted[/green]: {doc.title}")
        _auto_sync(config, repo)
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
        else:
            _auto_sync(config, repo)
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
    from .storage import get_repo

    config = load_config()
    if not yes and not typer.confirm(
        f"Re-embed everything with {config.embeddings.model_name}? This can take a while."
    ):
        raise typer.Exit(1)
    # bypass the fingerprint guard — reembed IS the migration it demands
    repo = get_repo(config)
    embedder = _embedder(config)
    done = 0
    try:
        for doc, units in list(repo.iter_documents_with_units()):
            if not units:
                continue
            embeddings = embedder.embed_passages([unit_index_text(u) for u in units])
            # local re-index only: same ids, no version bump, nothing to sync
            repo.replace_document(doc, units, embeddings, bump_version=False)
            done += 1
        from .embeddings import FINGERPRINT_KEY, embedding_fingerprint

        repo.set_meta(FINGERPRINT_KEY, embedding_fingerprint(config))
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
        raise typer.Exit(1) from None
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
                embeddings = _embedder(load_config()).embed_passages([unit_index_text(u) for u in units])
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
def doctor():
    """Diagnose this installation: config, models, providers, server link.
    Run it whenever something says "unavailable" — it names the broken part."""
    import time

    from .config import load_config

    def row(ok, label, detail=""):
        mark = {True: "[green]✓[/green]", False: "[red]✗[/red]",
                None: "[yellow]•[/yellow]"}[ok]
        console.print(f" {mark} {label}" + (f" [dim]{detail}[/dim]" if detail else ""))

    try:
        config = load_config()
        row(True, "config", str(config_path()))
    except Exception as e:  # noqa: BLE001
        row(False, "config", str(e))
        raise typer.Exit(1) from None

    # knowledge base + embedding fingerprint
    try:
        repo = _open_repo()
        counts = repo.counts()
        row(True, "knowledge base",
            f"{counts.get('documents', 0)} docs / {counts.get('knowledge_units', 0)} units")
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        row(False, "knowledge base", str(e))
        raise typer.Exit(1) from None

    # embedding model
    try:
        t0 = time.monotonic()
        _embedder(config).embed_query("doctor warmup")
        row(True, "embeddings", f"{config.embeddings.model_name} "
            f"({time.monotonic() - t0:.1f}s warmup)")
    except Exception as e:  # noqa: BLE001
        row(False, "embeddings", str(e)[:120])

    # reranker
    from .reranker import _cuda_available, get_reranker
    rr = get_reranker(config)
    if rr is None:
        row(None, "reranker", f"off (rerank={config.retrieval.rerank}) — "
            "search cannot say an honest 'No' without it")
    else:
        row(True, "reranker",
            f"{config.retrieval.rerank_model} on "
            f"{'CUDA' if _cuda_available() else 'CPU'}")

    # providers
    import urllib.request

    def ping(url):
        try:
            with urllib.request.urlopen(url, timeout=4) as r:
                return r.status == 200
        except Exception:  # noqa: BLE001
            return False

    import os
    for name in config.llm.provider_order:
        if name == "anthropic":
            row(bool(os.environ.get("ANTHROPIC_API_KEY")) or None, "provider anthropic",
                "ANTHROPIC_API_KEY " +
                ("set" if os.environ.get("ANTHROPIC_API_KEY") else "not set"))
        elif name == "openai":
            row(bool(os.environ.get("OPENAI_API_KEY")) or None, "provider openai",
                "OPENAI_API_KEY " +
                ("set" if os.environ.get("OPENAI_API_KEY") else "not set"))
        elif name == "ollama" and config.llm.ollama_url:
            ok = ping(config.llm.ollama_url.rstrip("/") + "/models")
            row(ok, "provider ollama", config.llm.ollama_url)
        elif name == "vllm" and config.llm.vllm_url:
            ok = ping(config.llm.vllm_url.rstrip("/") + "/models")
            row(ok, "provider vllm", config.llm.vllm_url
                + ("" if ok else " unreachable"))

    # searxng — local config OR delegated to the home server ("via server"
    # asks are grounded by the SERVER's searxng, not this machine's)
    server_features = {}
    if config.deployment.role == "client" and config.server.remote_url:
        try:
            import json as json_mod

            with urllib.request.urlopen(
                    config.server.remote_url.rstrip("/") + "/api/v1/status",
                    timeout=4) as r:
                server_features = json_mod.loads(r.read()).get("features", {})
        except Exception:  # noqa: BLE001 — offline: local rows still valid
            pass
    if config.websearch.searx_url:
        ok = ping(config.websearch.searx_url.rstrip("/")
                  + "/search?q=doctor&format=json")
        row(ok, "searxng", config.websearch.searx_url +
            ("" if ok else " unreachable or json format disabled"))
    elif server_features.get("searxng"):
        row(True, "searxng", "grounded via your home server "
            "(local fallback answers stay ungrounded)")
    else:
        row(None, "searxng", "not configured — web answers stay ungrounded/low")

    # home server (client role)
    if config.deployment.role == "client":
        if not config.server.remote_url:
            row(False, "home server", "role=client but server.remote_url is empty")
        else:
            from .client import RemoteRejected, remote_api
            try:
                out = remote_api(config, "/search", {"query": "doctor", "k": 1},
                                 timeout=10)
            except RemoteRejected:
                out = None   # reachable but refusing (bad token?) — not healthy
            row(out is not None, "home server", config.server.remote_url
                + ("" if out is not None else " unreachable or bad token"))

    # note directories
    for d in config.notes.directories:
        row(Path(d).expanduser().is_dir(), "notes dir", d)

    repo.close()
    console.print("[dim]fix anything marked ✗; • is optional[/dim]")


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
        raise typer.Exit(1) from None
    from .server.app import create_app
    from .server.jobs import recover_and_start

    config = load_config()
    bind_host = host or config.server.host
    bind_port = port or config.server.port
    insecure_token = config.server.token in ("", "CHANGE-ME")
    if insecure_token and bind_host not in ("127.0.0.1", "localhost", "::1"):
        console.print("[red]Refusing to bind beyond loopback without an API "
                      "token.[/red] Set a long random [server].token in "
                      f"[bold]{config_path()}[/bold] first — an empty token "
                      "disables authentication entirely.")
        raise typer.Exit(1)
    console.print(f"DronaCharya at [bold]http://{bind_host}:{bind_port}[/bold] "
                  f"(token in {config_path()})")
    app_instance = create_app(config)
    recover_and_start(app_instance)   # finish any distillations a crash orphaned
    uvicorn.run(app_instance, host=bind_host, port=bind_port, log_level="warning")


@app.command()
def export(
    out: Path = typer.Option(Path("dronacharya-export.zip"), "--out", "-o"),
    format: str = typer.Option("zip", "--format",
                               help="zip (full JSON+markdown+operational) | "
                                    "obsidian (a folder of Markdown notes "
                                    "with frontmatter)"),
):
    """Download all your data (JSON + markdown, zipped) — or an
    Obsidian-ready folder of Markdown notes with --format obsidian."""
    from .export import export_markdown_dir, export_to_file

    repo = _open_repo()
    try:
        if format == "obsidian":
            target = out if out.suffix == "" else out.with_suffix("")
            n = export_markdown_dir(repo, target)
            console.print(f"[green]Exported {n} notes to {target}/[/green] "
                          "— open the folder as (or inside) an Obsidian vault.")
            return
        path = export_to_file(repo, out)
        repo.log_event("export", {"path": str(path)})
        console.print(f"[green]Exported to {path}[/green]")
    finally:
        repo.close()


@token_app.command("create")
def token_create(
    name: str = typer.Argument(..., help="Device/client name, e.g. 'laptop'"),
    scopes: str = typer.Option("read,write", "--scopes",
                               help="Comma-separated: read, write, admin"),
):
    """Mint a scoped token; the plaintext is printed ONCE — store it in the
    device's config or extension settings."""
    repo = _open_repo()
    try:
        scope_list = [x.strip() for x in scopes.split(",") if x.strip()]
        bad = set(scope_list) - {"read", "write", "admin"}
        if bad or not scope_list:
            console.print(f"[red]invalid scopes:[/red] {', '.join(bad) or '(none)'}")
            raise typer.Exit(1)
        token_id, plaintext = repo.create_token(name, scope_list)
        console.print(f"[green]token #{token_id}[/green] ({name}, "
                      f"{'/'.join(scope_list)}):")
        console.print(f"[bold]{plaintext}[/bold]")
        console.print("[dim]shown once — only a hash is stored[/dim]")
    finally:
        repo.close()


@token_app.command("list")
def token_list():
    repo = _open_repo()
    try:
        rows = repo.list_tokens()
        if not rows:
            console.print("No device tokens. The [server].token in config.toml "
                          "remains the admin key.")
            return
        for t in rows:
            state = "[red]revoked[/red]" if t["revoked"] else "[green]active[/green]"
            console.print(f"#{t['id']} {t['name']} · {t['scopes']} · {state}"
                          f" · last used {t['last_used'] or 'never'}")
    finally:
        repo.close()


@token_app.command("revoke")
def token_revoke(token_id: int):
    repo = _open_repo()
    try:
        ok = repo.revoke_token(token_id)
        console.print("[green]revoked[/green]" if ok else "[red]not found[/red]")
    finally:
        repo.close()


@app.command()
def wipe(
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation"),
    factory: bool = typer.Option(False, "--factory",
                                 help="Also erase operational data: event log, "
                                      "sync history, conflict payloads, device "
                                      "registrations. Local-only — does not "
                                      "propagate to other devices."),
):
    """Delete ALL knowledge from this device (tombstones propagate to synced
    devices). Plain wipe keeps the sync/audit trail so deletions reach your
    other devices; --factory erases that too."""
    if not yes and not typer.confirm("Delete your entire knowledge base?"):
        raise typer.Exit(1)
    if factory and not yes and not typer.confirm(
            "FACTORY RESET also erases sync history and the event log — "
            "other devices will NOT learn about these deletions. Continue?"):
        raise typer.Exit(1)
    repo = _open_repo()
    try:
        n = repo.wipe(factory=factory)
        if not factory:
            repo.log_event("wipe", {"documents": n})
        console.print(f"[red]Deleted {n} documents"
                      f"{' + all operational data' if factory else ''}.[/red]")
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
