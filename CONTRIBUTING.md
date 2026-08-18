# Contributing

## Setup

```bash
uv venv && uv pip install -e ".[server,dev]"
.venv/bin/python -m pytest -q          # full suite, hermetic (no GPU, no network)
```

## Quality bars

- Tests accompany behavior changes; the suite must stay hermetic
  (fixtures pin `rerank = "off"` and a fake embedder — never depend on
  host CUDA or network).
- Retrieval-affecting changes must keep the golden eval green:
  `DC_RUN_EVALS=1 pytest tests/test_eval_retrieval.py` (loads real models).
- Lint: `ruff check .` (config in pyproject.toml).
- Dependencies must be permissively licensed (Apache/MIT/BSD — no
  AGPL/GPL/SSPL). SearxNG is used only as an external HTTP service.
- Web UI changes: verify in a real browser (headless Chromium works —
  see tests' Playwright usage) — code inspection alone has repeatedly
  missed visual/interaction bugs here.

## Architecture ground rules

Read ARCHITECTURE.md first. Load-bearing invariants:

- Only the explicitly saved page is fetched — never links.
- Raw page HTML is never persisted; user-authored files are stored as-is.
- Answers cite sources; unverifiable answers refuse rather than guess.
- One embedding model per KB (fingerprint-enforced); embeddings never sync.
- Single-tenant by design (`tenant_id='local'` everywhere).
