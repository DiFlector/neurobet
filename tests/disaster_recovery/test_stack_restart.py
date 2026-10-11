"""
Test Complete Stack Restart for Phase 31: Disaster Recovery.
Verifies graceful restart of the Neurobet Docker Compose stack, ensuring all core services
recover cleanly, re-establish connection pools, and pass health checks.
"""

import subprocess
import time
import urllib.request
import json
from pathlib import Path

def run_cmd(cmd):
    res = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return res.returncode, res.stdout.decode('utf-8', errors='replace'), res.stderr.decode('utf-8', errors='replace')

def wait_for_service(name, check_fn, timeout=60, interval=2):
    start = time.time()
    while time.time() - start < timeout:
        ok, msg = check_fn()
        if ok:
            return True, msg
        time.sleep(interval)
    return False, f"Timeout after {timeout}s: {msg}"

def test_complete_stack_restart():
    print("============================================================")
    print("🧪 TEST: COMPLETE STACK RESTART & RECOVERY")
    print("============================================================")

    # 1. Trigger graceful restart of core Neurobet platform services
    services_to_restart = ["postgres", "redis", "backend", "bet-manager", "collector", "neural"]
    cmd_restart = f"docker compose -f /srv/neurobet/docker-compose.yml restart {' '.join(services_to_restart)}"
    print(f"[*] Executing graceful restart of services: {services_to_restart}...")
    code, out, err = run_cmd(cmd_restart)
    assert code == 0, f"docker compose restart failed: {err}"
    print("[+] Docker restart command completed successfully.")

    # 2. Verify PostgreSQL readiness
    print("[*] Checking PostgreSQL health...")
    def check_postgres():
        code, out, _ = run_cmd("docker exec neurobet_postgres pg_isready -U neurobet -d neurobet")
        return code == 0, out.strip()

    ok, msg = wait_for_service("postgres", check_postgres, timeout=30)
    assert ok, f"PostgreSQL failed to recover: {msg}"
    print(f"[+] PostgreSQL recovered: {msg}")

    # 3. Verify Redis readiness
    print("[*] Checking Redis health...")
    redis_pass = ""
    env_file = Path("/srv/neurobet/.env")
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("REDIS_PASSWORD="):
                redis_pass = line.split("=", 1)[1].strip().strip('"').strip("'")
                break

    def check_redis():
        auth_flag = f"-a '{redis_pass}'" if redis_pass else ""
        code, out, _ = run_cmd(f"docker exec neurobet_redis redis-cli {auth_flag} ping 2>/dev/null")
        return code == 0 and "PONG" in out, out.strip()

    ok, msg = wait_for_service("redis", check_redis, timeout=30)
    assert ok, f"Redis failed to recover: {msg}"
    print(f"[+] Redis recovered: {msg}")

    # 4. Verify Backend API health endpoint
    print("[*] Checking Backend API (/health)...")
    def check_backend():
        try:
            req = urllib.request.Request("http://127.0.0.1:8005/health")
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return resp.status == 200, str(data)
        except Exception as e:
            return False, str(e)

    ok, msg = wait_for_service("backend", check_backend, timeout=30)
    assert ok, f"Backend API failed to recover: {msg}"
    print(f"[+] Backend API recovered: {msg}")

    # 5. Verify Collector, Bet Manager, Neural container statuses
    print("[*] Verifying container run statuses...")
    for container in ["neurobet_collector", "neurobet_bet_manager", "neurobet_neural"]:
        code, out, _ = run_cmd(f"docker inspect -f '{{{{.State.Status}}}}' {container}")
        status = out.strip()
        assert status == "running", f"Container {container} is not running (status: {status})"
        print(f"[+] Container {container}: {status}")

    # 6. Verify Database query execution after stack restart
    print("[*] Verifying database connectivity and query execution...")
    code, out, err = run_cmd("docker exec neurobet_postgres psql -U neurobet -d neurobet -c 'SELECT count(*) FROM events;'")
    assert code == 0, f"Database query failed after restart: {err}"
    print(f"[+] Database query executed successfully: {out.strip().splitlines()[-1]}")

    print("============================================================")
    print("✅ TEST COMPLETE STACK RESTART: PASSED")
    print("============================================================")

if __name__ == "__main__":
    test_complete_stack_restart()
