"""Embeddings — one canonical model per knowledge base (see ARCHITECTURE.md).

Presets (both 384-dim, CPU-capable, MIT-licensed models):
  english      BAAI/bge-small-en-v1.5   (query-side prefix)
  multilingual intfloat/multilingual-e5-small ("query: "/"passage: " prefixes)

The model is lazy-loaded so commands that don't embed stay fast, and tests can
inject a fake embedder.
"""

from __future__ import annotations

from typing import Protocol

_BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


def _silence_hf_progress() -> None:
    """Model loading is plumbing — it must never write progress bars over the
    user's answer."""
    try:
        from transformers.utils import logging as hf_logging

        hf_logging.disable_progress_bar()
        hf_logging.set_verbosity_error()
    except Exception:  # noqa: BLE001
        pass


class Embedder(Protocol):
    def embed_passages(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str, preset: str = "english"):
        self.model_name = model_name
        self.preset = preset
        self._model = None

    def _load(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            _silence_hf_progress()

            try:
                # offline-first: once cached, never ping the HF Hub again
                self._model = SentenceTransformer(self.model_name,
                                                  local_files_only=True)
            except Exception:  # noqa: BLE001 — not cached yet: download once
                self._model = SentenceTransformer(self.model_name)
        return self._model

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if self.preset == "multilingual":
            texts = [f"passage: {t}" for t in texts]
        vecs = self._load().encode(
            texts, normalize_embeddings=True, show_progress_bar=False, batch_size=32
        )
        return [v.tolist() for v in vecs]

    def embed_query(self, text: str) -> list[float]:
        if self.preset == "multilingual":
            text = f"query: {text}"
        else:
            text = _BGE_QUERY_PREFIX + text
        vec = self._load().encode(
            [text], normalize_embeddings=True, show_progress_bar=False
        )[0]
        return vec.tolist()


def get_embedder(config) -> Embedder:
    emb = config.embeddings
    return SentenceTransformerEmbedder(emb.model_name, emb.preset)


FINGERPRINT_KEY = "embedding_fingerprint"


def embedding_fingerprint(config) -> str:
    from .config import EMBEDDING_DIM

    return f"{config.embeddings.model_name}:{EMBEDDING_DIM}"


def ensure_embedding_compat(repo, config) -> None:
    """A KB is bound to ONE embedding model: mixing vectors from different
    models silently corrupts nearest-neighbor search. First use stamps the
    fingerprint; a mismatch refuses with the fix spelled out."""
    current = embedding_fingerprint(config)
    stored = repo.get_meta(FINGERPRINT_KEY)
    if stored is None:
        repo.set_meta(FINGERPRINT_KEY, current)
        return
    if stored != current:
        raise RuntimeError(
            f"embedding model changed ({stored} -> {current}); the existing "
            "index is incompatible — run `dc reembed` to rebuild it")
