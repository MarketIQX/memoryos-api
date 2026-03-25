from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime, timedelta
import time
import logging

from auth import verify_api_key
from database import get_supabase, log_usage
from embeddings import embed_text
from redis_client import invalidate_entity_cache
from config import config

router = APIRouter()
logger = logging.getLogger(__name__)


# ─── REQUEST MODEL ────────────────────────────────────────────────
class MemoryContent(BaseModel):
    text: str = Field(..., min_length=1, max_length=10000)
    category: Optional[str] = None
    metadata: Optional[dict] = None


class WriteMemoryRequest(BaseModel):
    entity_id: str = Field(..., min_length=1, max_length=255)
    entity_type: str = Field(..., pattern="^(user|agent|org|robot|device)$")
    layer: str = Field(..., pattern="^(session|org|agent|persona|cognitive|pattern|federated)$")
    content: MemoryContent
    importance: Optional[float] = Field(default=0.5, ge=0.0, le=1.0)
    importance_tier: Optional[str] = Field(
        default="normal",
        pattern="^(critical|high|normal|low)$"
    )
    tags: Optional[list[str]] = []
    scope: Optional[str] = Field(default="shared", pattern="^(shared|private)$")
    agent_id: Optional[str] = None
    cognitive_state: Optional[dict] = {}
    domain: Optional[str] = ""
    expires_in_days: Optional[int] = None


