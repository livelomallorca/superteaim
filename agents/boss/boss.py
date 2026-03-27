"""
Boss Agent. Receives requests, creates task plans, dispatches to sub-agents via Redis.
Collects results, reviews, and delivers the final answer.

Supports parallel tasks (depends_on: null) and sequential tasks (depends_on: index).
v0.3: heartbeat scheduling, approval gates, autonomy view, dashboard.
"""
import os
import json
import time
import uuid
import hmac
import logging
import threading
from datetime import datetime, timezone
import redis
import httpx
import psycopg2
from flask import Flask, request, jsonify, render_template

log = logging.getLogger("boss")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [boss] %(message)s")

LITELLM_URL = os.environ["LITELLM_URL"]
REDIS_URL = os.environ["REDIS_URL"]
POSTGRES_URL = os.environ.get("POSTGRES_URL", "")
BOSS_MODEL = os.environ.get("BOSS_MODEL", "fast")
BOSS_API_KEY = os.environ.get("BOSS_API_KEY", "")
BOSS_EXTERNAL_URL = os.environ.get("BOSS_EXTERNAL_URL", "http://localhost:8080")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
VAULT_KEYWORDS = [k.strip() for k in os.environ.get(
    "VAULT_KEYWORDS",
    "password,credential,secret,private,confidential,personal,financial"
).split(",") if k.strip()]

rdb = redis.from_url(REDIS_URL, decode_responses=True)

BOSS_SYSTEM = """You are the Boss Agent managing an AI team.

You have sub-agents available (each has a Redis queue):
- researcher: research, document analysis, web search (queue: tasks:researcher)
- writer: emails, reports, proposals, content (queue: tasks:writer)
- coder: code, scripts, automation, debugging (queue: tasks:coder)
- analyst: data analysis, financial review, trends (queue: tasks:analyst)
- ops: system health, file management, scheduling (queue: tasks:ops)
- worker: general-purpose fallback (queue: tasks:worker)

When a request comes in:
1. Classify data as VAULT (sensitive) or LIBRARY (public)
2. Break into subtasks if needed
3. Assign each subtask to the right agent

Respond with a JSON plan:
{
  "tasks": [
    {"agent": "analyst", "prompt": "...", "data_zone": "vault", "depends_on": null},
    {"agent": "writer", "prompt": "...", "data_zone": "vault", "depends_on": 0}
  ]
}

depends_on: index of a task that must complete first (null = can start immediately).
data_zone: "vault" for sensitive data, "library" for public.

For simple questions that don't need delegation, return: {"tasks": []}
"""


# ── Helpers ──────────────────────────────────────────────────

def _get_db():
    """Get a PostgreSQL connection."""
    return psycopg2.connect(POSTGRES_URL)


def classify_zone(prompt: str) -> str:
    """Return 'vault' if prompt contains sensitive keywords, else 'library'."""
    pl = prompt.lower()
    return "vault" if any(k in pl for k in VAULT_KEYWORDS) else "library"


def send_telegram_alert(message: str):
    """Send a Telegram message (if configured) or just log."""
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


def _send_approval_request(task_id: str, agent: str, prompt: str):
    """Notify that a VAULT task is pending approval."""
    msg = (
        f"[VAULT APPROVAL NEEDED]\n"
        f"Task ID: {task_id}\n"
        f"Agent: {agent}\n"
        f"Prompt: {prompt[:200]}\n"
        f"Approve: POST {BOSS_EXTERNAL_URL}/tasks/{task_id}/approve\n"
        f"Reject:  POST {BOSS_EXTERNAL_URL}/tasks/{task_id}/reject"
    )
    send_telegram_alert(msg)


