from datetime import datetime, timedelta, timezone

import pytest

from app.point_history import (
    _is_live_observed_document,
    _point_by_point_set_tabs,
    _point_history_tab_missing_document,
    _usable_alternative_point_version,
)


class _Page:
    url = "https://www.flashscore.com/match/tennis/player-a-id/player-b-id/summary/?mid=AbCd1234"

    def content(self):
        return "<html><body>Summary only</body></html>"


class _Response:
    status = 200


def test_no_pbp_tab_archives_summary_without_fabricating_set_one():
    assert _point_by_point_set_tabs([], "AbCd1234") == {}

    document = _point_history_tab_missing_document(_Page(), _Response())

    assert document["set"] is None
    assert document["url"].endswith("/summary/?mid=AbCd1234")
    assert document["html"] == "<html><body>Summary only</body></html>"
    assert document["fetch_status"] == "point_history_tab_missing"
    assert document["collection_status"] == "PBP_TAB_NOT_PUBLISHED"
    assert document["set_tab_confirmed"] is False


def test_live_provenance_requires_fresh_snapshot_and_does_not_backdate():
    retrieved = datetime(2026, 10, 21, 13, 30, tzinfo=timezone.utc)
    snapshot = retrieved - timedelta(seconds=30)

    assert _is_live_observed_document("live", snapshot, retrieved)
    assert not _is_live_observed_document("finished", snapshot, retrieved)
    assert not _is_live_observed_document("live", retrieved - timedelta(seconds=91), retrieved)
    assert not _is_live_observed_document("live", retrieved + timedelta(seconds=1), retrieved)
    assert not _is_live_observed_document("live", snapshot, retrieved, event_finished_at=retrieved)


def test_complete_alternative_point_version_can_satisfy_finished_event_without_flashscore():
    now = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
    assert _usable_alternative_point_version(
        source="api_tennis", events=[{"winner": 0, "confirmed": True}],
        quality={"complete": True, "gaps": [], "confirmed_points": 120},
        available_at=now - timedelta(days=30), retrospective=True, is_live=False, as_of=now,
    )


def test_partial_or_stale_alternative_pbp_keeps_flashscore_as_gap_fallback(monkeypatch):
    now = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
    base = {"source": "api_tennis", "events": [{"winner": 0, "confirmed": True}],
            "available_at": now, "retrospective": False, "is_live": True, "as_of": now}
    assert not _usable_alternative_point_version(**base, quality={"complete": False, "gaps": []})
    assert not _usable_alternative_point_version(**base, quality={"complete": True, "gaps": ["terminal_gap"]})
    assert not _usable_alternative_point_version(
        **{**base, "events": [{"winner": 0, "confirmed": False}]},
        quality={"complete": True, "gaps": []},
    )
    assert not _usable_alternative_point_version(**{
        **base, "available_at": now - timedelta(seconds=91),
    }, quality={"complete": True, "gaps": []})
    assert not _usable_alternative_point_version(**{
        **base, "retrospective": True,
    }, quality={"complete": True, "gaps": []})

    monkeypatch.setenv("TENNIS_SOURCE_API_TENNIS_POINT_BY_POINT_ENABLED", "false")
    assert not _usable_alternative_point_version(
        **base, quality={"complete": True, "gaps": []},
    )


def test_partial_alternative_pbp_is_usable_only_as_explicit_partial():
    now = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
    base = {
        "source": "api_tennis",
        "events": [
            {"winner": 0, "confirmed": True},
            {"winner": None, "confirmed": False},
        ],
        "quality": {"complete": False, "gaps": [{"reason": "missing_terminal_point"}],
                    "confirmed_points": 1},
        "available_at": now - timedelta(days=30), "retrospective": True,
        "is_live": False, "as_of": now,
    }
    assert not _usable_alternative_point_version(**base)
    assert _usable_alternative_point_version(**base, allow_partial=True)
    assert not _usable_alternative_point_version(
        **{**base, "events": [{"winner": None, "confirmed": False}]},
        allow_partial=True,
    )
    # Retrospective archives cannot mask a missing fresh live feed.
    assert not _usable_alternative_point_version(
        **{**base, "is_live": True}, allow_partial=True,
    )


def test_alternative_pbp_side_orientation_swaps_scores_and_players_without_mutating_source():
    from app.point_history import _orient_alternative_point_events

    source = [{
        "winner": 0, "server": 1, "state_before": ["30", "15"],
        "state_after": ["40", "15"], "point_score": ["40", "15"],
        "game_score": [2, 4], "confirmed": True,
    }]
    oriented = _orient_alternative_point_events(source, source_first_is_fonbet_a=False)

    assert oriented[0] == {
        "winner": 1, "server": 0, "state_before": ["15", "30"],
        "state_after": ["15", "40"], "point_score": ["15", "40"],
        "game_score": [4, 2], "confirmed": True,
    }
    assert source[0]["winner"] == 0


