"""Comprehensive test suite for Phase 7: Historical Data Quality."""

import sys
import uuid
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient

# Path configuration
for p in [
    "/srv/neurobet/packages/contracts/src",
    "/srv/neurobet/packages/db/src",
    "/srv/neurobet/packages/data-quality/src",
    "/srv/neurobet/services/backend",
    "/app/packages/contracts/src",
    "/app/packages/db/src",
    "/app/packages/data-quality/src",
    "/app",
]:
    if p not in sys.path:
        sys.path.insert(0, p)

from data_quality import (
    DataQualityEngine,
    DataQualityReport,
    IssueType,
    QualityIssueItem,
    Severity,
)
from db.connection import SessionLocal
from db.models import DataQualityIssue, Event, EventStateSnapshot, OddsSnapshot
from app.main import app


def test_time_order_detection():
    """Verify detection of non-chronological snapshot timestamps."""
    t0 = datetime(2026, 10, 8, 12, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(seconds=10)
    t_regressed = t0 - timedelta(seconds=5)

    ev_id = uuid.uuid4()
    s1 = EventStateSnapshot(event_id=ev_id, observed_at=t0, status="live", score="0-0", score_detail={}, sport_state={})
    s2 = EventStateSnapshot(event_id=ev_id, observed_at=t1, status="live", score="0-0", score_detail={}, sport_state={})
    s3 = EventStateSnapshot(event_id=ev_id, observed_at=t_regressed, status="live", score="0-0", score_detail={}, sport_state={})

    # Chronological sequence
    issues_clean = DataQualityEngine.check_time_order([s1, s2])
    assert len(issues_clean) == 0

    # Sequence with regression
    issues_bad = DataQualityEngine.check_time_order([s1, s2, s3])
    assert len(issues_bad) == 1
    assert issues_bad[0].issue_type == IssueType.TIME_ORDER_REGRESSION
    assert issues_bad[0].severity == Severity.CRITICAL


def test_invalid_odds_detection():
    """Verify detection of odds <= 1.0 or extreme odds > 100.0."""
    ev_id = uuid.uuid4()
    m_id = uuid.uuid4()
    s_id = uuid.uuid4()
    t0 = datetime.now(timezone.utc)

    o_valid = OddsSnapshot(event_id=ev_id, market_id=m_id, selection_id=s_id, observed_at=t0, market_type="match_winner", outcome="player_a", odds=1.85, probability_implied=0.54)
    o_negative = OddsSnapshot(event_id=ev_id, market_id=m_id, selection_id=s_id, observed_at=t0, market_type="match_winner", outcome="player_a", odds=0.95, probability_implied=0.5)
    o_extreme = OddsSnapshot(event_id=ev_id, market_id=m_id, selection_id=s_id, observed_at=t0, market_type="match_winner", outcome="player_b", odds=150.0, probability_implied=0.006)

    # Valid odds
    assert len(DataQualityEngine.check_odds_validity([o_valid])) == 0

    # Invalid negative/impossible odds
    issues_neg = DataQualityEngine.check_odds_validity([o_negative])
    assert len(issues_neg) == 1
    assert issues_neg[0].issue_type == IssueType.INVALID_ODDS
    assert issues_neg[0].severity == Severity.CRITICAL

    # Extreme odds
    issues_ext = DataQualityEngine.check_odds_validity([o_extreme])
    assert len(issues_ext) == 1
    assert issues_ext[0].severity == Severity.WARNING


def test_score_transition_and_clock_regressions():
    """Verify detection of negative scores and clock regressions."""
    t0 = datetime(2026, 10, 8, 12, 0, 0, tzinfo=timezone.utc)
    ev_id = uuid.uuid4()

    s_good = EventStateSnapshot(
        event_id=ev_id, observed_at=t0, status="live", score="1-0",
        score_detail={"current_game_a": 1, "current_game_b": 0},
        sport_state={}, match_clock_seconds=120
    )
    s_bad_score = EventStateSnapshot(
        event_id=ev_id, observed_at=t0 + timedelta(seconds=10), status="live", score="-1-0",
        score_detail={"current_game_a": -1, "current_game_b": 0},
        sport_state={}, match_clock_seconds=130
    )
    s_regressed_clock = EventStateSnapshot(
        event_id=ev_id, observed_at=t0 + timedelta(seconds=20), status="live", score="2-0",
        score_detail={"current_game_a": 2, "current_game_b": 0},
        sport_state={}, match_clock_seconds=90  # 90 < 130!
    )

    score_issues = DataQualityEngine.check_score_transitions([s_good, s_bad_score])
    assert len(score_issues) == 1
    assert score_issues[0].issue_type == IssueType.IMPOSSIBLE_SCORE_TRANSITION

    clock_issues = DataQualityEngine.check_clock_regressions([s_good, s_bad_score, s_regressed_clock])
    assert len(clock_issues) == 1
    assert clock_issues[0].issue_type == IssueType.CLOCK_REGRESSION


def test_observation_gap_detection():
    """Verify detection of live polling gaps exceeding threshold."""
    t0 = datetime(2026, 10, 8, 12, 0, 0, tzinfo=timezone.utc)
    ev_id = uuid.uuid4()

    s1 = EventStateSnapshot(event_id=ev_id, observed_at=t0, status="live", score="0-0", score_detail={}, sport_state={})
    s2 = EventStateSnapshot(event_id=ev_id, observed_at=t0 + timedelta(seconds=15), status="live", score="0-0", score_detail={}, sport_state={})
    s3 = EventStateSnapshot(event_id=ev_id, observed_at=t0 + timedelta(seconds=120), status="live", score="0-0", score_detail={}, sport_state={})  # 105s gap!

    gaps = DataQualityEngine.check_observation_gaps([s1, s2, s3], max_gap_seconds=60)
    assert len(gaps) == 1
    assert gaps[0].issue_type == IssueType.OBSERVATION_GAP
    assert gaps[0].severity == Severity.WARNING


def test_quality_score_calculation():
    """Verify quality score deductions based on severity weights."""
    # Clean: 100.0
    assert DataQualityEngine.calculate_quality_score([]) == 100.0

    # 1 Warning (-10.0) -> 90.0
    warn_issue = QualityIssueItem(issue_type=IssueType.OBSERVATION_GAP, severity=Severity.WARNING, message="gap")
    assert DataQualityEngine.calculate_quality_score([warn_issue]) == 90.0

    # 1 Critical (-25.0) -> 75.0
    crit_issue = QualityIssueItem(issue_type=IssueType.INVALID_ODDS, severity=Severity.CRITICAL, message="bad odds")
    assert DataQualityEngine.calculate_quality_score([crit_issue]) == 75.0

    # Multiple issues: 100 - 25 - 10 - 2 = 63.0
    info_issue = QualityIssueItem(issue_type=IssueType.CLOCK_REGRESSION, severity=Severity.INFO, message="minor")
    assert DataQualityEngine.calculate_quality_score([crit_issue, warn_issue, info_issue]) == 63.0


def test_audit_persistence_and_backend_api():
    """Verify that bad rows are auditable, persisted in DB, and exposed via API."""
    with SessionLocal() as session:
        # Create a test event with anomalies
        source_id = f"test_audit_{uuid.uuid4().hex[:8]}"
        ev = Event(
            source="fonbet",
            source_event_id=source_id,
            sport_code="tennis",
            participant_a_name="Тестовый Игрок 1",
            participant_b_name="Тестовый Игрок 2",
            scheduled_start_at=datetime.now(timezone.utc),
            status="finished",
            is_live=False,
            current_score={"sets_a": 2, "sets_b": 0},
            metadata_json={},  # missing winner!
        )
        session.add(ev)
        session.flush()

        t0 = datetime.now(timezone.utc)
        # Add a bad odds snapshot (odds = 0.5)
        bad_odds = OddsSnapshot(
            event_id=ev.id,
            market_id=uuid.uuid4(),
            selection_id=uuid.uuid4(),
            observed_at=t0,
            market_type="match_winner",
            outcome="player_a",
            odds=0.5,  # INVALID!
            probability_implied=2.0,
        )
        session.add(bad_odds)
        session.commit()

        # Audit the event
        summary = DataQualityEngine.audit_event(session, ev.id)
        assert summary.quality_score < 100.0
        assert summary.issues_count >= 1

        # Verify issues are saved in DB
        db_issues = session.query(DataQualityIssue).filter(DataQualityIssue.event_id == ev.id).all()
        assert len(db_issues) >= 1
        assert any(i.issue_type == IssueType.INVALID_ODDS.value for i in db_issues)

        # Test backend API
        client = TestClient(app)
        resp_event = client.get(f"/api/quality/events/{ev.id}")
        assert resp_event.status_code == 200
        data = resp_event.json()
        assert data["event_id"] == str(ev.id)
        assert data["quality_score"] < 100.0

        resp_report = client.get("/api/quality/report")
        assert resp_report.status_code == 200
        report = resp_report.json()
        assert report["total_events_checked"] >= 1
        assert report["total_issues_found"] >= 1


if __name__ == "__main__":
    test_time_order_detection()
    print("test_time_order_detection: OK")
    test_invalid_odds_detection()
    print("test_invalid_odds_detection: OK")
    test_score_transition_and_clock_regressions()
    print("test_score_transition_and_clock_regressions: OK")
    test_observation_gap_detection()
    print("test_observation_gap_detection: OK")
    test_quality_score_calculation()
    print("test_quality_score_calculation: OK")
    test_audit_persistence_and_backend_api()
    print("test_audit_persistence_and_backend_api: OK")
    print("ALL DATA QUALITY TESTS PASSED!")
