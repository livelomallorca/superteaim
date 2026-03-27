# Plugins & Adapters

superteaim provides **sockets** — standard interfaces that external frameworks plug into. This directory is where community-contributed adapters live.

## Socket Architecture

```
┌─────────────────────────────────────────────────────┐
│                 superteaim                       │
│                                                       │
│  ┌─────────┐  ┌─────────┐  ┌──────────┐  ┌───────┐ │
│  │ LiteLLM │  │  Redis   │  │PostgreSQL│  │ChromaDB│ │
│  │ :4000   │  │  :6379   │  │  :5432   │  │ :8000  │ │
│  └────┬────┘  └────┬────┘  └────┬─────┘  └───┬────┘ │
│       │            │            │             │       │
└───────┼────────────┼────────────┼─────────────┼───────┘
        │            │            │             │
   ┌────┴────┐  ┌────┴────┐  ┌───┴───┐   ┌────┴────┐
   │ OpenAI  │  │ Queue   │  │ Eval  │   │  RAG    │
   │ clients │  │consumers│  │ tools │   │pipelines│
   └─────────┘  └─────────┘  └───────┘   └─────────┘
```

## The 6 Sockets

### 1. LiteLLM Endpoint (port 4000)

**Interface:** OpenAI-compatible API (`/v1/chat/completions`, `/v1/embeddings`)

**What plugs in:** Any framework that speaks OpenAI API — LangGraph, CrewAI, AutoGen, Semantic Kernel, custom code.

**How:** Point your framework's `base_url` to `http://localhost:4000/v1`

### 2. Redis Queues (port 6379)

**Interface:** Redis lists for task dispatch (`tasks:{agent}`) and result collection (`results:boss`). Redis hashes for task state (`task:{id}`).

**What plugs in:** Custom orchestrators, n8n workflows, any Redis client.

**How:** Push JSON task objects to `tasks:{agent_name}`, poll `results:boss` for completions.

### 3. PostgreSQL Schema (port 5432)

**Interface:** Tables: `agent_tasks`, `agent_budgets`, `agent_autonomy`, `scheduled_tasks`, `agent_registry`, `security_scans`. Views: `daily_costs`, `agent_performance`.

**What plugs in:** Evaluation tools (Braintrust, Promptfoo), dashboards, custom analytics.

**How:** Connect to the database, query the views and tables.

### 4. ChromaDB Vector Store (port 8000)

**Interface:** ChromaDB HTTP API, collection `knowledge` with cosine similarity.

**What plugs in:** LlamaIndex, Haystack, any vector search client.

**How:** Use the ChromaDB client library or HTTP API directly.

### 5. Docker Network (`superteaim-net`)

**Interface:** Bridge network. Any container on this network can reach all services by name.

**What plugs in:** Any new service container — custom agents, UIs, data pipelines.

**How:** Add `networks: [superteaim-net]` to your container's compose file.

### 6. Environment Configuration (`.env`)

**Interface:** All behavior controlled via environment variables.

**What plugs in:** Different deployment configurations without code changes.

**How:** Copy `.env.example`, modify values, restart.

## Contributing an Adapter

1. Copy `TEMPLATE.md` and fill it in
2. Create a directory: `plugins/your-adapter-name/`
3. Include: adapter code, README, requirements/deps
4. Submit a PR

See [TEMPLATE.md](TEMPLATE.md) for the boilerplate.
