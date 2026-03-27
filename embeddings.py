"""
Embeddings — fastembed local inference.
Replaces Voyage AI. No external API. No rate limits. No cost.
Model: BAAI/bge-small-en-v1.5 — 384-dim, CPU, MIT license.
First boot: downloads 130MB model, cached permanently by Railway.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)
_model = None

def get_model():
    global _model
    if _model is None:
        from fastembed import TextEmbedding
        _model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
        logger.info("fastembed loaded: BAAI/bge-small-en-v1.5 (384-dim)")
    return _model

async def embed_text(text: str, importance: float = 0.5) -> Optional[list]:
    """384-dim embedding for memory storage. Local CPU — no API call."""
    try:
        return list(get_model().embed([text[:8192]]))[0].tolist()
    except Exception as e:
        logger.error(f"embed_text failed: {e}")
        return None

async def embed_query(query: str) -> Optional[list]:
    """384-dim embedding for semantic search. Same model as embed_text."""
    try:
        return list(get_model().embed([query[:8192]]))[0].tolist()
    except Exception as e:
        logger.error(f"embed_query failed: {e}")
        return None

async def verify_connection() -> dict:
    """Health check — model loaded and producing 384-dim vectors."""
    try:
        test = await embed_text("MemoryOS health check")
        if test and len(test) == 384:
            return {"status": "connected", "model": "BAAI/bge-small-en-v1.5", "dimensions": 384}
        return {"status": "error", "detail": f"Got {len(test) if test else 0} dims, expected 384"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}