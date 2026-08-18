# Security

## Deployment model & threat assumptions

DronaCharya is a **personal, self-hosted** system. The supported shapes are:

1. **Standalone laptop** — everything on loopback. Default posture.
2. **Home server on a trusted LAN** — the app on :8317 with bearer-token
   auth; model servers (Ollama/vLLM) and SearxNG on their own ports.
3. **Client devices** syncing to that server over the same trusted network.

**Deliberate decision (documented risk):** the docker stack *publishes* the
model-server and SearxNG ports instead of confining them to the compose
network, because personal setups legitimately share a GPU endpoint across
machines. Those services have **no authentication of their own** — anyone
who can reach the port can run prompts (Ollama/vLLM) or search (SearxNG).
Acceptable on a network where every device is yours; **not acceptable on a
shared or public network.** If that's your situation, bind them to
127.0.0.1 in a compose override and reach them through a VPN (WireGuard/
Tailscale) — see docs/operators/security-posture.md.

Nothing in DronaCharya is designed for direct internet exposure. Put TLS
and a VPN or reverse-proxy auth in front of anything that leaves your LAN.

## What the app enforces

- Bearer-token auth on the API; the server **refuses to bind beyond
  loopback with an empty or placeholder token**.
- Scoped per-device tokens (`dc token create`, read/write/admin) stored as
  SHA-256 hashes; revocable; the config token remains the admin key.
- SSRF defenses on server-side fetching: private/loopback/link-local/
  metadata targets and every redirect hop are blocked in server role
  (policy: `[guardrails] allow_private_urls`).
- Request-body caps, bounded pagination/k parameters, security headers.
- Fetched pages and imported content enter prompts fenced as untrusted
  data; the model is instructed to ignore embedded instructions (prompt
  injection is mitigated, not eliminated — no system that feeds web text
  to an LLM can claim elimination).
- MCP server is read-only unless `DRONACHARYA_MCP_WRITE=1`.
- The regex PII filter is a convenience, **not** a security boundary.

## Data rights

`dc export` includes operational data (event log, sync conflict payloads) —
not just knowledge. `dc wipe` deletes knowledge and propagates tombstones to
synced devices; `dc wipe --factory` additionally erases the event log, sync
history, and device registrations on this machine.

## Reporting

Security issues: open a private issue or contact the maintainer directly.
Please do not file public issues for exploitable problems.
