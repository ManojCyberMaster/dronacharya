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


def get_self_device_id(repo) -> str:
    row = repo.conn.execute(
        "SELECT id FROM devices WHERE name = ?", (SELF_DEVICE_NAME,)
    ).fetchone()
    if row:
        return row["id"]
    device_id = new_id()
    repo.device_update(device_id, name=SELF_DEVICE_NAME)
    return device_id


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


def sync_once(repo, config: Config) -> SyncReport:
    device_id = get_self_device_id(repo)
    last_push, last_pull = repo.device_state(device_id)
    report = SyncReport()

    # ids with un-pushed local changes — the genuine-conflict set for the pull
    pending_ids = {entity_id for _, entity, entity_id, _ in
                   repo.oplog_since(last_push, local_only=True)
                   if entity in ("document", "tags")}

    # 1. push
    ops, local_latest = collect_ops(repo, last_push, local_only=True)
    if ops:
        _request(config, "POST", "/api/v1/sync/push",
                 {"device_id": device_id, "ops": ops})
        report.pushed = len(ops)
    repo.device_update(device_id, push_seq=local_latest)

    # 2. pull
    resp = _request(config, "GET",
                    f"/api/v1/sync/pull?device_id={device_id}&since={last_pull}")
    summary = apply_ops(repo, resp.get("ops", []), origin="remote:server",
                        prefer_local_on_tie=False, pending_ids=pending_ids)
    report.pulled = summary["applied"]
    report.conflicts = summary["conflicts"]
    report.deleted = summary["deleted"]
    repo.device_update(device_id, pull_seq=int(resp.get("latest_seq", last_pull)))
    repo.log_event("sync", {"pushed": report.pushed, "pulled": report.pulled,
                            "conflicts": report.conflicts})
    return report
