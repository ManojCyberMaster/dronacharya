# Operator security posture

## Ports: open by design — know what that means

`docker compose` publishes these ports on the host by default:

| Port | Service | Auth |
|---|---|---|
| 8317 | DronaCharya app | Bearer token (required beyond loopback) |
| 11434 | Ollama (profile) | **None** |
| 8000 | vLLM (profile) | **None** |
| 8081 | SearxNG (profile) | None (rate limiter optional) |

This is a deliberate choice for personal LANs where several of your own
devices share one GPU box. The risk you accept: **anyone who can reach
those ports can use your models** (run prompts, burn GPU time, read
whatever they send). They cannot read your knowledge base through the
model ports — but the app port's bearer token is the only thing standing
between the network and your KB, so treat it like a password.

Not comfortable with that? Bind internals to loopback in an override file:

```yaml
# docker-compose.override.yml
services:
  vllm:    { ports: ["127.0.0.1:8000:8000"] }
  searxng: { ports: ["127.0.0.1:8081:8080"] }
```

and reach them via WireGuard/Tailscale from your other machines.

## Checklist

- [ ] Long random `[server].token` (the server refuses non-loopback start
      without one) — rotate by editing config + restarting.
- [ ] Per-device scoped tokens for the extension and clients
      (`dc token create laptop --scopes read,write`) so a leaked device
      token can be revoked without re-keying everything.
- [ ] Never expose any port directly to the internet; TLS + VPN or an
      authenticating reverse proxy in front, always.
- [ ] `POSTGRES_PASSWORD` via `.env`, not the default.
- [ ] Server role sets `[guardrails] allow_private_urls` implicitly to
      "never" — leave it unless you understand SSRF.
- [ ] Back up `server-data/` and the Postgres volume; test a restore.
