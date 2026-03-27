"""
Watchdog. Monitors agent health. Restarts frozen containers.
Alerts via Telegram (optional) if an agent keeps crashing.
Recovers stuck tasks from Redis.
"""
import os
import time
import json
import logging
from datetime import datetime, timezone

import docker
import redis
import httpx

log = logging.getLogger("watchdog")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [watchdog] %(message)s")

REDIS_URL = os.environ["REDIS_URL"]
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

crash_counts: dict[str, list[float]] = {}


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


def main():
    log.info("Watchdog starting. Monitoring agents...")

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
        time.sleep(30)


if __name__ == "__main__":
    main()
