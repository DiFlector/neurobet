"""Opt-in, local Flashscore browser recovery. No changes to general research fetches."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import unescape
from html.parser import HTMLParser
import json
import os
import re
import threading
import time
import uuid
from urllib.parse import urlsplit

from .domain_filter import DomainFilter

MAX_BODY = 5 * 1024 * 1024
_LOCAL = threading.local()


class _VisibleText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hidden = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def classify_response(status: int, body: str) -> str:
    """Only explicit challenge evidence counts; script/widget mentions are benign."""
    if status == 429:
        return "rate_limited"
    stripped = body.strip()
    # Source feeds and JSON are data, even if a field mentions a captcha.
    if status == 200 and (stripped.startswith(("[{", "{", "[]")) or
                          ("÷" in stripped and "<html" not in stripped.casefold())):
        return "data"
    parser = _VisibleText()
    parser.feed(body)
    visible = " ".join(parser.parts).casefold()
    lower = body.casefold()
    if any(x in visible for x in ("select all images", "solve the captcha", "complete the captcha", "enter the characters")):
        return "interactive_captcha"
    if any(x in visible for x in ("verify you are human", "checking your browser", "just a moment...", "attention required | cloudflare")):
        return "browser_challenge"
    if ("/cdn-cgi/challenge-platform/" in lower or "cf-chl-" in lower) and (
        "challenge-form" in lower or "cf-chl-widget" in lower or status == 403
    ):
        return "browser_challenge"
    return "forbidden" if status == 403 else "data" if status == 200 else "http_error"


def enabled() -> bool:
    return os.getenv("FLASHSCORE_RECOVERY_ENABLED", "false").lower() == "true"


def source_cooldown_enabled() -> bool:
    return os.getenv("FLASHSCORE_SOURCE_COOLDOWN_ENABLED", "true").lower() == "true"


def captcha_retry_seconds() -> int:
    return max(15, min(300, int(os.getenv("FLASHSCORE_CAPTCHA_RETRY_SECONDS", "60"))))


def allowed_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    return (parts.scheme == "https" and not parts.username and not parts.password and
            port in (None, 443) and DomainFilter.is_allowed(
                url, allowlist={"flashscore.com", "flashscore.com.ua", "flashscore.ninja", "livesport.services"}
            )[0])


@dataclass
class RecoveryResponse:
    status: int = 0
    body: str = ""
    url: str = ""
    outcome: str = "unavailable"
    challenge_type: str | None = None
    transport: str = "flaresolverr"

    def diagnostics(self):
        return {"fetch_transport": self.transport, "challenge_type": self.challenge_type,
                "recovery_result": self.outcome}


def diagnostics() -> dict:
    return dict(getattr(_LOCAL, "diagnostics", {}))


def reset_diagnostics():
    _LOCAL.diagnostics = {}


class FlareSolverrClient:
    def __init__(self):
        self.endpoint = os.getenv("FLASHSCORE_RECOVERY_URL", "http://flashscore-solver:8191/v1")
        self.timeout = max(1, min(60, int(os.getenv("FLASHSCORE_RECOVERY_TIMEOUT_SECONDS", "60"))))

    def command(self, cmd: str, **kwargs):
        import httpx
        # Explicit internal endpoint; never route control traffic through provider proxy.
        with httpx.Client(trust_env=False, timeout=self.timeout + 5 if cmd == "request.get" else 30) as client:
            response = client.post(self.endpoint, json={"cmd": cmd, **kwargs})
            response.raise_for_status()
            payload = response.json()
        if payload.get("status") != "ok":
            # Do not log upstream messages, cookies, proxy credentials or tokens.
            message = str(payload.get("message", "")).lower()
            reason = ("SOLVER_CAPTCHA_UNSUPPORTED" if "captcha" in message else
                      "SOLVER_CHALLENGE_TIMEOUT" if "timeout" in message else "SOLVER_REQUEST_FAILED")
            raise RuntimeError(reason)
        return payload

    def create(self, session_id: str):
        proxy = os.getenv("RESEARCH_HTTP_PROXY") or os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
        options = {"proxy": {"url": proxy}} if proxy else {}
        # Reclaim an orphan after a killed owner before launching another browser.
        self.destroy(session_id)
        self.command("sessions.create", session=session_id, **options)

    def destroy(self, session_id: str):
        sessions = self.command("sessions.list").get("sessions") or []
        if session_id in sessions:
            self.command("sessions.destroy", session=session_id)

    def get(self, session_id: str, url: str) -> RecoveryResponse:
        if not allowed_url(url):
            return RecoveryResponse(url=url, outcome="URL_REJECTED")
        if session_id not in (self.command("sessions.list").get("sessions") or []):
            # The sidecar may have restarted since the lease row was written.
            self.create(session_id)
        payload = self.command("request.get", session=session_id, url=url,
                               maxTimeout=self.timeout * 1000, waitInSeconds=2)
        solution = payload.get("solution") or {}
        final_url = str(solution.get("url") or "")
        if not allowed_url(final_url):
            return RecoveryResponse(url=url, outcome="REDIRECT_REJECTED")
        body = str(solution.get("response") or "")
        if len(body.encode("utf-8")) > MAX_BODY:
            return RecoveryResponse(url=final_url, outcome="BODY_TOO_LARGE")
        # Chromium wraps plain text/JSON documents in a pre element.
        pre = re.search(r"<pre\b[^>]*>(.*?)</pre>", body, re.S | re.I)
        if pre and urlsplit(final_url).hostname in {"global.flashscore.ninja", "s.livesport.services"}:
            body = unescape(pre.group(1))
        status = int(solution.get("status") or 0)
        kind = classify_response(status, body)
        return RecoveryResponse(status, body, final_url, "recovered" if kind == "data" else "unresolved", kind)


class RecoveryStore:
    """Short SQL transactions; a fenced lease serializes shared solver sessions."""
    def __init__(self):
        from db.connection import get_database_url
        from sqlalchemy import create_engine
        from sqlalchemy.pool import NullPool
        self.engine = create_engine(get_database_url(), poolclass=NullPool)

    def acquire(self, owner: str):
        from sqlalchemy import text
        with self.engine.begin() as conn:
            paused = conn.execute(text("SELECT cooldown_until > now() FROM flashscore_source_control WHERE id=1")).scalar()
            if paused and source_cooldown_enabled():
                return "paused", None
            row = conn.execute(text("""
                UPDATE flashscore_browser_recovery SET lease_owner=:owner,
                    lease_until=now()+interval '150 seconds'
                WHERE id=1 AND (lease_until IS NULL OR lease_until < now())
                RETURNING session_id, session_expires_at, egress_key
            """), {"owner": owner}).mappings().first()
            return ("acquired", dict(row)) if row else ("waiting", None)

    def renew(self, owner):
        from sqlalchemy import text
        with self.engine.begin() as conn:
            return bool(conn.execute(text("""
                UPDATE flashscore_browser_recovery SET lease_until=now()+interval '150 seconds'
                WHERE id=1 AND lease_owner=:owner AND lease_until > now() RETURNING id
            """), {"owner": owner}).first())

    def finish(self, owner, session_id, expires, egress_key, result, pause=False):
        from sqlalchemy import text
        with self.engine.begin() as conn:
            row = conn.execute(text("""
                UPDATE flashscore_browser_recovery SET session_id=:session,
                  session_expires_at=:expires, egress_key=:egress, last_result=coalesce(CAST(:result AS jsonb),last_result),
                  lease_owner=NULL, lease_until=NULL, updated_at=now()
                WHERE id=1 AND lease_owner=:owner AND lease_until > now() RETURNING id
            """), {"session": session_id, "expires": expires, "egress": egress_key,
                     "result": json.dumps(result.diagnostics()) if result is not None else None, "owner": owner}).first()
            if row and pause and source_cooldown_enabled():
                try:
                    hours = max(1, min(72, int(float(os.getenv("FLASHSCORE_CAPTCHA_COOLDOWN_HOURS", "6")))))
                except (ValueError, OverflowError):
                    hours = 6
                seconds = 900 if result.status == 429 else hours * 3600
                conn.execute(text("""
                    UPDATE flashscore_source_control SET cooldown_until=greatest(
                      coalesce(cooldown_until, now()), now()+make_interval(secs => :seconds)),
                      last_throttle_at=now(), success_count=0, slot_limit=1, updated_at=now() WHERE id=1
                """), {"seconds": seconds})
            return bool(row)


def _egress_key():
    import hashlib
    proxy = os.getenv("RESEARCH_HTTP_PROXY") or os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or "direct"
    return hashlib.sha256(proxy.encode()).hexdigest()


def _default_source_access():
    from .main import _flashscore_source_access
    return _flashscore_source_access()


def recover(url: str, *, source_access=None, require_session=False,
            client=None, store=None, challenge_type=None) -> RecoveryResponse | None:
    """One recovery per source episode. Followers reuse success or observe cooldown.

    require_session probes existing session only, preserving HTTP as the initial path.
    """
    if not enabled() or not allowed_url(url):
        return None
    client = client or FlareSolverrClient()
    store = store or RecoveryStore()
    owner = str(uuid.uuid4())
    deadline = time.monotonic() + max(75, float(os.getenv("FLASHSCORE_SOURCE_LOCK_WAIT_SECONDS", "300")) + 90)
    while True:
        state, saved = store.acquire(owner)
        if state == "paused":
            return None if require_session else RecoveryResponse(url=url, outcome="SOURCE_COOLDOWN")
        if state == "acquired":
            break
        if time.monotonic() >= deadline:
            return RecoveryResponse(url=url, outcome="RECOVERY_LEASE_TIMEOUT")
        time.sleep(0.2)
    session_id = saved.get("session_id")
    expires = saved.get("session_expires_at")
    result = RecoveryResponse(url=url)
    now = datetime.now(timezone.utc)
    active = bool(session_id and expires and expires > now and saved.get("egress_key") == _egress_key())
    pause = False
    stop = threading.Event()
    lease_lost = threading.Event()
    heartbeat = None
    def renew_lease():
        while not stop.wait(10):
            try:
                if store.renew(owner):
                    continue
            except Exception:
                pass
            lease_lost.set()
            return
    try:
        if require_session and not active:
            return None
        heartbeat = threading.Thread(target=renew_lease, name="flashscore-recovery-lease", daemon=True)
        heartbeat.start()
        if not active:
            if session_id:
                client.destroy(session_id)
            # A fixed id makes create idempotent even after a worker crash.
            session_id = "neurobet-flashscore"
            client.create(session_id)
            expires = now + timedelta(minutes=15)
        with (source_access or _default_source_access)():
            if lease_lost.is_set() or not store.renew(owner):
                raise RuntimeError("FLASHSCORE_RECOVERY_LEASE_LOST")
            result = client.get(session_id, url)
        pause = result.outcome != "recovered"
        return result
    except Exception as exc:
        already_paused = isinstance(exc, TimeoutError) and "FLASHSCORE_SOURCE_COOLDOWN" in str(exc)
        result = RecoveryResponse(url=url, outcome=("SOURCE_COOLDOWN" if already_paused else
                                  str(exc) if str(exc).startswith("SOLVER_") else type(exc).__name__))
        pause = not already_paused
        return result
    finally:
        stop.set()
        if heartbeat is not None:
            heartbeat.join(timeout=2)
        # An expired owner must not destroy a browser now owned by a successor.
        if lease_lost.is_set() or not store.renew(owner):
            raise RuntimeError("FLASHSCORE_RECOVERY_LEASE_LOST")
        result.challenge_type = result.challenge_type or challenge_type
        if pause:
            try:
                if session_id:
                    client.destroy(session_id)
            except Exception:
                pass
            session_id, expires = None, None
        recorded_result = None if require_session and not active else result
        if not store.finish(owner, session_id, expires, _egress_key(), recorded_result, pause=pause):
            raise RuntimeError("FLASHSCORE_RECOVERY_LEASE_LOST")
        _LOCAL.diagnostics = result.diagnostics() if not (require_session and not active) else {}


def recover_response(status, body, url, *, source_access=None):
    """Return legacy HTTP tuple and keep optional diagnostics out of its contract."""
    kind = classify_response(status, body)
    _LOCAL.diagnostics = {"challenge_type": kind, "fetch_transport": "http"}
    if kind not in {"browser_challenge", "interactive_captcha"} and not (kind == "forbidden" and enabled()):
        return status, body, url, None
    result = recover(url, source_access=source_access, challenge_type=kind)
    if result:
        _LOCAL.diagnostics = result.diagnostics()
    if result and result.outcome == "SOURCE_COOLDOWN":
        return 0, "", url, "FLASHSCORE_SOURCE_COOLDOWN"
    if result and (result.outcome == "recovered" or result.status == 429):
        return result.status, result.body, result.url, None
    return status, body, url, "CAPTCHA_CHALLENGE_ABORTED"
