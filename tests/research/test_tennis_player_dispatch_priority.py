from datetime import datetime, timedelta, timezone

from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, create_engine, insert, select

from app.main import (
    app,
    _profile_status_after_preemption,
    _external_history_cache_job_filters,
    _player_refresh_claim_progress,
    _tennis_player_dispatch_priority,
    _tennis_stale_running_job_filter,
)
from app.player_profiles import advance_player_refresh_progress


def test_player_dispatch_endpoint_remains_bound_to_dispatch_handler():
    routes = [route for route in app.routes if route.path == "/api/tennis-history/jobs/dispatch"]
    assert len(routes) == 1
    assert routes[0].endpoint.__name__ == "dispatch_tennis_player_refresh_jobs"


def test_current_live_and_prematch_players_precede_stale_archive_backlog():
    engine = create_engine("sqlite://")
    jobs = Table(
        "jobs",
        MetaData(),
        Column("participant_id", String, primary_key=True),
        Column("requested_order", Integer, nullable=False),
    )
    jobs.create(engine)
    with engine.begin() as connection:
        connection.execute(insert(jobs), [
            {"participant_id": "archive-old", "requested_order": 1},
            {"participant_id": "prematch", "requested_order": 2},
            {"participant_id": "live", "requested_order": 3},
            {"participant_id": "archive-new", "requested_order": 4},
        ])
        result = connection.execute(
            select(jobs.c.participant_id).order_by(
                _tennis_player_dispatch_priority(
                    jobs.c.participant_id,
                    {"live"},
                    {"live", "prematch"},
                ),
                jobs.c.requested_order,
            )
        ).scalars().all()

    assert result == ["live", "prematch", "archive-old", "archive-new"]


def test_only_unexpired_noneligible_running_jobs_are_preemptible():
    now = datetime.now(timezone.utc)
    engine = create_engine("sqlite://")
    jobs = Table(
        "active_jobs",
        MetaData(),
        Column("participant_id", String, primary_key=True),
        Column("status", String, nullable=False),
        Column("lease_until", DateTime(timezone=True)),
    )
    jobs.create(engine)
    with engine.begin() as connection:
        connection.execute(insert(jobs), [
            {"participant_id": "stale-live-lease", "status": "running", "lease_until": now + timedelta(minutes=2)},
            {"participant_id": "current-live", "status": "running", "lease_until": now + timedelta(minutes=2)},
            {"participant_id": "expired", "status": "running", "lease_until": now - timedelta(seconds=1)},
            {"participant_id": "queued", "status": "queued", "lease_until": now + timedelta(minutes=2)},
        ])
        result = connection.execute(
            select(jobs.c.participant_id).where(
                _tennis_stale_running_job_filter(jobs, {"current-live"}, now)
            )
        ).scalars().all()

    assert result == ["stale-live-lease"]


def test_preempted_profile_uses_cached_stale_status_or_pending_without_metrics():
    class Profile:
        def __init__(self, metrics_version):
            self.metrics_version = metrics_version

    assert _profile_status_after_preemption(Profile(3)) == "stale"
    assert _profile_status_after_preemption(Profile(0)) == "pending"


def test_reclaiming_player_job_preserves_nonempty_page_checkpoint():
    checkpoint = {
        "page_checkpoint": {
            "parser_version": "flashscore-tennis-player-v8",
            "pages": [
                {"page_index": 1, "content_hash": "sha256:page-one", "archived_path": "snapshots/page-1"},
                {"page_index": 2, "content_hash": "sha256:page-two", "archived_path": "snapshots/page-2"},
            ],
        },
        "last_archived_page": {"page_index": 2},
    }

    reclaimed = _player_refresh_claim_progress(checkpoint, attempt=4)

    assert reclaimed["stage"] == "claimed"
    assert reclaimed["attempt"] == 4
    assert reclaimed["page_checkpoint"] == checkpoint["page_checkpoint"]
    assert reclaimed["last_archived_page"] == checkpoint["last_archived_page"]

    queued = advance_player_refresh_progress(reclaimed, "queued")
    assert queued["stage"] == "queued"
    assert queued["page_checkpoint"] == checkpoint["page_checkpoint"]
    assert queued["last_archived_page"] == checkpoint["last_archived_page"]


