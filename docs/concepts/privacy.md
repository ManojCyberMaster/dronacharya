# Privacy — what leaves your machine, and when

## The table

| Action | What is sent | To |
|---|---|---|
| Capture / save | Extracted page text (≤24k chars) for distillation | Your configured LLM provider only |
| `dc search`, library, tags, graph | Nothing | — |
| Embedding / reranking | Nothing (local models) | — |
| Ask / chat | Your question + retrieved knowledge units | Your configured LLM provider |
| Web fallback (SearxNG) | The question as a search query; then plain fetches of result pages | Your own SearxNG; the result sites |
| Source verification | One fetch of a cited URL | The cited site |
| Sync | Documents/units/tags (text + metadata, no vectors) | Your own home server only |

No telemetry, no third-party services, and the browser extension talks only
to the server URL you configure.

## Enforcement, not intention

`[privacy]` in config.toml sets a per-task egress policy:

```toml
[privacy]
distill = "local-only"   # page text can NEVER reach a cloud API
answer  = "any"          # questions may use any configured provider
```

`local-only` removes cloud providers from that task's chain *structurally* —
an error in your local model cannot fall through to a cloud key, because the
cloud provider isn't in the chain at all. `[llm].distill_providers` further
lets a small local model handle distillation while answers use bigger ones.

## Capture consent

When the extension is configured against a non-localhost server, saving a
page first shows what will be sent (title, URL, size — including content
visible behind your login) and where. Toggle in extension settings.

## What is logged, and for how long

The local event log records operation types with limited metadata —
question text (truncated), saved URLs, error strings. It exists for your
own debugging and audit; it never leaves your machine except in your own
`dc export` (included deliberately, so "full export" is true), and it is
erased by `dc wipe --factory`. Retention is indefinite until you wipe;
there is no automatic expiry yet.