def test_event_collection_returns_complete_alternative_before_flashscore_search(monkeypatch):
    import uuid
    from types import SimpleNamespace
    from db.models import Event, TennisPointDocument, TennisPointHistoryVersion
    from app import point_history

    event_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    event = SimpleNamespace(id=event_id, sport_code="tennis", is_live=True, finished_at=now,
                            participant_a_name="Player A", participant_b_name="Player B")
    version = SimpleNamespace(
        available_at=now, availability_status="known", fonbet_event_id=event_id,
        events_json=[{"winner": 0, "confirmed": True}], quality_json={"complete": True, "gaps": [], "confirmed_points": 130},
        retrospective=True,
    )
    document = SimpleNamespace(
        source="api_tennis", source_match_id="api-match-42", available_at=now,
        availability_status="known", parse_status="complete",
    )

    class Query:
        def join(self, *_args): return self
        def filter(self, *_args): return self
        def order_by(self, *_args): return self
        def all(self): return [(version, document)]

    class Session:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def get(self, model, _id): return event if model is Event else None
        def query(self, *models):
            assert models == (TennisPointHistoryVersion, TennisPointDocument)
            return Query()

    monkeypatch.setattr("db.connection.SessionLocal", Session)
    result = point_history.collect_event_point_history(str(event_id))

    assert result["status"] == "available"
    assert result["source"] == "api_tennis"
    assert result["source_match_id"] == "api-match-42"
    assert result["cached"] is True
    assert result["documents_saved"] == 0


def test_event_collection_returns_linked_partial_alternative_without_flashscore(monkeypatch):
    import uuid
    from types import SimpleNamespace
    from db.models import Event, TennisPointDocument, TennisPointHistoryVersion
    from app import point_history

    event_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    event = SimpleNamespace(id=event_id, sport_code="tennis", is_live=False, finished_at=now,
                            participant_a_name="Player A", participant_b_name="Player B")
    version = SimpleNamespace(
        available_at=now, availability_status="known", fonbet_event_id=event_id,
        events_json=[{"winner": 0, "confirmed": True}],
        quality_json={"complete": False, "gaps": [{"reason": "terminal_point_missing"}],
                      "confirmed_points": 1},
        retrospective=True,
    )
    document = SimpleNamespace(
        source="api_tennis", source_match_id="api-partial-42", available_at=now,
        availability_status="known", parse_status="partial",
    )

    class Query:
        def join(self, *_args): return self
        def filter(self, *_args): return self
        def order_by(self, *_args): return self
        def all(self): return [(version, document)]

    class Session:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def get(self, model, _id): return event if model is Event else None
        def query(self, *models):
            assert models == (TennisPointHistoryVersion, TennisPointDocument)
            return Query()

    monkeypatch.setattr("db.connection.SessionLocal", Session)
    result = point_history.collect_event_point_history(
        str(event_id), source_access=lambda *_args, **_kwargs: pytest.fail("Flashscore must be skipped"),
    )

    assert result["status"] == "partial"
    assert result["source"] == "api_tennis"
    assert result["cached"] is True
    assert result["complete"] is False
    assert result["quality"]["gaps"] == [{"reason": "terminal_point_missing"}]


def test_disabled_flashscore_pbp_stops_before_match_search_or_http(monkeypatch):
    import uuid
    from types import SimpleNamespace
    from db.models import Event, TennisPointDocument, TennisPointHistoryVersion
    from app import point_history

    event = SimpleNamespace(id=uuid.uuid4(), sport_code="tennis", is_live=False, finished_at=None,
                            participant_a_name="Player A", participant_b_name="Player B")

    class Query:
        def join(self, *_args): return self
        def filter(self, *_args): return self
        def order_by(self, *_args): return self
        def all(self): return []

    class Session:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def get(self, model, _id): return event if model is Event else None
        def query(self, *models):
            assert models == (TennisPointHistoryVersion, TennisPointDocument)
            return Query()

    monkeypatch.setattr("db.connection.SessionLocal", Session)
    monkeypatch.setenv("TENNIS_SOURCE_FLASHSCORE_POINT_BY_POINT_ENABLED", "false")
    result = point_history.collect_event_point_history(
        str(uuid.uuid4()), source_access=lambda *_args, **_kwargs: pytest.fail("HTTP access must be skipped"),
    )

    assert result == {"status": "source_disabled", "reason": "SOURCE_CAPABILITY_DISABLED"}


