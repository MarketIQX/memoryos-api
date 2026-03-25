"""
MemoryOS API — v1.0.0
The memory layer for AI-run companies.

Four Laws:
1. Memory must be retrievable by meaning, not by exact key.
2. Memory must be shareable with controlled scope, and erasable with controlled intent.
3. Memory must respect full data sovereignty.
4. Memory must compound — every interaction must make the next one better.

This is the entry point. Every route is registered here.
Every startup check runs here. If anything fails on startup, the service refuses to start.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging
import sys

from config import config
from routes.health import router as health_router
from routes.memory_write import router as write_router
from routes.memory_recall import router as recall_router
from routes.memory_context import router as context_router

# ─── LOGGING ──────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    stream=sys.stdout
)
logger = logging.getLogger("memoryos")


# ─── STARTUP / SHUTDOWN ───────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Runs on startup and shutdown.
    Startup: validate all environment variables and verify all connections.
    If anything fails: service refuses to start. No silent failures.
    """
    logger.info("=" * 60)
    logger.info("MemoryOS API starting...")
    logger.info(f"Environment: {config.ENVIRONMENT}")
    logger.info(f"Version: 1.0.0")
    logger.info("=" * 60)

    # Validate all environment variables
    try:
        config.validate()
        logger.info("✓ Environment variables: all present")
    except ValueError as e:
        logger.error(f"✗ Environment validation failed: {e}")
        sys.exit(1)

    # Verify Supabase connection
    from database import verify_connection as db_verify
    db_status = await db_verify()
    if db_status.get("status") == "connected":
        logger.info("✓ Supabase: connected")
    else:
        logger.error(f"✗ Supabase connection failed: {db_status}")
        sys.exit(1)

    # Verify Redis connection
    from redis_client import verify_connection as redis_verify
    redis_status = await redis_verify()
    if redis_status.get("status") == "connected":
        logger.info("✓ Upstash Redis: connected")
    else:
        logger.warning(f"⚠ Redis connection failed (non-critical): {redis_status}")

    # Verify Voyage AI connection
    from embeddings import verify_connection as embeddings_verify
    embeddings_status = await embeddings_verify()
    if embeddings_status.get("status") == "connected":
        logger.info(f"✓ Voyage AI: connected ({embeddings_status.get('dimensions')} dimensions)")
    else:
        logger.error(f"✗ Voyage AI connection failed: {embeddings_status}")
        sys.exit(1)

    logger.info("=" * 60)
    logger.info("MemoryOS API ready. All systems operational.")
    logger.info("=" * 60)

    yield

    logger.info("MemoryOS API shutting down.")


# ─── APP ──────────────────────────────────────────────────────────
app = FastAPI(
    title="MemoryOS API",
    description=(
        "The memory layer for AI-run companies. "
        "Any LLM. Any agent. Any scale. Never forget."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc"
)

# CORS — allows any origin during development
# Tightened to specific origins before v2.0 public launch
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── ROUTES ───────────────────────────────────────────────────────
app.include_router(health_router, tags=["System"])
app.include_router(write_router, prefix="/v1", tags=["Memory"])
app.include_router(recall_router, prefix="/v1", tags=["Memory"])
app.include_router(context_router, prefix="/v1", tags=["Memory"])


# ─── ENTRY POINT ──────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=config.PORT,
        reload=config.ENVIRONMENT == "development"
    )
