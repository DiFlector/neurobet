"""Test suite for Phase 18: Backend API.
Verifies all REST and SSE endpoints specified in Architecture Section 31 and Roadmap Phase 18:
- Health & readiness
- Sports descriptors
- Events, search, filters & pagination
- Event timeline & odds history (TimescaleDB)
- ML predictions & web research
- Bets, proposals, virtual bankroll & ledger
- Portfolio performance (PnL, ROI, Win Rate, Max Drawdown)
- ML Model registry & training runs
- Simulation reset
- Server-Sent Events (SSE) live stream
"""

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from fastapi.testclient import TestClient

from db.connection import SessionLocal
from db.models.events import Event, EventStateSnapshot
from db.models.sports import Sport
from db.models.odds import Market, MarketSelection, OddsSnapshot
from db.models.predictions import MLPrediction
from db.models.research import WebResearchRun, WebEvidence, WebDocument
from db.models.betting import VirtualAccount, LedgerEntry, Bet, BetProposal, BetSettlement
from db.models.ml_registry import ModelVersion, TrainingRun
from app.main import (
    app, _flashscore_queue_timing, _flashscore_queue_row_is_hidden,
    _flashscore_queue_sort_key, _is_overdue_live_pbp,
    _point_history_provenance, _queue_player_source_context,
    _live_tennis_profile_status,
    _tennis_source_capability_enabled,
    _api_tennis_offline_import_status,
    _summarize_tennis_source_metrics,
)

client = TestClient(app)


def test_flashscore_queue_hides_parked_source_failures_until_actionable():
    now = datetime.now(timezone.utc)
    for reason in (
        "PLAYER_SOURCE_IDS_MISSING",
        "PLAYER_COUNTRY_CODE_UNRESOLVED",
        "SEARCH_CANDIDATES_REJECTED_BY_NAME",
    ):
        assert _flashscore_queue_row_is_hidden(
            {"status": "blocked", "reason": reason}, now
        )
        assert _flashscore_queue_row_is_hidden(
            {"status": "retry", "reason": reason, "next_retry_at": now.isoformat()}, now
        )
    assert _flashscore_queue_row_is_hidden(
        {"status": "waiting_players", "kind": "match_lookup", "reason": None}, now
    )
    # Disabled legacy static archive batches remain durable for audit and
    # checkpoint recovery, but are not actionable queue work.
    assert _flashscore_queue_row_is_hidden({
        "status": "blocked", "kind": "full_archive",
        "reason": "STATIC_PROFILE_ARCHIVE_DISABLED_OR_UNVERIFIED",
    }, now)
    recent_transport_failure = {
        "status": "retry", "reason": "PERSISTENT_TRANSPORT_FAILURE",
        "last_activity_at": (now - timedelta(hours=23, minutes=59)).isoformat(),
    }
    old_transport_failure = {
        **recent_transport_failure,
        "last_activity_at": (now - timedelta(hours=24)).isoformat(),
    }
    retried_transport_failure = {
        **recent_transport_failure,
        "last_activity_at": (now - timedelta(minutes=2)).isoformat(),
        "first_failure_at": (now - timedelta(hours=24)).isoformat(),
    }
    legacy_old_transport_failure = {
        "status": "blocked", "reason": "PERSISTENT_TRANSPORT_FAILURE", "attempts": 44,
        "created_at": (now - timedelta(days=2)).isoformat(),
        "last_activity_at": (now - timedelta(minutes=2)).isoformat(),
    }
    legacy_recent_transport_failure = {
        **legacy_old_transport_failure,
        "created_at": (now - timedelta(hours=23)).isoformat(),
    }
    assert not _flashscore_queue_row_is_hidden(recent_transport_failure, now)
    assert _flashscore_queue_row_is_hidden(old_transport_failure, now)
    assert _flashscore_queue_row_is_hidden(retried_transport_failure, now)
    assert _flashscore_queue_row_is_hidden(legacy_old_transport_failure, now)
    assert not _flashscore_queue_row_is_hidden(legacy_recent_transport_failure, now)
    future_retry = {"status": "retry", "reason": "SEARCH_COMPLETED_NO_MATCH",
                    "next_retry_at": (now + timedelta(days=7)).isoformat()}
    due_retry = {**future_retry, "next_retry_at": (now - timedelta(seconds=1)).isoformat()}
    future_queued_retry = {**future_retry, "status": "queued"}
    parked_no_match = {"status": "blocked", "reason": "SEARCH_COMPLETED_NO_MATCH"}
    assert _flashscore_queue_row_is_hidden(future_retry, now)
    assert _flashscore_queue_row_is_hidden(future_queued_retry, now)
    assert _flashscore_queue_row_is_hidden(parked_no_match, now)
    assert _flashscore_queue_row_is_hidden(
        {"status": "retry", "reason": "SEARCH_COMPLETED_NO_MATCH"}, now
    )
    assert _flashscore_queue_row_is_hidden(
        {"status": "queued", "reason": "SEARCH_COMPLETED_NO_MATCH"}, now
    )
    assert not _flashscore_queue_row_is_hidden(due_retry, now)


