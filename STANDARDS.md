# Standards

Operating standards for sovereign AI infrastructure. Every rule traces to a real failure or validated research finding.

## Section 1: Infrastructure

### Rule 1: Container Isolation

Every agent runs in its own Docker container. No shared processes, no monolithic scripts.

```yaml
agent-name:
  build: ./agents/worker
  restart: always
  mem_limit: 512m
  healthcheck:
    test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"]
    interval: 30s
    timeout: 10s
    retries: 3
```

**Why:** When chat and task queue share a process, a chat interaction blocks the task queue. Containers let you kill and restart one agent without affecting others.

### Rule 2: Persistent Volumes

Every container MUST have a named volume from Day 1.

```yaml
# WRONG
agent:
  image: agent

# RIGHT
agent:
  image: agent
  volumes:
    - agent-data:/data
```

**Why:** Completed tasks that exist only in memory are lost on container restart.

### Rule 3: Health Checks + Watchdog

Every agent exposes `/health` on port 8080. Watchdog pings every 30 seconds. No heartbeat for 2 minutes → restart. Same agent dies 3x in 1 hour → alert human.

### Rule 4: Task Queue (Redis)

All work flows through Redis queues. Never direct agent-to-agent communication.

```
Human → Boss → Redis Queue → Worker → Result → Redis → Boss delivers
```

Redis config: `appendonly yes` — queue survives restarts.

### Rule 5: Task Deduplication

Before dispatching, check if the same task completed recently:
```python
def should_dispatch(task_key: str, hours: int = 24) -> bool:
    result = db.execute(
        "SELECT 1 FROM agent_tasks WHERE task_key = %s AND status = 'completed' "
        "AND completed_at > NOW() - INTERVAL '%s hours'", (task_key, hours)
    )
    return result.rowcount == 0
```

**Why:** Restarted containers re-running old tasks waste compute and produce duplicates.

### Rule 6: Scheduling

Per-agent intervals, stored in PostgreSQL, checked every 30 seconds. Don't rely on container-level cron.

### Rule 7: Rate Limiting + Token Budgets

Every agent has daily token and dollar caps. Budget check BEFORE every LLM call. Exceeded budget → task deferred, not retried.

### Rule 8: Priority Queues

Human requests jump ahead of scheduled tasks. Use separate Redis lists:
- `tasks:{agent}:high` — human requests (checked first)
- `tasks:{agent}` — scheduled/background tasks

### Rule 9: Graceful Degradation

If a model is overloaded, LiteLLM falls back to the next model in the chain. If all local models are down, fall back to cloud API (if configured). If nothing works, queue the task and alert.

### Rule 10: Backup Before Destroy

No destructive operation without a backup. `pg_dump` before schema changes. Snapshot volumes before upgrades. This is non-negotiable.

### Rule 11: Secrets Never in Code

All credentials via environment variables or secret store (OpenBao). Never in source, never in compose files, never in agent prompts.

### Rule 12: Watchdog from Day 1

Don't add monitoring "later." Deploy the watchdog container alongside your first agent.

---

## Section 2: Intelligence

### Reasoning Loops

Workers implement a reflect-before-returning pattern:

1. Process the task (call LLM)
2. Self-check: ask the model to review its own output
3. If the review suggests improvements, iterate (up to `MAX_REASONING_STEPS`)
4. Return the final output

This catches obvious errors, hallucinations, and incomplete answers without external frameworks.

### Memory

Before processing a task, workers query PostgreSQL for similar past tasks:

```sql
SELECT task_description, status FROM agent_tasks
WHERE status = 'completed'
  AND started_at > NOW() - INTERVAL '72 hours'
  AND task_description ILIKE '%keyword%'
LIMIT 3
```

Past task results are injected as system context. This gives agents continuity without a dedicated memory framework.

### Planning

The Boss Agent breaks complex requests into task plans:

```json
{
  "tasks": [
    {"agent": "analyst", "prompt": "...", "depends_on": null},
    {"agent": "writer", "prompt": "...", "depends_on": 0}
  ]
}
```

- `depends_on: null` → parallel (dispatched immediately)
- `depends_on: N` → sequential (waits for task N to complete, injects its result as context)

### Autonomy Levels

Agents earn trust through track record:

| Level | Behavior | Criteria |
|-------|----------|----------|
| **0 — Supervised** | Boss reviews all output | Default |
| **1 — Semi-auto** | Boss reviews external-facing output only | 50+ tasks, <2% errors, 14+ days |
| **2 — Autonomous** | Boss spot-checks weekly | 200+ tasks, <1% errors, 30+ days at L1 |

Demotion is immediate on: >10% error rate in a week, security incident, or quality complaint.

### Data Classification

Every request is classified before routing:

| Zone | Processing | Can leave network? |
|------|-----------|-------------------|
| **VAULT** | Local models only | Never |
| **LIBRARY** | Any model | Yes |

If unsure, treat as VAULT. The Boss Agent tags every task. LiteLLM enforces routing policy.
