"""
Watchdog. Monitors agent health. Restarts frozen containers.
Alerts via Telegram (optional) if an agent keeps crashing.
Recovers stuck tasks from Redis.
v0.3: auto-autonomy escalation (hourly evaluation).
"""
import os
import time
import json
import logging
from datetime import datetime, timezone

import docker
import redis
import httpx
import psycopg2

log = logging.getLogger("watchdog")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [watchdog] %(message)s")

REDIS_URL = os.environ["REDIS_URL"]
POSTGRES_URL = os.environ.get("POSTGRES_URL", "")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

rdb = redis.from_url(REDIS_URL, decode_responses=True)
docker_client = docker.from_env()

# Agent containers to monitor (auto-discovered from Docker labels or hardcoded)
AGENT_PREFIXES = ["boss-agent", "researcher-agent", "writer-agent",
                  "coder-agent", "analyst-agent", "ops-agent", "worker"]
HEALTH_TIMEOUT = 120
CRASH_THRESHOLD = 3
STUCK_TASK_TIMEOUT = 600  # 10 minutes
AUTONOMY_CHECK_INTERVAL = 3600  # 1 hour

crash_counts: dict[str, list[float]] = {}


def _get_db():
    """Get a PostgreSQL connection."""
    return psycopg2.connect(POSTGRES_URL)


def discover_agents() -> list[str]:
    """Find running agent containers."""
    agents = []
    try:
        for container in docker_client.containers.list():
            name = container.name
            if any(prefix in name for prefix in AGENT_PREFIXES):
                agents.append(name)
    except Exception as e:
        log.error(f"Container discovery failed: {e}")
    return agents


def check_container_health(container_name: str) -> bool:
    """Check if a container is healthy via Docker health check."""
    try:
        container = docker_client.containers.get(container_name)
        if container.status != "running":
            return False
        health = container.attrs.get("State", {}).get("Health", {}).get("Status", "none")
        return health == "healthy"
    except docker.errors.NotFound:
        return False
    except Exception as e:
        log.error(f"Health check error for {container_name}: {e}")
        return False


def restart_container(container_name: str):
    """Restart a container and track crash frequency."""
    try:
        container = docker_client.containers.get(container_name)
        log.warning(f"Restarting {container_name}...")
        container.restart(timeout=10)

        now = time.time()
        if container_name not in crash_counts:
            crash_counts[container_name] = []
        crash_counts[container_name].append(now)
        # Keep only last hour
        crash_counts[container_name] = [
            t for t in crash_counts[container_name] if now - t < 3600
        ]

        if len(crash_counts[container_name]) >= CRASH_THRESHOLD:
            send_alert(
                f"ALERT: {container_name} has crashed "
                f"{len(crash_counts[container_name])}x in the last hour. "
                f"Needs manual attention."
            )
            crash_counts[container_name] = []

    except Exception as e:
        log.error(f"Failed to restart {container_name}: {e}")
        send_alert(f"ALERT: Failed to restart {container_name}: {e}")


def send_alert(message: str):
    """Send alert via Telegram (if configured) or just log it."""
    log.warning(f"Alert: {message}")
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        httpx.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message},
            timeout=10
        )
    except Exception as e:
        log.error(f"Telegram alert failed: {e}")


def check_stuck_tasks():
    """Find tasks that have been 'running' too long and requeue them."""
    try:
        for key in rdb.scan_iter("task:*"):
            status = rdb.hget(key, "status")
            if status != "running":
                continue
            started = rdb.hget(key, "started_at")
            if not started:
                continue
            try:
                started_dt = datetime.fromisoformat(started)
                age = (datetime.now(timezone.utc) - started_dt).total_seconds()
                if age > STUCK_TASK_TIMEOUT:
                    agent = rdb.hget(key, "agent") or "worker"
                    prompt = rdb.hget(key, "prompt") or ""
                    task_id = key.split(":")[-1]
                    log.warning(f"Task {task_id} stuck for {int(age)}s on {agent}. Requeuing.")
                    rdb.hset(key, "status", "requeued")
                    rdb.lpush(f"tasks:{agent}", json.dumps({
                        "task_id": task_id,
                        "prompt": prompt,
                        "data_zone": rdb.hget(key, "data_zone") or "library",
                        "context": ""
                    }))
            except (ValueError, TypeError):
                pass
    except Exception as e:
        log.error(f"Stuck task check failed: {e}")