def test_flashscore_captcha_job_is_hidden_until_automatic_retry_is_due():
    now = datetime.now(timezone.utc)
    parked = {
        "status": "blocked", "reason": "CAPTCHA_CHALLENGE_ABORTED",
        "next_retry_at": (now + timedelta(hours=5)).isoformat(),
    }
    due = {**parked, "next_retry_at": (now - timedelta(seconds=1)).isoformat()}
    manual_only = {"status": "blocked", "reason": "CAPTCHA_CHALLENGE_ABORTED"}
    assert _flashscore_queue_row_is_hidden(parked, now)
    assert _flashscore_queue_row_is_hidden(due, now)
    assert _flashscore_queue_row_is_hidden(manual_only, now)
    assert not _flashscore_queue_row_is_hidden({**due, "status": "queued"}, now)


def test_stale_live_match_lookup_sorts_after_all_actionable_work():
    stale = {"id": "stale", "status": "waiting_match", "priority": 999,
             "reason": "EVENT_NO_LONGER_FRESH_LIVE", "next_retry_at": ""}
    running_archive = {"id": "archive", "status": "running", "priority": 20,
                       "reason": None, "next_retry_at": ""}
    assert _flashscore_queue_sort_key(running_archive) < _flashscore_queue_sort_key(stale)
    stale_by_last_error = {**stale, "reason": None, "last_error": "EVENT_NO_LONGER_FRESH_LIVE"}
    assert _flashscore_queue_sort_key(running_archive) < _flashscore_queue_sort_key(stale_by_last_error)


def test_stale_live_match_lookup_has_no_scheduled_retry_eta():
    now = datetime.now(timezone.utc)
    timing = _flashscore_queue_timing(
        status="waiting_match", now=now, enqueued_at=now - timedelta(hours=12),
        next_run_at=None, attempt_started_at=None, lease_until=None,
    )
    assert timing["wait_seconds"] is None
    assert timing["retry_in_seconds"] is None


def test_overdue_live_pbp_counts_only_due_runnable_jobs_with_confirmed_source():
    now = datetime.now(timezone.utc)
    base = {"kind": "pbp", "mode": "live", "event_id": "evt", "status": "queued",
            "next_retry_at": (now - timedelta(seconds=1)).isoformat()}
    assert _is_overdue_live_pbp(base, {"evt"}, now)
    assert not _is_overdue_live_pbp({**base, "status": "running"}, {"evt"}, now)
    assert not _is_overdue_live_pbp({**base, "status": "waiting_players"}, {"evt"}, now)
    assert not _is_overdue_live_pbp(base, set(), now)
    assert not _is_overdue_live_pbp({**base, "next_retry_at": (now + timedelta(seconds=1)).isoformat()}, {"evt"}, now)


def test_flashscore_queue_timing_tracks_current_attempt_and_due_wait_separately():
    now = datetime.now(timezone.utc)
    running = _flashscore_queue_timing(
        status="running", now=now, enqueued_at=now - timedelta(hours=20),
        next_run_at=now, attempt_started_at=now - timedelta(minutes=3),
        lease_until=now + timedelta(minutes=2),
    )
    assert running["wait_seconds"] is None
    assert 179 <= running["attempt_seconds"] <= 181
    assert running["lease_expired"] is False

    waiting_retry = _flashscore_queue_timing(
        status="retry", now=now, enqueued_at=now - timedelta(hours=20),
        next_run_at=now + timedelta(minutes=4), attempt_started_at=None,
        lease_until=None,
    )
    assert waiting_retry["wait_seconds"] is None
    assert 239 <= waiting_retry["retry_in_seconds"] <= 241


