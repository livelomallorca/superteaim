#!/bin/bash
# superteaim — Security Scan
# Checks open ports, backup status, and logs results to PostgreSQL.
# Usage: ./scripts/security-scan.sh
# Recommended: run weekly via cron

set -euo pipefail

POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-postgres}"
POSTGRES_USER="${POSTGRES_USER:-superteaim}"
POSTGRES_DB="${POSTGRES_DB:-superteaim}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
DATE=$(date +%Y%m%d)
RESULTS=""
SEVERITY="info"

echo "=== superteaim security scan — $DATE ==="

# 1. Check which Docker containers are running
echo "Checking containers..."
CONTAINERS=$(docker ps --format '{{.Names}}: {{.Status}}' 2>/dev/null || echo "Docker not available")
RESULTS="CONTAINERS:\n$CONTAINERS"

# 2. Check for unexpected open ports on localhost
echo "Checking open ports..."
if command -v ss &>/dev/null; then
    PORTS=$(ss -tlnp 2>/dev/null | grep LISTEN | head -20)
    RESULTS="$RESULTS\n\nOPEN PORTS:\n$PORTS"
elif command -v netstat &>/dev/null; then
    PORTS=$(netstat -tlnp 2>/dev/null | grep LISTEN | head -20)
    RESULTS="$RESULTS\n\nOPEN PORTS:\n$PORTS"
else
    RESULTS="$RESULTS\n\nOPEN PORTS: (ss/netstat not available)"
fi

# 3. Verify recent backup exists
echo "Checking backups..."
if [ -d "$BACKUP_DIR/db" ]; then
    LAST_BACKUP=$(ls -t "$BACKUP_DIR/db/" 2>/dev/null | head -1)
    if [ -n "$LAST_BACKUP" ]; then
        RESULTS="$RESULTS\n\nLAST BACKUP: $LAST_BACKUP"
    else
        RESULTS="$RESULTS\n\nLAST BACKUP: NONE FOUND"
        SEVERITY="medium"
    fi
else
    RESULTS="$RESULTS\n\nBACKUP DIR: not found ($BACKUP_DIR/db)"
    SEVERITY="medium"
fi

# 4. Check .env file permissions (should not be world-readable)
echo "Checking .env permissions..."
if [ -f .env ]; then
    PERMS=$(stat -c '%a' .env 2>/dev/null || stat -f '%Lp' .env 2>/dev/null || echo "unknown")
    RESULTS="$RESULTS\n\n.ENV PERMISSIONS: $PERMS"
    if [ "$PERMS" = "644" ] || [ "$PERMS" = "666" ] || [ "$PERMS" = "777" ]; then
        RESULTS="$RESULTS (WARNING: too permissive, should be 600)"
        SEVERITY="medium"
    fi
fi

# 5. Check Docker socket exposure
echo "Checking Docker socket..."
if [ -e /var/run/docker.sock ]; then
    DOCKER_PERMS=$(stat -c '%a' /var/run/docker.sock 2>/dev/null || echo "unknown")
    RESULTS="$RESULTS\n\nDOCKER SOCKET: present (perms: $DOCKER_PERMS)"
fi

# 6. Log results to PostgreSQL
echo "Logging results..."
docker exec -i "$POSTGRES_CONTAINER" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<EOSQL 2>/dev/null || echo "  WARNING: Could not log to database"
INSERT INTO security_scans (scan_type, findings, severity)
VALUES ('weekly', \$\$$(echo -e "$RESULTS")\$\$, '$SEVERITY');
EOSQL

echo ""
echo -e "$RESULTS"
echo ""
echo "=== Scan complete (severity: $SEVERITY) ==="