def _log_task_to_db(task_id: str, agent_name: str, prompt: str,
                    status: str, org_id: int = None, data_zone: str = "library"):
    """Write a task record to PostgreSQL."""
    if not POSTGRES_URL:
        return
    try:
        conn = _get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO agent_tasks (agent_name, task_description, status, data_zone, org_id)
            VALUES (%s, %s, %s, %s, %s)
        """, (agent_name, prompt[:500], status, data_zone, org_id or 1))
        conn.commit()
        conn.close()
    except Exception as e:
        log.error(f"DB task log failed: {e}")


# ── Core Pipeline ─────────────────────────────────────────────

def plan_tasks(user_request: str) -> list[dict]:
    """Ask the Boss model to create a task plan."""
    with httpx.Client(timeout=60) as client:
        resp = client.post(f"{LITELLM_URL}/chat/completions", json={
            "model": BOSS_MODEL,
            "messages": [
                {"role": "system", "content": BOSS_SYSTEM},
                {"role": "user", "content": user_request}
            ],
            "response_format": {"type": "json_object"}
        })
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        plan = json.loads(content)
        return plan.get("tasks", [])


def dispatch_task(task: dict, task_id: str, context: str = "",
                  org_id: int = None, project_goal: str = "",
                  source: str = "api") -> bool:
    """Push a task to the right agent's Redis queue.

    Returns True if the task was held for approval (vault + api source).
    source='heartbeat' bypasses the approval gate (admin pre-approved).
    source='approved' bypasses the approval gate (human already approved).
    """
    data_zone = task.get("data_zone", "library")

    # Approval gate: VAULT tasks from API requests need human sign-off
    if data_zone == "vault" and source == "api":
        rdb.hset(f"task:{task_id}", mapping={
            "status": "pending_approval",
            "agent": task["agent"],
            "prompt": task["prompt"][:500],
            "data_zone": data_zone,
            "org_id": str(org_id or 1),
            "project_goal": project_goal,
            "context": context,
            "created_at": datetime.now(timezone.utc).isoformat()
        })
        rdb.expire(f"task:{task_id}", 86400 * 7)  # 7-day TTL
        _log_task_to_db(task_id, task["agent"], task["prompt"],
                        "pending_approval", org_id, data_zone)
        _send_approval_request(task_id, task["agent"], task["prompt"])
        log.info(f"Task {task_id} held for approval (vault zone)")
        return True

    # Normal dispatch
    queue = f"tasks:{task['agent']}"
    task_data = {
        "task_id": task_id,
        "prompt": task["prompt"],
        "data_zone": data_zone,
        "context": context,
        "org_id": org_id,
        "project_goal": project_goal
    }
    rdb.lpush(queue, json.dumps(task_data))
    rdb.hset(f"task:{task_id}", mapping={
        "status": "queued",
        "agent": task["agent"],
        "prompt": task["prompt"][:200],
        "created_at": datetime.now(timezone.utc).isoformat()
    })
    log.info(f"Dispatched task {task_id} → {queue}")
    return False


def wait_for_result(task_id: str, timeout: int = 600) -> dict:
    """Poll Redis for a task result."""
    start = time.time()
    while time.time() - start < timeout:
        status = rdb.hget(f"task:{task_id}", "status")
        if status in ("completed", "failed"):
            return {
                "status": status,
                "result": rdb.hget(f"task:{task_id}", "result") or "",
                "error": rdb.hget(f"task:{task_id}", "error") or ""
            }
        time.sleep(2)
    return {"status": "timeout", "result": "", "error": f"No response after {timeout}s"}


def execute_plan(tasks: list[dict], org_id: int = None,
                 project_goal: str = "") -> str:
    """Execute a task plan, respecting dependencies.

    Tasks with depends_on=null can run in parallel (dispatched immediately).
    Tasks with depends_on=N wait for task N to complete first.
    """
    results = {}
    task_ids = {}
    held = set()

    # Group tasks: independent (no deps) vs dependent
    independent = [(i, t) for i, t in enumerate(tasks) if t.get("depends_on") is None]
    dependent = [(i, t) for i, t in enumerate(tasks) if t.get("depends_on") is not None]

    # Dispatch all independent tasks at once
    for i, task in independent:
        task_id = f"boss-{uuid.uuid4().hex[:8]}"
        task_ids[i] = task_id
        was_held = dispatch_task(task, task_id, org_id=org_id, project_goal=project_goal)
        if was_held:
            held.add(i)

    # Collect results for independent tasks
    for i, task in independent:
        if i in held:
            task_id = task_ids[i]
            results[i] = {
                "status": "pending_approval",
                "result": f"Task held for approval (id: {task_id}). "
                          f"Approve via: POST /tasks/{task_id}/approve",
                "error": ""
            }
        else:
            results[i] = wait_for_result(task_ids[i])
            if results[i]["status"] == "failed":
                log.error(f"Task {i} ({task['agent']}) failed: {results[i]['error']}")

    # Process dependent tasks in order
    for i, task in dependent:
        task_id = f"boss-{uuid.uuid4().hex[:8]}"
        task_ids[i] = task_id

        context = ""
        dep_idx = task["depends_on"]
        if dep_idx in results and results[dep_idx]["status"] == "completed":
            context = results[dep_idx]["result"]

        was_held = dispatch_task(task, task_id, context, org_id=org_id,
                                 project_goal=project_goal)
        if was_held:
            results[i] = {
                "status": "pending_approval",
                "result": f"Task held for approval (id: {task_id}). "
                          f"Approve via: POST /tasks/{task_id}/approve",
                "error": ""
            }
        else:
            results[i] = wait_for_result(task_id)
            if results[i]["status"] == "failed":
                log.error(f"Task {i} ({task['agent']}) failed: {results[i]['error']}")

    # Collect final output
    final_parts = []
    for i, task in enumerate(tasks):
        r = results.get(i, {})
        if r.get("status") == "completed":
            final_parts.append(f"**{task['agent'].title()}:** {r['result']}")
        elif r.get("status") == "pending_approval":
            final_parts.append(f"**{task['agent'].title()}:** {r['result']}")
        elif r.get("status") == "failed":
            final_parts.append(f"**{task['agent'].title()}:** [Failed: {r.get('error', 'unknown')}]")

    return "\n\n---\n\n".join(final_parts)


def handle_request(user_message: str, org_id: int = None,
                   project_goal: str = "") -> str:
    """Full pipeline: plan → dispatch → collect → deliver."""
    log.info(f"Request: {user_message[:80]}...")

    # Step 1: Plan
    try:
        tasks = plan_tasks(user_message)
    except Exception as e:
        log.error(f"Planning failed: {e}")
        return "Planning failed due to an internal error."

    if not tasks:
        # Simple question — Boss answers directly
        try:
            with httpx.Client(timeout=60) as client:
                resp = client.post(f"{LITELLM_URL}/chat/completions", json={
                    "model": BOSS_MODEL,
                    "messages": [{"role": "user", "content": user_message}]
                })
                return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            log.error(f"Direct answer failed: {e}")
            return "Failed to process request due to an internal error."

    log.info(f"Plan: {len(tasks)} tasks → {[t['agent'] for t in tasks]}")

    # Step 2: Execute
    result = execute_plan(tasks, org_id=org_id, project_goal=project_goal)
    return result


# ── Heartbeat Scheduler ───────────────────────────────────────

def heartbeat_loop():
    """Poll scheduled_tasks every 30s and dispatch due tasks."""
    time.sleep(15)  # Wait for services to start
    while True:
        try:
            if POSTGRES_URL:
                conn = _get_db()
                cur = conn.cursor()
                cur.execute("""
                    SELECT id, agent_name, prompt, org_id, data_zone, interval_minutes
                    FROM scheduled_tasks
                    WHERE enabled = true
                      AND (next_run IS NULL OR next_run <= NOW())
                """)
                due = cur.fetchall()
                for row in due:
                    sched_id, agent, prompt, org_id, data_zone, interval_min = row
                    task_id = f"hb-{uuid.uuid4().hex[:8]}"
                    task = {
                        "agent": agent,
                        "prompt": prompt,
                        "data_zone": data_zone or "library"
                    }
                    dispatch_task(task, task_id, org_id=org_id, source="heartbeat")
                    cur.execute("""
                        UPDATE scheduled_tasks
                        SET next_run = NOW() + (%s * INTERVAL '1 minute'),
                            last_run = NOW(),
                            run_count = COALESCE(run_count, 0) + 1
                        WHERE id = %s
                    """, (interval_min, sched_id))
                    log.info(f"Heartbeat: dispatched {task_id} → {agent}")
                if due:
                    conn.commit()
                conn.close()
        except Exception as e:
            log.error(f"Heartbeat loop error: {e}")
        time.sleep(30)


# ── Flask API ────────────────────────────────────────────────
flask_app = Flask(__name__)
flask_app.config['MAX_CONTENT_LENGTH'] = 1 * 1024 * 1024  # 1MB


def _require_auth():
    """Check Bearer token. Returns error response tuple or None if OK."""
    if not BOSS_API_KEY:
        return jsonify({"error": "BOSS_API_KEY not configured"}), 500
    auth = request.headers.get("Authorization", "")
    if not hmac.compare_digest(auth, f"Bearer {BOSS_API_KEY}"):
        return jsonify({"error": "Unauthorized"}), 401
    return None


@flask_app.route("/health")
def health():
    return "ok", 200


@flask_app.route("/request", methods=["POST"])
def api_request():
    """HTTP endpoint for external clients. Requires Bearer token."""
    err = _require_auth()
    if err:
        return err
    data = request.json
    if not data or "message" not in data:
        return jsonify({"error": "Missing 'message' field"}), 400
    result = handle_request(
        data["message"],
        org_id=data.get("org_id"),
        project_goal=data.get("project_goal", "")
    )
    return jsonify({"result": result})


@flask_app.route("/tasks/recent")
def recent_tasks():
    """Return recent task activity. Requires Bearer token."""
    err = _require_auth()
    if err:
        return err
    if not POSTGRES_URL:
        return jsonify({"error": "No database configured"}), 503
    try:
        conn = _get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT agent_name, task_description, status, started_at, data_zone
            FROM agent_tasks
            ORDER BY started_at DESC
            LIMIT 20
        """)
        rows = cur.fetchall()
        conn.close()
        return jsonify({"tasks": [
            {"agent": r[0], "task": (r[1] or "")[:100], "status": r[2],
             "started": r[3].isoformat() if r[3] else None, "zone": r[4]}
            for r in rows
        ]})
    except Exception as e:
        log.error(f"Failed to fetch recent tasks: {e}")
        return jsonify({"error": "Internal server error"}), 500


