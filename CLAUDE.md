# superteaim — Development Rules

## Security-First Development (Non-Negotiable)

These rules exist because an audit found every one of these violations. They are gates, not suggestions.

### Docker Images
- **NEVER use `:latest`, `:main`, or floating tags.** Every image must be pinned to a specific version (e.g., `redis:7.4.6-alpine`).
- When adding a new service, find the current stable release and pin it.
- Watchtower is banned. Dependencies are updated via Dependabot/Renovate PRs, not auto-pull.

### Python Dependencies
- **Pin exact versions** (`==X.Y.Z`) in all `requirements.txt` files. Never use `>=` floor constraints.
- When adding a new dependency, pin the current version immediately.

### Secrets & Credentials
- **NEVER hardcode passwords, API keys, or connection strings** — not even as fallback defaults.
- `.env.example` must list all required vars with empty values and generation instructions as comments.
- `scripts/setup.sh` must auto-generate every secret using `openssl rand`.
- Any new env var that holds a secret must use `:?` guard in compose (fail-fast if unset).

### API Endpoints
- **Every HTTP endpoint that returns data must require auth** (Bearer token). The only exception is `/health`.
- Token comparison must use `hmac.compare_digest()` — never `==` or `!=`.
- Flask apps must set `MAX_CONTENT_LENGTH` (1MB default).
- Error responses must return generic messages. Log `str(e)` server-side, never send it to the client.

### Docker Compose
- All ports (except Caddy 80/443) must bind to `127.0.0.1`.
- Docker socket mounts require explicit justification and should use a socket proxy when possible.
- Healthchecks must use service credentials (e.g., `redis-cli -a $REDIS_PASSWORD ping`).

### Shell Scripts
- Never interpolate variables into SQL strings. Use dollar-quoting (`$$...$$`), `psql -v`, or parameterized queries.
- All scripts must have `set -euo pipefail`.

### Code Patterns
- Use `with conn:` context managers for all database connections. No manual close().
- No `from threading import Thread` unless actually used.
- No features documented in STANDARDS.md / README.md that aren't implemented in code.

## CI Checks

The CI pipeline runs these checks on every PR. If you add code that would fail any of them, fix it before committing:

1. **Smoke tests** (`tests/test_smoke.py`) — syntax, YAML validity, env completeness, no hardcoded secrets, no `:latest` tags, pinned Python deps, no `str(e)` in responses, auth on all endpoints
2. **Semgrep** — static analysis
3. **pip-audit** — known CVEs in Python dependencies
4. **Trivy** — container image vulnerability scanning

## Project Structure

- `agents/boss/` — Orchestrator. Receives HTTP requests, plans tasks, dispatches via Redis.
- `agents/worker/` — Executor. Picks tasks from Redis queue, processes with LLM, stores results.
- `agents/watchdog/` — Monitor. Health checks, stuck task detection, Telegram alerts.
- `config/` — Configuration templates (.env.example, litellm, prometheus, Caddyfile).
- `scripts/` — Operational scripts (setup, backup, security-scan, eval, indexing).
- `sql/schema.sql` — Database schema (8 tables, 2 views).
- `docker-compose.yml` — Core services (postgres, redis, chromadb, litellm).
- `docker-compose.agents.yml` — Agent services (boss, workers, watchdog).
- `docker-compose.monitoring.yml` — Monitoring (prometheus, grafana, node-exporter).
