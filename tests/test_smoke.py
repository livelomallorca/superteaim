"""
Smoke tests for superteaim.
Validates:
- All Python files parse correctly
- All compose files are valid YAML
- Every ${VAR} in compose files has a match in .env.example
- No hardcoded IPs or passwords in source files
"""
import ast
import os
import re
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ERRORS = []


def error(msg):
    ERRORS.append(msg)
    print(f"  FAIL: {msg}")


def test_python_syntax():
    """All .py files must parse without syntax errors."""
    print("Checking Python syntax...")
    py_files = []
    for dirpath, _, filenames in os.walk(ROOT):
        if ".git" in dirpath or "venv" in dirpath:
            continue
        for f in filenames:
            if f.endswith(".py"):
                py_files.append(os.path.join(dirpath, f))

    for fpath in py_files:
        rel = os.path.relpath(fpath, ROOT)
        try:
            with open(fpath) as f:
                ast.parse(f.read(), filename=rel)
            print(f"  OK: {rel}")
        except SyntaxError as e:
            error(f"{rel}: {e}")


def test_yaml_validity():
    """All compose files must be valid YAML."""
    print("Checking YAML validity...")
    compose_files = [
        "docker-compose.yml",
        "docker-compose.agents.yml",
        "docker-compose.monitoring.yml",
    ]
    for fname in compose_files:
        fpath = os.path.join(ROOT, fname)
        if not os.path.exists(fpath):
            error(f"{fname}: file not found")
            continue
        try:
            with open(fpath) as f:
                yaml.safe_load(f)
            print(f"  OK: {fname}")
        except yaml.YAMLError as e:
            error(f"{fname}: {e}")

    # Also check config YAML files
    config_yamls = ["config/prometheus.yml", "config/litellm_config.example.yaml"]
    for fname in config_yamls:
        fpath = os.path.join(ROOT, fname)
        if not os.path.exists(fpath):
            continue
        try:
            with open(fpath) as f:
                yaml.safe_load(f)
            print(f"  OK: {fname}")
        except yaml.YAMLError as e:
            error(f"{fname}: {e}")


