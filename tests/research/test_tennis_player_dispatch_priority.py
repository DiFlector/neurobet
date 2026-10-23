from datetime import datetime, timedelta, timezone

from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, create_engine, insert, select

from app.main import (
    app,
    _profile_status_after_preemption,
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
