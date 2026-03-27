-- superteaim — Database Schema
-- Auto-loaded by PostgreSQL on first start via docker-entrypoint-initdb.d

-- ── Organizations ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS organizations (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    data_zone_default TEXT DEFAULT 'library' CHECK (data_zone_default IN ('vault', 'library')),
    created_at TIMESTAMP DEFAULT NOW()
);

-- ── Projects (goal ancestry) ──────────────────────────────────
CREATE TABLE IF NOT EXISTS projects (
    id SERIAL PRIMARY KEY,
    org_id INTEGER REFERENCES organizations(id),
    name TEXT NOT NULL,
    goal TEXT,
    status TEXT DEFAULT 'active' CHECK (status IN ('active', 'paused', 'completed')),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_projects_org ON projects(org_id);

-- ── Config Snapshots ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS config_snapshots (
    id SERIAL PRIMARY KEY,
    label TEXT,
    snapshot JSONB NOT NULL,
    created_by TEXT DEFAULT 'system',
    created_at TIMESTAMP DEFAULT NOW()
);

-- ── Agent Task Log ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_tasks (
    id SERIAL PRIMARY KEY,
    org_id INTEGER REFERENCES organizations(id),
    project_id INTEGER REFERENCES projects(id),
    agent_name TEXT NOT NULL,
    task_description TEXT,
    data_zone TEXT CHECK (data_zone IN ('vault', 'library')),
    model_used TEXT,
    node TEXT,
    tokens_in INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    cost_usd NUMERIC(10,6) DEFAULT 0,
    status TEXT CHECK (status IN ('running', 'completed', 'failed', 'pending_approval', 'rejected')),
    error_message TEXT,
    autonomy_level INTEGER DEFAULT 0,
    approved_by TEXT,
    approved_at TIMESTAMP,
    started_at TIMESTAMP DEFAULT NOW(),
    completed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_agent_tasks_agent ON agent_tasks(agent_name);
CREATE INDEX IF NOT EXISTS idx_agent_tasks_status ON agent_tasks(status);
CREATE INDEX IF NOT EXISTS idx_agent_tasks_started ON agent_tasks(started_at DESC);

-- ── Agent Autonomy Tracking ─────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_autonomy (
    agent_name TEXT PRIMARY KEY,
    current_level INTEGER DEFAULT 0,
    total_tasks INTEGER DEFAULT 0,
    error_count INTEGER DEFAULT 0,
    consecutive_successes INTEGER DEFAULT 0,
    promoted_at TIMESTAMP DEFAULT NOW(),
    demoted_at TIMESTAMP,
    last_error TEXT,
    last_review TIMESTAMP
);

-- ── Autonomy History (audit trail) ──────────────────────────
CREATE TABLE IF NOT EXISTS autonomy_history (
    id SERIAL PRIMARY KEY,
    agent_name TEXT NOT NULL,
    from_level INTEGER,
    to_level INTEGER,
    reason TEXT,
    changed_at TIMESTAMP DEFAULT NOW()
);

-- ── Token Budgets ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_budgets (
    id SERIAL PRIMARY KEY,
    org_id INTEGER REFERENCES organizations(id),
    agent_name TEXT NOT NULL,
    daily_token_limit INTEGER DEFAULT 500000,
    daily_tokens_used INTEGER DEFAULT 0,
    daily_api_dollar_limit NUMERIC DEFAULT 5.00,
    daily_api_dollars_used NUMERIC DEFAULT 0,
    reset_at TIMESTAMP DEFAULT NOW() + INTERVAL '24 hours',
    UNIQUE(org_id, agent_name)
);

-- ── Scheduled Tasks ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id SERIAL PRIMARY KEY,
    org_id INTEGER REFERENCES organizations(id),
    agent_name TEXT NOT NULL,
    prompt TEXT NOT NULL,
    data_zone TEXT DEFAULT 'library',
    interval_minutes INTEGER NOT NULL,
    priority INTEGER DEFAULT 5,
    enabled BOOLEAN DEFAULT TRUE,
    last_run TIMESTAMP,
    next_run TIMESTAMP,
    run_count INTEGER DEFAULT 0,
    max_runtime_minutes INTEGER DEFAULT 10,
    created_at TIMESTAMP DEFAULT NOW(),
    notes TEXT
);

-- ── Agent Registry ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_registry (
    agent_name TEXT PRIMARY KEY,
    status TEXT DEFAULT 'running',
    container_id TEXT,
    started_at TIMESTAMP DEFAULT NOW(),
    last_task_at TIMESTAMP,
    total_tasks INTEGER DEFAULT 0,
    total_errors INTEGER DEFAULT 0,
    paused_reason TEXT,
    config JSONB DEFAULT '{}'
);

-- ── Security Scan Results ───────────────────────────────────
CREATE TABLE IF NOT EXISTS security_scans (
    id SERIAL PRIMARY KEY,
    scan_type TEXT,
    findings TEXT,
    severity TEXT CHECK (severity IN ('info', 'low', 'medium', 'high', 'critical')),
    resolved BOOLEAN DEFAULT FALSE,
    scanned_at TIMESTAMP DEFAULT NOW()
);

-- ── Views ───────────────────────────────────────────────────

-- Daily cost summary (org-scoped)
CREATE OR REPLACE VIEW daily_costs AS
SELECT
    org_id,
    DATE(started_at) AS day,
    data_zone,
    COUNT(*) AS tasks,
    SUM(tokens_in + tokens_out) AS total_tokens,
    SUM(cost_usd) AS total_cost
FROM agent_tasks
WHERE status = 'completed'
GROUP BY org_id, DATE(started_at), data_zone
ORDER BY day DESC;

-- Agent performance summary (org-scoped)
CREATE OR REPLACE VIEW agent_performance AS
SELECT
    org_id,
    agent_name,
    COUNT(*) AS total_tasks,
    COUNT(*) FILTER (WHERE status = 'completed') AS completed,
    COUNT(*) FILTER (WHERE status = 'failed') AS failed,
    ROUND(100.0 * COUNT(*) FILTER (WHERE status = 'failed') / NULLIF(COUNT(*), 0), 1) AS error_rate_pct,
    ROUND(AVG(EXTRACT(EPOCH FROM (completed_at - started_at)))::numeric, 1) AS avg_duration_seconds,
    SUM(tokens_in + tokens_out) AS total_tokens
FROM agent_tasks
GROUP BY org_id, agent_name
ORDER BY total_tasks DESC;

-- ── Seed Data ───────────────────────────────────────────────

-- Default organization
INSERT INTO organizations (id, name, data_zone_default) VALUES
    (1, 'default', 'library')
ON CONFLICT (name) DO NOTHING;

-- Default agent autonomy records
INSERT INTO agent_autonomy (agent_name) VALUES
    ('researcher'), ('writer'), ('coder'), ('analyst'), ('ops'), ('worker')
ON CONFLICT (agent_name) DO NOTHING;

-- Default budgets (scoped to default org)
INSERT INTO agent_budgets (org_id, agent_name, daily_token_limit, daily_api_dollar_limit) VALUES
    (1, 'researcher', 1000000, 10.00),
    (1, 'writer',      500000,  5.00),
    (1, 'coder',       500000,  5.00),
    (1, 'analyst',     300000,  3.00),
    (1, 'ops',         100000,  1.00),
    (1, 'worker',      500000,  5.00)
ON CONFLICT (org_id, agent_name) DO NOTHING;

-- Default agent registry
INSERT INTO agent_registry (agent_name) VALUES
    ('researcher'), ('writer'), ('coder'), ('analyst'), ('ops'), ('worker'), ('boss'), ('watchdog')
ON CONFLICT (agent_name) DO NOTHING;

-- ── Live Migration (safe for existing installs) ─────────────
-- These ALTER statements are idempotent — safe to re-run on existing databases.

-- agent_tasks: add approval columns and new status values
ALTER TABLE agent_tasks ADD COLUMN IF NOT EXISTS approved_by TEXT;
ALTER TABLE agent_tasks ADD COLUMN IF NOT EXISTS approved_at TIMESTAMP;
ALTER TABLE agent_tasks DROP CONSTRAINT IF EXISTS agent_tasks_status_check;
ALTER TABLE agent_tasks ADD CONSTRAINT agent_tasks_status_check
    CHECK (status IN ('running', 'completed', 'failed', 'pending_approval', 'rejected'));

-- scheduled_tasks: add heartbeat tracking columns
ALTER TABLE scheduled_tasks ADD COLUMN IF NOT EXISTS next_run TIMESTAMP;
ALTER TABLE scheduled_tasks ADD COLUMN IF NOT EXISTS run_count INTEGER DEFAULT 0;

-- agent_autonomy: ensure promoted_at has a default
ALTER TABLE agent_autonomy ALTER COLUMN promoted_at SET DEFAULT NOW();
UPDATE agent_autonomy SET promoted_at = NOW() WHERE promoted_at IS NULL;
