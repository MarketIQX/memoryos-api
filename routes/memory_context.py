from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
import anthropic
import time
import logging

from auth import verify_api_key
from database import get_supabase, log_usage
from embeddings import embed_query
from redis_client import get_cached_context, set_cached_context
from config import config

router = APIRouter()
logger = logging.getLogger(__name__)

# Anthropic client
_anthropic_client = None


def get_anthropic():
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _anthropic_client


# ─── SYNTHESIS PROMPT ─────────────────────────────────────────────
SYNTHESIS_SYSTEM_PROMPT = """You are a memory synthesis engine for MemoryOS.
You receive ranked memory fragments about an entity (person, company, or agent).
Your job: synthesise them into one coherent, dense context block that another AI agent
can use directly as a system prompt injection.

Rules:
1. CRITICAL memories must always appear first, clearly marked with [CRITICAL].
2. Write in present tense where relevant: "The client prefers..." not "The client preferred..."
3. Be specific and concrete. No vague generalities. Use the actual facts from the memories.
4. If cognitive_state is provided, adapt your response:
   - stress > 70: use simple sentences, keep it brief, defer non-urgent items
   - focus > 80: include full analytical depth and all relevant context
   - energy < 30: highlight only urgent items, flag everything else as deferred
5. Do NOT invent or assume. Use only what is in the memories provided.
6. Format as natural prose paragraphs — not bullet points.
7. If memories contradict each other: surface both versions explicitly.
   Write: "Note: conflicting information exists — [version A] vs [version B]. Verify before acting."
   Do NOT silently choose one version. The reading agent resolves contradictions.
8. Include confidence annotations for memories with confidence below 0.7:
   Write: "(confidence: X.X — last confirmed [timeframe])"
9. Maximum 2,000 characters. Be dense, not exhaustive.
10. End with: [END MEMORY CONTEXT]"""


# ─── REQUEST MODEL ────────────────────────────────────────────────
class ContextRequest(BaseModel):
    entity_id: str = Field(..., min_length=1, max_length=255)
    prompt: str = Field(..., min_length=1, max_length=2000)
    layers: Optional[list[str]] = []
    scope: Optional[str] = Field(default="shared", pattern="^(shared|private|all)$")
    enrich: Optional[bool] = True
    cognitive_state: Optional[dict] = {}
    max_tokens: Optional[int] = Field(default=800, ge=100, le=2000)


# ─── CONTEXT ENDPOINT ─────────────────────────────────────────────
@router.post("/memory/context")
async def get_context(
    request: ContextRequest,
    tenant: dict = Depends(verify_api_key)
):
    """
    THE PRIMARY DIFFERENTIATOR.

    No competitor has this endpoint.
    Mem0, Zep, Hindsight all return a list of memory chunks.
    The developer must assemble them into usable context.
    MemoryOS does the assembly. Claude synthesises everything
    into one injection-ready system prompt block.

    Developer pastes one block. Done.

    Pipeline:
    1. Redis cache check (~3ms on hit)
    2. Embed the prompt for semantic search
    3. Inject critical memories (always, regardless of similarity)
    4. Semantic search with composite scoring
    5. Confidence filtering
    6. Temporal chain construction
    7. Claude synthesis (adapts to cognitive state)
    8. Cache result, return to agent

    Confirmation Test 3: this must return a coherent English paragraph
    that a human could read and act on. Not a JSON list. A narrative.
    """
    start_time = time.time()
    tenant_id = tenant["tenant_id"]
    supabase = get_supabase()
    layers = request.layers if request.layers else []

    try:
        # ── STEP 1: Redis cache check ────────────────────────────
        cached = await get_cached_context(
            tenant_id, request.entity_id, request.prompt, layers
        )
        if cached:
            cached["cache_hit"] = True
            cached["latency_ms"] = int((time.time() - start_time) * 1000)
            return cached

        # ── STEP 2: Embed the prompt ─────────────────────────────
        query_embedding = await embed_query(request.prompt)
        if query_embedding is None:
            raise HTTPException(
                status_code=503,
                detail="Embedding service unavailable. Cannot retrieve context."
            )

        # ── STEP 3: Critical memories (always injected) ──────────
        critical_memories = []
        try:
            critical_result = supabase.table("memories").select(
                "id, layer, content_text, importance, confidence, "
                "contradiction_flag, agent_id, created_at, outcome_score"
            ).eq("tenant_id", tenant_id).eq(
                "entity_id", request.entity_id
            ).eq(
                "importance_tier", "critical"
            ).is_("expires_at", "null").order(
                "created_at", desc=True
            ).limit(config.CRITICAL_MEMORY_CAP).execute()

            if critical_result.data:
                critical_memories = critical_result.data
        except Exception as e:
            logger.warning(f"Critical memory fetch failed: {e}")

        # ── STEP 4: Semantic search ──────────────────────────────
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
                    "match_threshold": config.SIMILARITY_THRESHOLD,
                    "match_count": 10
                }
            ).execute()

            if search_result.data:
                semantic_memories = search_result.data
        except Exception as e:
            logger.warning(f"Semantic search failed: {e}")

        # ── STEP 5: Deduplicate (critical may overlap with semantic) ──
        critical_ids = {m["id"] for m in critical_memories}
        semantic_only = [m for m in semantic_memories if m["id"] not in critical_ids]

        all_memories = critical_memories + semantic_only
        total_count = len(all_memories)

        if total_count == 0:
            # No memories yet — return honest empty state
            result = {
                "context_block": f"[MEMORY CONTEXT]\nNo previous memories found for {request.entity_id}. This appears to be the first interaction.\n[END MEMORY CONTEXT]",
                "memory_count": 0,
                "critical_count": 0,
                "semantic_count": 0,
                "layers_searched": layers,
                "synthesis_used": False,
                "cognitive_state": request.cognitive_state,
                "cache_hit": False,
                "latency_ms": int((time.time() - start_time) * 1000)
            }
            return result

        # ── STEP 6: Temporal chain ───────────────────────────────
        # Sort all memories chronologically to build narrative arc
        all_memories_sorted = sorted(
            all_memories,
            key=lambda m: m.get("created_at", ""),
        )

        # ── STEP 7: Synthesis ────────────────────────────────────
        context_block = ""
        synthesis_used = False
        tokens_used = 0

        if request.enrich and all_memories:
            try:
                # Format memories for Claude
                memory_text = _format_memories_for_synthesis(
                    critical_memories,
                    semantic_only,
                    all_memories_sorted
                )

                cognitive_note = _format_cognitive_state(request.cognitive_state)

                # Claude synthesis call
                client = get_anthropic()
                response = client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=request.max_tokens,
                    system=SYNTHESIS_SYSTEM_PROMPT,
                    messages=[{
                        "role": "user",
                        "content": (
                            f"Entity: {request.entity_id}\n"
                            f"Situation: {request.prompt}\n"
                            f"Cognitive state: {cognitive_note}\n\n"
                            f"Memories (ranked by composite score):\n"
                            f"{memory_text}"
                        )
                    }]
                )

                context_block = response.content[0].text
                synthesis_used = True
                tokens_used = response.usage.input_tokens + response.usage.output_tokens

            except Exception as e:
                logger.error(f"Claude synthesis failed: {e}")
                # Fallback: format memories without synthesis
                context_block = _format_memories_fallback(
                    critical_memories, semantic_only
                )
                synthesis_used = False
        else:
            # No synthesis requested — format memories directly
            context_block = _format_memories_fallback(
                critical_memories, semantic_only
            )

        # ── STEP 8: Cache and return ─────────────────────────────
        result = {
            "context_block": context_block,
            "memory_count": total_count,
            "critical_count": len(critical_memories),
            "semantic_count": len(semantic_only),
            "layers_searched": layers if layers else ["all"],
            "synthesis_used": synthesis_used,
            "cognitive_state": request.cognitive_state,
            "cache_hit": False,
            "latency_ms": int((time.time() - start_time) * 1000)
        }

        # Cache the result
        await set_cached_context(
            tenant_id, request.entity_id, request.prompt, layers, result
        )

        # Usage log
        await log_usage(
            tenant_id=tenant_id,
            endpoint="/memory/context",
            entity_id=request.entity_id,
            latency_ms=result["latency_ms"],
            tokens_used=tokens_used,
            status_code=200
        )

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Context retrieval failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Context retrieval failed: {str(e)}"
        )


