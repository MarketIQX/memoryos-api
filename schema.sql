-- MemoryOS schema.sql · Foundation Release · March 2026
-- QC Pass 1 · Apple + McKinsey + Infosys Enterprise standard
-- Every column justified. Every law enforced. Survives to 2031.

-- ============================================================
-- EXTENSIONS
-- ============================================================
CREATE EXTENSION IF NOT EXISTS vector;

-- ============================================================
-- TENANTS — one row per company using MemoryOS
-- ============================================================
CREATE TABLE IF NOT EXISTS tenants (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name       TEXT NOT NULL,
  plan       TEXT DEFAULT 'free' CHECK (plan IN ('free','starter','pro','enterprise')),
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);
ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;

-- ============================================================
-- API KEYS — each tenant gets one or more keys
-- ============================================================
CREATE TABLE IF NOT EXISTS api_keys (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    UUID REFERENCES tenants(id) ON DELETE CASCADE,
  key_hash     TEXT NOT NULL UNIQUE,
  key_prefix   TEXT NOT NULL,
  name         TEXT,
  is_active    BOOLEAN DEFAULT true,
  created_at   TIMESTAMPTZ DEFAULT now(),
  last_used_at TIMESTAMPTZ
);
ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY;

-- ============================================================
-- MEMORIES — the core table. Every law lives here.
-- Law 1: embedding enables meaning-based retrieval
-- Law 2: scope + tenant_id enforce controlled sharing
-- Law 3: sync_status enables edge + offline + 6G nodes
-- Law 4: outcome_score + confidence_decay_rate enable compounding
-- ============================================================
CREATE TABLE IF NOT EXISTS memories (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id             UUID REFERENCES tenants(id) ON DELETE CASCADE,
  entity_id             TEXT NOT NULL,
  entity_type           TEXT CHECK (entity_type IN ('user','agent','org','robot','device','network_node')),
  layer                 TEXT CHECK (layer IN ('session','org','agent','persona','cognitive','pattern','federated')),
  scope                 TEXT DEFAULT 'shared' CHECK (scope IN ('shared','private')),
  agent_id              TEXT,
  modality              TEXT DEFAULT 'text' CHECK (modality IN ('text','image','audio','sensor','neural','document')),
  content               JSONB,
  content_text          TEXT NOT NULL,
  content_type          TEXT DEFAULT 'text',
  embedding             vector(1024),
  embedding_model       TEXT DEFAULT 'voyage-3-lite',
  importance            FLOAT DEFAULT 0.5 CHECK (importance >= 0 AND importance <= 1),
  importance_tier       TEXT DEFAULT 'normal' CHECK (importance_tier IN ('critical','high','normal','low')),
  outcome_score         FLOAT DEFAULT 0.5 CHECK (outcome_score >= 0 AND outcome_score <= 1),
  contradiction_flag    BOOLEAN DEFAULT false,
  contradicts_ids       UUID[] DEFAULT '{}',
  confidence            FLOAT DEFAULT 1.0 CHECK (confidence >= 0 AND confidence <= 1),
  confidence_decay_rate FLOAT DEFAULT 0.1,
  last_confirmed_at     TIMESTAMPTZ DEFAULT now(),
  tags                  TEXT[] DEFAULT '{}',
  cognitive_state       JSONB DEFAULT '{}',
  domain                TEXT DEFAULT '',
  expires_at            TIMESTAMPTZ,
  health_status         TEXT DEFAULT 'active' CHECK (health_status IN ('active','stale','orphan')),
  opt_in_federated      BOOLEAN DEFAULT false,
  federated_pool_id     TEXT DEFAULT '',
  anonymised            BOOLEAN DEFAULT false,
  sync_status           TEXT DEFAULT 'synced' CHECK (sync_status IN ('synced','pending','conflict')),
  created_at            TIMESTAMPTZ DEFAULT now(),
  updated_at            TIMESTAMPTZ DEFAULT now()
);
ALTER TABLE memories ENABLE ROW LEVEL SECURITY;

CREATE POLICY memories_tenant_isolation ON memories
  USING (tenant_id = current_setting('app.tenant_id', true)::uuid);