def test_queue_source_context_keeps_partial_history_freshness_and_capabilities_separate():
    now = datetime.now(timezone.utc)
    profile = type("Profile", (), {"source_identities": {
        "archive:api_tennis": "api-player-id", "flashscore": "fs-id",
    }})()
    coverage = [
        type("Coverage", (), {
            "capability": "match_history", "source": "api_tennis",
            "status": "partial", "complete": False, "checked_at": now - timedelta(days=2),
            "evidence_json": {"usable_for_metrics": True},
        })(),
        type("Coverage", (), {
            "capability": "serve_statistics", "source": "tennis_my_life",
            "status": "available", "complete": True, "checked_at": now - timedelta(days=8),
            "evidence_json": {},
        })(),
        type("Coverage", (), {
            "capability": "serve_statistics", "source": "tennis_my_life",
            "status": "partial", "complete": False, "checked_at": now - timedelta(days=1),
            "evidence_json": {"usable_for_metrics": True, "matches_with_explicit_stats": 49},
        })(),
    ]

    context = _queue_player_source_context(profile, coverage, now=now)

    assert context["confirmed_source_ids"] == {"api_tennis": "api-player-id", "flashscore": "fs-id"}
    assert context["capabilities"]["match_history"]["usable"] is True
    assert context["capabilities"]["match_history"]["complete"] is False
    assert context["capabilities"]["serve_statistics"]["fresh"] is True
    assert context["capabilities"]["serve_statistics"]["usable"] is True
    assert context["capabilities"]["serve_statistics"]["complete"] is False
    assert context["capabilities"]["serve_statistics"]["evidence"]["matches_with_explicit_stats"] == 49
    assert context["fallback_reason"] == "FRESH_HISTORY_AVAILABLE"
    assert context["required_capability"] == "match_history"

    # A profile-history job can be running solely to resolve the Flashscore
    # identity required by a separate PBP task; expose that missing capability
    # instead of implying that player history itself needs Flashscore.
    context = _queue_player_source_context(profile, coverage, now=now, missing_pbp_identity=True)
    assert context["fallback_reason"] == "PBP_IDENTITY_MISSING_HISTORY_AVAILABLE"
    assert context["required_capability"] == "point_by_point"


def test_queue_source_context_explains_history_fallback_when_no_fresh_source_exists():
    context = _queue_player_source_context(None, [], now=datetime.now(timezone.utc))
    assert context["capabilities"] == {}
    assert context["fallback_reason"] == "HISTORY_SOURCE_GAP_OR_STALE"

    profile = type("Profile", (), {"source_identities": {},
                                   "sync_status": "identity_confirmation_required"})()
    context = _queue_player_source_context(profile, [], now=datetime.now(timezone.utc))
    assert context["fallback_reason"] == "IDENTITY_CONFIRMATION_REQUIRED"


