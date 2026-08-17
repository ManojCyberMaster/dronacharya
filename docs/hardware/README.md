# Choosing where your LLM answers come from

DronaCharya's knowledge base is always yours and local. What's *pluggable* is the
LLM that distills saved pages and answers questions. Every option below is
optional and configured only in `~/.dronacharya/config.toml` → `[llm]` — no code
changes, ever. Providers are tried in `provider_order` until one answers.

| Your setup | Guide | What you get |
|---|---|---|
| Any laptop, no GPU, cloud LLMs | [laptop-cloud-only.md](laptop-cloud-only.md) | **Full functionality.** Frontier-quality distillation and answers via your API keys. Capture + search always work fully offline. |
| Laptop/desktop with an RTX-class GPU (e.g. RTX 5060) | [rtx.md](rtx.md) | Local 8–14B models for private/offline answers, GPU-fast embeddings, reranking for better search. |
| Home server, DGX-class (recommended full setup) | [dgx.md](dgx.md) | 70B-class local models, Postgres knowledge base, reranking, background distillation upgrades, sync target for all your devices. |
| A GPU you rent or run elsewhere | [remote-gpu.md](remote-gpu.md) | Point `vllm_url` at any OpenAI-compatible endpoint you control. |

**Recommended defaults**

- Start with **cloud-only** — it works everywhere and gives the best distillation quality on day one.
- Add **Ollama** when you want privacy/offline answers: it's a one-command install on macOS, Linux, and Windows, auto-detects your GPU, and every tier uses it the same way.
- Add **vLLM** only on the big server, when you care about throughput (it's also what a rented GPU endpoint typically runs).
- Mix freely: a common `provider_order` on a laptop that sometimes has the home server reachable is `["vllm", "ollama", "anthropic"]` — best local first, a cloud API as the safety net.

**Two rules that never change**

1. The **embedding model is fixed per knowledge base** (`[embeddings].preset`) and must match across all devices you sync — it runs fine on CPU everywhere. Changing it later means re-embedding (`dc reembed`).
2. LLM content flows wherever *you* point it (your cloud account, your GPU box); the knowledge store itself stays on your machines.