@flask_app.route("/tasks/pending")
def pending_tasks():
    """Return tasks waiting for approval. Requires Bearer token."""
    err = _require_auth()
    if err:
        return err
    # Check Redis for pending_approval tasks
    pending = []
    try:
        for key in rdb.scan_iter("task:*"):
            status = rdb.hget(key, "status")
            if status == "pending_approval":
                task_id = key.split(":", 1)[1]
                pending.append({
                    "task_id": task_id,
                    "agent": rdb.hget(key, "agent") or "",
                    "prompt": rdb.hget(key, "prompt") or "",
                    "zone": rdb.hget(key, "data_zone") or "vault",
                    "created_at": rdb.hget(key, "created_at") or ""
                })
    except Exception as e:
        log.error(f"Failed to fetch pending tasks: {e}")
        return jsonify({"error": "Internal server error"}), 500
    return jsonify({"pending": pending, "count": len(pending)})


@flask_app.route("/tasks/<task_id>/approve", methods=["POST"])
def approve_task(task_id):
    """Approve a pending VAULT task and dispatch it. Requires Bearer token."""
    err = _require_auth()
    if err:
        return err
    status = rdb.hget(f"task:{task_id}", "status")
    if status != "pending_approval":
        return jsonify({"error": "Task not found or not pending approval"}), 404

    agent = rdb.hget(f"task:{task_id}", "agent") or "worker"
    prompt = rdb.hget(f"task:{task_id}", "prompt") or ""
    data_zone = rdb.hget(f"task:{task_id}", "data_zone") or "vault"
    context = rdb.hget(f"task:{task_id}", "context") or ""
    project_goal = rdb.hget(f"task:{task_id}", "project_goal") or ""
    org_id_str = rdb.hget(f"task:{task_id}", "org_id")
    try:
        org_id = int(org_id_str) if org_id_str else 1
    except (ValueError, TypeError):
        org_id = 1

    approver = (request.json or {}).get("approved_by", "api")
    task = {"agent": agent, "prompt": prompt, "data_zone": data_zone}
    dispatch_task(task, task_id, context=context, org_id=org_id,
                  project_goal=project_goal, source="approved")

    if POSTGRES_URL:
        try:
            conn = _get_db()
            cur = conn.cursor()
            cur.execute("""
                UPDATE agent_tasks SET approved_by = %s, approved_at = NOW()
                WHERE task_description LIKE %s AND status = 'pending_approval'
            """, (approver, f"{prompt[:100]}%"))
            conn.commit()
            conn.close()
        except Exception as e:
            log.error(f"DB approval update failed: {e}")

    log.info(f"Task {task_id} approved by {approver} and dispatched")
    return jsonify({"approved": task_id, "agent": agent})