def test_cache_closed_profile_jobs_are_counted_as_prevented_flashscore(monkeypatch):
    from app import main, source_metrics

    recorded = []
    monkeypatch.setattr(
        source_metrics,
        "try_record_source_metric",
        lambda engine, source, **values: recorded.append((engine, source, values)),
    )

    main._record_history_cache_preventions(
        "db-engine", {"tennis_my_life": 2, "tennis_explorer": 1, "empty": 0}
    )

    assert recorded == [
        ("db-engine", "tennis_my_life", {"prevented_flashscore": 2}),
        ("db-engine", "tennis_explorer", {"prevented_flashscore": 1}),
    ]


def test_static_flashscore_profile_import_requires_explicit_manual_or_opted_in_origin():
    from app.main import _static_profile_import_is_authorized

    assert _static_profile_import_is_authorized("live-player:123", None, enabled=False)
    assert _static_profile_import_is_authorized(
        "profiles:manual:v9:hour:hash", {"request_origin": "manual"}, enabled=False,
    )
    assert _static_profile_import_is_authorized(
        "profiles:automatic_static:v9:hour:hash",
        {"request_origin": "automatic_static"}, enabled=True,
    )
    assert not _static_profile_import_is_authorized(
        "profiles:automatic_static:v9:hour:hash",
        {"request_origin": "automatic_static"}, enabled=False,
    )
    assert not _static_profile_import_is_authorized("profiles:v8:legacy-hour:hash", {}, enabled=True)


def test_fresh_external_history_closes_retry_even_before_next_run_at():
    from sqlalchemy import Boolean, DateTime, ForeignKey, and_, or_

    now = datetime.now(timezone.utc)
    metadata = MetaData()
    profiles = Table(
        "profiles", metadata,
        Column("participant_id", String, primary_key=True),
        Column("metrics_version", Integer, nullable=False),
        Column("history_match_count", Integer, nullable=False),
        Column("metrics_available_at", DateTime(timezone=True), nullable=False),
    )
    jobs = Table(
        "jobs", metadata,
        Column("participant_id", String, ForeignKey("profiles.participant_id"), primary_key=True),
        Column("status", String, nullable=False),
        Column("next_run_at", DateTime(timezone=True), nullable=False),
    )
    coverage = Table(
        "coverage", metadata,
        Column("participant_id", String, ForeignKey("profiles.participant_id"), primary_key=True),
        Column("capability", String, nullable=False),
        Column("status", String, nullable=False),
        Column("complete", Boolean, nullable=False),
        Column("usable_for_metrics", Boolean, nullable=False),
        Column("checked_at", DateTime(timezone=True), nullable=False),
    )
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(insert(profiles), {
            "participant_id": "p1", "metrics_version": 3, "history_match_count": 100,
            "metrics_available_at": now - timedelta(minutes=2),
        })
        connection.execute(insert(jobs), {
            "participant_id": "p1", "status": "retry", "next_run_at": now + timedelta(hours=6),
        })
        connection.execute(insert(coverage), {
            "participant_id": "p1", "capability": "match_history", "status": "partial",
            "complete": False, "usable_for_metrics": True, "checked_at": now - timedelta(hours=1),
        })
        query = select(jobs.c.participant_id).select_from(
            jobs.join(profiles, profiles.c.participant_id == jobs.c.participant_id).join(
                coverage, coverage.c.participant_id == profiles.c.participant_id,
            )
        ).where(*_external_history_cache_job_filters(
            jobs, profiles, coverage, now,
            or_(and_(coverage.c.status == "partial", coverage.c.complete.is_(False),
                     coverage.c.usable_for_metrics.is_(True))),
        ))

        assert connection.execute(query).scalars().all() == ["p1"]