def _format_memories_for_synthesis(
    critical: list,
    semantic: list,
    chronological: list
) -> str:
    """Formats memories for Claude synthesis."""
    parts = []

    if critical:
        parts.append("=== CRITICAL MEMORIES (always act on these) ===")
        for m in critical:
            conf = m.get("confidence", 1.0)
            conf_note = f" (confidence: {conf:.1f})" if conf < 0.7 else ""
            contradiction = " ⚠️ CONTRADICTED" if m.get("contradiction_flag") else ""
            parts.append(f"[CRITICAL{contradiction}]{conf_note}: {m['content_text']}")

    if semantic:
        parts.append("\n=== RELEVANT MEMORIES (ranked by relevance) ===")
        for m in semantic:
            conf = m.get("confidence", 1.0)
            conf_note = f" (confidence: {conf:.1f})" if conf < 0.7 else ""
            contradiction = " ⚠️ CONTRADICTED" if m.get("contradiction_flag") else ""
            score = m.get("composite_score", 0)
            parts.append(
                f"[score: {score:.2f}{contradiction}]{conf_note}: {m['content_text']}"
            )

    if chronological:
        parts.append("\n=== CHRONOLOGICAL NARRATIVE ===")
        if len(chronological) >= 2:
            first = chronological[0]
            last = chronological[-1]
            parts.append(
                f"First known: {first['content_text'][:100]}... "
                f"→ Most recent: {last['content_text'][:100]}..."
            )

    return "\n".join(parts)


def _format_memories_fallback(critical: list, semantic: list) -> str:
    """Formats memories without Claude synthesis — fallback only."""
    lines = ["[MEMORY CONTEXT]"]

    if critical:
        lines.append("\n[CRITICAL]")
        for m in critical:
            lines.append(f"• {m['content_text']}")

    if semantic:
        lines.append("\n[CONTEXT]")
        for m in semantic[:5]:  # Top 5 only in fallback
            lines.append(f"• {m['content_text']}")

    lines.append("\n[END MEMORY CONTEXT]")
    return "\n".join(lines)


def _format_cognitive_state(cognitive_state: dict) -> str:
    """Formats cognitive state for the synthesis prompt."""
    if not cognitive_state:
        return "Not provided — use standard depth"

    focus = cognitive_state.get("focus", 50)
    stress = cognitive_state.get("stress", 50)
    energy = cognitive_state.get("energy", 50)
    source = cognitive_state.get("source", "unknown")

    notes = []
    if stress > 70:
        notes.append("HIGH STRESS — keep it simple and brief")
    if focus > 80:
        notes.append("HIGH FOCUS — full depth appropriate")
    if energy < 30:
        notes.append("LOW ENERGY — urgent items only")

    return (
        f"focus={focus}/100, stress={stress}/100, energy={energy}/100 "
        f"(source: {source})"
        + (f" | {', '.join(notes)}" if notes else "")
    )