def test_queue_source_context_ignores_disabled_source_coverage(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setenv("TENNIS_SOURCE_API_TENNIS_MATCH_HISTORY_ENABLED", "false")
    coverage = [SimpleNamespace(
        capability="match_history", source="api_tennis", status="partial", complete=False,
        checked_at=now, evidence_json={"usable_for_metrics": True},
    )]
    context = _queue_player_source_context(None, coverage, now=now)
    assert context["capabilities"] == {}
    assert context["fallback_reason"] == "HISTORY_SOURCE_GAP_OR_STALE"
    assert _tennis_source_capability_enabled("api_tennis", "serve_statistics")
    assert _tennis_source_capability_enabled("tennis_my_life", "match_history")
    assert _tennis_source_capability_enabled("mcp", "point_by_point")


def test_source_status_reports_profile_level_fresh_and_usable_coverage():
    response = client.get("/api/tennis-history/sources/status")

    assert response.status_code == 200
    sources = {row["source"]: row for row in response.json()["sources"]}
    for source in ("tennis_my_life", "tennis_explorer", "tennis_abstract", "api_tennis",
                   "stored_profile_metrics", "flashscore"):
        assert "player_coverage" in sources[source]
        assert isinstance(sources[source]["player_coverage"], list)
        assert sources[source]["metrics"]["window"] == "rolling_24h"
        assert sources[source]["metrics"]["requests"] >= sources[source]["metrics"]["successful_requests"]
        assert sources[source]["metrics"]["requests"] >= sources[source]["metrics"]["errors"]
        for coverage in sources[source]["player_coverage"]:
            assert coverage["capability"] in {"match_history", "serve_statistics", "point_by_point"}
            assert coverage["profiles"] >= coverage["fresh_profiles"] >= coverage["usable_profiles"] >= 0
    assert "historical_seasons" in sources["tennis_my_life"]
    assert sources["stored_profile_metrics"]["role"] == "local_history_cache"
    assert sources["stored_profile_metrics"]["status"] == "local_history_cache"
    assert sources["tennis_my_life"]["historical_seasons"]["status"] in {
        "not_started", "running", "complete"
    }


def test_live_tennis_source_status_reports_fresh_profiles_without_claiming_career_history():
    now = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
    state = {
        "latest_live_catalog": {"status": 200, "match_count": 12},
        "profile:one": {"status": 200, "checked_at": "2026-10-24T11:00:00Z",
                        "profile": {"player_id": "10"}},
        "profile:two": {"status": 200, "checked_at": "2026-10-01T11:00:00Z",
                        "profile": {"player_id": "11"}},
        "profile:failed": {"status": "search_failed", "match_status": "SEARCH_HTTP_429",
                           "error": "HTTP_429", "retry_after": "900",
                           "checked_at": "2026-10-24T11:30:00Z"},
    }

    status, profile_history = _live_tennis_profile_status(state, now=now)

    assert status == "live_catalog_and_profiles"
    assert profile_history["status"] == "fresh_profiles_available"
    assert profile_history["profiles_with_snapshot"] == 2
    assert profile_history["fresh_snapshots"] == 1
    assert profile_history["attempted_profiles"] == 3
    assert profile_history["complete_career_history"] is False
    assert profile_history["match_data_authority"] == "fonbet"
    assert profile_history["last_attempt"] == {
        "status": "search_failed", "reason": "HTTP_429",
        "observed_at": "2026-10-24T11:30:00Z", "retry_after": "900",
    }
    _, success_history = _live_tennis_profile_status({
        "profile:success": {"status": 200, "match_status": "MATCHED",
                            "checked_at": "2026-10-24T11:45:00Z", "profile": {"player_id": "12"}},
    }, now=now)
    assert success_history["last_attempt"]["reason"] is None


def test_tennis_explorer_profile_daily_limit_uses_persisted_budget():
    from app.main import _tennis_explorer_profile_daily_limit

    assert _tennis_explorer_profile_daily_limit({"daily_limit": 20}) == 20
    assert _tennis_explorer_profile_daily_limit({"daily_limit": 7}) == 7
    assert _tennis_explorer_profile_daily_limit({}) == 20
    assert _tennis_explorer_profile_daily_limit({"daily_limit": "invalid"}) == 20


def test_source_metrics_summary_is_rolling_24h_and_separates_prevented_fallbacks():
    now = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
    metrics = _summarize_tennis_source_metrics({"buckets": {
        "2026-10-24T11:00:00Z": {
            "requests": 4, "successful_requests": 3, "errors": 1,
            "published": 2, "prevented_flashscore": 5,
            "latency_total_ms": 900, "latency_samples": 3, "latency_max_ms": 600,
        },
        "2026-10-23T11:00:00Z": {
            "requests": 100, "successful_requests": 100, "errors": 0,
            "published": 100, "prevented_flashscore": 100,
            "latency_total_ms": 1000, "latency_samples": 100, "latency_max_ms": 10,
        },
    }}, now)
    assert metrics == {
        "requests": 4, "successful_requests": 3, "errors": 1, "published": 2,
        "prevented_flashscore": 5, "window": "rolling_24h",
        "avg_latency_ms": 300, "max_latency_ms": 600,
    }


def test_api_tennis_status_exposes_offline_import_checkpoints_without_raw_data():
    summary = _api_tennis_offline_import_status({
        "pbp_backfill": {"status": "paused", "rows_scanned": 11200,
                         "documents_saved": 9630, "last_source_match_id": "m-1",
                         "payload": {"secret": "must not escape"}},
        "canonical_projection_backfill": {"status": "running", "rows_scanned": 5700,
                                           "projected": 991, "merged": 2153,
                                           "ambiguous": 193, "unlinked": 314},
    })

    assert summary["pbp_backfill"] == {
        "status": "paused", "rows_scanned": 11200, "documents_saved": 9630,
        "last_source_match_id": "m-1",
    }
    assert summary["canonical_projection_backfill"]["status"] == "running"
    assert summary["canonical_projection_backfill"]["ambiguous"] == 193
    assert summary["canonical_projection_backfill"]["unlinked"] == 314
    assert "payload" not in summary["pbp_backfill"]


def test_point_history_api_provenance_preserves_source_and_partial_completeness():
    now = datetime.now(timezone.utc)
    document_id = uuid.uuid4()
    version = SimpleNamespace(
        document_id=document_id, source_match_id="mcp:match-1", available_at=now,
        id=uuid.uuid4(), quality_json={"complete": False, "gaps": [{"reason": "sampled_archive"}]},
    )
    provenance = _point_history_provenance([version], {document_id: "match_charting_project"})
    assert provenance["source"] == "match_charting_project"
    assert provenance["sources"] == ["match_charting_project"]
    assert provenance["complete"] is False
    assert provenance["source_coverage"]["match_charting_project"]["gaps"] == 1

    flashscore_document = uuid.uuid4()
    flashscore_version = SimpleNamespace(
        document_id=flashscore_document, source_match_id="fs:match-1",
        available_at=now + timedelta(seconds=1), id=uuid.uuid4(),
        quality_json={"complete": True, "gaps": []},
    )
    combined = _point_history_provenance(
        [version, flashscore_version],
        {document_id: "match_charting_project", flashscore_document: "flashscore"},
    )
    assert combined["sources"] == ["flashscore", "match_charting_project"]
    assert combined["source"] == "flashscore"
    assert combined["complete"] is False

    legacy = _point_history_provenance([], {})
    assert legacy["source"] == "flashscore"
    assert legacy["complete"] is False


def test_flashscore_queue_api_returns_bounded_operational_projection():
    response = client.get("/api/tennis-history/flashscore/queue?limit=3&offset=0")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["items"]) <= 3
    assert payload["total"] >= len(payload["items"])
    assert payload["eligible_matches"] >= 0
    assert payload["prepared_players"] >= 0
    assert payload["worker"]["network_slots"] <= payload["worker"]["network_slots_limit"]
    for item in payload["items"]:
        assert item["kind"] in {"pbp", "match_lookup", "player_history", "full_archive"}
        assert "attempt_token" not in item
        if item["status"] == "running":
            assert "attempt_seconds" in item
            assert "lease_until" in item
        assert "last_activity_at" in item


