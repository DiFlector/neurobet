"""Test suite for Phase 21: Security Hardening.
Verifies:
- BET_MODE=SIMULATION strictly enforced on application startup (rejection of REAL, LIVE_MONEY)
- Domain allowlist & denylist enforcement
- SSRF prevention (blocking loopback, private ranges, metadata service, docker hostnames)
- Sandboxed HTTP Fetcher strictly forbids POST/PUT/DELETE mutations
- LLM sandbox isolation (no database drivers, no shell subprocess capability)
- Docker Compose security policy (loopback bindings, cap_drop, no-new-privileges, resource limits)
- .env restricted file permissions (0600)
- Network isolation (external ports verification)
"""

import os
import sys
import stat
import subprocess
import yaml

# Add repo root to python path so services can be imported
sys.path.insert(0, "/srv/neurobet")

from services.research.app.domain_filter import DomainFilter
from services.research.app.fetcher import SandboxedFetcher


def test_simulation_mode_enforcement():
    """Verify system strictly permits BET_MODE=SIMULATION and raises RuntimeError on invalid modes."""
    # Test bet-manager container startup validation
    cmd = [
        "docker", "exec", "-e", "BET_MODE=REAL", "neurobet_bet_manager",
        "python", "-c",
        'import os, asyncio; from app.main import lifespan; from fastapi import FastAPI; app = FastAPI(); exec(\'async def t():\\n async with lifespan(app): pass\\ntry:\\n asyncio.run(t())\\n print("FAILED")\\nexcept RuntimeError as e:\\n print("BLOCKED_SECURITY:", e)\')'
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)
    assert "BLOCKED_SECURITY" in result.stdout or "FATAL SECURITY VIOLATION" in result.stderr or "FATAL SECURITY VIOLATION" in result.stdout
    assert "FAILED" not in result.stdout

    # Test with LIVE_MONEY
    cmd_live = [
        "docker", "exec", "-e", "BET_MODE=LIVE_MONEY", "neurobet_bet_manager",
        "python", "-c",
        'import os, asyncio; from app.main import lifespan; from fastapi import FastAPI; app = FastAPI(); exec(\'async def t():\\n async with lifespan(app): pass\\ntry:\\n asyncio.run(t())\\n print("FAILED")\\nexcept RuntimeError as e:\\n print("BLOCKED_SECURITY:", e)\')'
    ]
    result_live = subprocess.run(cmd_live, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)
    assert "BLOCKED_SECURITY" in result_live.stdout or "FATAL SECURITY VIOLATION" in result_live.stderr or "FATAL SECURITY VIOLATION" in result_live.stdout
    assert "FAILED" not in result_live.stdout

    print("✓ test_simulation_mode_enforcement passed")


def test_domain_allowlist_and_denylist():
    """Verify research domain allowlist and denylist enforcement."""
    # Allowed domains
    allowed_urls = [
        "https://www.atptour.com/en/news/alcaraz-indian-wells-preview",
        "https://tennis.com/baseline/articles/swiatek-djokovic-analysis",
        "https://reuters.com/sports/tennis/us-open-results",
        "https://sports.ru/tennis/1234567.html",
    ]
    for url in allowed_urls:
        allowed, reason = DomainFilter.is_allowed(url)
        assert allowed is True, f"Expected {url} to be allowed, got: {reason}"
        assert reason == "ALLOWED"

    # Denied bookmaker domains
    blocked_urls = [
        "https://fon.bet/sports/tennis/123",
        "https://www.fonbet.ru/live/tennis",
        "https://1xbet.com/line/tennis",
        "https://bet365.com/sport",
        "https://vprognoze.ru/bets",
    ]
    for url in blocked_urls:
        allowed, reason = DomainFilter.is_allowed(url)
        assert allowed is False, f"Expected bookmaker {url} to be blocked"
        assert "DOMAIN_DENIED" in reason

    # Unlisted random domains
    unlisted_urls = [
        "https://random-blog-42.xyz/post",
        "https://sketchy-betting-tips.org",
    ]
    for url in unlisted_urls:
        allowed, reason = DomainFilter.is_allowed(url)
        assert allowed is False, f"Expected unlisted {url} to be blocked"
        assert "DOMAIN_NOT_IN_ALLOWLIST" in reason
    print("✓ test_domain_allowlist_and_denylist passed")


