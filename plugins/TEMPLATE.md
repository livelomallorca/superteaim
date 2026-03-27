# Plugin Template

Use this as a starting point for contributing a new adapter.

## Plugin Name

_e.g., "LangGraph Reasoning Adapter"_

## Which Socket

_Which superteaim socket does this plug into? (LiteLLM, Redis, PostgreSQL, ChromaDB, Docker network)_

## What It Replaces

_What built-in component does this replace or enhance?_

_e.g., "Replaces the simple reflection loop in worker.py with LangGraph's state machine reasoning"_

## Prerequisites

_What does the user need installed?_

```bash
pip install langgraph  # example
```

## Installation

_Step-by-step:_

1. Copy files to `agents/worker/` (or wherever)
2. Update `requirements.txt`
3. Rebuild the container: `docker compose build worker`
4. Restart: `docker compose up -d worker`

## Configuration

_What .env changes are needed?_

```env
# Add to .env
REASONING_FRAMEWORK=langgraph
LANGGRAPH_MAX_STEPS=5
```

## How It Works

_Brief explanation of what the adapter does and how it connects to the socket._

## Limitations

_What doesn't work, what's experimental, what needs more testing._

## Author

_Your name/handle and how to reach you._