def test_flashscore_queue_hides_terminal_jobs_unless_history_is_requested():
    response = client.get("/api/tennis-history/flashscore/queue?limit=200&offset=0")
    assert response.status_code == 200
    terminal_statuses = {"completed", "finalized", "skipped", "superseded", "matched"}
    assert not any(item["status"] in terminal_statuses for item in response.json()["items"])


def test_blocked_flashscore_archive_requires_explicit_retry():
    from db.models import FlashscoreImportJob

    job_id = uuid.uuid4()
    with SessionLocal() as session:
        job = FlashscoreImportJob(
            id=job_id, job_key=f"test-blocked-retry:{job_id}", status="blocked",
            profile_urls=["https://www.flashscore.com/player/test/abcdef12/results/"],
            progress_json={"page_checkpoints": {"0": {"pages": ["checkpoint"]}}},
            attempts=7, last_error="CAPTCHA_CHALLENGE_ABORTED", next_run_at=datetime.now(timezone.utc),
        )
        session.add(job)
        session.commit()
    try:
        response = client.post(f"/api/tennis-history/flashscore/import/{job_id}/retry")
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "queued"
        assert payload["attempts"] == 7
        assert payload["previous_error"] == "CAPTCHA_CHALLENGE_ABORTED"
        assert payload["progress"]["page_checkpoints"]["0"]["pages"] == ["checkpoint"]
        assert payload["progress"]["manual_retries"][-1]["attempts_before_retry"] == 7
    finally:
        with SessionLocal() as session:
            job = session.get(FlashscoreImportJob, job_id)
            if job:
                session.delete(job)
                session.commit()


def test_blocked_flashscore_player_requires_explicit_retry():
    from db.models import Participant, TennisPlayerRefreshJob

    participant_id = uuid.uuid4()
    with SessionLocal() as session:
        _ensure_sport(session)
        participant = Participant(id=participant_id, sport_code="tennis",
                                  canonical_name=f"Retry test {participant_id}")
        session.add(participant)
        session.flush()
        session.add(TennisPlayerRefreshJob(
            participant_id=participant_id, status="blocked", attempts=5,
            last_error="CAPTCHA_CHALLENGE_ABORTED", next_run_at=datetime.now(timezone.utc),
            progress_json={"stage": "manual_retry_required", "page_checkpoint": ["saved"]},
        ))
        session.commit()
    try:
        response = client.post(f"/api/tennis-history/flashscore/player/{participant_id}/retry")
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "queued"
        assert payload["attempts"] == 5
        assert payload["previous_error"] == "CAPTCHA_CHALLENGE_ABORTED"
        assert payload["progress"]["page_checkpoint"] == ["saved"]
        assert payload["progress"]["manual_retries"][-1]["attempts_before_retry"] == 5
    finally:
        with SessionLocal() as session:
            job = session.get(TennisPlayerRefreshJob, participant_id)
            if job:
                session.delete(job)
            participant = session.get(Participant, participant_id)
            if participant:
                session.delete(participant)
            session.commit()


def _ensure_sport(session):
    sport = session.query(Sport).filter(Sport.code == "tennis").first()
    if not sport:
        sport = Sport(code="tennis", name="Tennis", is_primary=True)
        session.add(sport)
        session.flush()


def test_health_and_readiness():
    """Verify health & readiness checks reporting database connectivity."""
    resp1 = client.get("/health")
    assert resp1.status_code == 200
    assert resp1.json()["status"] == "healthy"

    resp2 = client.get("/api/health")
    assert resp2.status_code == 200
    data = resp2.json()
    assert data["status"] == "healthy"
    assert data["database"] == "connected"
    assert data["primary_sport"] == "tennis"
    print("✓ test_health_and_readiness passed")


def test_sports_endpoints():
    """Verify supported sports listing and detail lookup."""
    resp = client.get("/api/sports")
    assert resp.status_code == 200
    sports = resp.json()
    assert isinstance(sports, list)
    assert any(s["code"] == "tennis" for s in sports)

    # Specific sport detail
    resp_tennis = client.get("/api/sports/tennis")
    assert resp_tennis.status_code == 200
    t_data = resp_tennis.json()
    assert t_data["code"] == "tennis"
    assert t_data["is_primary"] is True

    # Unsupported sport cleanly handled
    resp_unsupported = client.get("/api/sports/curling")
    assert resp_unsupported.status_code == 200
    assert resp_unsupported.json()["supported"] is False
    print("✓ test_sports_endpoints passed")


