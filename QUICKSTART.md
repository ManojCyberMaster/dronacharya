# DronaCharya — Quick Start & Troubleshooting

A tiered guide: start on any machine with **no GPU and no LLM**, add a local
GPU model when you have one, and only then (optionally) plug in a cloud LLM
API — with a clear statement of **what leaves your machine and when**.

---

## Tier 0 — Any machine, CPU only, no LLM (5 minutes)

Everything that matters for *capturing and finding* knowledge works with no
model provider at all.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh     # install uv (once)
uv tool install "dronacharya[server] @ git+https://github.com/ManojCyberMaster/dronacharya.git"
dc init                                             # config + KB + embedding model (one-time download)
dc save https://en.wikipedia.org/wiki/Retrieval-augmented_generation
dc add ~/Documents/report.docx        # local files too: pdf/docx/xlsx/pptx/md/txt
dc search "retrieval augmented generation"
dc serve                                            # web UI at http://localhost:8317
```

What you get at this tier:

- **Capture** (CLI, web UI, browser extension) — pages are stored via the
  *extractive* fallback: heading-chunked excerpts, fully searchable.
- **Search** — hybrid keyword + semantic, entirely local (embeddings are
  computed on your CPU).
- Library, tags word map, mind maps, knowledge graph, to-dos, export, sync.
- No LLM answers yet: `dc "question"` will tell you no provider is available.

Reranking is **on by default** (the cross-encoder is small enough for CPU)
— it is what lets search honestly say "No" instead of listing weak keyword
matches. Check the whole setup any time with:

```bash
dc doctor            # config, models, providers, server link — names what's broken
```

**Privacy at this tier: nothing ever leaves your machine.**

## Tier 1 — Local GPU (private answers and distillation)

Install [Ollama](https://ollama.com/download) and pull a model sized to your
VRAM:

| VRAM | Model | Notes |
|---|---|---|
| 8 GB | `llama3.1:8b` | serviceable |
| 12–16 GB (e.g. RTX 5060 Ti) | **`gpt-oss:20b`** | best pick: MoE, 3.6B active params → fast *and* strong |
| 24–48 GB | `qwen2.5:32b` | dense-quality sweet spot |
| 80 GB+ / DGX-class | `gpt-oss:120b` via vLLM | prefer MoE over dense 70B — bandwidth-bound hardware generates at active-param speed |

```toml
[llm]
provider_order = ["ollama"]
ollama_url = "http://localhost:11434/v1"
ollama_model = "gpt-oss:20b"
```

Now `dc "question"`, chat, and *real* distillation (facts/concepts/how-tos in
your own words) run privately. Re-upgrade earlier extractive saves with
`dc redistill`.

A model-sizing note: everything RAG-critical here is **context-grounded
extraction** — the model reads text you give it. A good 20B-class model does
this reliably; giant models only pay off for `--deeper` general-knowledge
answers.

**Privacy at this tier: page text and questions go to your own GPU process on
localhost (or your own home server). Still nothing to third parties.**

### Optional: grounded web answers with your own SearxNG

Without web grounding, a fallback answer is *model memory* and is always
labeled low-confidence. Run SearxNG (self-hosted metasearch, called over HTTP
only) and quick answers get grounded on real fetched pages:

```toml
# ~/.dronacharya/config.toml — DronaCharya's OWN config, not searxng/settings.yml
# (settings.yml configures SearxNG itself: secret_key + enabling the json format)
[websearch]
searx_url = "http://localhost:8081"    # where your SearxNG instance listens
```

Grounded high-confidence answers auto-embed into your KB with their source.

## Tier 2 — Cloud LLM APIs (optional)

```bash
export ANTHROPIC_API_KEY=sk-ant-...    # and/or
export OPENAI_API_KEY=sk-...
```

```toml
[llm]
provider_order = ["ollama", "anthropic", "openai"]   # local first, cloud as fallback
```

### What goes out to a cloud LLM, and when

| Moment | Sent to the provider |
|---|---|
| Saving a page | The **extracted article text** (up to ~24k chars) + title, for distillation |
| `dc "question"` / chat | Your **question** + the **retrieved knowledge units** (your distilled notes, not original pages) |
| `dc query --deeper` | Your question + weak KB context (clearly bannered as outside-KB) |
| Everything else | **Nothing.** Search, embeddings, reranking, library, tags, graph, sync are all local |

If a page is too sensitive to send anywhere, make it *impossible* rather
than remembering not to:

```toml
[privacy]
distill = "local-only"     # page text can never reach a cloud API — cloud
                           # providers are removed from that task's chain
[llm]
distill_providers = ["ollama"]   # optional: small local model distills,
                                 # big models still answer
```

## Multi-device (optional)

Run the Docker home-server stack on one box (`docker compose up -d` —
Postgres + app; model server and SearxNG behind profiles), then on each
device:

```toml
[deployment]
role = "client"
[server]
remote_url = "http://<server-host>:8317"
token = "<token from the server's config.toml>"
```

`dc sync` reconciles (and runs automatically after saves when `[sync]
auto` is on): deletions win, conflicts resolve by per-document version
counters (clock-skew safe) with the losing version kept for review
(`dc conflicts`), weak offline saves get upgraded by the server's model.
Sync ships text only — each device re-embeds locally.

Give each device its own **scoped token** instead of sharing the admin one:

```bash
dc token create laptop --scopes read,write    # printed once; revocable
```

## Browser extension

1. `chrome://extensions` → Developer mode → **Load unpacked** → the
   `extension/` folder.
2. Popup → Settings → server URL + token. A non-localhost URL triggers a
   one-time host-permission prompt — that's the extension asking for exactly
   your server's origin, nothing else.
