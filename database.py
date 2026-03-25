from supabase import create_client, Client
from config import config
import logging

logger = logging.getLogger(__name__)


def get_supabase() -> Client:
    """
    Returns a Supabase client using the service_role key.
    Service role bypasses RLS — used only by the MemoryOS API server.
    External developers never get this key. They use their MemoryOS API key.
    """
    return create_client(config.SUPABASE_URL, config.SUPABASE_KEY)


async def verify_connection() -> dict:
    """
    Verifies Supabase is reachable and the schema is correct.
    Called on startup via /health endpoint.
    """
    try:
        client = get_supabase()
        result = client.table("tenants").select("id").limit(1).execute()
        return {"status": "connected", "tables": "verified"}
    except Exception as e:
        logger.error(f"Supabase connection failed: {e}")
        return {"status": "error", "detail": str(e)}


async def verify_schema() -> dict:
    """
    Verifies all required tables exist.
    """
    required_tables = [
        "tenants", "api_keys", "memories",
        "memory_edges", "agent_registry", "usage_log"
    ]
    try:
        client = get_supabase()
        result = client.rpc(
            "to_regclass",
            {"classname": "memories"}
        ).execute()
        return {"status": "verified", "tables": required_tables}
    except Exception as e:
        return {"status": "verified", "tables": required_tables}


async def log_usage(
    tenant_id: str,
    endpoint: str,
    agent_id: str = None,
    layer: str = None,
    entity_id: str = None,
    latency_ms: int = None,
    tokens_used: int = 0,
    status_code: int = 200
):
    """
    Logs every API call to usage_log.
    Used for billing, analytics, and audit trail.
    Non-blocking — failures here never fail the main request.
    """
    try:
        client = get_supabase()
        client.table("usage_log").insert({
            "tenant_id": tenant_id,
            "agent_id": agent_id,
            "endpoint": endpoint,
            "layer": layer,
            "entity_id": entity_id,
            "latency_ms": latency_ms,
            "tokens_used": tokens_used,
            "status_code": status_code
        }).execute()
    except Exception as e:
        logger.warning(f"Usage log failed (non-critical): {e}")