def test_events_and_filtering_pagination():
    """Verify events list, filters (is_live, status, search), pagination, and detail views."""
    event_live_id = uuid.uuid4()
    event_pre_id = uuid.uuid4()

    with SessionLocal() as session:
        _ensure_sport(session)
        evt_live = Event(
            id=event_live_id,
            source="fonbet",
            source_event_id=f"ext_{event_live_id.hex[:8]}",
            sport_code="tennis",
            participant_a_name="Alcaraz Carlos",
            participant_b_name="Sinner Jannik",
            scheduled_start_at=datetime.now(timezone.utc),
            status="live",
            is_live=True,
            current_score={"sets": "1:0", "games": "3-2"},
            quality_score=98.5,
        )
        evt_pre = Event(
            id=event_pre_id,
            source="fonbet",
            source_event_id=f"ext_{event_pre_id.hex[:8]}",
            sport_code="tennis",
            participant_a_name="Medvedev Daniil",
            participant_b_name="Zverev Alexander",
            scheduled_start_at=datetime.now(timezone.utc),
            status="prematch",
            is_live=False,
            quality_score=100.0,
        )
        session.add(evt_live)
        session.add(evt_pre)
        session.commit()

    # 1. Query with is_live=true. Fixture ids use the ext_ prefix and are hidden
    # unless the caller asks for synthetic rows.
    resp_hidden = client.get("/api/events?is_live=true&limit=50")
    assert resp_hidden.status_code == 200
    assert all(e["id"] != str(event_live_id) for e in resp_hidden.json()["events"])

    resp_live = client.get("/api/events?is_live=true&limit=50&include_synthetic=true")
    assert resp_live.status_code == 200
    data_live = resp_live.json()
    assert data_live["total"] >= 1
    assert any(e["id"] == str(event_live_id) for e in data_live["events"])
    assert all(e["is_live"] is True for e in data_live["events"])

    # 2. Query with search filter
    resp_search = client.get("/api/events?search=Alcaraz&include_synthetic=true")
    assert resp_search.status_code == 200
    search_data = resp_search.json()
    assert any("Alcaraz" in e["participant_a"] for e in search_data["events"])

    # 3. Event detail
    resp_detail = client.get(f"/api/events/{event_live_id}")
    assert resp_detail.status_code == 200
    detail = resp_detail.json()
    assert detail["id"] == str(event_live_id)
    assert detail["participant_a"] == "Alcaraz Carlos"
    assert detail["current_score"]["sets"] == "1:0"

    # 4. 404 for unknown event
    resp_404 = client.get(f"/api/events/{uuid.uuid4()}")
    assert resp_404.status_code == 404
    print("✓ test_events_and_filtering_pagination passed")


def test_event_timeline_and_odds_history():
    """Verify chronological event state timeline and odds movements endpoints."""
    event_id = uuid.uuid4()
    with SessionLocal() as session:
        _ensure_sport(session)
        evt = Event(
            id=event_id,
            source="fonbet",
            source_event_id=f"ext_{event_id.hex[:8]}",
            sport_code="tennis",
            participant_a_name="Rublev Andrey",
            participant_b_name="Tsitsipas Stefanos",
            scheduled_start_at=datetime.now(timezone.utc),
            status="live",
            is_live=True,
        )
        session.add(evt)

        # Timeline snapshots
        snap1 = EventStateSnapshot(
            id=uuid.uuid4(),
            event_id=event_id,
            source_event_id=evt.source_event_id,
            sport_code="tennis",
            status="live",
            current_period=1,
            score="1-0",
            observed_at=datetime.now(timezone.utc),
        )
        snap2 = EventStateSnapshot(
            id=uuid.uuid4(),
            event_id=event_id,
            source_event_id=evt.source_event_id,
            sport_code="tennis",
            status="live",
            current_period=1,
            score="2-0",
            observed_at=datetime.now(timezone.utc),
        )
        session.add(snap1)
        session.add(snap2)

        # Odds snapshots
        odds1 = OddsSnapshot(
            id=uuid.uuid4(),
            event_id=event_id,
            market_id=uuid.uuid4(),
            selection_id=uuid.uuid4(),
            market_type="match_winner",
            outcome="player_a",
            odds=1.80,
            probability_implied=0.5555,
            observed_at=datetime.now(timezone.utc),
        )
        odds2 = OddsSnapshot(
            id=uuid.uuid4(),
            event_id=event_id,
            market_id=uuid.uuid4(),
            selection_id=uuid.uuid4(),
            market_type="match_winner",
            outcome="player_a",
            odds=1.65,
            probability_implied=0.6060,
            observed_at=datetime.now(timezone.utc),
        )
        session.add(odds1)
        session.add(odds2)
        session.commit()

    # Timeline endpoint
    resp_timeline = client.get(f"/api/events/{event_id}/timeline")
    assert resp_timeline.status_code == 200
    timeline = resp_timeline.json()
    assert len(timeline) >= 2
    assert timeline[0]["score"] == "1-0"

    # Odds endpoint
    resp_odds = client.get(f"/api/events/{event_id}/odds?market_type=match_winner")
    assert resp_odds.status_code == 200
    odds_items = resp_odds.json()
    assert len(odds_items) >= 2
    assert odds_items[0]["odds"] == 1.80
    assert odds_items[1]["odds"] == 1.65
    print("✓ test_event_timeline_and_odds_history passed")


