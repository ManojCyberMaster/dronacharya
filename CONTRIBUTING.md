# Contributing

## Setup

```bash
uv venv && uv pip install -e ".[server,dev]"
.venv/bin/python -m pytest -q          # full suite, hermetic (no GPU, no network)
```

## Quality bars

- Lint: `ruff check .` (config in pyproject.toml) must be clean.
- The maintainer validates changes against a comprehensive private test
  suite (unit, integration, golden retrieval evals, browser E2E) before
  merging — describe how you verified your change in the PR.
- Dependencies must be permissively licensed (Apache/MIT/BSD — no
  AGPL/GPL/SSPL). SearxNG is used only as an external HTTP service.
- Web UI changes: verify in a real browser and say so in the PR — code
  inspection alone has repeatedly missed visual/interaction bugs here.

## Architecture ground rules

Read ARCHITECTURE.md first. Load-bearing invariants:

- Only the explicitly saved page is fetched — never links.
- Raw page HTML is never persisted; user-authored files are stored as-is.
- Answers cite sources; unverifiable answers refuse rather than guess.
- One embedding model per KB (fingerprint-enforced); embeddings never sync.
- Single-tenant by design (`tenant_id='local'` everywhere).
