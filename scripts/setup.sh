#!/bin/bash
# superteaim — Interactive Setup Wizard
# Detects your OS, checks Docker, configures .env, and starts the stack.
# Usage: ./scripts/setup.sh

set -euo pipefail

echo "╔══════════════════════════════════════╗"
echo "║     superteaim setup wizard     ║"
echo "╚══════════════════════════════════════╝"
echo ""

# ── 1. Detect OS ─────────────────────────────────────────────
OS="unknown"
if [[ "$OSTYPE" == "darwin"* ]]; then
    OS="macos"
elif [[ "$OSTYPE" == "linux"* ]]; then
    OS="linux"
elif [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "cygwin" ]]; then
    OS="windows"
fi
echo "Detected OS: $OS"

# ── 2. Check Docker ──────────────────────────────────────────
if ! command -v docker &>/dev/null; then
    echo ""
    echo "ERROR: Docker is not installed."
    echo "Install it from: https://docs.docker.com/get-docker/"
    exit 1
fi

if ! docker info &>/dev/null; then
    echo ""
    echo "ERROR: Docker daemon is not running."
    echo "Start Docker Desktop or run: sudo systemctl start docker"
    exit 1
fi

DOCKER_VERSION=$(docker --version | head -1)
echo "Docker: $DOCKER_VERSION"

if ! command -v docker compose &>/dev/null && ! docker compose version &>/dev/null 2>&1; then
    echo ""
    echo "ERROR: Docker Compose v2 not found."
    echo "Update Docker or install the compose plugin."
    exit 1
fi
echo "Docker Compose: OK"
echo ""

# ── 3. Configure .env ────────────────────────────────────────
if [ -f .env ]; then
    echo "Found existing .env file."
    read -rp "Overwrite? (y/N): " OVERWRITE
    if [[ ! "$OVERWRITE" =~ ^[Yy]$ ]]; then
        echo "Keeping existing .env"
    else
        cp config/.env.example .env
        echo "Created fresh .env from template"
        echo "Generating secure credentials..."
        sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(openssl rand -hex 16)|" .env
        sed -i "s|^REDIS_PASSWORD=.*|REDIS_PASSWORD=$(openssl rand -hex 16)|" .env
        sed -i "s|^CHROMA_TOKEN=.*|CHROMA_TOKEN=$(openssl rand -hex 16)|" .env
        sed -i "s|^LITELLM_MASTER_KEY=.*|LITELLM_MASTER_KEY=sk-$(openssl rand -hex 16)|" .env
        sed -i "s|^BOSS_API_KEY=.*|BOSS_API_KEY=$(openssl rand -hex 16)|" .env
        sed -i "s|^GRAFANA_ADMIN_PASSWORD=.*|GRAFANA_ADMIN_PASSWORD=$(openssl rand -hex 16)|" .env
    fi
else
    cp config/.env.example .env
    echo "Created .env from template"
    echo "Generating secure credentials..."
    sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(openssl rand -hex 16)|" .env
    sed -i "s|^REDIS_PASSWORD=.*|REDIS_PASSWORD=$(openssl rand -hex 16)|" .env
    sed -i "s|^CHROMA_TOKEN=.*|CHROMA_TOKEN=$(openssl rand -hex 16)|" .env
    sed -i "s|^LITELLM_MASTER_KEY=.*|LITELLM_MASTER_KEY=sk-$(openssl rand -hex 16)|" .env
    sed -i "s|^BOSS_API_KEY=.*|BOSS_API_KEY=$(openssl rand -hex 16)|" .env
    sed -i "s|^GRAFANA_ADMIN_PASSWORD=.*|GRAFANA_ADMIN_PASSWORD=$(openssl rand -hex 16)|" .env
fi

# ── 4. Ask for inference URL ─────────────────────────────────
echo ""
echo "Where are your AI models running?"
echo "  1) Local Ollama (default — http://host.docker.internal:11434)"
echo "  2) Custom URL (vLLM, remote server, etc.)"
echo "  3) Cloud only (OpenAI/Anthropic API — no local models)"
read -rp "Choice [1]: " INFERENCE_CHOICE
INFERENCE_CHOICE=${INFERENCE_CHOICE:-1}

case $INFERENCE_CHOICE in
    1)
        INFERENCE_URL="http://host.docker.internal:11434"
        ;;
    2)
        read -rp "Enter your inference URL: " INFERENCE_URL
        ;;
    3)
        INFERENCE_URL="https://api.openai.com/v1"
        echo "NOTE: Set OPENAI_API_KEY or ANTHROPIC_API_KEY in .env"
        ;;
    *)
        INFERENCE_URL="http://host.docker.internal:11434"
        ;;
esac

# Update .env with inference URL
sed -i.bak "s|INFERENCE_URL=.*|INFERENCE_URL=$INFERENCE_URL|" .env
sed -i.bak "s|EMBEDDING_URL=.*|EMBEDDING_URL=$INFERENCE_URL|" .env
rm -f .env.bak

# ── 5. Choose profile ────────────────────────────────────────
echo ""
echo "Which setup do you want?"
echo "  1) Default — Core services (postgres, redis, chromadb, litellm) + boss + 1 worker + watchdog"
echo "  2) Full    — Everything including 5 specialized workers, monitoring, OpenBao, web UI"
echo "  3) Minimal — Just the database and model router (for development)"
read -rp "Choice [1]: " PROFILE_CHOICE
PROFILE_CHOICE=${PROFILE_CHOICE:-1}

# ── 6. Copy litellm config ───────────────────────────────────
if [ ! -f config/litellm_config.yaml ]; then
    cp config/litellm_config.example.yaml config/litellm_config.yaml
    echo ""
    echo "Created config/litellm_config.yaml from template"
    echo "Edit it to match your model names and endpoints."
fi

# ── 7. Set secure .env permissions ───────────────────────────
chmod 600 .env

# ── 8. Start the stack ───────────────────────────────────────
echo ""
echo "Starting superteaim..."
echo ""

case $PROFILE_CHOICE in
    2)
        docker compose -f docker-compose.yml -f docker-compose.agents.yml \
            -f docker-compose.monitoring.yml --profile full up -d
        ;;
    3)
        docker compose --profile minimal up -d
        ;;
    *)
        docker compose -f docker-compose.yml -f docker-compose.agents.yml up -d
        ;;
esac

echo ""
echo "╔══════════════════════════════════════╗"
echo "║       superteaim is running     ║"
echo "╚══════════════════════════════════════╝"
echo ""
echo "Services:"
echo "  LiteLLM API:  http://localhost:4000"
echo "  Boss Agent:   http://localhost:8080"
echo "  PostgreSQL:   localhost:5432"
echo "  Redis:        localhost:6379"
echo "  ChromaDB:     http://localhost:8000"
if [[ "$PROFILE_CHOICE" == "2" ]]; then
    echo "  Grafana:      http://localhost:3000"
    echo "  Open WebUI:   http://localhost:3001"
fi
echo ""
echo "Quick test:"
echo '  curl -X POST http://localhost:8080/request -H "Authorization: Bearer YOUR_BOSS_API_KEY" -H "Content-Type: application/json" -d '\''{"message": "Hello, what can you do?"}'\'''
echo ""
echo "Logs: docker compose logs -f"
echo "Stop: docker compose down"
