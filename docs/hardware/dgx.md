# DGX-class home server — the recommended full setup

The server hosts the shared knowledge base (Postgres + pgvector), big local
models, reranking, and the sync upgrade pass. Laptops stay fully usable offline
and reconcile with it via `dc sync`.

## Prerequisites

- Linux with docker + docker compose
- NVIDIA drivers + [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
  (`docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi` should work)

## Steps

1. **Get the code and configure:**
   ```bash
   git clone <your-repo> dronacharya && cd dronacharya
   mkdir -p server-data
   cp config.server.example.toml server-data/config.toml
   # EDIT server-data/config.toml: set a long random [server].token
   ```
2. **Start the stack:**
   ```bash
   docker compose up -d --build
   ```
   This runs Postgres (pgvector) and the DronaCharya app on port 8317. Model
   servers are opt-in profiles — vLLM (recommended, below) or Ollama; profile
   services must be named on every `up` that should include them.
3. **Pick a model server.** Recommended: the vLLM profile with `gpt-oss:120b`
   (next section). Or the simpler Ollama container:
   ```bash
   docker compose --profile ollama up -d
   docker compose exec ollama ollama pull gpt-oss:120b
   ```
   Match `[llm].ollama_model` in `server-data/config.toml`, then `docker compose restart app`.
   (If the host already runs its own Ollama on 11434, skip the container and
   point `[llm].ollama_url` at `http://host.docker.internal:11434/v1` instead.)
4. **Verify from the server:**
   ```bash
   curl http://localhost:8317/api/v1/status
   ```
5. **Connect each laptop** (`~/.dronacharya/config.toml` on the laptop):
   ```toml
   [deployment]
   role = "client"

   [server]
   remote_url = "http://<server-ip-or-hostname>:8317"
   token = "<same token as the server config>"
   ```
   Then:
   ```bash
   dc sync        # push your local knowledge, pull everyone else's + upgrades
   ```
   Offline saves on the laptop are fully usable immediately and get re-distilled
   by the server's big model on the next sync; conflicts are auto-resolved and
   reviewable with `dc conflicts`.

## DGX Spark (GB10, aarch64)

The Spark's 128 GB **unified** memory is shared by every container, and decode
speed is bound by its ~273 GB/s memory bandwidth — so MoE models with few
active parameters massively outperform dense ones. Use the Spark override,
which serves **`openai/gpt-oss-120b`** (Apache-2.0) through NVIDIA's aarch64
vLLM container and parks Ollama (its aggregate throughput plateaus ~5–10×
below vLLM's on batch work like distillation and seed-kit builds):

```bash
docker compose -f docker-compose.yml -f docker-compose.dgx-spark.yml \
  --profile vllm up -d --build
```

In `server-data/config.toml`:

```toml
[llm]
provider_order = ["vllm", "anthropic", "openai"]
vllm_url = "http://vllm:8000/v1"
vllm_model = "openai/gpt-oss-120b"
```

Notes:
- gpt-oss-120b ships MXFP4-quantized natively — no `--quantization` flag.
- Lighter/faster fallback if you want more free memory: `Qwen/Qwen3.6-35B-A3B`
  (`VLLM_MODEL=... docker compose ... up -d`).
- For pure batch jobs (`dc seed build` over hundreds of pages) you can push
  concurrency: edit the override's `--max-num-seqs` toward 128–256 and drop
  `--gpu-memory-utilization` to ~0.65.
- Don't `pip install vllm` on the Spark — there are no stable aarch64/cu130
  wheels; the container is the supported path. Plain PyTorch for the app's
  embeddings/reranker is fine on the host via
  `uv pip install torch --index-url https://download.pytorch.org/whl/cu130`
  (the default cu12x wheels won't see the GB10).

## Optional: vLLM for maximum throughput

Ollama is the simplest path and fine for one user. If you want the DGX fully
utilized (fastest tokens/sec, concurrent requests):

```bash
VLLM_MODEL=Qwen/Qwen2.5-72B-Instruct-AWQ docker compose --profile vllm up -d
```

and in `server-data/config.toml`:
```toml
[llm]
provider_order = ["vllm", "ollama", "anthropic"]
vllm_url = "http://vllm:8000/v1"
vllm_model = "Qwen/Qwen2.5-72B-Instruct-AWQ"
```

## Recommended models by VRAM

| VRAM | Ollama model | Notes |
|---|---|---|
| 16 GB | `gpt-oss:20b` | MoE (3.6B active) — fast and strong |
| 24–48 GB | `qwen2.5:32b`, `gpt-oss:20b` | 32B dense is the quality sweet spot |
| 80 GB+ (DGX class) | `gpt-oss:120b` | Prefer MoE over dense 70B: bandwidth-bound hardware generates at active-param speed (5.1B) with 120B-class quality; dense `llama3.3:70b`/`qwen2.5:72b` run ~5× slower here |

## Remote access (optional, your choice)

Nothing in DronaCharya requires exposure beyond your LAN. If you want the
laptop to sync from outside your home, any mechanism you already trust works
(Tailscale, WireGuard, an SSH tunnel, a reverse proxy with TLS). This is
entirely optional and outside the app's design.
