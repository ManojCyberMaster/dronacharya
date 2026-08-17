"""Tag word-map: a 2D semantic layout of the user's tags.

Tags are embedded with the KB's own embedder, projected to 2D with PCA
(related topics land near each other), and grouped into color clusters with
a deterministic k-means. Pure computation over what the KB already stores —
no LLM calls, works offline, nothing persisted.
"""

from __future__ import annotations

import math

import numpy as np

from .embeddings import Embedder

KMEANS_ITERS = 25


def retag_documents(repo, transform) -> int:
    """Apply a tags-list transform to every document; returns changed count.

    Shared by the `dc tags` CLI group and the web tag-management endpoints
    (rename / delete). Tag membership changes go through set_tags, which
    writes the oplog — so they sync like any other change.
    """
    changed = 0
    for doc in repo.list_documents(limit=1_000_000):
        old = repo.get_tags(doc.id)
        if not old:
            continue
        new = []
        for t in transform(old):
            if t and t not in new:
                new.append(t)
        if new != old:
            repo.set_tags(doc.id, new)
            changed += 1
    return changed


def _kmeans(vecs: np.ndarray, k: int) -> list[int]:
    """Deterministic k-means on L2-normalized vectors (cosine ≙ dot)."""
    n = len(vecs)
    if k <= 1 or n <= k:
        return list(range(n)) if n <= k else [0] * n
    # deterministic init: evenly spaced points of the (stable) input order
    centers = vecs[np.linspace(0, n - 1, k).astype(int)].copy()
    labels = np.zeros(n, dtype=int)
    for _ in range(KMEANS_ITERS):
        sims = vecs @ centers.T                     # (n, k)
        new_labels = sims.argmax(axis=1)
        if (new_labels == labels).all() and _ > 0:
            break
        labels = new_labels
        for c in range(k):
            members = vecs[labels == c]
            if len(members):
                center = members.mean(axis=0)
                norm = np.linalg.norm(center)
                centers[c] = center / norm if norm > 1e-12 else center
            else:  # empty cluster: reseed with the point farthest from its center
                worst = (vecs @ centers[labels].T).diagonal().argmin() \
                    if n else 0
                centers[c] = vecs[worst]
    return [int(x) for x in labels]


def build_tag_map(repo, embedder: Embedder) -> dict:
    tags = repo.list_tags()                          # [(name, count)]
    if not tags:
        return {"tags": [], "clusters": 0}
    tags = sorted(tags)                              # stable order → stable layout
    names = [n for n, _ in tags]
    counts = [c for _, c in tags]
    texts = [n.replace("/", " ").replace("-", " ").replace("_", " ")
             for n in names]
    vecs = np.asarray(embedder.embed_passages(texts), dtype=float)
    n = len(names)

    if n == 1:
        xy = np.array([[0.5, 0.5]])
    else:
        centered = vecs - vecs.mean(axis=0)
        _, _, vt = np.linalg.svd(centered, full_matrices=False)
        comps = min(2, vt.shape[0])
        pts = centered @ vt[:comps].T
        if comps == 1:
            pts = np.hstack([pts, np.zeros((n, 1))])
        span = pts.max(axis=0) - pts.min(axis=0)
        span[span < 1e-9] = 1.0
        xy = (pts - pts.min(axis=0)) / span

    k = max(1, min(8, round(math.sqrt(n))))
    labels = _kmeans(vecs, k)
    return {
        "tags": [{"name": names[i], "count": counts[i],
                  "x": round(float(xy[i][0]), 4), "y": round(float(xy[i][1]), 4),
                  "cluster": labels[i]}
                 for i in range(n)],
        "clusters": max(labels) + 1 if labels else 0,
    }