@flask_app.route("/tasks/<task_id>/reject", methods=["POST"])
def reject_task(task_id):
    """Reject a pending VAULT task. Requires Bearer token."""
    err = _require_auth()
    if err:
        return err
    status = rdb.hget(f"task:{task_id}", "status")
    if status != "pending_approval":
        return jsonify({"error": "Task not found or not pending approval"}), 404

    rdb.hset(f"task:{task_id}", "status", "rejected")
    reason = (request.json or {}).get("reason", "")
    log.info(f"Task {task_id} rejected. Reason: {reason}")
    return jsonify({"rejected": task_id, "reason": reason})


@flask_app.route("/schedule", methods=["GET"])
def list_schedule():
    """List scheduled tasks. Requires Bearer token."""
    err = _require_auth()
    if err:
        return err
    if not POSTGRES_URL:
        return jsonify({"error": "No database configured"}), 503
    try:
        conn = _get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, agent_name, prompt, data_zone, interval_minutes,
                   enabled, last_run, next_run, run_count, notes
            FROM scheduled_tasks ORDER BY id
        """)
        rows = cur.fetchall()
        conn.close()
        return jsonify({"schedule": [
            {"id": r[0], "agent": r[1], "prompt": (r[2] or "")[:100],
             "zone": r[3], "interval_minutes": r[4], "enabled": r[5],
             "last_run": r[6].isoformat() if r[6] else None,
             "next_run": r[7].isoformat() if r[7] else None,
             "run_count": r[8], "notes": r[9]}
            for r in rows
        ]})
    except Exception as e:
        log.error(f"Failed to list schedule: {e}")
        return jsonify({"error": "Internal server error"}), 500


@flask_app.route("/schedule", methods=["POST"])
def create_schedule():
    """Create a scheduled task. Requires Bearer token."""
    err = _require_auth()
    if err:
        return err
    if not POSTGRES_URL:
        return jsonify({"error": "No database configured"}), 503
    data = request.json or {}
    if not data.get("agent") or not data.get("prompt") or not data.get("interval_minutes"):
        return jsonify({"error": "Missing required fields: agent, prompt, interval_minutes"}), 400
    try:
        conn = _get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO scheduled_tasks
                (org_id, agent_name, prompt, data_zone, interval_minutes, notes, next_run)
            VALUES (%s, %s, %s, %s, %s, %s, NOW())
            RETURNING id
        """, (
            data.get("org_id", 1), data["agent"], data["prompt"],
            data.get("data_zone", "library"), data["interval_minutes"],
            data.get("notes", "")
        ))
        sched_id = cur.fetchone()[0]
        conn.commit()
        conn.close()
        log.info(f"Scheduled task created: id={sched_id} agent={data['agent']}")
        return jsonify({"id": sched_id, "agent": data["agent"],
                        "interval_minutes": data["interval_minutes"]}), 201
    except Exception as e:
        log.error(f"Failed to create scheduled task: {e}")
        return jsonify({"error": "Internal server error"}), 500


