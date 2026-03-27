# Contributing to superteaim

Thank you for your interest in contributing! This project is built around **sockets** — standard interfaces that external frameworks and tools can plug into.

## How to Contribute

### Plugins & Adapters

The most valuable contributions are **adapters** that connect external frameworks to our sockets:

| Socket | Interface | Example adapter |
|--------|-----------|-----------------|
| LiteLLM endpoint | OpenAI-compatible API on port 4000 | "Use LangGraph reasoning with superteaim" |
| Redis queues | `tasks:{agent}` and `results:boss` | "Use CrewAI orchestration with superteaim" |
| PostgreSQL schema | `agent_tasks`, `agent_budgets`, `agent_autonomy` | "Braintrust eval dashboard for superteaim" |
| ChromaDB | Vector store on port 8000 | "LlamaIndex RAG with superteaim" |
| Docker network | `superteaim-net` bridge | Any new service container |

See `plugins/README.md` for the socket architecture and `plugins/TEMPLATE.md` for the adapter boilerplate.

### Bug Fixes & Improvements

1. Fork the repo
2. Create a branch: `git checkout -b fix/description`
3. Make your changes
4. Run the smoke tests: `python tests/test_smoke.py`
5. Submit a PR with a clear description

### Documentation

- Fix typos, improve explanations, add examples
- Translate docs (we welcome multilingual contributions)

## What We Accept

- Bug fixes to core services (compose files, agent code, scripts)
- New plugins/adapters in `plugins/` or `examples/`
- Documentation improvements
- Test coverage improvements

## What We Don't Accept

- Changes that add external framework dependencies to core agent code
- Hardware-specific configurations (those belong in your `.env`)
- Credentials, IPs, or any deployment-specific values
- Features that require cloud services to function

## Code Style

- Python: standard library + minimal deps (redis, httpx, flask, psycopg2-binary)
- YAML: 2-space indent, comments for non-obvious settings
- Shell: POSIX-compatible where possible, bash when needed
- All configurable values go in `.env`, never hardcoded

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
