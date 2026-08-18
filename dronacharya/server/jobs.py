"""Durable background jobs.

Background work used to live in in-process FastAPI BackgroundTasks — a crash
or restart silently lost every queued distillation. Job state now lives in
the `jobs` table (survives restarts); execution is triggered both by the
request that enqueued it (immediate, keeps test semantics) and by a startup
recovery pass that requeues anything left 'running' by a crash.

Single-flight: claim_next_job() flips queued→running atomically, so however
many triggers race, each job runs exactly once. _WORK_LOCK additionally
serializes actual execution (embedding is the bottleneck; single-user scale).
"""

from __future__ import annotations

import threading

_WORK_LOCK = threading.Lock()


def _run_save(app, payload: dict) -> None:
    from ..ingest.pipeline import save_web
    from .app import open_repo

    repo = open_repo(app)
    try:
        save_web(repo, app.state.embedder, app.state.config,
                 payload["url"], html=payload.get("html"),
                 title_hint=payload.get("title_hint", ""),
                 tags=payload.get("tags"), note=payload.get("note"),
                 overwrite=True)
    finally:
        repo.close()


_HANDLERS = {"save": _run_save}


def run_pending_jobs(app) -> int:
    """Drain the queue; returns how many jobs ran. Safe to call from
    concurrent triggers — claiming is atomic and execution is serialized."""
    from .app import open_repo

    ran = 0
    with _WORK_LOCK:
        while True:
            repo = open_repo(app)
            try:
                job = repo.claim_next_job()
            finally:
                repo.close()
            if job is None:
                return ran
            error = None
            try:
                _HANDLERS[job["kind"]](app, job["payload"])
            except Exception as e:  # noqa: BLE001 — job failure must not kill the worker
                error = str(e)[:300]
            repo = open_repo(app)
            try:
                repo.finish_job(job["id"], error)
                if error:
                    repo.log_event("job_error", {"kind": job["kind"],
                                                 "key": job["key"],
                                                 "error": error})
            finally:
                repo.close()
            ran += 1


def recover_and_start(app) -> None:
    """Startup pass: requeue crash-orphaned jobs and drain the queue in a
    daemon thread so a restart finishes what the previous process accepted."""
    from .app import open_repo

    repo = open_repo(app)
    try:
        repo.requeue_stale_jobs()
    finally:
        repo.close()
    threading.Thread(target=run_pending_jobs, args=(app,), daemon=True).start()