def evaluate_autonomy():
    """Promote or demote agents based on their task track record.

    Promotion L0→L1: 50+ tasks, <2% error rate, 14+ days at L0.
    Promotion L1→L2: 200+ tasks, <1% error rate, 30+ days at L1.
    Demotion (immediate): >10% error rate in last 7 days.
    """
    if not POSTGRES_URL:
        return
    try:
        conn = _get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT
                aa.agent_name,
                aa.current_level,
                aa.promoted_at,
                COUNT(at.id) AS total_tasks,
                ROUND(AVG(CASE WHEN at.status = 'failed' THEN 1.0 ELSE 0.0 END)::numeric, 4)
                    AS error_rate,
                ROUND(
                    COUNT(CASE WHEN at.started_at > NOW() - INTERVAL '7 days'
                               AND at.status = 'failed' THEN 1 END)::numeric /
                    NULLIF(COUNT(CASE WHEN at.started_at > NOW() - INTERVAL '7 days'
                                     THEN 1 END), 0),
                    4
                ) AS error_rate_7d
            FROM agent_autonomy aa
            LEFT JOIN agent_tasks at
                ON at.agent_name = aa.agent_name
                AND at.status IN ('completed', 'failed')
            GROUP BY aa.agent_name, aa.current_level, aa.promoted_at
        """)

        now = datetime.now(timezone.utc)
        for row in cur.fetchall():
            agent, level, promoted_at, total, error_rate, error_rate_7d = row
            new_level = level
            reason = None

            err7d = float(error_rate_7d) if error_rate_7d is not None else None
            err_all = float(error_rate) if error_rate is not None else None

            # Demotion: >10% error rate in last 7 days
            if err7d is not None and err7d > 0.10:
                new_level = max(0, level - 1)
                reason = f"error_rate_7d={err7d:.1%} > 10%"

            # Promotion L0 → L1
            elif (level == 0 and (total or 0) >= 50
                  and err_all is not None and err_all < 0.02):
                if promoted_at:
                    pa = promoted_at if promoted_at.tzinfo else promoted_at.replace(tzinfo=timezone.utc)
                    days = (now - pa).days
                else:
                    days = 0
                if days >= 14:
                    new_level = 1
                    reason = f"{total} tasks, {err_all:.1%} errors, {days}d at L0"

            # Promotion L1 → L2
            elif (level == 1 and (total or 0) >= 200
                  and err_all is not None and err_all < 0.01):
                if promoted_at:
                    pa = promoted_at if promoted_at.tzinfo else promoted_at.replace(tzinfo=timezone.utc)
                    days = (now - pa).days
                else:
                    days = 0
                if days >= 30:
                    new_level = 2
                    reason = f"{total} tasks, {err_all:.1%} errors, {days}d at L1"

            if new_level != level:
                cur.execute("""
                    UPDATE agent_autonomy
                    SET current_level = %s, promoted_at = NOW()
                    WHERE agent_name = %s
                """, (new_level, agent))
                cur.execute("""
                    INSERT INTO autonomy_history (agent_name, from_level, to_level, reason)
                    VALUES (%s, %s, %s, %s)
                """, (agent, level, new_level, reason))
                action = "promoted" if new_level > level else "demoted"
                msg = f"Autonomy: {agent} {action} L{level}->L{new_level} ({reason})"
                log.info(msg)
                send_alert(msg)

        conn.commit()
        conn.close()
    except Exception as e:
        log.error(f"Autonomy evaluation failed: {e}")


def main():
    log.info("Watchdog starting. Monitoring agents...")
    last_autonomy_check = 0

    while True:
        agents = discover_agents()
        for agent in agents:
            healthy = check_container_health(agent)
            if not healthy:
                log.warning(f"{agent}: UNHEALTHY")
                restart_container(agent)
            else:
                log.debug(f"{agent}: OK")

        check_stuck_tasks()

        now = time.time()
        if now - last_autonomy_check >= AUTONOMY_CHECK_INTERVAL:
            evaluate_autonomy()
            last_autonomy_check = now

        time.sleep(30)


if __name__ == "__main__":
    main()
