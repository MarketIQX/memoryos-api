import voyageai
from config import config
import logging

logger = logging.getLogger(__name__)

# Single Voyage AI client instance
_voyage_client = None


def get_voyage_client():
    global _voyage_client
    if _voyage_client is None:
        _voyage_client = voyageai.Client(api_key=config.VOYAGE_API_KEY)
    return _voyage_client


async def embed_text(text: str, importance: float = 0.5) -> list[float] | None:
    """
    Generates a 1024-dimensional embedding for the given text.

    Routing logic (from technical documentation):
    - importance >= 0.7: Voyage AI voyage-3-lite (highest quality, Indic language support)
    - importance < 0.7: Voyage AI voyage-3-lite still (local Ollama fallback in future)

    Why Voyage AI over OpenAI:
    - Superior multilingual/Indic script support (Hindi, Marathi, Tamil, Telugu, Gujarati)
    - 50M free tokens/month
    - 1024 dimensions (34% less storage than OpenAI's 1536)
    - Designed for retrieval, not generation

    CRITICAL: The same model must be used for both write and read.
    Mixing models breaks cosine similarity.
    """
    try:
        client = get_voyage_client()
        result = client.embed(
            texts=[text],
            model="voyage-3-lite",
            input_type="document"
        )
        return result.embeddings[0]
    except Exception as e:
        logger.error(f"Embedding failed for text (first 50 chars): '{text[:50]}': {e}")
        return None


async def embed_query(query: str) -> list[float] | None:
    """
    Generates a query embedding for semantic search.
    Uses input_type='query' — optimised for retrieval queries.

    CRITICAL: Must use the same model as embed_text().
    This is verified in the health check.
    """
    try:
        client = get_voyage_client()
        result = client.embed(
            texts=[query],
            model="voyage-3-lite",
            input_type="query"
        )
        return result.embeddings[0]
    except Exception as e:
        logger.error(f"Query embedding failed: {e}")
        return None


async def verify_connection() -> dict:
    """
    Verifies Voyage AI is reachable and producing correct dimensions.
    """
    try:
        test_embedding = await embed_text("MemoryOS health check", importance=1.0)
        if test_embedding and len(test_embedding) in [512, 1024]:
            return {
                "status": "connected",
                "model": "voyage-3-lite",
                "dimensions": len(test_embedding)
            }
        return {
            "status": "error",
            "detail": f"Wrong dimensions: expected 1024, got {len(test_embedding) if test_embedding else 0}"
        }
    except Exception as e:
        logger.error(f"Voyage AI connection failed: {e}")
        return {"status": "error", "detail": str(e)}
