"""Recovery must preserve source pauses and never mistake challenge HTML for sports data."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import threading
import time

import pytest
from app import flashscore_recovery as recovery

URL = "https://www.flashscore.com/player/one/player01/results/"


@pytest.mark.parametrize("status,body,kind", [
    (200, '<html><script>const captcha="verify you are human";</script>Results</html>', "data"),
    (200, '<html><script src="captcha.js"></script>Results</html>', "data"),
    (200, '<html><div class="g-recaptcha"></div>Results</html>', "data"),
    (200, '{"captcha":"verify you are human"}', "data"),
    (200, 'AA÷match123¬AD÷123456', "data"),
    (200, '<html>Verify you are human</html>', "browser_challenge"),
    (403, '<html><form id="challenge-form"><script src="/cdn-cgi/challenge-platform/x"></script></form></html>', "browser_challenge"),
    (200, '<html>Select all images with a bus</html>', "interactive_captcha"),
    (403, 'Forbidden', "forbidden"),
    (429, 'Verify you are human', "rate_limited"),
])
def test_classification(status, body, kind):
    assert recovery.classify_response(status, body) == kind


class Store:
    def __init__(self):
        self.lock = threading.Lock()
        self.saved = {}
        self.paused = False
        self.finishes = []

    def acquire(self, owner):
        if self.paused:
            return "paused", None
        if not self.lock.acquire(blocking=False):
            return "waiting", None
        return "acquired", dict(self.saved)

    def renew(self, owner):
        return True

    def finish(self, owner, session_id, expires, egress_key, result, pause=False):
        self.saved = {"session_id": session_id, "session_expires_at": expires, "egress_key": egress_key}
        self.paused = pause
        self.finishes.append(result.outcome if result is not None else None)
        self.lock.release()
        return True


class Client:
    def __init__(self, outcome="recovered"):
        self.created, self.destroyed, self.urls = [], [], []
        self.outcome = outcome

    def create(self, session): self.created.append(session)
    def destroy(self, session): self.destroyed.append(session)
    def get(self, session, url):
        self.urls.append((session, url))
        time.sleep(0.01)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return recovery.RecoveryResponse(200, "<html>Sports results</html>", url, self.outcome, "data")


@pytest.fixture(autouse=True)
def enable(monkeypatch):
    monkeypatch.setenv("FLASHSCORE_RECOVERY_ENABLED", "true")
    recovery.reset_diagnostics()
    from contextlib import nullcontext
    monkeypatch.setattr(recovery, "_default_source_access", nullcontext)


def test_initial_request_stays_http_without_browser_session():
    client, store = Client(), Store()
    assert recovery.recover(URL, require_session=True, client=client, store=store) is None
    assert not client.created and not client.urls and not store.lock.locked()


def test_session_reused_and_each_request_acquires_source_access():
    client, store, slots = Client(), Store(), []
    @contextmanager
    def access():
        slots.append("enter")
        yield
        slots.append("exit")
    first = recovery.recover(URL, source_access=access, client=client, store=store)
    second = recovery.recover(URL, source_access=access, require_session=True, client=client, store=store)
    assert first.outcome == second.outcome == "recovered"
    assert len(client.created) == 1
    assert client.urls[0][0] == client.urls[1][0]
    assert slots == ["enter", "exit", "enter", "exit"]


@pytest.mark.parametrize("outcome", ["unresolved", "REDIRECT_REJECTED", TimeoutError(), ConnectionError(),
                                    RuntimeError("SOLVER_CAPTCHA_UNSUPPORTED")])
def test_failures_release_lease_park_source_and_destroy_session(outcome):
    client, store = Client(outcome), Store()
    assert recovery.recover(URL, client=client, store=store).outcome != "recovered"
    assert store.paused and not store.lock.locked()
    assert store.saved["session_id"] is None and len(client.destroyed) == 1
    assert recovery.recover(URL, client=client, store=store).outcome == "SOURCE_COOLDOWN"
    assert len(client.urls) == 1


def test_active_cooldown_never_calls_solver_or_clears_pause():
    client, store = Client(), Store()
    store.paused = True
    assert recovery.recover(URL, client=client, store=store).outcome == "SOURCE_COOLDOWN"
    assert not client.created and not client.urls and store.paused


def test_expired_session_is_destroyed_before_recreation():
    client, store = Client(), Store()
    store.saved = {"session_id": "expired", "session_expires_at": datetime.now(timezone.utc)-timedelta(seconds=1),
                   "egress_key": recovery._egress_key()}
    recovery.recover(URL, client=client, store=store)
    assert client.destroyed == ["expired"] and len(client.created) == 1


def test_two_workers_share_one_recovery_and_followers_reuse_session():
    client, store, results = Client(), Store(), []
    threads = [threading.Thread(target=lambda: results.append(recovery.recover(URL, client=client, store=store)))
               for _ in range(2)]
    for thread in threads: thread.start()
    for thread in threads: thread.join(timeout=3)
    assert len(results) == 2 and all(item.outcome == "recovered" for item in results)
    assert len(client.created) == 1 and len(client.urls) == 2


def test_failed_recovery_is_not_repeated_by_waiting_worker():
    client, store, results = Client("unresolved"), Store(), []
    threads = [threading.Thread(target=lambda: results.append(recovery.recover(URL, client=client, store=store)))
               for _ in range(2)]
    for thread in threads: thread.start()
    for thread in threads: thread.join(timeout=3)
    assert sorted(item.outcome for item in results) == ["SOURCE_COOLDOWN", "unresolved"]
    assert len(client.urls) == 1


def test_rate_limit_never_invokes_recovery(monkeypatch):
    monkeypatch.setattr(recovery, "recover", lambda *a, **kw: pytest.fail("429 recovery forbidden"))
    assert recovery.recover_response(429, "verify you are human", URL) == (429, "verify you are human", URL, None)


def test_client_rejects_external_redirect_and_unwraps_feed(monkeypatch):
    client = recovery.FlareSolverrClient()
    monkeypatch.setattr(client, "command", lambda *a, **kw: {"solution": {
        "status": 200, "url": "http://localhost/private", "response": "secret"}})
    assert client.get("session", URL).outcome == "REDIRECT_REJECTED"
    monkeypatch.setattr(client, "command", lambda *a, **kw: {"solution": {
        "status": 200, "url": "https://global.flashscore.ninja/2/x/feed/pr_2_1_player01_0_1_en_1", "response": '<html><pre>AA÷match123¬NAME÷A &amp; B</pre></html>'}})
    assert client.get("session", URL).body == "AA÷match123¬NAME÷A & B"


def test_invalid_destinations_never_reach_solver():
    for url in ("http://localhost", "https://flashscore.com.evil.test", "https://user:pass@flashscore.com"):
        assert recovery.recover(url, client=Client(), store=Store()) is None


def test_changed_egress_does_not_reuse_clearance_session():
    client, store = Client(), Store()
    store.saved = {"session_id": "old", "session_expires_at": datetime.now(timezone.utc)+timedelta(minutes=5),
                   "egress_key": "old-proxy"}
    recovery.recover(URL, client=client, store=store)
    assert client.destroyed == ["old"] and len(client.created) == 1


def test_lost_lease_cannot_publish_success():
    client, store = Client(), Store()
    finish = store.finish
    def lost(*args, **kwargs):
        finish(*args, **kwargs)
        return False
    store.finish = lost
    with pytest.raises(RuntimeError, match="LEASE_LOST"):
        recovery.recover(URL, client=client, store=store)


def test_http_profile_recovers_before_parsing_and_records_transport(monkeypatch):
    from app import tennis_flashscore
    monkeypatch.setattr(recovery, "recover", lambda url, **kw: None if kw.get("require_session") else
                        recovery.RecoveryResponse(200, "AA÷match123¬AD÷123456", url, "recovered", "data"))
    response = SimpleNamespace(status_code=200, text="<html>Verify you are human</html>",
                               url=URL, content=b"challenge", is_redirect=False)
    monkeypatch.setattr(tennis_flashscore, "_get_profile_page_http_client", lambda: SimpleNamespace(get=lambda *a, **kw: response))
    status, body, _, error = tennis_flashscore._profile_http_get(URL, source_access=lambda: __import__('contextlib').nullcontext())
    assert status == 200 and "AA÷" in body and error is None


def test_pbp_feed_captcha_does_not_start_another_browser(monkeypatch):
    from app import point_history, tennis_flashscore
    monkeypatch.setattr(tennis_flashscore, "_profile_http_get", lambda *a, **kw:
                        (200, "challenge", URL, "CAPTCHA_CHALLENGE_ABORTED"))
    assert point_history.fetch_point_documents(
        "https://www.flashscore.com/match/tennis/a/b/summary/?mid=A5F0BCMP") == ([], "CAPTCHA_CHALLENGE_ABORTED")


def test_recovered_pbp_requires_published_set_tabs(monkeypatch):
    from app.point_history import _recover_point_documents
    monkeypatch.setattr(recovery, "recover", lambda url, **kw:
                        recovery.RecoveryResponse(200, "<html>Match summary</html>", url, "recovered"))
    docs, status = _recover_point_documents(
        "https://www.flashscore.com/match/tennis/a/b/summary/?mid=A5F0BCMP")
    assert docs == [] and status == "POINT_HISTORY_RECOVERY_NO_SET_TABS"


@pytest.fixture
def postgres_store():
    """Exercise actual PostgreSQL lease SQL in an isolated disposable schema."""
    import importlib.util
    import os
    import uuid
    from pathlib import Path
    from sqlalchemy import create_engine, text
    from db.connection import get_database_url
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    if os.getenv("RUN_FLASHSCORE_RECOVERY_DB_TESTS") != "true":
        pytest.skip("explicit PostgreSQL integration opt-in")
    base = create_engine(get_database_url())
    schema = "test_recovery_" + uuid.uuid4().hex
    with base.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(get_database_url(), connect_args={"options": f"-csearch_path={schema}"})
    try:
        with engine.begin() as conn:
            path = Path(__file__).resolve().parents[2] / "migrations/versions/055_flashscore_browser_recovery.py"
            spec = importlib.util.spec_from_file_location("recovery_migration", path)
            migration = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(migration)
            migration.op = Operations(MigrationContext.configure(conn))
            migration.upgrade()
            conn.execute(text('''CREATE TABLE flashscore_source_control (
                id int PRIMARY KEY, cooldown_until timestamptz, last_throttle_at timestamptz,
                success_count int DEFAULT 0, slot_limit int DEFAULT 2, updated_at timestamptz)'''))
            conn.execute(text("INSERT INTO flashscore_source_control(id) VALUES (1)"))
        store = recovery.RecoveryStore.__new__(recovery.RecoveryStore)
        store.engine = engine
        yield store
    finally:
        engine.dispose()
        with base.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        base.dispose()


def test_postgres_lease_fences_expired_owner_and_retains_source_pause(postgres_store):
    from sqlalchemy import text
    store = postgres_store
    assert store.acquire("first")[0] == "acquired"
    assert store.acquire("second")[0] == "waiting"
    assert store.renew("first")
    assert not store.renew("second")
    with store.engine.begin() as conn:
        conn.execute(text("UPDATE flashscore_browser_recovery SET lease_until=now()-interval '1 second'"))
    assert store.acquire("second")[0] == "acquired"
    response = recovery.RecoveryResponse(url=URL, outcome="unresolved")
    assert not store.finish("first", None, None, "egress", response, pause=True)
    assert store.finish("second", None, None, "egress", response, pause=True)
    with store.engine.connect() as conn:
        before = conn.execute(text("SELECT cooldown_until FROM flashscore_source_control")).scalar()
    assert store.acquire("third")[0] == "paused"
    with store.engine.connect() as conn:
        after = conn.execute(text("SELECT cooldown_until FROM flashscore_source_control")).scalar()
    assert before == after and after > datetime.now(timezone.utc)+timedelta(hours=5)


def test_postgres_solver_429_keeps_short_rate_limit_pause(postgres_store):
    from sqlalchemy import text
    store = postgres_store
    assert store.acquire("first")[0] == "acquired"
    response = recovery.RecoveryResponse(429, "Too many requests", URL, "unresolved", "rate_limited")
    assert store.finish("first", None, None, "egress", response, pause=True)
    with store.engine.connect() as conn:
        delta = conn.execute(text("SELECT extract(epoch FROM cooldown_until-now()) FROM flashscore_source_control")).scalar()
    assert 890 < delta <= 900


def test_solver_rate_limit_is_forwarded_as_rate_limit(monkeypatch):
    monkeypatch.setattr(recovery, "recover", lambda *a, **kw:
                        recovery.RecoveryResponse(429, "Too many requests", URL, "unresolved", "rate_limited"))
    assert recovery.recover_response(200, "Verify you are human", URL) == (429, "Too many requests", URL, None)


def test_bootstrap_sets_feed_headers_only_for_exact_source(monkeypatch):
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / "scripts/flashscore_solver_entrypoint.py"
    spec = importlib.util.spec_from_file_location("solver_bootstrap", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("FLASHSCORE_X_FSIGN", "public-feed-signature")
    assert module.feed_headers("https://global.flashscore.ninja/2/x/feed/df_mh_2_match001")["x-fsign"] == "public-feed-signature"
    for url in (URL, "https://global.flashscore.ninja.evil.test/2/x/feed/foo", "http://global.flashscore.ninja/2/x/feed/foo"):
        assert module.feed_headers(url) == {}


def test_live_acceptance_respects_existing_pause_without_provider_calls(monkeypatch, tmp_path):
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / "scripts/research/verify_flashscore_recovery.py"
    spec = importlib.util.spec_from_file_location("recovery_acceptance", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "enabled", lambda: True)
    pause = datetime.now(timezone.utc)+timedelta(hours=1)
    monkeypatch.setattr(module, "_flashscore_source_paused", lambda: pause)
    result = module.run(tmp_path / "report.json")
    assert result["status"] == "deferred_source_cooldown" and result["verified"] is False
    assert result["initial_cooldown_until"] == pause.isoformat()


def test_lost_owner_does_not_destroy_successor_browser():
    client, store = Client(), Store()
    store.saved = {"session_id": "current", "session_expires_at": datetime.now(timezone.utc)+timedelta(minutes=5),
                   "egress_key": recovery._egress_key()}
    store.renew = lambda owner: False
    with pytest.raises(RuntimeError, match="LEASE_LOST"):
        recovery.recover(URL, client=client, store=store)
    assert not client.destroyed and not client.urls


def test_pause_during_source_queue_wait_is_not_extended():
    client, store = Client(), Store()
    @contextmanager
    def access():
        raise TimeoutError("FLASHSCORE_SOURCE_COOLDOWN:already-paused")
        yield
    result = recovery.recover(URL, source_access=access, client=client, store=store)
    assert result.outcome == "SOURCE_COOLDOWN"
    assert not store.paused and not client.urls and not client.destroyed


@pytest.mark.parametrize("http_status", [200, 403, 429, 0])
def test_bootstrap_preserves_api_status_and_reports_actual_document_status(http_status):
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / "scripts/flashscore_solver_entrypoint.py"
    spec = importlib.util.spec_from_file_location("solver_bootstrap_status", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []
    driver = SimpleNamespace(execute_cdp_cmd=lambda *args: calls.append(args),
                             execute_script=lambda _: http_status)
    upstream = SimpleNamespace(status="ok", result=SimpleNamespace(status=200))
    result = module.request_with_browser_metadata(lambda *args: upstream, SimpleNamespace(url=URL), driver, "GET")
    assert result.status == "ok" and result.result.status == http_status
    assert calls[-1] == ("Network.setExtraHTTPHeaders", {"headers": {}})


def test_disabled_global_cooldown_never_parks_jobs(monkeypatch):
    from app import main
    monkeypatch.setenv("FLASHSCORE_SOURCE_COOLDOWN_ENABLED", "false")
    monkeypatch.setenv("FLASHSCORE_CAPTCHA_RETRY_SECONDS", "60")
    assert main._record_flashscore_throttle("CAPTCHA_CHALLENGE_ABORTED") is None
    assert main._record_flashscore_throttle("HTTP_403_BLOCKED") is None
    assert main._flashscore_source_cooldown_until() is None
    assert main._flashscore_import_failure_schedule("CAPTCHA_CHALLENGE_ABORTED", 104) == ("queued", 60)
    checkpoint = {"page_checkpoint": {"pages": [1]}, "captcha_retry_at": "2099-01-01T00:00:00Z"}
    status, progress = main._flashscore_profile_job_outcome("retry", "CAPTCHA_CHALLENGE_ABORTED", checkpoint)
    assert status == "retry" and progress["page_checkpoint"] == {"pages": [1]}
    assert "captcha_retry_at" not in progress
    for delay in (main._player_refresh_retry_delay, main._live_match_lookup_retry_delay,
                  main._source_point_refresh_retry_delay):
        assert delay("CAPTCHA_CHALLENGE_ABORTED", 104).total_seconds() == 60
    assert main._player_refresh_retry_delay("HTTP_429_BLOCKED", 104).total_seconds() == 900


def test_postgres_failed_solver_does_not_install_disabled_global_pause(postgres_store, monkeypatch):
    from sqlalchemy import text
    monkeypatch.setenv("FLASHSCORE_SOURCE_COOLDOWN_ENABLED", "false")
    store = postgres_store
    assert store.acquire("first")[0] == "acquired"
    failure = recovery.RecoveryResponse(url=URL, outcome="unresolved")
    assert store.finish("first", None, None, "egress", failure, pause=True)
    with store.engine.connect() as conn:
        assert conn.execute(text("SELECT cooldown_until FROM flashscore_source_control")).scalar() is None
    assert store.acquire("second")[0] == "acquired"


def test_postgres_disabled_pause_cannot_block_recovery(postgres_store, monkeypatch):
    from sqlalchemy import text
    monkeypatch.setenv("FLASHSCORE_SOURCE_COOLDOWN_ENABLED", "false")
    with postgres_store.engine.begin() as conn:
        conn.execute(text("UPDATE flashscore_source_control SET cooldown_until=now()+interval '6 hours'"))
    assert postgres_store.acquire("worker")[0] == "acquired"


def test_plain_403_uses_local_browser_recovery(monkeypatch):
    calls = []
    def solve(url, **kwargs):
        calls.append(url)
        return recovery.RecoveryResponse(200, "sports data", url, "recovered")
    monkeypatch.setattr(recovery, "recover", solve)
    assert recovery.recover_response(403, "Forbidden", URL) == (200, "sports data", URL, None)
    assert calls == [URL]


def test_postgres_empty_session_probe_preserves_last_actual_result(postgres_store):
    from sqlalchemy import text
    store = postgres_store
    assert store.acquire("failure")[0] == "acquired"
    result = recovery.RecoveryResponse(url=URL, outcome="SOLVER_CAPTCHA_UNSUPPORTED")
    assert store.finish("failure", None, None, "egress", result, pause=False)
    assert store.acquire("empty-probe")[0] == "acquired"
    assert store.finish("empty-probe", None, None, "egress", None, pause=False)
    with store.engine.connect() as conn:
        last = conn.execute(text("SELECT last_result FROM flashscore_browser_recovery")).scalar()
    assert last["recovery_result"] == "SOLVER_CAPTCHA_UNSUPPORTED"
