# Desktop/laptop with an RTX-class GPU (e.g. RTX 5060, 8–16 GB VRAM)

Adds private local answers, offline distillation, and search reranking.
Cloud providers keep working as fallback — nothing is replaced.

## Steps

1. **Install Ollama** (one command / installer): <https://ollama.com/download>
   — macOS, Linux, and Windows installers auto-detect NVIDIA GPUs.
2. **Pull a model sized for your VRAM:**
   ```bash
   ollama pull gpt-oss:20b        # ~13 GB — best pick for 16 GB cards (RTX 5060 Ti/4080):
                                  #   MoE, only 3.6B active params → fast AND strong
   ollama pull qwen2.5:14b        # ~9 GB — great quality for 12-16 GB cards
   ollama pull llama3.1:8b        # ~5 GB — fits 8 GB cards
   ```
3. **Point DronaCharya at it** (`~/.dronacharya/config.toml`):
   ```toml
   [llm]
   provider_order = ["ollama", "anthropic"]
   ollama_url = "http://localhost:11434/v1"
   ollama_model = "qwen2.5:14b"

   [retrieval]
   rerank = "auto"                # auto-enables on your GPU
   ```
4. **Verify:**
   ```bash
   dc query "something you saved"     # footer shows: answered by ollama
   ```

## Notes

- PyTorch will use your GPU for embeddings and the reranker automatically when
  installed with CUDA support (`pip install torch` default wheels on Windows/Linux).
- `rerank = "auto"` turns the cross-encoder on when CUDA is present — noticeably
  better "find that reference" precision.
- Quality expectation: 8–14B models distill and answer well; for the hardest
  questions keep a cloud provider later in `provider_order` and use `--deeper`.
