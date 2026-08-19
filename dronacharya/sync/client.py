"""Client-side sync: push local changes to the home server, pull deltas back.

OSS/single-user scope. Offline saves are already fully usable locally; this
reconciles them with the server (which then upgrade-distills weak saves and
sends the improved knowledge back on the next pull)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from ..config import Config
from ..models import new_id
from .merge import apply_ops, collect_ops

SELF_DEVICE_NAME = "self"


class SyncError(RuntimeError):
    pass


# One push carries whole documents (note_source, mindmap JSON and all), so an
# un-pushed backlog can outgrow the server's 25 MB request ceiling. Above it the
# push 413s, the cursor never advances, and auto-sync retries the identical
# oversize payload forever — silently. Batch well under the limit instead.
MAX_PUSH_BYTES = 8 * 1024 * 1024
MAX_PUSH_OPS = 200


def get_self_device_id(repo) -> str:
    for device in repo.list_devices():
        if device.get("name") == SELF_DEVICE_NAME:
            return device["id"]
    device_id = new_id()
    repo.device_update(device_id, name=SELF_DEVICE_NAME)
    return device_id


def _batches(ops: list[dict]) -> list[list[dict]]:
    """Split ops into requests that stay under the server's body limit. A single
    op larger than the limit is still sent alone — it will fail loudly with a
    413 naming one document, which is far better than a wedged, silent sync."""
    out: list[list[dict]] = []
    current: list[dict] = []
    size = 0
    for op in ops:
        op_size = len(json.dumps(op))
        if current and (size + op_size > MAX_PUSH_BYTES
                        or len(current) >= MAX_PUSH_OPS):
            out.append(current)
            current, size = [], 0
        current.append(op)
        size += op_size
    if current:
        out.append(current)
    return out


def _request(config: Config, method: str, path: str, payload: dict | None = None) -> dict:
    base = config.server.remote_url.rstrip("/")
    if not base:
        raise SyncError("no server configured — set [server].remote_url in config.toml "
                        "and [deployment].role = \"client\"")
    req = urllib.request.Request(
        base + path, method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {config.server.token}"}
               if config.server.token else {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise SyncError(f"server returned HTTP {e.code} for {path}") from e
    except urllib.error.URLError as e:
        raise SyncError(f"server unreachable ({e.reason})") from e


@dataclass
class SyncReport:
    pushed: int = 0
    pulled: int = 0
    conflicts: int = 0
    deleted: int = 0
    failed: int = 0


_AUTO_SYNC_KEY = "__auto_sync_ts__"

# Escape hatch for testing against a real config that points at a live server:
# auto-sync is on by default, fires from `dc save`/`add`/`note`/`sync-notes`,
# and a first sync pushes the ENTIRE local KB (a new device starts at seq 0).
SYNC_DISABLED_ENV = "DRONACHARYA_NO_SYNC"


def sync_disabled() -> bool:
    import os

    return os.environ.get(SYNC_DISABLED_ENV, "").strip().lower() not in ("", "0", "false")


def maybe_auto_sync(repo, config: Config, *, quiet: bool = True,
                    on_start=None, on_error=None) -> "SyncReport | None":
    """Opportunistic reconcile implementing [sync] auto / interval_seconds:
    called by CLI commands after KB-touching work. Rate-limited via a
    timestamp in sync_state; never raises (offline is normal, not an error)."""
    import time

    if (not config.sync.auto or config.deployment.role != "client"
            or not config.server.remote_url or sync_disabled()):
        return None
    state = repo.get_sync_state(_AUTO_SYNC_KEY)
    now = time.time()
    if state and now - state[0] < config.sync.interval_seconds:
        return None
    if on_start is not None:
        on_start()
    try:
        report = sync_once(repo, config)
    except Exception as exc:  # noqa: BLE001 — offline is normal, not an error
        # Stamp only on a REAL attempt outcome, and say something: stamping
        # before the try meant a failing sync went quiet for a whole interval.
        repo.set_sync_state(_AUTO_SYNC_KEY, now, "auto")
        if on_error is not None and not quiet:
            on_error(exc)
        return None
    repo.set_sync_state(_AUTO_SYNC_KEY, now, "auto")
    return report


def sync_once(repo, config: Config) -> SyncReport:
    device_id = get_self_device_id(repo)
    last_push, last_pull = repo.device_state(device_id)
    report = SyncReport()

    # ids with un-pushed local changes — the genuine-conflict set for the pull
    pending_ids = {entity_id for _, entity, entity_id, _ in
                   repo.oplog_since(last_push, local_only=True)
                   if entity in ("document", "tags")}

    # 1. push — in batches that fit the server's request ceiling
    ops, local_latest = collect_ops(repo, last_push, local_only=True)
    for batch in _batches(ops):
        _request(config, "POST", "/api/v1/sync/push",
                 {"device_id": device_id, "ops": batch})
        report.pushed += len(batch)
        # record WHICH documents left this device, not just how many: without
        # it there is no way to tell afterwards what a sync actually sent
        repo.log_event("sync_push", {
            "device_id": device_id,
            "document_ids": [o.get("entity_id") or o.get("doc", {}).get("id")
                             for o in batch],
        })
    repo.device_update(device_id, push_seq=local_latest)

    # 2. pull
    resp = _request(config, "GET",
                    f"/api/v1/sync/pull?device_id={device_id}&since={last_pull}")
    from ..embeddings import get_embedder

    pulled_ops = resp.get("ops", [])
    summary = apply_ops(repo, pulled_ops, origin="remote:server",
                        prefer_local_on_tie=False, pending_ids=pending_ids,
                        embedder=get_embedder(config) if pulled_ops else None)
    report.pulled = summary["applied"]
    report.conflicts = summary["conflicts"]
    report.deleted = summary["deleted"]
    report.failed = summary.get("failed", 0)
    repo.device_update(device_id, pull_seq=int(resp.get("latest_seq", last_pull)))
    repo.log_event("sync", {"pushed": report.pushed, "pulled": report.pulled,
                            "conflicts": report.conflicts,
                            "deleted": report.deleted, "failed": report.failed})
    return report
