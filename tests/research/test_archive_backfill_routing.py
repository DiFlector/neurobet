from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import uuid

from app.main import _finalize_historical_alternative_pbp


class _Session:
    def __init__(self):
        self.added = []

    def add(self, value):
        self.added.append(value)


def test_archive_backfill_closes_from_complete_alternative_without_flashscore():
    session = _Session()
    event = SimpleNamespace(id=uuid.uuid4())
    now = datetime.now(timezone.utc)
    alternative = {
        "status": "available", "source": "api_tennis", "source_match_id": "at-123",
        "quality": {"confirmed_points": 42, "complete": True}, "available_at": now,
    }

    job = _finalize_historical_alternative_pbp(session, event, None, alternative, now)

    assert job in session.added
    assert job.status == "finalized"
    assert job.last_error == "FINALIZED_WITH_ALTERNATIVE_PBP"
    assert job.next_run_at == now


def test_partial_alternative_is_visible_and_does_not_claim_complete():
    session = _Session()
    event = SimpleNamespace(id=uuid.uuid4())
    now = datetime.now(timezone.utc)
    job = SimpleNamespace(status="waiting_players", last_error=None, attempt_token="old", lease_until=now,
                          updated_at=None)
    alternative = {"status": "partial", "source": "match_charting_project", "quality": {"gaps": [2]}}

    result = _finalize_historical_alternative_pbp(session, event, job, alternative, now)

    assert result.status == "finalized"
    assert result.last_error == "FINALIZED_WITH_PARTIAL_ALTERNATIVE"
    assert result.attempt_token is None
    assert result.lease_until is None


def test_running_archive_lease_remains_owned_by_worker():
    session = _Session()
    event = SimpleNamespace(id=uuid.uuid4())
    job = SimpleNamespace(status="running", last_error=None)
    now = datetime.now(timezone.utc)

    assert _finalize_historical_alternative_pbp(
        session, event, job, {"status": "available", "source": "api_tennis"}, now,
    ) is job
    assert job.last_error is None
    assert not session.added
