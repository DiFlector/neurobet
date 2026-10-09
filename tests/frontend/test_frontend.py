"""Test suite for Phase 19: Frontend Dashboard.
Verifies:
- Frontend HTTP health and availability
- Serving of semantic dashboard HTML, CSS tokens, and JavaScript assets
- Transparent reverse-proxying of backend REST API endpoints (/api/* and /diflector/neurobet/api/*)
- Real-time Server-Sent Events (SSE) streaming pass-through
- Integration with Virtual Bankroll, Models Registry, and Simulation Reset
"""

import json
import os
import urllib.request
import urllib.error
import urllib.parse
import http.client


BASE_URL = os.environ.get("FRONTEND_URL")
if not BASE_URL:
    try:
        with urllib.request.urlopen("http://frontend:3000/health", timeout=1) as r:
            if r.status == 200:
                BASE_URL = "http://frontend:3000"
    except Exception:
        BASE_URL = "http://localhost:8085"
if not BASE_URL:
    BASE_URL = "http://localhost:8085"


def test_frontend_health():
    """Verify frontend Express server healthcheck."""
    req = urllib.request.Request(f"{BASE_URL}/health")
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["status"] == "healthy"
        assert data["service"] in ("frontend", "neurobet-frontend")
    print("✓ test_frontend_health passed")


def test_static_html_and_assets():
    """Verify semantic dashboard markup and static assets."""
    # Index HTML
    req = urllib.request.Request(f"{BASE_URL}/")
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 200
        html = resp.read().decode("utf-8")
        assert "Neurobet" in html
        assert 'id="bankroll-summary-section"' in html
        assert 'id="tab-btn-matches"' in html
        assert 'id="matches-grid"' in html
        assert 'id="event-detail-modal"' in html

    # CSS Stylesheet
    req_css = urllib.request.Request(f"{BASE_URL}/css/dashboard.css")
    with urllib.request.urlopen(req_css, timeout=5) as resp:
        assert resp.status == 200
        assert "text/css" in resp.headers.get("Content-Type", "")

    # JS Modules
    for script in ["charts.js", "app.js"]:
        req_js = urllib.request.Request(f"{BASE_URL}/js/{script}")
        with urllib.request.urlopen(req_js, timeout=5) as resp:
            assert resp.status == 200
            assert "javascript" in resp.headers.get("Content-Type", "")

    print("✓ test_static_html_and_assets passed")


def test_backend_api_proxying():
    """Verify transparent API proxying to backend for standard and base paths."""
    # Standard path /api/sports
    req1 = urllib.request.Request(f"{BASE_URL}/api/sports")
    with urllib.request.urlopen(req1, timeout=5) as resp:
        assert resp.status == 200
        sports = json.loads(resp.read().decode("utf-8"))
        assert isinstance(sports, list)
        assert any(s.get("code") == "tennis" for s in sports)

    # Basepath /diflector/neurobet/api/account
    req2 = urllib.request.Request(f"{BASE_URL}/diflector/neurobet/api/account")
    with urllib.request.urlopen(req2, timeout=5) as resp:
        assert resp.status == 200
        acc = json.loads(resp.read().decode("utf-8"))
        assert "balance" in acc or "available_balance" in acc

    # Performance analytics /api/performance
    req3 = urllib.request.Request(f"{BASE_URL}/api/performance")
    with urllib.request.urlopen(req3, timeout=5) as resp:
        assert resp.status == 200
        perf = json.loads(resp.read().decode("utf-8"))
        assert "total_bets" in perf
        assert "win_rate" in perf

    print("✓ test_backend_api_proxying passed")


def test_sse_live_streaming_passthrough():
    """Verify SSE streaming connection /api/live/stream forwards events."""
    parsed = urllib.parse.urlparse(BASE_URL)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=5)
    conn.request("GET", "/api/live/stream")
    resp = conn.getresponse()
    assert resp.status == 200
    assert "text/event-stream" in resp.getheader("Content-Type", "")

    # Read SSE lines until data line
    data_line = None
    for _ in range(5):
        line = resp.readline().decode("utf-8").strip()
        if line.startswith("data:"):
            data_line = line
            break

    assert data_line is not None
    chunk_data = json.loads(data_line.replace("data:", "").strip())
    assert "message" in chunk_data or "type" in chunk_data or "heartbeat" in chunk_data
    conn.close()
    print("✓ test_sse_live_streaming_passthrough passed")


def test_simulation_reset_via_frontend():
    """Verify simulation reset works through frontend proxy."""
    req = urllib.request.Request(
        f"{BASE_URL}/api/simulation/reset",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["status"] in ["success", "reset"]
        assert data["new_balance"] == 100000.0

    print("✓ test_simulation_reset_via_frontend passed")


if __name__ == "__main__":
    test_frontend_health()
    test_static_html_and_assets()
    test_backend_api_proxying()
    test_sse_live_streaming_passthrough()
    test_simulation_reset_via_frontend()
    print("\n🎉 ALL PHASE 19 FRONTEND TESTS PASSED SUCCESSFULLY!")