def test_env_completeness():
    """Every ${VAR} in compose files must exist in .env.example."""
    print("Checking .env.example completeness...")
    env_path = os.path.join(ROOT, "config", ".env.example")
    if not os.path.exists(env_path):
        error(".env.example not found")
        return

    with open(env_path) as f:
        env_content = f.read()

    # Extract all VAR=... definitions (ignore comments)
    env_vars = set()
    for line in env_content.split("\n"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            var = line.split("=")[0].strip()
            env_vars.add(var)

    # Find all ${VAR} and ${VAR:-default} references in compose files
    compose_files = [
        "docker-compose.yml",
        "docker-compose.agents.yml",
        "docker-compose.monitoring.yml",
    ]
    var_pattern = re.compile(r'\$\{([A-Z_]+?)(?::-[^}]*)?\}')

    missing = set()
    for fname in compose_files:
        fpath = os.path.join(ROOT, fname)
        if not os.path.exists(fpath):
            continue
        with open(fpath) as f:
            content = f.read()
        refs = var_pattern.findall(content)
        for ref in refs:
            if ref not in env_vars:
                missing.add(ref)

    if missing:
        for var in sorted(missing):
            error(f"${{{var}}} used in compose but missing from .env.example")
    else:
        print(f"  OK: All compose variables found in .env.example ({len(env_vars)} vars)")


def test_no_hardcoded_secrets():
    """No hardcoded IPs, passwords, or credentials in source files."""
    print("Checking for hardcoded secrets...")

    # Patterns that should NOT appear in source (except in examples/comments)
    ip_pattern = re.compile(r'192\.168\.\d+\.\d+')
    password_patterns = [
        re.compile(r'password\s*=\s*["\'][^$][^"\']+["\']', re.IGNORECASE),
        re.compile(r'api_key\s*[:=]\s*["\']sk-[^"\']+["\']'),
    ]

    # Files to check (exclude .env.example, docs, tests, git)
    skip_dirs = {".git", "venv", "__pycache__", "docs", "plugins"}
    skip_files = {".env.example", "test_smoke.py", "STANDARDS.md", "CONTRIBUTING.md", "README.md"}

    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for fname in filenames:
            if fname in skip_files:
                continue
            if not fname.endswith((".py", ".yml", ".yaml", ".sh")):
                continue
            fpath = os.path.join(dirpath, fname)
            rel = os.path.relpath(fpath, ROOT)
            with open(fpath) as f:
                content = f.read()

            # Check for hardcoded IPs
            ips = ip_pattern.findall(content)
            if ips:
                error(f"{rel}: hardcoded IP(s) found: {', '.join(set(ips))}")

            # Check for hardcoded passwords (but allow ${VAR} patterns)
            for pat in password_patterns:
                matches = pat.findall(content)
                if matches:
                    error(f"{rel}: possible hardcoded credential: {matches[0][:40]}...")

    if not any("hardcoded" in e for e in ERRORS):
        print("  OK: No hardcoded IPs or credentials found")


def test_docker_images_pinned():
    """All Docker images must use pinned versions, not :latest or :main."""
    print("Checking Docker image pinning...")
    compose_files = [
        "docker-compose.yml",
        "docker-compose.agents.yml",
        "docker-compose.monitoring.yml",
    ]
    floating_pattern = re.compile(r'image:\s*\S+:(latest|main)\s*$', re.MULTILINE)
    bare_pattern = re.compile(r'image:\s*([a-z][a-z0-9._/-]+)\s*$', re.MULTILINE)

    for fname in compose_files:
        fpath = os.path.join(ROOT, fname)
        if not os.path.exists(fpath):
            continue
        with open(fpath) as f:
            content = f.read()
        for m in floating_pattern.finditer(content):
            error(f"{fname}: floating tag found: {m.group(0).strip()}")
        for m in bare_pattern.finditer(content):
            # bare image name with no tag at all
            img = m.group(1)
            if "/" in img or img in ("postgres", "redis", "caddy", "grafana"):
                error(f"{fname}: unpinned image (no tag): {img}")

    if not any("floating tag" in e or "unpinned image" in e for e in ERRORS):
        print("  OK: All Docker images are pinned")


def test_python_deps_pinned():
    """All Python dependencies must use == pinning."""
    print("Checking Python dependency pinning...")
    req_files = []
    for dirpath, _, filenames in os.walk(ROOT):
        if ".git" in dirpath or "venv" in dirpath:
            continue
        for f in filenames:
            if f == "requirements.txt":
                req_files.append(os.path.join(dirpath, f))

    for fpath in req_files:
        rel = os.path.relpath(fpath, ROOT)
        with open(fpath) as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("-"):
                    continue
                if "==" not in line:
                    error(f"{rel}:{lineno}: unpinned dependency: {line}")

    if not any("unpinned dependency" in e for e in ERRORS):
        print("  OK: All Python dependencies are pinned with ==")


def test_no_default_creds():
    """No default credentials like 'changeme', 'sk-changeme', 'admin' as values in .env.example."""
    print("Checking for default credentials in .env.example...")
    env_path = os.path.join(ROOT, "config", ".env.example")
    if not os.path.exists(env_path):
        error(".env.example not found")
        return

    bad_defaults = {"changeme", "sk-changeme", "admin", "password", "secret", "test"}
    with open(env_path) as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.strip().strip("'\"")
            if value.lower() in bad_defaults:
                error(f".env.example:{lineno}: default credential for {key.strip()}: {value}")

    if not any("default credential" in e for e in ERRORS):
        print("  OK: No default credentials in .env.example")


def test_auth_on_endpoints():
    """All Flask routes (except /health) must check auth."""
    print("Checking auth on endpoints...")
    boss_path = os.path.join(ROOT, "agents", "boss", "boss.py")
    if not os.path.exists(boss_path):
        error("agents/boss/boss.py not found")
        return

    with open(boss_path) as f:
        content = f.read()

    # Find all route decorators and their function bodies
    route_pattern = re.compile(
        r'@flask_app\.route\(["\']([^"\']+)["\'].*?\)\s*\ndef\s+(\w+)\(.*?\):\s*\n(.*?)(?=\n(?:@|def |if __name__|$))',
        re.DOTALL
    )
    for m in route_pattern.finditer(content):
        path, func_name, body = m.group(1), m.group(2), m.group(3)
        if path in ("/health", "/dashboard"):
            continue
        if ("hmac.compare_digest" not in body
                and "Authorization" not in body
                and "_require_auth" not in body):
            error(f"boss.py: route {path} ({func_name}) has no auth check")

    if not any("no auth check" in e for e in ERRORS):
        print("  OK: All non-health endpoints have auth")


def test_no_error_leaks():
    """No str(e) in return/jsonify statements in agent Python files."""
    print("Checking for error detail leaks...")
    agent_dirs = ["agents/boss", "agents/worker", "agents/watchdog"]
    leak_pattern = re.compile(r'(return|jsonify).*str\(e\)')

    for agent_dir in agent_dirs:
        dirpath = os.path.join(ROOT, agent_dir)
        if not os.path.isdir(dirpath):
            continue
        for fname in os.listdir(dirpath):
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(dirpath, fname)
            rel = os.path.relpath(fpath, ROOT)
            with open(fpath) as f:
                for lineno, line in enumerate(f, 1):
                    if leak_pattern.search(line):
                        error(f"{rel}:{lineno}: error detail leak: {line.strip()[:80]}")

    if not any("error detail leak" in e for e in ERRORS):
        print("  OK: No error details leaked in responses")


def test_redis_healthcheck_auth():
    """Redis healthcheck must include -a password flag."""
    print("Checking Redis healthcheck auth...")
    compose_files = [
        "docker-compose.yml",
        "docker-compose.agents.yml",
        "docker-compose.monitoring.yml",
    ]
    for fname in compose_files:
        fpath = os.path.join(ROOT, fname)
        if not os.path.exists(fpath):
            continue
        with open(fpath) as f:
            data = yaml.safe_load(f)
        if not data or "services" not in data:
            continue
        for svc_name, svc in data["services"].items():
            if "redis" not in svc_name:
                continue
            hc = svc.get("healthcheck", {})
            test_cmd = hc.get("test", [])
            if isinstance(test_cmd, list):
                test_str = " ".join(test_cmd)
            else:
                test_str = str(test_cmd)
            if "redis-cli" in test_str and "-a" not in test_str:
                error(f"{fname}: Redis healthcheck missing -a password flag")

    if not any("Redis healthcheck" in e for e in ERRORS):
        print("  OK: Redis healthcheck includes auth")


def test_no_watchtower():
    """No watchtower in compose files (supply chain risk)."""
    print("Checking for watchtower...")
    compose_files = [
        "docker-compose.yml",
        "docker-compose.agents.yml",
        "docker-compose.monitoring.yml",
    ]
    for fname in compose_files:
        fpath = os.path.join(ROOT, fname)
        if not os.path.exists(fpath):
            continue
        with open(fpath) as f:
            content = f.read().lower()
        if "watchtower" in content:
            error(f"{fname}: watchtower found — supply chain risk, use pinned images instead")

    if not any("watchtower" in e for e in ERRORS):
        print("  OK: No watchtower in compose files")


def test_required_files_exist():
    """All required files from the plan exist."""
    print("Checking required files...")
    required = [
        "README.md", "LICENSE", ".gitignore", "CONTRIBUTING.md", "STANDARDS.md",
        "docker-compose.yml", "docker-compose.agents.yml", "docker-compose.monitoring.yml",
        "config/.env.example", "config/litellm_config.example.yaml",
        "config/prometheus.yml", "config/Caddyfile",
        "agents/worker/worker.py", "agents/worker/Dockerfile", "agents/worker/requirements.txt",
        "agents/boss/boss.py", "agents/boss/Dockerfile", "agents/boss/requirements.txt",
        "agents/boss/templates/dashboard.html",
        "agents/watchdog/watchdog.py", "agents/watchdog/Dockerfile", "agents/watchdog/requirements.txt",
        "sql/schema.sql",
        "scripts/backup.sh", "scripts/security-scan.sh", "scripts/index-knowledge.py",
        "scripts/eval.py", "scripts/setup.sh",
        "docs/DATA-CLASSIFICATION.md",
        "plugins/README.md", "plugins/TEMPLATE.md",
    ]
    for fname in required:
        fpath = os.path.join(ROOT, fname)
        if os.path.exists(fpath):
            print(f"  OK: {fname}")
        else:
            error(f"{fname}: MISSING")


def test_v03_features():
    """v0.3 features are present in source files."""
    print("Checking v0.3 features...")

    # Heartbeat loop in boss
    boss_path = os.path.join(ROOT, "agents", "boss", "boss.py")
    with open(boss_path) as f:
        boss_src = f.read()
    if "heartbeat_loop" not in boss_src:
        error("boss.py: heartbeat_loop function missing")
    else:
        print("  OK: heartbeat_loop in boss.py")

    if "classify_zone" not in boss_src:
        error("boss.py: classify_zone function missing")
    else:
        print("  OK: classify_zone in boss.py")

    if "pending_approval" not in boss_src:
        error("boss.py: pending_approval status missing")
    else:
        print("  OK: pending_approval in boss.py")

    # Autonomy evaluation in watchdog
    watchdog_path = os.path.join(ROOT, "agents", "watchdog", "watchdog.py")
    with open(watchdog_path) as f:
        watchdog_src = f.read()
    if "evaluate_autonomy" not in watchdog_src:
        error("watchdog.py: evaluate_autonomy function missing")
    else:
        print("  OK: evaluate_autonomy in watchdog.py")

    # autonomy_history table in schema
    schema_path = os.path.join(ROOT, "sql", "schema.sql")
    with open(schema_path) as f:
        schema_src = f.read()
    if "autonomy_history" not in schema_src:
        error("schema.sql: autonomy_history table missing")
    else:
        print("  OK: autonomy_history in schema.sql")

    if "pending_approval" not in schema_src:
        error("schema.sql: pending_approval status missing from agent_tasks constraint")
    else:
        print("  OK: pending_approval in schema.sql")

    if "next_run" not in schema_src:
        error("schema.sql: next_run column missing from scheduled_tasks")
    else:
        print("  OK: next_run in schema.sql")


if __name__ == "__main__":
    print("=" * 60)
    print("superteaim smoke tests")
    print("=" * 60)
    print()

    test_required_files_exist()
    print()
    test_python_syntax()
    print()
    test_yaml_validity()
    print()
    test_env_completeness()
    print()
    test_no_hardcoded_secrets()
    print()
    test_docker_images_pinned()
    print()
    test_python_deps_pinned()
    print()
    test_no_default_creds()
    print()
    test_auth_on_endpoints()
    print()
    test_no_error_leaks()
    print()
    test_redis_healthcheck_auth()
    print()
    test_no_watchtower()
    print()
    test_v03_features()
    print()

    if ERRORS:
        print("=" * 60)
        print(f"FAILED: {len(ERRORS)} error(s)")
        for e in ERRORS:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print("=" * 60)
        print("ALL TESTS PASSED")
        sys.exit(0)
