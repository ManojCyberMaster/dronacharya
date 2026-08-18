# Laptop, no GPU — cloud generation, local retrieval

Generation runs on a cloud API you configure; capture, embeddings, search,
and reranking stay entirely on your machine.

Full functionality with zero local model hosting. Works on macOS, Linux,
Windows, and WSL.

## Steps

1. Install and initialize:
   ```bash
   pip install -e ".[server]"        # or plain `pip install -e .` for CLI-only
   dc init
   ```
2. Pick your provider(s) — any ONE of these is enough:

   **Anthropic API key:**
   ```bash
   export ANTHROPIC_API_KEY=sk-ant-...
   ```

   **OpenAI API key:**
   ```bash
   export OPENAI_API_KEY=sk-...
   ```
3. Order them in `~/.dronacharya/config.toml`:
   ```toml
   [llm]
   provider_order = ["anthropic", "openai"]
   ```
4. Verify:
   ```bash
   dc save https://en.wikipedia.org/wiki/Retrieval-augmented_generation
   dc query "what did I just save about RAG?"
   ```

## What to expect

- Distillation on save uses the cloud model (seconds, spends a little quota per page).
- **Offline**: capture and `dc search` keep working fully; saves fall back to
  excerpt mode and are upgraded automatically the next time an LLM is reachable
  (`dc redistill`, or your home server's upgrade pass after `dc sync`).
- Reranking is off (no GPU) — hybrid search is still strong.
