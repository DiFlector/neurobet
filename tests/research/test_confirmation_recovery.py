from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.confirmation_recovery import (
    exact_confirmed_match_orientation,
    resume_match_job_after_closed_confirmation,
    resume_player_job_after_closed_confirmation,
)


def test_exact_single_match_candidate_is_auto_resolvable_only_with_two_confirmed_player_ids():
    url = "https://www.flashscore.com/match/tennis/adkar-vaishnavi-riKJvzYF/shkutova-anna-O8EO0106/?mid=rRgwzmTF"
    assert exact_confirmed_match_orientation(url, "rRgwzmTF", "riKJvzYF", "O8EO0106", 1) is True
    assert exact_confirmed_match_orientation(url, "rRgwzmTF", "O8EO0106", "riKJvzYF", 1) is False
    assert exact_confirmed_match_orientation(url, "rRgwzmTF", "riKJvzYF", "O8EO0106", 2) is None
    assert exact_confirmed_match_orientation(url, "rRgwzmTF", None, "O8EO0106", 1) is None
    assert exact_confirmed_match_orientation(url, "other-mid", "riKJvzYF", "O8EO0106", 1) is None
    assert exact_confirmed_match_orientation(url.replace("flashscore.com", "untrusted.example"),
        "rRgwzmTF", "riKJvzYF", "O8EO0106", 1) is None


def test_orphaned_player_confirmation_is_requeued_and_cached_metrics_are_preserved():
    now = datetime.now(timezone.utc)
    job = SimpleNamespace(
        status="waiting_confirmation", last_error="IDENTITY_CONFIRMATION_REQUIRED",
        progress_json={"stage": "identity_confirmation_required"}, next_run_at=now,
        attempt_token="expired", lease_until=now, completed_at=now, updated_at=now,
    )
    profile = SimpleNamespace(
        sync_status="identity_confirmation_required", metrics_version=3,
        sync_error="IDENTITY_CONFIRMATION_REQUIRED",
    )

    assert resume_player_job_after_closed_confirmation(job, profile, now)
    assert job.status == "queued"
    assert job.attempt_token is None and job.lease_until is None
    assert job.next_run_at == now and job.completed_at is None
    assert profile.sync_status == "stale"
    assert profile.metrics_version == 3


def test_player_confirmation_recovery_does_not_change_non_parked_jobs():
    now = datetime.now(timezone.utc)
    job = SimpleNamespace(status="running")
    assert not resume_player_job_after_closed_confirmation(job, None, now)
    assert job.status == "running"


def test_orphaned_match_confirmation_resumes_search_but_not_point_collection():
    now = datetime.now(timezone.utc)
    job = SimpleNamespace(status="waiting_confirmation", last_error="MATCH_CONFIRMATION_REQUIRED",
                          next_run_at=now, attempt_token="old", lease_until=now, updated_at=now)
    point_job = SimpleNamespace(status="waiting_confirmation", last_error="MATCH_CONFIRMATION_REQUIRED",
                                next_run_at=now, attempt_token="old", lease_until=now, updated_at=now)

    assert resume_match_job_after_closed_confirmation(job, point_job, False, now)
    assert job.status == "waiting_players"
    assert job.last_error == "PLAYER_SOURCE_IDS_MISSING"
    assert point_job.status == "waiting_match"
    assert point_job.next_run_at == now + timedelta(minutes=10)
    assert job.attempt_token is None and point_job.attempt_token is None


def test_match_confirmation_recovery_queues_search_when_both_players_are_ready():
    now = datetime.now(timezone.utc)
    job = SimpleNamespace(status="waiting_confirmation", last_error="MATCH_CONFIRMATION_REQUIRED",
                          next_run_at=now, attempt_token="old", lease_until=now, updated_at=now)
    assert resume_match_job_after_closed_confirmation(job, None, True, now)
    assert job.status == "queued" and job.last_error is None
    assert job.next_run_at == now and job.attempt_token is None and job.lease_until is None
