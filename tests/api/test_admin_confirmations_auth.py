import hashlib

from fastapi.testclient import TestClient

from app.main import app


def test_admin_session_and_confirmation_api_require_server_auth(monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", "admin-test")
    monkeypatch.setenv("ADMIN_PASSWORD_SHA256", hashlib.sha256(b"secret-test").hexdigest())
    monkeypatch.setenv("ADMIN_SESSION_SECRET", "test-session-secret-long-enough")
    monkeypatch.setenv("ADMIN_COOKIE_SECURE", "false")
    client = TestClient(app)

    assert client.get("/api/admin/confirmations").status_code == 401
    assert client.post("/api/events/00000000-0000-0000-0000-000000000000/point-history/link",
                       json={"source_match_id": "source"}).status_code == 401
    assert client.post("/api/admin/session", json={"username": "admin-test", "password": "wrong"}).status_code == 401

    login = client.post("/api/admin/session", json={"username": "admin-test", "password": "secret-test"})
    assert login.status_code == 200
    assert login.json()["authenticated"] is True
    assert client.get("/api/admin/session").json()["authenticated"] is True
    assert client.get("/api/admin/confirmations").status_code == 200

    logout = client.delete("/api/admin/session")
    assert logout.status_code == 200
    assert client.get("/api/admin/confirmations").status_code == 401


def test_admin_routes_fail_closed_without_server_configuration(monkeypatch):
    monkeypatch.delenv("ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD_SHA256", raising=False)
    monkeypatch.delenv("ADMIN_SESSION_SECRET", raising=False)
    client = TestClient(app)
    assert client.post("/api/admin/session", json={"username": "x", "password": "y"}).status_code == 503
    assert client.get("/api/admin/confirmations").status_code == 503
