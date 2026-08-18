# Seed knowledge kits

A **seed kit** (`*.dckit.json`) is portable starter knowledge for a fresh
DronaCharya KB: distilled summaries and knowledge units with source links,
built from a curated manifest of documentation URLs. The first-party kit,
`cli-essentials`, covers Linux distro command lines (Ubuntu/Debian apt,
RHEL-family dnf, Alpine apk, Tiny Core, Arch pacman), core shell tools, git,
python/pip/uv, Docker, WSL, and the Windows command line (cmd + PowerShell).

## Why kits work with any model — OSS or commercial

Kits deliberately contain **no embeddings and no model-specific data**:

- `dc seed install` embeds every unit locally with whatever embedding preset
  *your* KB was initialized with (`english` or `multilingual`).
- Answering uses whatever LLM provider *you* configured — your API keys,
  Anthropic/OpenAI API keys, or your own Ollama/vLLM endpoint. Even with no
  provider at all, `dc search` over the seeded knowledge works fully offline.

So one published kit file serves every user regardless of their model choice.

## Installing a kit

```bash
dc seed install cli-essentials.dckit.json          # local file
dc seed install https://example.com/kits/cli-essentials.dckit.json
```

Installation never overwrites your knowledge: URLs already in your KB are
skipped. Seeded documents are tagged with their plain topic (e.g. `wsl`,
`linux/alpine`, `windows/powershell`) so you can filter or bulk-
manage them, and carry `meta.seed_kit` for provenance.

## Building a kit

Building fetches each manifest URL **exactly once** (pages are never crawled),
applies the PII guardrails, distills with your configured LLM chain, and never
stores original page content — only distilled knowledge plus the source link.

Run it where your best distillation model lives (e.g. a DGX with vLLM):

```bash
dc seed build seedkits/cli-essentials.toml -o cli-essentials.dckit.json \
  --version 2026.08
```

- **Resume:** interrupted builds pick up where they stopped — the kit file is
  rewritten after every page and already-built URLs are never re-fetched.
  Re-running also retries only the previously failed URLs.
- **Licensing:** each manifest source declares its documentation license.
  Sources marked `redistributable = false` (e.g. `-NC-` licensed docs) are
  excluded by default so the resulting kit can be published; `--include-all`
  builds them too for a personal, not-for-publication kit.
- `--limit N` builds a small sample first — do this once to sanity-check your
  provider chain before a full run.

## Manifest format

```toml
[[source]]
topic = "wsl"                          # becomes the tag "wsl"
title = "Mount a Windows drive in WSL"
url = "https://learn.microsoft.com/en-us/windows/wsl/filesystems"
license = "CC BY 4.0"
redistributable = true
```

## Publishing a kit (kit authors)

The kit is a single JSON file — host it anywhere (GitHub release asset works
well) and users install straight from the URL. Publish only kits built without
`--include-all`, and keep the per-document `license` fields intact: they are
the attribution record for the distilled sources.