@flask_app.route("/schedule/<int:sched_id>", methods=["DELETE"])
def delete_schedule(sched_id):
    """Delete a scheduled task. Requires Bearer token."""
    err = _require_auth()
    if err:
        return err
    if not POSTGRES_URL:
        return jsonify({"error": "No database configured"}), 503
    try:
        conn = _get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM scheduled_tasks WHERE id = %s RETURNING id", (sched_id,))
        deleted = cur.fetchone()
        conn.commit()
        conn.close()
        if not deleted:
            return jsonify({"error": "Schedule not found"}), 404
        return jsonify({"deleted": sched_id})
    except Exception as e:
        log.error(f"Failed to delete schedule: {e}")
        return jsonify({"error": "Internal server error"}), 500


@flask_app.route("/autonomy")
def get_autonomy():
    """Return current autonomy levels for all agents. Requires Bearer token."""
    err = _require_auth()
    if err:
        return err
    if not POSTGRES_URL:
        return jsonify({"error": "No database configured"}), 503
    try:
        conn = _get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT agent_name, current_level, total_tasks, error_count,
                   consecutive_successes, promoted_at, last_review
            FROM agent_autonomy ORDER BY agent_name
        """)
        rows = cur.fetchall()
        conn.close()
        return jsonify({"autonomy": [
            {"agent": r[0], "level": r[1], "total_tasks": r[2],
             "error_count": r[3], "consecutive_successes": r[4],
             "promoted_at": r[5].isoformat() if r[5] else None,
             "last_review": r[6].isoformat() if r[6] else None}
            for r in rows
        ]})
    except Exception as e:
        log.error(f"Failed to fetch autonomy: {e}")
        return jsonify({"error": "Internal server error"}), 500


@flask_app.route("/budgets")
def get_budgets():
    """Return budget usage for all agents. Requires Bearer token."""
    err = _require_auth()
    if err:
        return err
    if not POSTGRES_URL:
        return jsonify({"error": "No database configured"}), 503
    try:
        conn = _get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT agent_name, daily_token_limit, daily_tokens_used,
                   daily_api_dollar_limit, daily_api_dollars_used, reset_at
            FROM agent_budgets ORDER BY agent_name
        """)
        rows = cur.fetchall()
        conn.close()
        return jsonify({"budgets": [
            {"agent": r[0], "token_limit": r[1], "tokens_used": r[2],
             "dollar_limit": float(r[3]), "dollars_used": float(r[4]),
             "reset_at": r[5].isoformat() if r[5] else None}
            for r in rows
        ]})
    except Exception as e:
        log.error(f"Failed to fetch budgets: {e}")
        return jsonify({"error": "Internal server error"}), 500


