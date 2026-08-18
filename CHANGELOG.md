# Changelog

## Unreleased — trust, security & durability wave (2026-08)

Driven by an external four-lens review plus an adversarial code review
(26 confirmed findings). Highlights:

### Fixed
- 13 correctness bugs: Excel column misalignment, overwrite-review race,
  `--no-save` bypass via server, challenge-page false positives, offline
  30–90s stalls, to-do corruption through Library edits, mind-map
  double-create, entity double-decoding, lost citations on dropped
  streams, and more.

### Trust
- Confidence gating rebuilt: reranked scores are sigmoid probabilities
  gated by `retrieval.min_relevance` (calibrated against a golden eval
  set); raw fusion scores no longer masquerade as confidence.
- Reranking defaults to on (CPU-capable) — honest "No" everywhere.
- Grounded high-confidence answers additionally verified claim-to-passage.
- Citations computed server-side; every client renders the same set.

### Security
- SSRF-safe fetching (role-aware, per-redirect validation).
- Scoped per-device API tokens (`dc token`), hashed at rest.
- Insecure non-loopback startup refused; body/param bounds; security
  headers; prompt-injection fencing; MCP read-only by default.
- `dc wipe --factory` + truly-full export (operational data included).

### Reliability & sync
- Durable job queue: queued distillations survive restarts.
- Sync ships text only (receiver re-embeds); version-counter conflict
  resolution immune to clock skew; embedding-model fingerprint with
  `dc reembed` migration; auto-sync actually implemented.

### New
- Web file upload with drag-and-drop (AbstractSpoon .tdl, notes, PDF,
  Office) on the Library page.
- `dc doctor`, `dc token`, `dc export --format obsidian`, `dc add`
  (pdf/docx/xlsx/pptx), `--local` flags, per-task provider routing with
  `[privacy]` local-only policies, extension pre-upload consent panel,
  published OpenAPI reference at /api/v1/docs.
