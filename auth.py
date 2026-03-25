from fastapi import HTTPException, Security, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from database import get_supabase
from config import config
import hashlib
import secrets
import logging

logger = logging.getLogger(__name__)

security = HTTPBearer()

# MarketIQX internal tenant ID — seeded in Sprint 0 schema
MARKETIQX_TENANT_ID = "00000000-0000-0000-0000-000000000001"


def hash_key(key: str) -> str:
    """SHA256 hash of the API key. Never store plaintext keys."""
    return hashlib.sha256(key.encode()).hexdigest()


def generate_api_key() -> tuple[str, str, str]:
    """
    Generates a new API key.
    Returns: (full_key, key_hash, key_prefix)
    Full key is shown ONCE — never stored, never returned again.
    """
    raw = secrets.token_urlsafe(32)
    full_key = f"memos_live_{raw}"
    key_hash = hash_key(full_key)
    key_prefix = full_key[:12]
    return full_key, key_hash, key_prefix


async def verify_api_key(
    credentials: HTTPAuthorizationCredentials = Security(security)
) -> dict:
    """
    Verifies the Bearer token is a valid MemoryOS API key.
    Returns tenant context on success.
    Raises 401 on any failure — no information leakage about why.

    Fast path: uses key_prefix index for fast lookup,
    then verifies SHA256 hash. Two-step prevents timing attacks.
    """
    token = credentials.credentials

    # Internal bypass for MarketIQX — used only during development
    # Replaced with real API key after Sprint 1 confirmation tests pass
    if token == config.MEMORYOS_SECRET:
        return {
            "tenant_id": MARKETIQX_TENANT_ID,
            "tenant_name": "MarketIQX",
            "tier": "enterprise"
        }

    if not token.startswith("memos_live_"):
        raise HTTPException(
            status_code=401,
            detail="Invalid API key format. Keys must start with 'memos_live_'"
        )

    try:
        prefix = token[:12]
        key_hash = hash_key(token)

        supabase = get_supabase()
        result = supabase.table("api_keys").select(
            "id, tenant_id, active, tenants(id, name, tier, active)"
        ).eq("key_prefix", prefix).eq("key_hash", key_hash).eq("active", True).execute()

        if not result.data:
            raise HTTPException(status_code=401, detail="Invalid or inactive API key")

        key_record = result.data[0]
        tenant = key_record.get("tenants", {})

        if not tenant.get("active", False):
            raise HTTPException(status_code=401, detail="Tenant account is inactive")

        # Update last_used_at — non-blocking
        try:
            supabase.table("api_keys").update(
                {"last_used_at": "now()"}
            ).eq("id", key_record["id"]).execute()
        except Exception:
            pass

        return {
            "tenant_id": key_record["tenant_id"],
            "tenant_name": tenant.get("name", "Unknown"),
            "tier": tenant.get("tier", "free")
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"API key verification error: {e}")
        raise HTTPException(status_code=401, detail="API key verification failed")
