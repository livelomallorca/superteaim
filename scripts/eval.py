"""
Basic self-evaluation script for superteaim.
Queries agent_tasks table and reports metrics:
- Success rate per agent
- Average duration
- Error patterns
- Token usage and budget status

Usage:
    python scripts/eval.py
    python scripts/eval.py --days 7

Environment variables:
    POSTGRES_URL - PostgreSQL connection string
"""
import os
import sys
import argparse

try:
    import psycopg2
except ImportError:
    print("ERROR: psycopg2 not installed. Run: pip install psycopg2-binary")
    sys.exit(1)

POSTGRES_URL = os.environ.get("POSTGRES_URL")
if not POSTGRES_URL:
    print("ERROR: POSTGRES_URL environment variable not set")
    sys.exit(1)


def run_eval(days: int = 7):
    try:
        conn = psycopg2.connect(POSTGRES_URL)
    except Exception as e:
        print(f"ERROR: Cannot connect to database: {e}")
        sys.exit(1)

    cur = conn.cursor()

    print(f"=== superteaim eval — last {days} days ===\n")

    # Overall stats
    cur.execute("""
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE status = 'completed') AS completed,
            COUNT(*) FILTER (WHERE status = 'failed') AS failed,
            SUM(tokens_in + tokens_out) AS total_tokens
        FROM agent_tasks
        WHERE started_at > NOW() - INTERVAL '%s days'
    """, (days,))
    row = cur.fetchone()
    total, completed, failed, tokens = row
    if total == 0:
        print("No tasks found in this period.")
        conn.close()
        return

    print(f"Total tasks:    {total}")
    print(f"Completed:      {completed} ({100*completed/total:.1f}%)")
    print(f"Failed:         {failed} ({100*failed/total:.1f}%)")
    print(f"Total tokens:   {tokens or 0:,}")
    print()

    # Per-agent breakdown
    print("--- Per Agent ---")
    print(f"{'Agent':<15} {'Tasks':>6} {'OK':>6} {'Fail':>6} {'Err%':>6} {'Avg(s)':>8} {'Tokens':>10}")
    print("-" * 63)

    cur.execute("""
        SELECT
            agent_name,
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE status = 'completed') AS completed,
            COUNT(*) FILTER (WHERE status = 'failed') AS failed,
            ROUND(AVG(EXTRACT(EPOCH FROM (completed_at - started_at)))::numeric, 1) AS avg_dur,
            SUM(tokens_in + tokens_out) AS tokens
        FROM agent_tasks
        WHERE started_at > NOW() - INTERVAL '%s days'
        GROUP BY agent_name
        ORDER BY total DESC
    """, (days,))

    for row in cur.fetchall():
        name, t, c, f, avg_dur, tok = row
        err_pct = 100 * f / t if t > 0 else 0
        print(f"{name:<15} {t:>6} {c:>6} {f:>6} {err_pct:>5.1f}% {avg_dur or 0:>7.1f}s {tok or 0:>10,}")

    print()

    # Top errors
    cur.execute("""
        SELECT agent_name, error_message, COUNT(*) AS cnt
        FROM agent_tasks
        WHERE status = 'failed'
          AND started_at > NOW() - INTERVAL '%s days'
          AND error_message IS NOT NULL
          AND error_message != ''
        GROUP BY agent_name, error_message
        ORDER BY cnt DESC
        LIMIT 5
    """, (days,))

    errors = cur.fetchall()
    if errors:
        print("--- Top Errors ---")
        for agent, msg, cnt in errors:
            print(f"  [{agent}] ({cnt}x) {msg[:80]}")
        print()

    # Budget status
    cur.execute("""
        SELECT agent_name, daily_token_limit, daily_tokens_used,
               daily_api_dollar_limit, daily_api_dollars_used
        FROM agent_budgets
        ORDER BY agent_name
    """)
    budgets = cur.fetchall()
    if budgets:
        print("--- Budget Status ---")
        print(f"{'Agent':<15} {'Tokens Used':>12} {'/ Limit':>12} {'$ Used':>8} {'/ $ Limit':>10}")
        print("-" * 59)
        for name, tok_lim, tok_used, dol_lim, dol_used in budgets:
            print(f"{name:<15} {tok_used:>12,} {tok_lim:>12,} {float(dol_used):>7.2f}$ {float(dol_lim):>9.2f}$")

    conn.close()
    print("\n=== eval complete ===")


def main():
    parser = argparse.ArgumentParser(description="Evaluate agent performance")
    parser.add_argument("--days", type=int, default=7, help="Number of days to analyze")
    args = parser.parse_args()
    run_eval(args.days)


if __name__ == "__main__":
    main()
