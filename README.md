# superteaim

**Sovereign AI infrastructure. Clone, configure, run.** Your agents, your hardware, your data.

[![CI](https://github.com/livelomallorca/superteaim/actions/workflows/ci.yml/badge.svg)](https://github.com/livelomallorca/superteaim/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

## What is this?

A complete Docker Compose stack for running autonomous AI agents on your own hardware. No cloud dependencies. No API keys required (but supported as optional fallback).

**3 commands to start:**
```bash
cp config/.env.example .env        # configure
nano .env                           # set your INFERENCE_URL
docker compose up -d                # run
```

Or use the interactive wizard:
```bash
./scripts/setup.sh
```

## What's in the box

| Service | What it does | Port |
|---------|-------------|------|
| **PostgreSQL** | Task log, budgets, autonomy tracking, schedules | 5432 |
| **Redis** | Task queue between boss and workers | 6379 |
| **ChromaDB** | Vector memory for RAG | 8000 |
| **LiteLLM** | Model router (local + cloud fallback) | 4000 |
| **Boss Agent** | Plans tasks, dispatches to workers, collects results | 8080 |
| **Worker Agents** | Execute tasks with reflection + memory | — |
| **Watchdog** | Monitors health, restarts frozen containers | — |

**Optional (with profiles):**
- Prometheus + Grafana + Node Exporter (monitoring)
- OpenBao (secret management)
- Caddy (reverse proxy with auto-TLS)
- Open WebUI (chat interface)
- Watchtower (auto-update containers)

## Architecture

```
                    ┌─────────────────────────────────────┐
   Your client      │          Docker Network              │
   (curl/API/UI)    │                                      │
        │           │  ┌──────────┐    ┌──────────────┐   │
        └───────────┼─►│ Boss     │───►│ LiteLLM      │   │
                    │  │ Agent    │    │ (model router)│───┼──► Your inference
                    │  └────┬─────┘    └──────────────┘   │    (Ollama/vLLM/API)
                    │       │ Redis                        │
                    │  ┌────┴────┬────────┬───────┐       │
                    │  ▼         ▼        ▼       ▼       │
                    │ Worker   Worker   Worker  Worker     │
                    │ (research)(write) (code)  (ops)      │
                    │  │         │        │       │        │
                    │  └────┬────┴────────┴───────┘        │
                    │       ▼                               │
                    │  ┌──────────┐  ┌──────────┐          │
                    │  │PostgreSQL│  │ ChromaDB │          │
                    │  │(tasks,   │  │(vector   │          │
                    │  │ budgets) │  │ memory)  │          │
                    │  └──────────┘  └──────────┘          │
                    │                                      │
                    │  ┌──────────┐                        │
                    │  │ Watchdog │ monitors all containers │
                    │  └──────────┘                        │
                    └─────────────────────────────────────┘
```

## Intelligence Features

Built-in, no external framework dependencies:

- **Reflection** — Workers self-check output before returning (configurable via `ENABLE_REFLECTION`)
- **Memory** — Workers query past similar tasks from PostgreSQL before processing (configurable via `ENABLE_MEMORY`)
- **Planning** — Boss breaks complex requests into parallel + sequential subtasks
- **Autonomy levels** — Agents earn trust through track record (L0 supervised → L2 autonomous)
- **Token budgets** — Per-agent daily limits prevent runaway costs

## What Plugs In

This stack provides **sockets** — standard interfaces that any framework can connect to:

| Socket | Interface | What plugs in |
|--------|-----------|---------------|
| **LiteLLM** | OpenAI-compatible API | LangGraph, CrewAI, AutoGen, any OpenAI client |
| **Redis queues** | `tasks:{agent}`, `results:boss` | Custom orchestrators, n8n, any queue consumer |
| **PostgreSQL** | `agent_tasks`, `agent_budgets`, `agent_autonomy` | Braintrust, Promptfoo, custom dashboards |
| **ChromaDB** | Vector store API | LlamaIndex, Haystack, any RAG pipeline |
| **Docker network** | `superteaim-net` bridge | Any new container joins and connects |
| **.env config** | All behavior configurable | Swap components without touching code |

See [plugins/README.md](plugins/README.md) for the socket architecture and how to build adapters.

## Profiles

```bash
# Default: core services + boss + 1 worker + watchdog
docker compose -f docker-compose.yml -f docker-compose.agents.yml up -d

# Full: all 5 specialized workers + monitoring + OpenBao + web UI
docker compose -f docker-compose.yml -f docker-compose.agents.yml \
  -f docker-compose.monitoring.yml --profile full up -d

# Minimal: just database + model router (for development)
docker compose --profile minimal up -d

# Add monitoring to any setup
docker compose -f docker-compose.yml -f docker-compose.monitoring.yml up -d
```

## Configuration

All configuration lives in `.env`. Key settings:

| Variable | What | Default |
|----------|------|---------|
| `INFERENCE_URL` | Where your models run | `http://host.docker.internal:11434` |
| `ENABLE_REFLECTION` | Workers self-check output | `true` |
| `ENABLE_MEMORY` | Workers recall similar past tasks | `true` |
| `BOSS_MODEL` | Model for planning | `fast` |

See `config/.env.example` for all options with descriptions.

## Scripts

| Script | What it does |
|--------|-------------|
| `scripts/setup.sh` | Interactive setup wizard |
| `scripts/backup.sh` | Backup DB + ChromaDB + configs |
| `scripts/security-scan.sh` | Check ports, permissions, backup status |
| `scripts/index-knowledge.py` | Index documents into ChromaDB for RAG |
| `scripts/eval.py` | Report agent performance metrics |

## Data Classification

All data is classified into two zones:

| Zone | Where | Processed by | Can leave network? |
|------|-------|-------------|-------------------|
| **VAULT** | Local only | Local models ONLY | Never |
| **LIBRARY** | Anywhere | Any model including APIs | Yes |

The Boss Agent classifies every request before routing. If unsure, it defaults to VAULT (local only).

See [docs/DATA-CLASSIFICATION.md](docs/DATA-CLASSIFICATION.md) for the full policy.

## Requirements

- Docker + Docker Compose v2
- An inference endpoint (Ollama, vLLM, or cloud API)
- 4GB+ RAM for core services
- No GPU needed on the server (inference is external)

## Roadmap

**v0.1 (current):** Core stack, basic intelligence, socket architecture

**Future:**
- `pip install superteaim` (PyPI package with CLI)
- One-liner install script
- MkDocs documentation site
- Migration guides ("From AutoGen to superteaim")
- Hardware configurator tool
- Community plugin registry

## Agent Personas

superteaim ships with rich agent personas via [agency-agents](https://github.com/msitarzewski/agency-agents) (MIT, included as git submodule). These provide deep role definitions with personality, workflows, and success metrics for 61+ agent types.

Located at `agents/personas/community/`. Custom overrides go in `agents/personas/custom/`.

To use a community persona, set `AGENT_PERSONA_FILE` in your agent's environment:
```yaml
AGENT_PERSONA_FILE: /app/personas/community/engineering/engineering-backend-architect.md
```

## License

MIT — see [LICENSE](LICENSE).

AI models have their own licenses (Apache 2.0 for Qwen, etc.). This repo contains no model weights.

Agent personas in `agents/personas/community/` are from [agency-agents](https://github.com/msitarzewski/agency-agents) by [@msitarzewski](https://github.com/msitarzewski) and contributors, used under MIT license.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).