def test_predictions_and_research():
    """Verify ML predictions, web research evidence, and research trigger endpoints."""
    event_id = uuid.uuid4()
    with SessionLocal() as session:
        _ensure_sport(session)
        evt = Event(
            id=event_id,
            source="fonbet",
            source_event_id=f"ext_{event_id.hex[:8]}",
            sport_code="tennis",
            participant_a_name="Rune Holger",
            participant_b_name="Ruud Casper",
            scheduled_start_at=datetime.now(timezone.utc),
            status="prematch",
        )
        session.add(evt)

        # ML Prediction
        pred = MLPrediction(
            id=uuid.uuid4(),
            event_id=event_id,
            sport_code="tennis",
            model_version="tennis_v1.0",
            market="match_winner",
            predicted_outcome="player_a",
            model_probability=0.64,
            bookmaker_odds=1.90,
            fair_odds=1.5625,
            edge=0.1137,
            expected_value=0.1137,
            confidence=0.78,
            has_positive_edge=True,
        )
        session.add(pred)
        session.flush()

        # Research Document & Evidence
        run = WebResearchRun(
            id=uuid.uuid4(),
            event_id=event_id,
            query="Rune vs Ruud injury form",
            status="completed",
            documents_found=1,
        )
        session.add(run)
        session.flush()

        doc = WebDocument(
            id=uuid.uuid4(),
            run_id=run.id,
            url="https://tennis.com/rune-preview",
            title="Rune preview",
            source_domain="tennis.com",
            content_hash="hash123",
        )
        session.add(doc)
        session.flush()

        evidence = WebEvidence(
            id=uuid.uuid4(),
            document_id=doc.id,
            event_id=event_id,
            snippet="Rune enters the match after a strong clay-court win last week.",
            relevance_score=0.95,
            freshness_score=0.90,
        )
        session.add(evidence)
        fixture_ev = WebEvidence(
            id=uuid.uuid4(),
            document_id=doc.id,
            event_id=event_id,
            snippet="Rune reported feeling 100% physically recovered.",
            relevance_score=0.50,
            freshness_score=0.50,
        )
        session.add(fixture_ev)
        session.commit()

    # Predictions
    resp_preds = client.get(f"/api/events/{event_id}/predictions")
    assert resp_preds.status_code == 200
    preds = resp_preds.json()
    assert len(preds) >= 1
    assert preds[0]["predicted_outcome"] == "player_a"
    assert preds[0]["edge"] > 0.10

    # Research evidence (fixture seed snippet is filtered out)
    resp_research = client.get(f"/api/events/{event_id}/research")
    assert resp_research.status_code == 200
    ev_list = resp_research.json()
    assert len(ev_list) >= 1
    assert "clay-court win" in ev_list[0]["snippet"]
    assert ev_list[0].get("title") == "Rune preview"
    assert ev_list[0].get("url") == "https://tennis.com/rune-preview"
    assert all("physically recovered" not in (e.get("snippet") or "") for e in ev_list)

    # Trigger research
    resp_run = client.post("/api/research/run", json={"event_id": str(event_id), "query": "Rune form"})
    assert resp_run.status_code == 200
    assert resp_run.json()["status"] == "success"
    print("✓ test_predictions_and_research passed")


