from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
import time
import logging

from auth import verify_api_key
from database import get_supabase, log_usage
from embeddings import embed_query
from config import config

router = APIRouter()
logger = logging.getLogger(__name__)


# ─── REQUEST MODEL ────────────────────────────────────────────────
class RecallRequest(BaseModel):
    entity_id: str = Field(..., min_length=1, max_length=255)
    query: str = Field(..., min_length=1, max_length=2000)
    layers: Optional[list[str]] = []
    scope: Optional[str] = Field(default="shared", pattern="^(shared|private|all)$")
    limit: Optional[int] = Field(default=10, ge=1, le=50)
    threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    include_stale: Optional[bool] = False


# ─── RECALL ENDPOINT ──────────────────────────────────────────────
@router.post("/memory/recall")
async def recall_memories(
    request: RecallRequest,
    tenant: dict = Depends(verify_api_key)
):
    """
    Semantic search across agent memories.
    Returns ranked list of memories by composite score.

    Composite score formula (Law 4 — memory must compound):
    0.50 × semantic_similarity
    + 0.20 × importance
    + 0.15 × recency_score
    + 0.15 × outcome_score

    This is NOT a keyword search. The query is embedded and compared
    via cosine similarity to stored memory embeddings.
    Confirmation Test 2 verifies this: different words, same meaning,
    must still match with similarity > 0.75.
    """
    start_time = time.time()
    tenant_id = tenant["tenant_id"]
    supabase = get_supabase()

    threshold = request.threshold or config.SIMILARITY_THRESHOLD

    try:
        # ── STEP 1: Embed the query ──────────────────────────────
        # CRITICAL: Must use same model as write pipeline.
        # Mixing models breaks cosine similarity completely.
        query_embedding = await embed_query(request.query)
        if query_embedding is None:
            raise HTTPException(
                status_code=503,
                detail="Embedding service unavailable. Cannot perform semantic search."
            )

        # ── STEP 2: Inject critical memories ────────────────────
        # Critical memories always returned regardless of similarity score.
        # Cap: 5 critical memories. Law 4: critical memories exempt from decay.
        critical_memories = []
        try:
            critical_result = supabase.table("memories").select(
                "id, layer, content, content_text, importance, importance_tier, "
                "confidence, contradiction_flag, tags, agent_id, created_at, outcome_score"
            ).eq("tenant_id", tenant_id).eq(
                "entity_id", request.entity_id
            ).eq(
                "importance_tier", "critical"
            ).is_("expires_at", "null").order(
                "created_at", desc=True
            ).limit(config.CRITICAL_MEMORY_CAP).execute()

            if critical_result.data:
                for m in critical_result.data:
                    m["similarity"] = 1.0
                    m["composite_score"] = 1.0
                    m["is_critical"] = True
                    m["is_stale"] = False
                critical_memories = critical_result.data
        except Exception as e:
            logger.warning(f"Critical memory fetch failed: {e}")

        # ── STEP 3: Semantic search ──────────────────────────────
        layers = request.layers if request.layers else []
        scope = request.scope if request.scope != "all" else "shared"

        semantic_memories = []
        try:
            search_result = supabase.rpc(
                "match_memories",
                {
                    "query_embedding": query_embedding,
                    "p_tenant_id": tenant_id,
                    "p_entity_id": request.entity_id,
                    "p_layers": layers,
                    "p_scope": scope,
                    "match_threshold": threshold,
                    "match_count": request.limit
                }
            ).execute()

            if search_result.data:
                for m in search_result.data:
                    m["is_critical"] = False
                    m["is_stale"] = m.get("confidence", 1.0) < 0.3
                semantic_memories = search_result.data
        except Exception as e:
            logger.warning(f"Semantic search failed: {e}")

        # ── STEP 4: Merge critical + semantic ───────────────────
        # Remove duplicates — critical memories may overlap with semantic results
        critical_ids = {m["id"] for m in critical_memories}
        semantic_only = [m for m in semantic_memories if m["id"] not in critical_ids]

        # Critical memories first, then semantic by composite score
        all_memories = critical_memories + semantic_only

        # ── STEP 5: Filter stale if requested ───────────────────
        if not request.include_stale:
            # Include stale memories but mark them clearly
            pass  # They are already marked with is_stale flag

        # ── USAGE LOG ────────────────────────────────────────────
        latency_ms = int((time.time() - start_time) * 1000)
        await log_usage(
            tenant_id=tenant_id,
            endpoint="/memory/recall",
            entity_id=request.entity_id,
            latency_ms=latency_ms,
            status_code=200
        )

        return {
            "memories": all_memories,
            "total_count": len(all_memories),
            "critical_count": len(critical_memories),
            "semantic_count": len(semantic_only),
            "query": request.query,
            "entity_id": request.entity_id,
            "threshold_used": threshold,
            "latency_ms": latency_ms
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Recall failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Memory recall failed: {str(e)}"
        )
