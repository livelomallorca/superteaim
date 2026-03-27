#!/bin/bash
# superteaim — Backup Script
# Backs up PostgreSQL, ChromaDB data, and config files.
# Usage: ./scripts/backup.sh
# Recommended: run via cron at 3 AM daily

set -euo pipefail

# Config from environment or defaults
BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"
POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-postgres}"
POSTGRES_USER="${POSTGRES_USER:-superteaim}"
POSTGRES_DB="${POSTGRES_DB:-superteaim}"
DATE=$(date +%Y%m%d_%H%M%S)

echo "=== superteaim backup — $DATE ==="

# Create backup directories
mkdir -p "$BACKUP_DIR/db" "$BACKUP_DIR/chromadb" "$BACKUP_DIR/configs"

# 1. PostgreSQL dump
echo "Backing up PostgreSQL..."
if docker exec "$POSTGRES_CONTAINER" pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" > "$BACKUP_DIR/db/$DATE.sql" 2>/dev/null; then
    echo "  DB backup: $BACKUP_DIR/db/$DATE.sql"
else
    echo "  WARNING: PostgreSQL backup failed (is the container running?)"
fi

# 2. ChromaDB data (volume snapshot)
echo "Backing up ChromaDB..."
CHROMA_VOLUME=$(docker volume inspect superteaim_chromadata --format '{{.Mountpoint}}' 2>/dev/null || true)
if [ -n "$CHROMA_VOLUME" ] && [ -d "$CHROMA_VOLUME" ]; then
    tar czf "$BACKUP_DIR/chromadb/$DATE.tar.gz" -C "$CHROMA_VOLUME" . 2>/dev/null
    echo "  ChromaDB backup: $BACKUP_DIR/chromadb/$DATE.tar.gz"
else
    echo "  WARNING: ChromaDB volume not found"
fi

# 3. Config files
echo "Backing up configs..."
for f in docker-compose.yml docker-compose.agents.yml docker-compose.monitoring.yml \
         config/litellm_config.yaml config/prometheus.yml config/Caddyfile; do
    if [ -f "$f" ]; then
        cp "$f" "$BACKUP_DIR/configs/"
    fi
done
echo "  Configs copied to $BACKUP_DIR/configs/"

# 4. Cleanup old backups
echo "Cleaning backups older than $RETENTION_DAYS days..."
find "$BACKUP_DIR/db" -name "*.sql" -mtime +"$RETENTION_DAYS" -delete 2>/dev/null || true
find "$BACKUP_DIR/chromadb" -name "*.tar.gz" -mtime +"$RETENTION_DAYS" -delete 2>/dev/null || true

echo "=== Backup complete ==="