-- ============================================================
-- MEMORY EDGES — connects memories to each other
-- Enables causal chains, temporal narratives, contradiction maps
-- Required for 2031 graph intelligence layer
-- ============================================================
CREATE TABLE IF NOT EXISTS memory_edges (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      UUID REFERENCES tenants(id) ON DELETE CASCADE,
  from_memory_id UUID REFERENCES memories(id) ON DELETE CASCADE,
  to_memory_id   UUID REFERENCES memories(id) ON DELETE CASCADE,
  edge_type      TEXT CHECK (edge_type IN ('causes','follows','contradicts','supports','relates')),
  weight         FLOAT DEFAULT 1.0,
  created_at     TIMESTAMPTZ DEFAULT now()
);
ALTER TABLE memory_edges ENABLE ROW LEVEL SECURITY;

-- ============================================================
-- AGENT REGISTRY — every AI person registered here
-- employee = Arjun, Priya, Alex
-- professional = Digital CTO, Digital CA, Digital CFO
-- advisor = Personal advisor, Legal counsel
-- system = Aria brain, Nova self-builder
-- robot = warehouse, surgical, service robots
-- network_node = 6G AI nodes
-- ============================================================
CREATE TABLE IF NOT EXISTS agent_registry (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      UUID REFERENCES tenants(id) ON DELETE CASCADE,
  agent_id       TEXT NOT NULL,
  agent_name     TEXT,
  agent_type     TEXT DEFAULT 'employee' CHECK (agent_type IN ('employee','professional','advisor','system','robot','iot_device','network_node')),
  capabilities   JSONB DEFAULT '{}',
  webhook_url    TEXT,
  is_active      BOOLEAN DEFAULT true,
  created_at     TIMESTAMPTZ DEFAULT now(),
  last_active_at TIMESTAMPTZ DEFAULT now()
);
ALTER TABLE agent_registry ENABLE ROW LEVEL SECURITY;

-- ============================================================
-- USAGE LOG — every API call logged
-- Billing, analytics, audit trail, GDPR compliance
-- ============================================================
CREATE TABLE IF NOT EXISTS usage_log (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   UUID,
  agent_id    TEXT,
  endpoint    TEXT,
  layer       TEXT,
  entity_id   TEXT,
  latency_ms  INT,
  tokens_used INT DEFAULT 0,
  status_code INT DEFAULT 200,
  created_at  TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- UPDATED_AT TRIGGER — auto-updates on every row change
-- Infosys enterprise standard — data integrity requirement
-- ============================================================
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER memories_updated_at
  BEFORE UPDATE ON memories
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE OR REPLACE TRIGGER tenants_updated_at
  BEFORE UPDATE ON tenants
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ============================================================
-- INDEXES — performance at scale
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_memories_tenant_entity    ON memories(tenant_id, entity_id);
CREATE INDEX IF NOT EXISTS idx_memories_layer            ON memories(tenant_id, layer);
CREATE INDEX IF NOT EXISTS idx_memories_importance_tier  ON memories(tenant_id, importance_tier);
CREATE INDEX IF NOT EXISTS idx_memories_health           ON memories(health_status);
CREATE INDEX IF NOT EXISTS idx_memories_expires          ON memories(expires_at) WHERE expires_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_memories_federated        ON memories(federated_pool_id) WHERE opt_in_federated = true;
CREATE INDEX IF NOT EXISTS idx_memories_sync             ON memories(sync_status) WHERE sync_status = 'pending';
CREATE INDEX IF NOT EXISTS idx_memories_embedding        ON memories USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- ============================================================
-- RPC FUNCTIONS — called by every API request
-- ============================================================

CREATE OR REPLACE FUNCTION match_memories(
  query_embedding  vector(1024),
  p_tenant_id      uuid,
  p_entity_id      text,
  p_layers         text[]  DEFAULT '{}',
  p_scope          text    DEFAULT 'shared',
  match_threshold  float   DEFAULT 0.30,
  match_count      int     DEFAULT 10
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
    AND m.health_status != 'orphan'
  ORDER BY composite_score DESC
  LIMIT match_count;
$$;

CREATE OR REPLACE FUNCTION match_memories_for_dedup(
  query_embedding  vector(1024),
  p_tenant_id      uuid,
  p_entity_id      text,
  dedup_threshold  float DEFAULT 0.92
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
    AND 1 - (m.embedding <=> query_embedding) > dedup_threshold
    AND (m.expires_at IS NULL OR m.expires_at > now())
  ORDER BY similarity DESC
  LIMIT 1;
$$;

CREATE OR REPLACE FUNCTION check_contradiction(
  query_embedding         vector(1024),
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