def test_feed_source_queue_timeout_does_not_fall_back_to_chromium(monkeypatch):
    from app import browser_pool, point_history, tennis_flashscore

    monkeypatch.setattr(
        tennis_flashscore, "_profile_http_get",
        lambda *_args, **_kwargs: (0, "", "https://feed.test", "TimeoutError:FLASHSCORE_SOURCE_LOCK_TIMEOUT"),
    )
    monkeypatch.setattr(
        browser_pool.flashscore_chromium, "context",
        lambda *_args, **_kwargs: pytest.fail("queue contention must not start Chromium"),
    )

    documents, status = point_history.fetch_point_documents(
        "https://www.flashscore.com/match/tennis/a-b/?mid=AbCd1234",
        source_access=lambda: None,
    )
    assert documents == []
    assert status == "SOURCE_QUEUE_TIMEOUT"


def test_prevented_flashscore_metric_counts_only_a_source_cache_hit(monkeypatch):
    from app import source_metrics
    recorded = []
    monkeypatch.setattr(
        source_metrics, "try_record_source_metric",
        lambda engine, source, **kwargs: recorded.append((engine, source, kwargs)),
    )

    assert source_metrics.try_record_prevented_flashscore("db", "api_tennis")
    assert not source_metrics.try_record_prevented_flashscore("db", None)
    assert recorded == [("db", "api_tennis", {"prevented_flashscore": 1})]


def test_disabled_flashscore_pbp_dispatchers_do_not_open_database(monkeypatch):
    import asyncio
    from app import main

    def no_database():
        pytest.fail("disabled Flashscore PBP dispatch must not inspect or claim work")

    monkeypatch.setattr("db.connection.SessionLocal", no_database)
    monkeypatch.setenv("TENNIS_SOURCE_FLASHSCORE_POINT_BY_POINT_ENABLED", "false")
    live = asyncio.run(main.dispatch_live_point_history())
    lookup = asyncio.run(main.dispatch_tennis_match_search())

    assert live["status"] == "source_disabled" and live["claimed_count"] == 0
    assert lookup == {"status": "source_disabled", "claimed_count": 0, "active_count": 0}


def test_stale_match_lookup_resumes_only_for_same_fresh_live_event():
    from types import SimpleNamespace
    from app.main import _stale_match_lookup_is_live_again

    now = datetime(2026, 10, 24, 16, tzinfo=timezone.utc)
    job = SimpleNamespace(status="waiting_match", last_error="EVENT_NO_LONGER_FRESH_LIVE")
    fresh = SimpleNamespace(is_live=True, finished_at=None, last_seen_at=now - timedelta(seconds=80))
    assert _stale_match_lookup_is_live_again(job, fresh, now)

    too_old = SimpleNamespace(**{**fresh.__dict__, "last_seen_at": now - timedelta(seconds=91)})
    prematch = SimpleNamespace(**{**fresh.__dict__, "is_live": False})
    finished = SimpleNamespace(**{**fresh.__dict__, "finished_at": now})
    assert not _stale_match_lookup_is_live_again(job, too_old, now)
    assert not _stale_match_lookup_is_live_again(job, prematch, now)
    assert not _stale_match_lookup_is_live_again(job, finished, now)
    assert not _stale_match_lookup_is_live_again(
        SimpleNamespace(status="retry", last_error=job.last_error), fresh, now
    )
    assert not _stale_match_lookup_is_live_again(
        SimpleNamespace(status=job.status, last_error="LIVE_MATCH_NOT_FOUND"), fresh, now
    )


def test_reenabling_flashscore_pbp_resumes_only_gate_blocked_jobs():
    from types import SimpleNamespace
    from app.main import _resume_source_disabled_point_jobs

    now = datetime(2026, 10, 24, 5, tzinfo=timezone.utc)
    resumable = SimpleNamespace(status="blocked", last_error="SOURCE_CAPABILITY_DISABLED",
                                next_run_at=now - timedelta(hours=1), updated_at=None)
    unrelated = SimpleNamespace(status="blocked", last_error="HTTP_403",
                                next_run_at=now - timedelta(hours=1), updated_at=None)

    class Query:
        def filter_by(self, **filters):
            assert filters == {"status": "blocked", "last_error": "SOURCE_CAPABILITY_DISABLED"}
            return self
        def with_for_update(self, **_kwargs): return self
        def all(self): return [resumable]

    class Session:
        def query(self, _model): return Query()

    assert _resume_source_disabled_point_jobs(Session(), now) == 1
    assert (resumable.status, resumable.last_error, resumable.next_run_at, resumable.updated_at) == (
        "queued", None, now, now,
    )
    assert unrelated.status == "blocked" and unrelated.last_error == "HTTP_403"
