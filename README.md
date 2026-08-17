# DronaCharya

**Personal knowledge management with a local RAG.** Save web pages and notes, distill them into searchable knowledge, and find your references again — with source links, on your own machine.

- Purely **personal** knowledge management — no teams, no sharing, by design.
- Runs on **macOS, Linux, Windows, or WSL**. Works standalone on a laptop; optionally against your own home server for heavier models.
- **Copyright-conscious**: only the single page you save is ever fetched (no crawling); only distilled knowledge is stored, never the original page content; answers always link back to the source.
- **Your data, your rights**: full export (`dc export`), full deletion (`dc wipe`), basic PII filter at ingest.
- LLM answers via **your** providers: Anthropic/OpenAI API keys or your own Ollama/vLLM endpoint. None are mandatory for capture and search.

## Quick start

```bash
pip install -e .
dc init                      # config + knowledge base + embedding model
dc seed install cli-essentials.dckit.json   # optional starter knowledge (see below)
dc save https://example.com/article --tag reading
dc add ~/Documents/report.docx --tag work    # local files: pdf/docx/xlsx/pptx/md/txt
dc sync-notes                # after adding note directories to the config
dc search "that thing I read about chunking"
dc "command line for mounting local windows drive in wsl"   # quick answer
```

See [docs/install.md](docs/install.md) for per-OS install (Windows, Linux, WSL, macOS).

## Quick answers: `dc "<question>"`

Any argument that isn't a subcommand is a question. The answer is deliberately
minimal — for command questions, the command line plus one usage example;
otherwise a sentence or two. If your KB doesn't know, `dc` falls back to the
internet and embeds the qualified answer back into your KB so the next ask is
instant and offline. Trust is earned, not claimed: answers auto-save only when
grounded on real fetched pages (your own SearxNG, or a web-capable provider).
An answer that can't be verified is not shown at all — `dc` says "No verified
answer" and points you at `dc query "…" --deeper` (full model answer, clearly
labeled) or `--guess` (show the unverified quick answer; it then asks before
saving — `--yes` to skip the prompt, `--no-save` to never store). `dc search`
likewise answers "No" instead of listing weak matches (`--all` shows them).

## Seed knowledge kits

A fresh KB can start pre-loaded: `dc seed install <file-or-url>` installs a
**seed kit** — distilled command-line knowledge (Linux distros, bash/coreutils,
git/python/pip/uv/docker, WSL, Windows cmd/PowerShell) with source links.
Kits contain **no embeddings and nothing model-specific**, so one kit works
with any embedding preset and any LLM provider — or none (search works
offline). Build your own from a curated URL manifest with `dc seed build`;
see [docs/seed-kits.md](docs/seed-kits.md).

Configuration lives in `~/.dronacharya/config.toml`. See [ARCHITECTURE.md](ARCHITECTURE.md) for the full architecture and [QUICKSTART.md](QUICKSTART.md) for setup + troubleshooting.

## Where do the LLM answers come from? (DGX, Ollama, vLLM, cloud APIs)

All pluggable, none mandatory — see **[docs/hardware/](docs/hardware/README.md)** for the
recommended way and basic steps per setup:

- **[Any laptop, cloud LLMs only](docs/hardware/laptop-cloud-only.md)** — full functionality via your Anthropic/OpenAI API keys.
- **[RTX-class GPU](docs/hardware/rtx.md)** — private local answers with Ollama + search reranking.
- **[DGX-class home server](docs/hardware/dgx.md)** — the recommended full setup: `docker compose up` (Postgres + Ollama + app), 70B-class models, optional vLLM profile, and `dc sync` from all your devices.
- **[Remote GPU endpoint](docs/hardware/remote-gpu.md)** — point at any OpenAI-compatible server you control.

## Import your existing bookmarks

```bash
dc import-bookmarks bookmarks.html            # exported from Chrome/Edge/Brave/Firefox
dc import-bookmarks bookmarks.html --refresh  # later: re-fetch and pick up changed pages
```

Bookmark **folders become hierarchical tags** (`Research/RAG`). Already-imported
pages are skipped instantly on re-runs; **dead pages are reported at the end**
(HTTP errors, vanished domains) so you know exactly what couldn't be captured.
Bulk imports use fast distillation by default — upgrade afterwards with
`dc redistill` (or let your home server do it on `dc sync`).

## Tags

Tags are plain strings; a `/` makes them hierarchical by convention only
(`Research/RAG` is one tag — no tree logic anywhere, so everything stays fast).
Filtering by `Research` also matches `Research/RAG`. Tags filter search and
chat everywhere: `dc search "..." --tag Research`, `dc query "..." --tag Research`,
the tag box in the Web UI chat, and the library view. Tag inputs suggest your
existing tags after you type 4 characters.

The web UI's **Tags page** draws a semantic word map of all your tags —
related topics near each other, one color per topic family, darker where you
have more knowledge; pan/zoom the map, click through to browse and edit the
items, and rename or delete a tag right from its modal. Mind-map node tags
share the same namespace — browsing such a tag shows
`MindMap:<Name> > <node path>`. Manage tags in bulk with
`dc tags list|rename|remove|strip-prefix`.

## Your knowledge stays editable

Every document opens into its individual knowledge items (Library or Tags
page) — edit or remove any item; changes are re-embedded instantly and sync
like any other change. Deleting a document removes all its knowledge.

## Surfaces

- `dc` CLI — ask (default), save, add (local pdf/docx/xlsx/pptx), seed, tags, sync-notes, search, query, sync, export, wipe. Local files are parsed with the stdlib (no Office libraries), and answers reference the file path — search "that revenue figure" and get `~/Documents/plan.docx · Quarterly targets` back.
- Web UI — chat with cited answers + library (`dc serve` → http://localhost:8317)
- Tags word map (web) — semantic map of your topics with pan/zoom, click-through browsing, editing, tag rename/delete ([docs](docs/mindmaps-graph.md))
- Mind maps (web) — full XMind-style editor: 4 layouts (incl. hierarchical), themes, node styles/tags, per-node rich-text notes, styled cross-links, outline, focus, PNG/SVG/JSON/Markdown export; every node (and note) becomes searchable knowledge ([docs](docs/mindmaps-graph.md))
- Knowledge graph (web) — search results as connected facts/documents/tags ([docs](docs/mindmaps-graph.md))
- To-dos (web + extension) — reminders stored in the KB: synced, searchable, browser notifications when due
- Browser extension — `extension/` (load unpacked): save pages **straight from the tab** (works behind logins); an in-page panel shows distillation progress, then the extracted summary + knowledge items to review — edit the summary, drop items, or discard, right on the page. Plus selection saves, ask-from-popup, and **to-do reminders** with browser notifications that sync through your KB
- MCP server — add `python -m dronacharya.mcp_server` as a stdio server in any MCP client

## Multi-device

Each device is a fully functional offline knowledge base. `dc sync` reconciles
with your home server: deletions always win, conflicts are auto-resolved by
last-write-wins with the losing version kept for review (`dc conflicts`), and
weakly-distilled offline saves get upgraded by the server's bigger model.

## Status

Implemented and tested: capture + distillation + guardrails, LLM provider
chain, server/web UI/extension, Postgres home-server backend, offline-first
multi-device sync. See [ARCHITECTURE.md](ARCHITECTURE.md) for how it all fits
together.

## License

Apache-2.0. Dependencies are restricted to permissive licenses (no AGPL/copyleft obligations).
