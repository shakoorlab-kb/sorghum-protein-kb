"""Local embeddings (fastembed, ONNX on CPU). Nothing leaves the server for search."""
from __future__ import annotations

import os
import threading

from .config import settings

_model = None
_lock = threading.Lock()

# bge models are trained with this prefix on the query side only.
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


def _get():
    global _model
    with _lock:
        if _model is None:
            from fastembed import TextEmbedding
            _model = TextEmbedding(model_name=settings.embed_model,
                                   cache_dir=os.environ.get("FASTEMBED_CACHE_PATH") or None)
    return _model


def embed_passages(texts: list[str], batch_size: int = 64) -> list[list[float]]:
    if not texts:
        return []
    return [v.tolist() for v in _get().embed(texts, batch_size=batch_size)]


def embed_query(text: str) -> list[float]:
    prefix = QUERY_PREFIX if "bge" in settings.embed_model.lower() else ""
    return next(iter(_get().embed([prefix + text]))).tolist()
