# Remote GPU endpoint (your own box elsewhere, or a rented GPU service)

Anything that speaks the OpenAI-compatible API works — vLLM, Ollama, LM Studio,
llama.cpp server, or a rented GPU service endpoint you control.

## Steps

1. Start the endpoint on the remote machine, e.g. vLLM:
   ```bash
   pip install vllm
   vllm serve Qwen/Qwen2.5-32B-Instruct-AWQ --port 8000
   ```
   (or `ollama serve` — its OpenAI API lives at `http://<host>:11434/v1`)
2. Point DronaCharya at it (`~/.dronacharya/config.toml`):
   ```toml
   [llm]
   provider_order = ["vllm", "anthropic"]
   vllm_url = "http://<remote-host>:8000/v1"
   vllm_model = "Qwen/Qwen2.5-32B-Instruct-AWQ"
   ```
3. Verify: `dc query "…"` — the footer shows `answered by vllm`.

Notes

- The `vllm` and `ollama` provider slots are just names — each is "any
  OpenAI-compatible endpoint"; use whichever slot reads better in your config.
- Your saved-page text is sent to that endpoint for distillation/answers — it's
  your machine or your rental agreement, your call. The knowledge base itself
  never moves.
- Reachability of the remote host (VPN, tunnel, private network) is up to you —
  DronaCharya just needs the URL.