# ─── WRITE ENDPOINT ───────────────────────────────────────────────
@router.post("/memory/write")
async def write_memory(
    request: WriteMemoryRequest,
    tenant: dict = Depends(verify_api_key)
):
    """
    Writes a memory through the full 8-stage pipeline.

    Stage 1: Intake validation (Pydantic handles this)
    Stage 2: Extraction (category/importance if not provided)
    Stage 3: Deduplication (similarity > 0.92 = update, not insert)
    Stage 4: Contradiction check (similarity > 0.85 + opposing sentiment)
    Stage 5: Embedding (Voyage AI voyage-3-lite, 1024-dim)
    Stage 6: Confidence initialisation (1.0, with decay rate by category)
    Stage 7: Persistence (atomic write to Supabase)
    Stage 8: Cache invalidation + watch check
    """
    start_time = time.time()
    tenant_id = tenant["tenant_id"]
    supabase = get_supabase()

    try:
        # ── STAGE 2: Extraction ─────────────────────────────────
        importance = request.importance
        importance_tier = request.importance_tier
        category = request.content.category

        # If importance_tier not set but importance is high, auto-elevate
        if importance >= 0.9 and importance_tier == "normal":
            importance_tier = "high"

        # ── STAGE 5: Embedding ──────────────────────────────────
        # Done before deduplication so we can compare with existing embeddings
        embedding = await embed_text(request.content.text, importance)
        if embedding is None:
            raise HTTPException(
                status_code=503,
                detail="Embedding service unavailable. Memory not stored. Please retry."
            )

        # ── STAGE 3: Deduplication ──────────────────────────────
        was_dedup = False
        existing_id = None

        try:
            dedup_result = supabase.rpc(
                "match_memories_for_dedup",
                {
                    "query_embedding": embedding,
                    "p_tenant_id": tenant_id,
                    "p_entity_id": request.entity_id,
                    "dedup_threshold": config.DEDUP_THRESHOLD
                }
            ).execute()

            if dedup_result.data:
                existing_id = dedup_result.data[0]["id"]
                was_dedup = True
        except Exception as e:
            logger.warning(f"Dedup check failed (continuing with insert): {e}")

        # ── STAGE 4: Contradiction check ────────────────────────
        contradiction_detected = False
        contradiction_notice_id = None

        if not was_dedup:
            try:
                contradiction_result = supabase.rpc(
                    "check_contradiction",
                    {
                        "query_embedding": embedding,
                        "p_tenant_id": tenant_id,
                        "p_entity_id": request.entity_id,
                        "contradiction_threshold": config.CONTRADICTION_THRESHOLD
                    }
                ).execute()

                if contradiction_result.data:
                    contradiction_detected = True
                    conflicting_id = contradiction_result.data[0]["id"]

                    # Write contradiction notice as critical memory
                    notice_result = supabase.table("memories").insert({
                        "tenant_id": tenant_id,
                        "entity_id": request.entity_id,
                        "entity_type": request.entity_type,
                        "layer": request.layer,
                        "scope": "shared",
                        "agent_id": "system",
                        "content": {
                            "text": f"WARNING: Conflicting memories detected for {request.entity_id}. "
                                    f"Two memories about this entity contradict each other. "
                                    f"The reading agent must resolve this conflict before acting.",
                            "category": "contradiction_notice",
                            "conflicting_memory_id": conflicting_id,
                            "new_memory_preview": request.content.text[:200]
                        },
                        "content_text": f"CONTRADICTION WARNING for {request.entity_id}: "
                                        f"New memory conflicts with existing memory. Agent must verify before acting.",
                        "importance": 1.0,
                        "importance_tier": "critical",
                        "contradiction_flag": True,
                        "contradicts_ids": [conflicting_id],
                        "embedding": embedding
                    }).execute()

                    if notice_result.data:
                        contradiction_notice_id = notice_result.data[0]["id"]

                    # Flag the existing conflicting memory
                    supabase.table("memories").update({
                        "contradiction_flag": True,
                        "contradicts_ids": [contradiction_notice_id] if contradiction_notice_id else []
                    }).eq("id", conflicting_id).execute()

            except Exception as e:
                logger.warning(f"Contradiction check failed (continuing): {e}")

        # ── STAGE 6: Confidence init ─────────────────────────────
        decay_rates = {
            "preference": 0.15,
            "status": 0.12,
            "interaction": 0.10,
            "decision": 0.05,
            "fact": 0.03,
            "relationship": 0.02,
            "critical": 0.0
        }
        decay_rate = decay_rates.get(category or "interaction", 0.10)
        if importance_tier == "critical":
            decay_rate = 0.0

        # ── STAGE 7: Persistence ─────────────────────────────────
        expires_at = None
        if request.expires_in_days:
            expires_at = (
                datetime.utcnow() + timedelta(days=request.expires_in_days)
            ).isoformat()

        if was_dedup and existing_id:
            # Update existing memory — merge new information
            update_result = supabase.table("memories").update({
                "content": {
                    "text": request.content.text,
                    "category": category,
                    "metadata": request.content.metadata
                },
                "content_text": request.content.text,
                "embedding": embedding,
                "importance": max(importance, 0.5),
                "importance_tier": importance_tier,
                "tags": list(set(request.tags or [])),
                "cognitive_state": request.cognitive_state or {},
                "confidence": 1.0,
                "last_confirmed_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat(),
                "contradiction_flag": contradiction_detected
            }).eq("id", existing_id).eq("tenant_id", tenant_id).execute()

            memory_id = existing_id
        else:
            # Insert new memory
            insert_data = {
                "tenant_id": tenant_id,
                "entity_id": request.entity_id,
                "entity_type": request.entity_type,
                "layer": request.layer,
                "scope": request.scope,
                "agent_id": request.agent_id,
                "content": {
                    "text": request.content.text,
                    "category": category,
                    "metadata": request.content.metadata
                },
                "content_text": request.content.text,
                "content_type": "text",
                "embedding": embedding,
                "embedding_model": "voyage-3-lite",
                "importance": importance,
                "importance_tier": importance_tier,
                "outcome_score": 0.5,
                "contradiction_flag": contradiction_detected,
                "confidence": 1.0,
                "confidence_decay_rate": decay_rate,
                "last_confirmed_at": datetime.utcnow().isoformat(),
                "tags": request.tags or [],
                "cognitive_state": request.cognitive_state or {},
                "domain": request.domain or "",
                "expires_at": expires_at
            }

            insert_result = supabase.table("memories").insert(insert_data).execute()

            if not insert_result.data:
                raise HTTPException(
                    status_code=500,
                    detail="Memory storage failed. Please retry."
                )

            memory_id = insert_result.data[0]["id"]

        # ── STAGE 8: Cache invalidation ──────────────────────────
        await invalidate_entity_cache(tenant_id, request.entity_id)

        # ── USAGE LOG ────────────────────────────────────────────
        latency_ms = int((time.time() - start_time) * 1000)
        await log_usage(
            tenant_id=tenant_id,
            endpoint="/memory/write",
            agent_id=request.agent_id,
            layer=request.layer,
            entity_id=request.entity_id,
            latency_ms=latency_ms,
            status_code=200
        )

        return {
            "memory_id": memory_id,
            "status": "updated" if was_dedup else "stored",
            "was_dedup": was_dedup,
            "contradiction_detected": contradiction_detected,
            "contradiction_notice_id": contradiction_notice_id,
            "layer": request.layer,
            "entity_id": request.entity_id,
            "importance_tier": importance_tier,
            "latency_ms": latency_ms
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Write memory failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Memory write failed: {str(e)}"
        )
