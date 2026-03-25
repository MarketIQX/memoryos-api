from upstash_redis import Redis
from config import config
import hashlib
import json
import logging

logger = logging.getLogger(__name__)

# Single Redis client instance
_redis_client = None


def get_redis() -> Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = Redis(
            url=config.UPSTASH_REDIS_REST_URL,
            token=config.UPSTASH_REDIS_REST_TOKEN
        )
    return _redis_client


def make_cache_key(tenant_id: str, entity_id: str, query: str, layers: list) -> str:
    """
    Generates a deterministic cache key for /context results.
    Same inputs always produce the same key.
    """
    raw = f"{tenant_id}:{entity_id}:{query}:{sorted(layers)}"
    return f"context:{hashlib.sha256(raw.encode()).hexdigest()[:32]}"


async def get_cached_context(
    tenant_id: str,
    entity_id: str,
    query: str,
    layers: list
) -> dict | None:
    """
    Returns cached /context result if available.
    Cache hit: ~3ms total latency.
    Cache miss: returns None, proceed to Supabase.
    """
    try:
        redis = get_redis()
        key = make_cache_key(tenant_id, entity_id, query, layers)
        cached = redis.get(key)
        if cached:
            return json.loads(cached)
        return None
    except Exception as e:
        logger.warning(f"Redis cache get failed (non-critical): {e}")
        return None


async def set_cached_context(
    tenant_id: str,
    entity_id: str,
    query: str,
    layers: list,
    result: dict
) -> None:
    """
    Caches /context result for 60 seconds.
    TTL prevents stale context from affecting agent decisions.
    """
    try:
        redis = get_redis()
        key = make_cache_key(tenant_id, entity_id, query, layers)
        redis.setex(key, config.CACHE_TTL_SECONDS, json.dumps(result))
    except Exception as e:
        logger.warning(f"Redis cache set failed (non-critical): {e}")


async def invalidate_entity_cache(tenant_id: str, entity_id: str) -> None:
    """
    Called after every /memory/write for this entity.
    Ensures next /context call reflects the new memory.
    Pattern-based deletion: removes all cached contexts for this entity.
    """
    try:
        redis = get_redis()
        pattern = f"context:*"
        keys = redis.keys(pattern)
        if keys:
            redis.delete(*keys)
    except Exception as e:
        logger.warning(f"Redis cache invalidation failed (non-critical): {e}")


async def verify_connection() -> dict:
    """
    Verifies Redis is reachable.
    Called on startup via /health endpoint.
    """
    try:
        redis = get_redis()
        redis.set("memoryos:health_check", "ok")
        result = redis.get("memoryos:health_check")
        if result == "ok":
            return {"status": "connected"}
        return {"status": "error", "detail": "ping failed"}
    except Exception as e:
        logger.error(f"Redis connection failed: {e}")
        return {"status": "error", "detail": str(e)}
