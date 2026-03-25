from fastapi import APIRouter
from database import verify_connection as db_verify
from redis_client import verify_connection as redis_verify
from embeddings import verify_connection as embeddings_verify
from config import config
from datetime import datetime

router = APIRouter()


@router.get("/health")
async def health():
    """
    System health check. Verifies all three layers are reachable.
    Called after every deployment as Confirmation Test prerequisite.
    Called by UptimeRobot every 5 minutes.

    Returns 200 only when ALL systems are healthy.
    Returns 503 if any system is down.
    """
    db_status = await db_verify()
    redis_status = await redis_verify()
    embeddings_status = await embeddings_verify()

    all_healthy = all([
        db_status.get("status") == "connected",
        redis_status.get("status") == "connected",
        embeddings_status.get("status") == "connected",
    ])

    response = {
        "status": "healthy" if all_healthy else "degraded",
        "version": "1.0.0",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "service": "memoryos-api",
        "environment": config.ENVIRONMENT,
        "systems": {
            "database": db_status,
            "cache": redis_status,
            "embeddings": embeddings_status,
        }
    }

    if not all_healthy:
        from fastapi import Response
        return Response(
            content=str(response),
            status_code=503
        )

    return response


@router.get("/")
async def root():
    return {
        "product": "MemoryOS",
        "tagline": "The memory layer for AI-run companies",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health"
    }