3. Save any page: for a non-localhost server a consent panel first shows
   what will be sent and where; then the in-page panel shows distillation
   progress and the distilled summary + knowledge items to review (edit,
   remove, or discard).

---

# Troubleshooting FAQ

Real issues, in the order you're likely to hit them.

**"No LLM provider available" — but I configured one.**
The message now prints the underlying error beneath it — read that first.
Checklist: is the model server actually running (`curl http://<host>:11434/v1/models`
or `:8000/v1/models`)? For API providers, is the key exported in the *same
shell*? Note `dc query` always answers locally — a home server being up
doesn't help if the local `[llm]` block is wrong.

**My vLLM/Ollama model name doesn't match.**
Servers use exact ids (`openai/gpt-oss-120b`, not `gpt-oss-120b`). DronaCharya
auto-resolves unambiguous mismatches by asking the server for its model list,
but setting the exact id avoids a wasted round-trip.

**Docker: everything worked, then after a redeploy the model server is gone.**
Compose *profiles* footgun: `docker compose down` removes profile services
(vLLM, SearxNG), and a plain `up -d` does **not** restart them. Re-run with
the profile: `docker compose --profile vllm up -d`. For routine app updates
prefer `docker compose up -d --build app` and avoid `down` entirely.

**Saving a page takes minutes.**
Three usual causes: (1) a *reasoning* model at default effort — DronaCharya
caps gpt-oss reasoning to "low" for distillation, so update if you're on an
old build; (2) a **dense** 70B on bandwidth-bound hardware — switch to an MoE
(see the VRAM table); (3) saves queue one-at-a-time — clicking save again just
queues another. The extension overlay reports "still distilling / queued"
honestly and surfaces server-side failures instead of spinning.

**The saved summary just describes the page ("This article explains…").**
That was a prompt bug in old builds — distillation now demands the summary
*teach* the takeaways. Upgrade, then run `dc redistill` to regenerate old
summaries.

**Answers come back in the wrong language.**
Old builds asked the model to "answer in the question's language", which small
local models misjudge. Current builds detect the language and name it
explicitly. Upgrade if you see this.

**`dc "question"` says "No verified answer."**
Working as designed: the answer couldn't be verified against real fetched
pages. Your options are printed with it — `dc query "…" --deeper` for a
clearly-labeled model answer, `--guess` to see the unverified quick answer, or
set up SearxNG so fallback answers get grounded (and can be high-confidence).

**`dc search` says "No." but I think it should match.**
Below-threshold results are hidden on purpose; `--all` shows them. The
threshold (`retrieval.min_relevance`, reranked-probability scale) is tuned
against the golden eval set — if you change it, run
`DC_RUN_EVALS=1 pytest tests/test_eval_retrieval.py`. Never disable
reranking if you care about honest refusals.

**"embedding model changed … run `dc reembed`".**
A KB is bound to one embedding model (mixing vectors corrupts search). You
changed `[embeddings]`; `dc reembed` rebuilds the index and re-stamps the
fingerprint. Each synced device re-embeds for itself — sync never ships
vectors.

**What's the difference between `dc wipe` and `dc wipe --factory`?**
Plain wipe deletes all knowledge and keeps sync tombstones so the deletion
propagates to your other devices. `--factory` also erases the event log,
sync history, and device registrations on this machine — local-only by
design.

**Sources listed that have nothing to do with the answer.**
Retrieval matches keywords too ("how to make X" can match *make(1)* docs).
Only sources the answer actually **cited** are listed; if an old build shows
uncited candidates, upgrade.

**The model refuses a legitimate question (medical, legal, security…).**
The prompts explicitly frame these as legitimate personal knowledge, but a
model's trained-in refusals can occasionally override prompts. Remedies:
retry, put a second provider in `provider_order`, or use a different local
model.

**"could not extract, or blocked by guardrails" when saving.**
The thin-page guard rejects extractions under ~300 chars — usually a login
wall or cookie screen reached by server-side fetch. Use the **browser
extension** instead: it captures the rendered DOM of your logged-in tab.

**Extension says "Server unreachable or bad token."**
Check the server URL (scheme + port), the bearer token (from the server's
`config.toml`), and — for a non-localhost server — that you accepted the
host-permission prompt when saving settings.

**WSL specifics.**
Load the unpacked extension from `\\wsl.localhost\<Distro>\home\<you>\...`;
Windows browsers reach a WSL `dc serve` via localhost forwarding
automatically. If the picker rejects the UNC path, copy `extension/` to the
Windows filesystem.

**Where is my data? How do I get it out?**
Everything lives in `~/.dronacharya/` (config + SQLite KB). `dc export`
produces a full zip; `dc wipe` deletes everything. Back that directory up
like any personal data — or run the home server and let sync be your second
copy.

**Which config file does a setting go in?**

| File | Belongs to | What goes in it |
|---|---|---|
| `searxng/settings.yml` (copy of the tracked `settings.example.yml`) | SearxNG itself | `use_default_settings`, `secret_key`, json format, engine selection |
| `server-data/config.toml` | DronaCharya **server** (docker host) | `[server] token`, `[llm]` models, `[websearch] searx_url` |
| `~/.dronacharya/config.toml` | DronaCharya **client** (each device) | `[deployment] role`, `[server] remote_url` + token, local `[llm]` |

`searx_url` never goes in `settings.yml`, and engine selection never goes in
`config.toml` — each side configures its own service.

**Port 8317 is taken.**
`[server] port = ...` in the config; the web UI, extension, and clients all
follow the URL you give them.
