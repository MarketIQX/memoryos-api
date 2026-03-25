-- ================================================================
-- MATCH_MEMORIES — core retrieval function
-- Called by every /recall and /context request
-- Composite score: 0.50×semantic + 0.20×importance + 0.15×recency + 0.15×outcome
-- ================================================================
CREATE OR REPLACE FUNCTION match_memories(
  query_embedding  vector(512),
  p_tenant_id      uuid,
  p_entity_id      text,
  p_layers         text[]   DEFAULT '{}',
  p_scope          text     DEFAULT 'shared',
  match_threshold  float    DEFAULT 0.75,
  match_count      int      DEFAULT 10
)
RETURNS TABLE (
  id                uuid,
  layer             text,
  content           jsonb,
  content_text      text,
  importance        float,
  importance_tier   text,
  confidence        float,
  contradiction_flag boolean,
  tags              text[],
  agent_id          text,
  created_at        timestamptz,
  outcome_score     float,
  similarity        float,
  composite_score   float
)
LANGUAGE sql STABLE AS $$
  SELECT
    m.id,
    m.layer,
    m.content,
    m.content_text,
    m.importance,
    m.importance_tier,
    m.confidence,
    m.contradiction_flag,
    m.tags,
    m.agent_id,
    m.created_at,
    m.outcome_score,
    1 - (m.embedding <=> query_embedding) AS similarity,
    -- Law 4: compound scoring
    (0.50 * (1 - (m.embedding <=> query_embedding)))
    + (0.20 * m.importance)
    + (0.15 * GREATEST(0, 1 - EXTRACT(EPOCH FROM (now() - m.created_at)) / 2592000.0))
    + (0.15 * m.outcome_score)
    AS composite_score
  FROM memories m
  WHERE
    m.tenant_id = p_tenant_id
    AND m.entity_id = p_entity_id
    AND (p_layers = '{}' OR m.layer = ANY(p_layers))
    AND (m.scope = p_scope OR m.scope = 'shared')
    AND 1 - (m.embedding <=> query_embedding) > match_threshold
    AND (m.expires_at IS NULL OR m.expires_at > now())
    AND m.importance_tier != 'critical'

  ORDER BY composite_score DESC
  LIMIT match_count;
$$;


-- ================================================================
-- MATCH_MEMORIES_FOR_DEDUP — checks for duplicate before write
-- Higher threshold (0.92) means only near-identical memories merge
-- ================================================================
CREATE OR REPLACE FUNCTION match_memories_for_dedup(
  query_embedding  vector(512),
  p_tenant_id      uuid,
  p_entity_id      text,
  dedup_threshold  float    DEFAULT 0.92
)
RETURNS TABLE (
  id          uuid,
  content_text text,
  similarity  float
)
LANGUAGE sql STABLE AS $$
  SELECT
    m.id,
    m.content_text,
    1 - (m.embedding <=> query_embedding) AS similarity
  FROM memories m
  WHERE
    m.tenant_id = p_tenant_id
    AND m.entity_id = p_entity_id
    AND 1 - (m.embedding <=> query_embedding) > dedup_threshold
    AND (m.expires_at IS NULL OR m.expires_at > now())
  ORDER BY similarity DESC
  LIMIT 1;
$$;


-- ================================================================
-- CHECK_CONTRADICTION — detects conflicting memories before write
-- High similarity + opposing content = contradiction
-- ================================================================
CREATE OR REPLACE FUNCTION check_contradiction(
  query_embedding         vector(512),
  p_tenant_id             uuid,
  p_entity_id             text,
  contradiction_threshold float DEFAULT 0.85
)
RETURNS TABLE (
  id           uuid,
  content_text text,
  similarity   float
)
LANGUAGE sql STABLE AS $$
  SELECT
    m.id,
    m.content_text,
    1 - (m.embedding <=> query_embedding) AS similarity
  FROM memories m
  WHERE
    m.tenant_id = p_tenant_id
    AND m.entity_id = p_entity_id
    AND 1 - (m.embedding <=> query_embedding) > contradiction_threshold
    AND 1 - (m.embedding <=> query_embedding) < 0.98
    AND m.importance_tier != 'critical'
    AND (m.expires_at IS NULL OR m.expires_at > now())
  ORDER BY similarity DESC
  LIMIT 1;
$$;
