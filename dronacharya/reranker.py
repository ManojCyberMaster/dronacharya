"""Optional cross-encoder reranker — the quality upgrade that GPUs unlock.

retrieval.rerank: "auto" (on when a CUDA GPU is present), "on", or "off".
Runs after hybrid retrieval, rescoring the fused candidates against the query.
"""

from __future__ import annotations

from .models import SearchResult

_singleton = None
_singleton_key: tuple | None = None


class CrossEncoderReranker:
    def __init__(self, model_name: str):
        self.model_name = model_name
        self._model = None

    def _load(self):
        if self._model is None:
            from sentence_transformers import CrossEncoder

            from .embeddings import _silence_hf_progress
            _silence_hf_progress()

            try:
                # offline-first: once cached, never ping the HF Hub again
                self._model = CrossEncoder(self.model_name, local_files_only=True)
            except Exception:  # noqa: BLE001 — not cached yet: download once
                self._model = CrossEncoder(self.model_name)
        return self._model

    def rerank(self, query: str, results: list[SearchResult],
               top_k: int) -> list[SearchResult]:
        if not results:
            return results
        scores = self._load().predict([(query, r.unit.text) for r in results])
        ranked = sorted(zip(results, scores), key=lambda pair: pair[1], reverse=True)
        out = []
        for result, score in ranked[:top_k]:
            result.score = float(score)
            out.append(result)
        return out


def _cuda_available() -> bool:
    try:
        import torch

        return torch.cuda.is_available()
    except Exception:
        return False


def get_reranker(config):
    """None when reranking is off/unavailable; cached singleton otherwise."""
    global _singleton, _singleton_key
    mode = config.retrieval.rerank
    if mode == "off" or (mode == "auto" and not _cuda_available()):
        return None
    key = (config.retrieval.rerank_model,)
    if _singleton is None or _singleton_key != key:
        _singleton = CrossEncoderReranker(config.retrieval.rerank_model)
        _singleton_key = key
    return _singleton
