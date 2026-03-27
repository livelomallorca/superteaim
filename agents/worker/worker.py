"""
Generic sub-agent worker. Configured entirely via environment variables.
Pulls tasks from Redis queue, processes via LiteLLM, posts results back.

Intelligence features (configurable via .env):
- Reflection: worker checks its own output before returning
- Memory: worker queries past similar tasks before processing
"""
import os
import time
import json
import logging
from datetime import datetime, timezone
from threading import Thread

import redis
import httpx
from flask import Flask

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
log = logging.getLogger(os.environ.get("AGENT_NAME", "worker"))

# ── Config from environment ──────────────────────────────────
AGENT_NAME = os.environ["AGENT_NAME"]
AGENT_ROLE = os.environ["AGENT_ROLE"]
AGENT_GOAL = os.environ["AGENT_GOAL"]
AGENT_MODEL = os.environ["AGENT_MODEL"]
AGENT_QUEUE = os.environ["AGENT_QUEUE"]
LITELLM_URL = os.environ["LITELLM_URL"]
REDIS_URL = os.environ["REDIS_URL"]
POSTGRES_URL = os.environ.get("POSTGRES_URL", "")
TASK_TIMEOUT = int(os.environ.get("TASK_TIMEOUT", "300"))

# Intelligence toggles
ENABLE_REFLECTION = os.environ.get("ENABLE_REFLECTION", "true").lower() == "true"
MAX_REASONING_STEPS = int(os.environ.get("MAX_REASONING_STEPS", "2"))
ENABLE_MEMORY = os.environ.get("ENABLE_MEMORY", "true").lower() == "true"
MEMORY_LOOKBACK_HOURS = int(os.environ.get("MEMORY_LOOKBACK_HOURS", "72"))

AGENT_PERSONA_FILE = os.environ.get("AGENT_PERSONA_FILE", "")


def load_system_prompt() -> str:
    """Load system prompt from persona file if available, else use default."""
    if AGENT_PERSONA_FILE:
        try:
            with open(AGENT_PERSONA_FILE) as f:
                persona = f.read()
            log.info(f"Loaded persona from {AGENT_PERSONA_FILE}")
            return persona
        except FileNotFoundError:
            log.warning(f"Persona file not found: {AGENT_PERSONA_FILE}, using default")
    return f"""You are {AGENT_ROLE} in an AI team.
Your goal: {AGENT_GOAL}
Be concise. Be accurate. If unsure, say so.
Respond in the same language the user uses."""


SYSTEM_PROMPT = load_system_prompt()

# ── Redis connection ─────────────────────────────────────────
rdb = redis.from_url(REDIS_URL, decode_responses=True)

# ── Health check endpoint ────────────────────────────────────
app = Flask(__name__)
last_heartbeat = time.time()


@app.route("/health")
def health():
    age = time.time() - last_heartbeat
    if age > TASK_TIMEOUT + 60:
        return "stale", 503
    return "ok", 200


def run_health_server():
    app.run(host="0.0.0.0", port=8080, debug=False)  # nosemgrep: avoid_app_run_with_bad_host  # binds inside Docker container, not host


# ── Memory: query past similar tasks ─────────────────────────
def recall_similar_tasks(prompt: str) -> str:
    """Query PostgreSQL for past similar tasks to use as context."""
    if not ENABLE_MEMORY or not POSTGRES_URL:
        return ""
    try:
        import psycopg2
        conn = psycopg2.connect(POSTGRES_URL)
        cur = conn.cursor()
        # Simple keyword match — find past completed tasks with overlapping words
        keywords = [w.lower() for w in prompt.split() if len(w) > 4][:5]
        if not keywords:
            conn.close()
            return ""
        conditions = " OR ".join(
            ["task_description ILIKE %s"] * len(keywords)
        )
        params = [f"%{kw}%" for kw in keywords]
        # MEMORY_LOOKBACK_HOURS is validated as int() at startup
        params.append(MEMORY_LOOKBACK_HOURS)
        query = (
            "SELECT agent_name, task_description, status, completed_at "
            "FROM agent_tasks "
            "WHERE status = 'completed' "
            "  AND started_at > NOW() - make_interval(hours => %s) "
            "  AND (" + conditions + ") "
            "ORDER BY completed_at DESC LIMIT 3"
        )
        cur.execute(query, params)  # nosemgrep: psycopg-sqli, sqlalchemy-execute-raw-query  # all values are %s-parameterized
        rows = cur.fetchall()
        conn.close()
        if not rows:
            return ""
        memory_lines = ["Past similar tasks:"]
        for agent, desc, status, completed in rows:
            memory_lines.append(f"- [{agent}] {desc[:100]} ({status})")
        return "\n".join(memory_lines)
    except Exception as e:
        log.warning(f"Memory recall failed: {e}")
        return ""