def test_ssrf_mitigation():
    """Verify SSRF protection blocks private IPs, metadata IP, internal service names, and non-HTTP schemes."""
    dangerous_urls = [
        ("http://127.0.0.1:8000/api/account", "SSRF_BLOCKED"),
        ("http://localhost:5432/db", "SSRF_BLOCKED"),
        ("http://169.254.169.254/latest/meta-data/", "SSRF_BLOCKED"),
        ("http://10.0.0.15/secrets", "SSRF_BLOCKED"),
        ("http://172.18.0.2:6379", "SSRF_BLOCKED"),
        ("http://192.168.1.92:8005/api", "SSRF_BLOCKED"),
        ("http://postgres:5432", "SSRF_BLOCKED"),
        ("http://redis:6379", "SSRF_BLOCKED"),
        ("http://backend:8000/metrics", "SSRF_BLOCKED"),
        ("file:///etc/passwd", "SCHEME_NOT_ALLOWED"),
        ("gopher://127.0.0.1:6379/_flushall", "SCHEME_NOT_ALLOWED"),
        ("ftp://ftp.test.com/file", "SCHEME_NOT_ALLOWED"),
    ]
    for url, expected_error in dangerous_urls:
        allowed, reason = DomainFilter.is_allowed(url)
        assert allowed is False, f"Dangerous URL {url} was erroneously allowed!"
        assert expected_error in reason, f"Expected {expected_error} for {url}, got: {reason}"
    print("✓ test_ssrf_mitigation passed")


def test_fetcher_forbids_mutations():
    """Verify SandboxedFetcher strictly forbids HTTP mutations."""
    fetcher = SandboxedFetcher(timeout_seconds=2.0)
    for method in ["POST", "PUT", "DELETE", "PATCH"]:
        raised = False
        try:
            fetcher.fetch("https://atptour.com/news", method=method)
        except ValueError as exc:
            raised = True
            assert "strictly forbidden" in str(exc)
        assert raised, f"Expected ValueError for method {method}"
    print("✓ test_fetcher_forbids_mutations passed")


def test_llm_isolation():
    """Verify LLM service has no database drivers, no shell subprocess capability."""
    llm_main_path = "/srv/neurobet/services/llm/app/main.py"
    llm_pyproject_path = "/srv/neurobet/services/llm/pyproject.toml"

    with open(llm_main_path, "r") as f:
        llm_main_code = f.read()

    with open(llm_pyproject_path, "r") as f:
        llm_pyproject = f.read()

    forbidden_terms = [
        "psycopg2",
        "asyncpg",
        "sqlalchemy",
        "subprocess",
        "os.system",
        "shlex",
    ]
    for term in forbidden_terms:
        assert term not in llm_main_code, f"LLM code contains forbidden term: {term}"
        assert term not in llm_pyproject, f"LLM dependencies contain forbidden driver: {term}"

    print("✓ test_llm_isolation passed")


def test_docker_compose_security_policy():
    """Verify docker-compose.yml enforces port bindings, resource limits, and capabilities drop."""
    compose_path = "/srv/neurobet/docker-compose.yml"
    assert os.path.exists(compose_path), "docker-compose.yml missing"

    with open(compose_path, "r") as f:
        config = yaml.safe_load(f)

    services = config.get("services", {})

    # 1. Check Postgres, Redis, MinIO, Backend, Prometheus, Grafana are bound to 127.0.0.1
    isolated_services = ["postgres", "redis", "minio", "backend", "prometheus", "grafana"]
    for svc_name in isolated_services:
        svc_config = services.get(svc_name, {})
        ports = svc_config.get("ports", [])
        for p in ports:
            assert p.startswith("127.0.0.1:"), f"{svc_name} port '{p}' must be bound to 127.0.0.1!"

    # 2. Check security_opt and resource limits across all services
    all_app_services = ["backend", "collector", "bet-manager", "neural", "research", "worker", "frontend"]
    for svc_name in all_app_services:
        svc_config = services.get(svc_name, {})
        sec_opts = svc_config.get("security_opt", [])
        assert "no-new-privileges:true" in sec_opts, f"{svc_name} missing no-new-privileges"

        limits = svc_config.get("deploy", {}).get("resources", {}).get("limits", {})
        assert "memory" in limits, f"{svc_name} missing memory limit"
        assert "cpus" in limits, f"{svc_name} missing cpus limit"

        cap_drop = svc_config.get("cap_drop", [])
        assert "ALL" in cap_drop, f"{svc_name} missing cap_drop: [ALL]"

    print("✓ test_docker_compose_security_policy passed")


def test_env_file_security_permissions():
    """Verify .env file is restricted to owner read/write (0600)."""
    env_path = "/srv/neurobet/.env"
    assert os.path.exists(env_path), ".env not found"

    file_mode = os.stat(env_path).st_mode
    group_other_perms = file_mode & (stat.S_IRWXG | stat.S_IRWXO)
    assert group_other_perms == 0, f".env file permissions {oct(file_mode)} are too permissive! Must be 0600."
    print("✓ test_env_file_security_permissions passed")


def main():
    print("Running Phase 21 Security Hardening test suite...")
    test_simulation_mode_enforcement()
    test_domain_allowlist_and_denylist()
    test_ssrf_mitigation()
    test_fetcher_forbids_mutations()
    test_llm_isolation()
    test_docker_compose_security_policy()
    test_env_file_security_permissions()
    print("\n🎉 ALL PHASE 21 SECURITY HARDENING TESTS PASSED SUCCESSFULLY!")


if __name__ == "__main__":
    main()