@flask_app.route("/status")
def get_status():
    """Aggregate status for dashboard. Requires Bearer token."""
    err = _require_auth()
    if err:
        return err
    if not POSTGRES_URL:
        return jsonify({"error": "No database configured"}), 503
    try:
        conn = _get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT agent_name, status, total_tasks, total_errors
            FROM agent_registry ORDER BY agent_name
        """)
        agents = [{"agent": r[0], "status": r[1], "total_tasks": r[2],
                   "total_errors": r[3]} for r in cur.fetchall()]

        cur.execute("""
            SELECT agent_name, task_description, status, started_at, data_zone
            FROM agent_tasks ORDER BY started_at DESC LIMIT 20
        """)
        recent = [{"agent": r[0], "task": (r[1] or "")[:100], "status": r[2],
                   "started": r[3].isoformat() if r[3] else None, "zone": r[4]}
                  for r in cur.fetchall()]

        cur.execute("""
            SELECT agent_name, daily_token_limit, daily_tokens_used,
                   daily_api_dollar_limit, daily_api_dollars_used
            FROM agent_budgets ORDER BY agent_name
        """)
        budgets = [{"agent": r[0], "token_limit": r[1], "tokens_used": r[2],
                    "dollar_limit": float(r[3]), "dollars_used": float(r[4])}
                   for r in cur.fetchall()]

        cur.execute("""
            SELECT agent_name, current_level, total_tasks, error_count, promoted_at
            FROM agent_autonomy ORDER BY agent_name
        """)
        autonomy = [{"agent": r[0], "level": r[1], "total_tasks": r[2],
                     "error_count": r[3],
                     "promoted_at": r[4].isoformat() if r[4] else None}
                    for r in cur.fetchall()]

        cur.execute("""
            SELECT id, agent_name, prompt, interval_minutes, last_run, next_run, run_count
            FROM scheduled_tasks WHERE enabled = true ORDER BY next_run NULLS FIRST LIMIT 10
        """)
        schedule = [{"id": r[0], "agent": r[1], "prompt": (r[2] or "")[:80],
                     "interval_minutes": r[3],
                     "last_run": r[4].isoformat() if r[4] else None,
                     "next_run": r[5].isoformat() if r[5] else None,
                     "run_count": r[6]}
                    for r in cur.fetchall()]

        conn.close()

        # Pending approvals from Redis
        pending = []
        for key in rdb.scan_iter("task:*"):
            if rdb.hget(key, "status") == "pending_approval":
                task_id = key.split(":", 1)[1]
                pending.append({
                    "task_id": task_id,
                    "agent": rdb.hget(key, "agent") or "",
                    "prompt": (rdb.hget(key, "prompt") or "")[:100],
                    "created_at": rdb.hget(key, "created_at") or ""
                })

        return jsonify({
            "agents": agents,
            "recent_tasks": recent,
            "budgets": budgets,
            "autonomy": autonomy,
            "schedule": schedule,
            "pending": pending,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
    except Exception as e:
        log.error(f"Failed to fetch status: {e}")
        return jsonify({"error": "Internal server error"}), 500


@flask_app.route("/dashboard")
def dashboard():
    """Serve the operator dashboard HTML page."""
    return render_template("dashboard.html")


@flask_app.route("/config/snapshot", methods=["POST"])
def config_snapshot():
    """Save a snapshot of current runtime config. Requires Bearer token."""
    err = _require_auth()
    if err:
        return err
    if not POSTGRES_URL:
        return jsonify({"error": "No database configured"}), 503
    try:
        conn = _get_db()
        cur = conn.cursor()
        cur.execute("SELECT json_agg(row_to_json(t)) FROM agent_budgets t")
        budgets = cur.fetchone()[0] or []
        cur.execute("SELECT json_agg(row_to_json(t)) FROM agent_autonomy t")
        autonomy = cur.fetchone()[0] or []
        cur.execute("SELECT json_agg(row_to_json(t)) FROM agent_registry t")
        registry = cur.fetchone()[0] or []
        snapshot = {"budgets": budgets, "autonomy": autonomy, "registry": registry}
        data = request.json or {}
        label = data.get("label", "manual")
        cur.execute(
            "INSERT INTO config_snapshots (label, snapshot) VALUES (%s, %s) RETURNING id",
            (label, json.dumps(snapshot))
        )
        snap_id = cur.fetchone()[0]
        conn.commit()
        conn.close()
        log.info(f"Config snapshot saved: id={snap_id} label={label}")
        return jsonify({"id": snap_id, "label": label})
    except Exception as e:
        log.error(f"Config snapshot failed: {e}")
        return jsonify({"error": "Internal server error"}), 500


@flask_app.route("/config/restore/<int:snapshot_id>", methods=["POST"])
def config_restore(snapshot_id):
    """Restore runtime config from a snapshot. Requires Bearer token."""
    err = _require_auth()
    if err:
        return err
    if not POSTGRES_URL:
        return jsonify({"error": "No database configured"}), 503
    try:
        conn = _get_db()
        cur = conn.cursor()
        cur.execute("SELECT snapshot, label FROM config_snapshots WHERE id = %s", (snapshot_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Snapshot not found"}), 404
        snapshot, label = row
        if isinstance(snapshot, str):
            snapshot = json.loads(snapshot)
        for b in snapshot.get("budgets", []):
            cur.execute("""
                UPDATE agent_budgets SET
                    daily_token_limit = %s, daily_api_dollar_limit = %s
                WHERE agent_name = %s
            """, (b["daily_token_limit"], b["daily_api_dollar_limit"], b["agent_name"]))
        for a in snapshot.get("autonomy", []):
            cur.execute("""
                UPDATE agent_autonomy SET current_level = %s WHERE agent_name = %s
            """, (a["current_level"], a["agent_name"]))
        conn.commit()
        conn.close()
        log.info(f"Config restored from snapshot {snapshot_id} ({label})")
        return jsonify({"restored": snapshot_id, "label": label})
    except Exception as e:
        log.error(f"Config restore failed: {e}")
        return jsonify({"error": "Internal server error"}), 500


@flask_app.route("/config/snapshots")
def config_list():
    """List config snapshots. Requires Bearer token."""
    err = _require_auth()
    if err:
        return err
    if not POSTGRES_URL:
        return jsonify({"error": "No database configured"}), 503
    try:
        conn = _get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, label, created_by, created_at
            FROM config_snapshots ORDER BY created_at DESC LIMIT 20
        """)
        rows = cur.fetchall()
        conn.close()
        return jsonify({"snapshots": [
            {"id": r[0], "label": r[1], "created_by": r[2],
             "created_at": r[3].isoformat() if r[3] else None}
            for r in rows
        ]})
    except Exception as e:
        log.error(f"Failed to list snapshots: {e}")
        return jsonify({"error": "Internal server error"}), 500


if __name__ == "__main__":
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    log.info("Heartbeat scheduler started")
    flask_app.run(host="0.0.0.0", port=8080)