# ── Reflection: self-check before returning ──────────────────
def reflect_on_output(prompt: str, output: str) -> str:
    """Ask the model to check its own work. Returns improved output or original."""
    if not ENABLE_REFLECTION:
        return output

    reflection_prompt = f"""Review your previous response for accuracy and completeness.

Original task: {prompt[:500]}
Your response: {output[:1000]}

If the response is good, reply with exactly: APPROVED
If it needs improvement, provide the improved response directly (no explanation)."""

    try:
        with httpx.Client(timeout=TASK_TIMEOUT) as client:
            resp = client.post(f"{LITELLM_URL}/chat/completions", json={
                "model": AGENT_MODEL,
                "messages": [
                    {"role": "system", "content": "You are a quality reviewer. Be brief."},
                    {"role": "user", "content": reflection_prompt}
                ]
            })
            resp.raise_for_status()
            review = resp.json()["choices"][0]["message"]["content"].strip()
            if review == "APPROVED" or review.startswith("APPROVED"):
                return output
            return review
    except Exception as e:
        log.warning(f"Reflection failed: {e}")
        return output


# ── Budget enforcement ────────────────────────────────────────
def check_budget() -> bool:
    """Check if this agent has budget remaining. Returns True if OK."""
    if not POSTGRES_URL:
        return True
    try:
        import psycopg2
        conn = psycopg2.connect(POSTGRES_URL)
        cur = conn.cursor()
        cur.execute("""
            SELECT daily_token_limit, daily_tokens_used, reset_at
            FROM agent_budgets WHERE agent_name = %s
            ORDER BY id LIMIT 1
        """, (AGENT_NAME,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return True
        limit, used, reset_at = row
        # Reset if past reset time
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        if reset_at and now > reset_at.replace(tzinfo=timezone.utc):
            cur.execute("""
                UPDATE agent_budgets
                SET daily_tokens_used = 0,
                    daily_api_dollars_used = 0,
                    reset_at = NOW() + INTERVAL '24 hours'
                WHERE agent_name = %s
            """, (AGENT_NAME,))
            conn.commit()
            conn.close()
            return True
        conn.close()
        if used >= limit:
            log.warning(f"Budget exhausted: {used}/{limit} tokens used")
            return False
        return True
    except Exception as e:
        log.warning(f"Budget check failed: {e}")
        return True  # fail open — don't block on DB errors


def update_budget(tokens_in: int, tokens_out: int):
    """Update token usage after a task completes."""
    if not POSTGRES_URL:
        return
    try:
        import psycopg2
        conn = psycopg2.connect(POSTGRES_URL)
        cur = conn.cursor()
        cur.execute("""
            UPDATE agent_budgets
            SET daily_tokens_used = daily_tokens_used + %s
            WHERE agent_name = %s
        """, (tokens_in + tokens_out, AGENT_NAME))
        conn.commit()
        conn.close()
    except Exception as e:
        log.warning(f"Budget update failed: {e}")


# ── Task processing ──────────────────────────────────────────
def process_task(task_data: dict) -> dict:
    """Process a single task. Returns result dict."""
    global last_heartbeat
    last_heartbeat = time.time()

    task_id = task_data["task_id"]
    prompt = task_data["prompt"]
    context = task_data.get("context", "")
    data_zone = task_data.get("data_zone", "library")

    # Budget gate
    if not check_budget():
        log.warning(f"Task {task_id}: rejected — budget exhausted")
        return {"status": "failed", "error": "Agent budget exhausted",
                "tokens_in": 0, "tokens_out": 0}

    log.info(f"Task {task_id}: starting — {prompt[:60]}...")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
    ]

    # Add memory context from past similar tasks
    memory = recall_similar_tasks(prompt)
    if memory:
        messages.append({
            "role": "system",
            "content": memory
        })

    # Add project goal context (goal ancestry)
    project_goal = task_data.get("project_goal", "")
    if project_goal:
        messages.append({
            "role": "system",
            "content": f"Project goal: {project_goal}"
        })

    # Add context from previous agent if this is a chained task
    if context:
        messages.append({
            "role": "system",
            "content": f"Context from previous step:\n{context}"
        })

    messages.append({"role": "user", "content": prompt})

    # Call LiteLLM
    try:
        with httpx.Client(timeout=TASK_TIMEOUT) as client:
            resp = client.post(f"{LITELLM_URL}/chat/completions", json={
                "model": AGENT_MODEL,
                "messages": messages,
                "metadata": {"data_zone": data_zone}
            })
            resp.raise_for_status()
            result_data = resp.json()
            result = result_data["choices"][0]["message"]["content"]
            tokens_in = result_data.get("usage", {}).get("prompt_tokens", 0)
            tokens_out = result_data.get("usage", {}).get("completion_tokens", 0)
    except httpx.TimeoutException:
        log.error(f"Task {task_id}: TIMEOUT after {TASK_TIMEOUT}s")
        return {"status": "failed", "error": f"Timeout after {TASK_TIMEOUT}s",
                "tokens_in": 0, "tokens_out": 0}
    except Exception as e:
        log.error(f"Task {task_id}: ERROR — {e}")
        return {"status": "failed", "error": "Task processing failed",
                "tokens_in": 0, "tokens_out": 0}

    # Reflection step: self-check output quality
    if ENABLE_REFLECTION:
        for step in range(MAX_REASONING_STEPS):
            improved = reflect_on_output(prompt, result)
            if improved == result:
                break
            log.info(f"Task {task_id}: reflection step {step + 1} improved output")
            result = improved

    last_heartbeat = time.time()
    log.info(f"Task {task_id}: completed ({tokens_out} tokens)")

    # Track token usage against budget
    update_budget(tokens_in, tokens_out)

    return {
        "status": "completed",
        "result": result,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out
    }


# ── Main loop ────────────────────────────────────────────────
def main():
    log.info(f"Worker '{AGENT_NAME}' starting. Queue: {AGENT_QUEUE}. Model: {AGENT_MODEL}")
    log.info(f"Reflection: {ENABLE_REFLECTION}, Memory: {ENABLE_MEMORY}")

    # Start health check server in background
    Thread(target=run_health_server, daemon=True).start()

    while True:
        # Block-wait for a task from Redis (timeout 5s, then loop to keep heartbeat alive)
        task_raw = rdb.brpop(AGENT_QUEUE, timeout=5)
        if not task_raw:
            update_heartbeat()
            continue

        _, task_json = task_raw
        task_data = json.loads(task_json)
        task_id = task_data["task_id"]

        # Mark as running in Redis
        rdb.hset(f"task:{task_id}", mapping={
            "status": "running",
            "agent": AGENT_NAME,
            "started_at": datetime.now(timezone.utc).isoformat()
        })

        # Process
        result = process_task(task_data)

        # Post result back to Redis
        rdb.hset(f"task:{task_id}", mapping={
            "status": result["status"],
            "result": result.get("result", ""),
            "error": result.get("error", ""),
            "tokens_in": result.get("tokens_in", 0),
            "tokens_out": result.get("tokens_out", 0),
            "completed_at": datetime.now(timezone.utc).isoformat()
        })

        # Notify Boss that task is done
        rdb.lpush("results:boss", json.dumps({
            "task_id": task_id,
            "agent": AGENT_NAME,
            "status": result["status"]
        }))

        # Log to PostgreSQL
        log_to_db(task_data, result)


def update_heartbeat():
    global last_heartbeat
    last_heartbeat = time.time()
    rdb.hset(f"heartbeat:{AGENT_NAME}", "last_seen", str(time.time()))


def log_to_db(task_data, result):
    """Log task to PostgreSQL for Mission Control."""
    if not POSTGRES_URL:
        return
    try:
        import psycopg2
        conn = psycopg2.connect(POSTGRES_URL)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO agent_tasks (agent_name, task_description, data_zone,
                model_used, status, error_message, tokens_in, tokens_out)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            AGENT_NAME, task_data["prompt"][:500], task_data.get("data_zone", "library"),
            AGENT_MODEL, result["status"], result.get("error", ""),
            result.get("tokens_in", 0), result.get("tokens_out", 0)
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        log.error(f"DB log failed: {e}")


if __name__ == "__main__":
    main()