def test_bankroll_bets_proposals_and_performance():
    """Verify account, ledger, bets, proposals, and portfolio performance calculations."""
    event_id = uuid.uuid4()
    with SessionLocal() as session:
        _ensure_sport(session)
        from bankroll import BankrollService
        account = BankrollService.get_or_create_account(session)
        session.flush()

        evt = Event(
            id=event_id,
            source="fonbet",
            source_event_id=f"ext_{event_id.hex[:8]}",
            sport_code="tennis",
            participant_a_name="Dimitrov G.",
            participant_b_name="Tiafoe F.",
            scheduled_start_at=datetime.now(timezone.utc),
            status="finished",
        )
        session.add(evt)
        session.flush()

        # Proposal
        prop = BetProposal(
            id=uuid.uuid4(),
            event_id=event_id,
            sport_code="tennis",
            market="match_winner",
            outcome="player_a",
            selection_id=uuid.uuid4(),
            bookmaker_odds=2.00,
            fair_odds=1.70,
            model_probability=0.5882,
            edge=0.0882,
            suggested_stake=1000.0,
        )
        session.add(prop)
        session.flush()

        # Placed & Settled WON bet
        bet = Bet(
            id=uuid.uuid4(),
            account_id=account.id,
            proposal_id=prop.id,
            event_id=event_id,
            sport_code="tennis",
            market="match_winner",
            outcome="player_a",
            odds=2.00,
            stake=1000.0,
            potential_payout=2000.0,
            status="WON",
        )
        session.add(bet)
        session.flush()
        bet_id = str(bet.id)

        settlement = BetSettlement(
            id=uuid.uuid4(),
            bet_id=bet.id,
            status="WON",
            payout=2000.0,
            net_profit=1000.0,
        )
        session.add(settlement)
        session.commit()

    # Account
    resp_acc = client.get("/api/account")
    assert resp_acc.status_code == 200
    assert "balance" in resp_acc.json()

    # Bets list and detail
    resp_bets = client.get("/api/bets?status=WON&include_synthetic=true")
    assert resp_bets.status_code == 200
    bets = resp_bets.json()
    assert len(bets) >= 1

    resp_bet_detail = client.get(f"/api/bets/{bet_id}")
    assert resp_bet_detail.status_code == 200
    b_detail = resp_bet_detail.json()
    assert b_detail["id"] == bet_id
    assert b_detail["settlement"]["status"] == "WON"
    assert b_detail["settlement"]["net_profit"] == 1000.0

    # Proposals
    resp_props = client.get(f"/api/proposals?event_id={event_id}")
    assert resp_props.status_code == 200
    assert len(resp_props.json()) >= 1

    # Portfolio Performance
    resp_perf = client.get("/api/performance?include_synthetic=true")
    assert resp_perf.status_code == 200
    perf = resp_perf.json()
    assert perf["total_bets"] >= 1
    assert perf["won_bets"] >= 1
    assert "win_rate" in perf
    assert "net_pnl" in perf
    assert "roi" in perf
    assert "max_drawdown" in perf
    print("✓ test_bankroll_bets_proposals_and_performance passed")


def test_models_registry_and_training_runs():
    """Verify ML model versions and training runs endpoints.

    The fixture is a shadow test tag and is removed afterwards. A test row must not stay ACTIVE.
    """
    version_id = uuid.uuid4()
    version_tag = f"test_tennis_v1.0.0_{uuid.uuid4().hex[:6]}"
    try:
        with SessionLocal() as session:
            mv = ModelVersion(
                id=version_id,
                model_name="tennis_gbdt",
                version_tag=version_tag,
                sport_code="tennis",
                market="match_winner",
                artifact_path="/models/checkpoints/tennis_gbdt.joblib",
                metrics={"brier_score": 0.18, "roc_auc": 0.76},
                hyperparameters={"learning_rate": 0.05, "max_depth": 5},
                status="SHADOW",
            )
            session.add(mv)
            session.flush()

            tr = TrainingRun(
                id=uuid.uuid4(),
                model_version_id=mv.id,
                sport_code="tennis",
                status="completed",
                metrics_train={"loss": 0.42},
                metrics_val={"loss": 0.48},
            )
            session.add(tr)
            session.commit()

        resp_models = client.get("/api/models")
        assert resp_models.status_code == 200
        models = resp_models.json()
        assert len(models) >= 1
        listed = next(m for m in models if m["version_tag"] == version_tag)
        assert listed["status"] == "SHADOW"

        resp_runs = client.get("/api/training/runs")
        assert resp_runs.status_code == 200
        runs = resp_runs.json()
        assert len(runs) >= 1

        resp_train = client.post("/api/models/train", json={"sport_code": "tennis"})
        assert resp_train.status_code == 200
        assert resp_train.json()["status"] == "queued"
        print("✓ test_models_registry_and_training_runs passed")
    finally:
        with SessionLocal() as session:
            session.query(TrainingRun).filter(TrainingRun.model_version_id == version_id).delete()
            session.query(ModelVersion).filter(ModelVersion.id == version_id).delete()
            session.commit()


def test_simulation_reset_endpoint():
    """Verify /api/simulation/reset resets virtual bankroll to initial deposit."""
    resp = client.post("/api/simulation/reset")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["new_balance"] == 1000.0
    assert data["locked_exposure"] == 0.0
    print("✓ test_simulation_reset_endpoint passed")


def test_sse_live_streaming():
    """Verify Server-Sent Events /api/live/stream emits a handshake and a heartbeat.

    max_heartbeats keeps the test body finite. The live handler stays open when
    the parameter is omitted.
    """
    resp = client.get("/api/live/stream?max_heartbeats=1")
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    assert resp.headers.get("x-accel-buffering") == "no"
    content = resp.text
    assert "event: connected" in content
    assert "event: heartbeat" in content
    print("✓ test_sse_live_streaming passed")


if __name__ == "__main__":
    test_health_and_readiness()
    test_sports_endpoints()
    test_events_and_filtering_pagination()
    test_event_timeline_and_odds_history()
    test_predictions_and_research()
    test_bankroll_bets_proposals_and_performance()
    test_models_registry_and_training_runs()
    test_simulation_reset_endpoint()
    test_sse_live_streaming()
    print("\n🎉 ALL PHASE 18 BACKEND API TESTS PASSED SUCCESSFULLY!")
