"""Test suite for Phase 17: Candidate Scheduling and Cost Control.
Verifies multi-factor scoring, priority queue ordering & capacity pruning,
per-event cooldowns, snapshot-based caching, rate/concurrency limiters, and backend API.
"""

import time
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from db.connection import SessionLocal
from db.models.events import Event
from db.models.sports import Sport
from contracts.decisions import CandidateItem, LLMStructuredVerdict, LLMEvidenceItem
from contracts.scheduling import (
    CandidateSchedulerConfig,
    CandidatePriorityItem,
    QueueStatusReport,
)
from bankroll.candidate_scheduler import (
    CandidateScorer,
    CandidatePriorityQueue,
    CooldownManager,
    SnapshotLLMCache,
    TokenBucketRateLimiter,
    BrowserConcurrencyLimiter,
    CandidateScheduler,
)
from bankroll.decision_pipeline import CombinedDecisionPipeline
from app.main import app


def _ensure_sport(session):
    sport = session.query(Sport).filter(Sport.code == "tennis").first()
    if not sport:
        sport = Sport(code="tennis", name="Tennis", is_primary=True)
        session.add(sport)
        session.flush()


def test_candidate_scorer_math():
    """Verify CandidateScorer generates accurate normalized and weighted composite scores."""
    cfg = CandidateSchedulerConfig()

    high_candidate = CandidateItem(
        event_id=str(uuid.uuid4()),
        participant_a="Alcaraz C.",
        participant_b="Sinner J.",
        market="match_winner",
        selection="player_a",
        odds=1.85,
        model_probability=0.68,
        market_probability=0.5405,
        edge=0.1395, # high edge
        confidence=0.88, # high confidence
        model_version="test_v1",
    )

    low_candidate = CandidateItem(
        event_id=str(uuid.uuid4()),
        participant_a="Player X",
        participant_b="Player Y",
        market="total_games_over",
        selection="over_22.5",
        odds=1.90,
        model_probability=0.53,
        market_probability=0.5263,
        edge=0.0037, # near-zero edge
        confidence=0.52, # low confidence
        model_version="test_v1",
    )

    high_score = CandidateScorer.calculate_score(high_candidate, cfg, seconds_since_observation=5.0)
    low_score = CandidateScorer.calculate_score(low_candidate, cfg, seconds_since_observation=20.0)

    assert high_score.composite_score >= cfg.min_candidate_score
    assert high_score.edge_score > 0.80
    assert high_score.confidence_score > 0.70

    assert low_score.composite_score < cfg.min_candidate_score
    assert low_score.edge_score < 0.10
    print("✓ test_candidate_scorer_math passed")


def test_priority_queue_ordering_and_capacity():
    """Verify PriorityQueue pops highest score first and drops lowest score when at capacity."""
    queue = CandidatePriorityQueue(max_capacity=3)

    def make_item(name, score):
        cand = CandidateItem(
            event_id=str(uuid.uuid4()),
            participant_a=name,
            participant_b="Opponent",
            selection="player_a",
            odds=2.0,
            model_probability=0.6,
            market_probability=0.5,
            edge=0.10,
            confidence=0.80,
            model_version="test_v1",
        )
        from contracts.scheduling import CandidateScoreBreakdown
        breakdown = CandidateScoreBreakdown(
            edge_score=score,
            confidence_score=score,
            liquidity_score=score,
            freshness_score=score,
            composite_score=score,
        )
        return CandidatePriorityItem(candidate=cand, score_breakdown=breakdown)

    item1 = make_item("Medvedev (0.65)", 0.65)
    item2 = make_item("Alcaraz (0.95)", 0.95)
    item3 = make_item("Sinner (0.80)", 0.80)

    assert queue.push(item1) is True
    assert queue.push(item2) is True
    assert queue.push(item3) is True
    assert queue.size() == 3

    # Top item must be Alcaraz (0.95)
    assert queue.peek().score_breakdown.composite_score == 0.95

    # Push item4 with score 0.90 -> should evict lowest item1 (0.65)
    item4 = make_item("Djokovic (0.90)", 0.90)
    assert queue.push(item4) is True
    assert queue.size() == 3

    # Try pushing item5 with score 0.50 -> should be rejected because lower than all in queue
    item5 = make_item("Low Player (0.50)", 0.50)
    assert queue.push(item5) is False
    assert queue.size() == 3

    # Popping order must be 0.95 -> 0.90 -> 0.80
    pop1 = queue.pop()
    assert pop1.score_breakdown.composite_score == 0.95
    pop2 = queue.pop()
    assert pop2.score_breakdown.composite_score == 0.90
    pop3 = queue.pop()
    assert pop3.score_breakdown.composite_score == 0.80
    assert queue.pop() is None

    print("✓ test_priority_queue_ordering_and_capacity passed")


def test_per_event_cooldowns():
    """Verify CooldownManager tracks research and LLM cooldowns per event."""
    cm = CooldownManager(research_cooldown_seconds=10, llm_cooldown_seconds=5)
    evt_a = "evt_1001"
    evt_b = "evt_1002"

    assert cm.is_research_in_cooldown(evt_a) is False
    assert cm.is_llm_in_cooldown(evt_a) is False

    cm.record_research(evt_a)
    assert cm.is_research_in_cooldown(evt_a) is True
    assert cm.is_research_in_cooldown(evt_b) is False

    cm.record_llm(evt_b)
    assert cm.is_llm_in_cooldown(evt_b) is True
    assert cm.is_llm_in_cooldown(evt_a) is False

    active_res, active_llm = cm.get_active_cooldowns()
    assert active_res == 1
    assert active_llm == 1

    cm.clear()
    assert cm.is_research_in_cooldown(evt_a) is False
    assert cm.is_llm_in_cooldown(evt_b) is False
    print("✓ test_per_event_cooldowns passed")


def test_snapshot_llm_cache():
    """Verify SnapshotLLMCache caches and returns verdicts for identical match states."""
    cache = SnapshotLLMCache(ttl_seconds=60)
    cand = CandidateItem(
        event_id=str(uuid.uuid4()),
        participant_a="Player A",
        participant_b="Player B",
        selection="player_a",
        odds=1.75,
        model_probability=0.65,
        market_probability=0.5714,
        edge=0.0786,
        confidence=0.80,
        model_version="test_v1",
    )

    hash1 = cache.generate_state_hash(cand, snapshot_version="snap_01", score_state="1:0 (6-4 2-1)")
    hash2 = cache.generate_state_hash(cand, snapshot_version="snap_01", score_state="1:0 (6-4 2-1)")
    hash_changed = cache.generate_state_hash(cand, snapshot_version="snap_02", score_state="1:1 (6-4 4-6)")

    assert hash1 == hash2
    assert hash1 != hash_changed

    verdict = LLMStructuredVerdict(
        verdict="BET",
        market_type="match_winner",
        selection_id="player_a",
        confidence=0.85,
        stake_recommendation_fraction=0.015,
        summary="Positive signal from snapshot cache test",
        model_identifier="test-llm",
    )

    assert cache.get(hash1) is None
    cache.set(hash1, verdict)

    cached_item = cache.get(hash1)
    assert cached_item is not None
    assert cached_item.verdict == "BET"
    assert cached_item.confidence == 0.85

    assert cache.get(hash_changed) is None
    assert cache.size() == 1

    cache.clear()
    assert cache.size() == 0
    print("✓ test_snapshot_llm_cache passed")


def test_rate_limiter_and_concurrency_limiter():
    """Verify TokenBucketRateLimiter and BrowserConcurrencyLimiter enforce quotas."""
    # Rate limiter: max 5 rps, burst 2
    limiter = TokenBucketRateLimiter(max_rps=5.0, burst_size=2)
    assert limiter.acquire() is True
    assert limiter.acquire() is True
    # Tokens depleted
    assert limiter.acquire() is False

    # Browser concurrency limiter: max 2
    browser_limiter = BrowserConcurrencyLimiter(max_concurrency=2)
    assert browser_limiter.acquire(blocking=False) is True
    assert browser_limiter.acquire(blocking=False) is True
    assert browser_limiter.acquire(blocking=False) is False
    browser_limiter.release()
    assert browser_limiter.acquire(blocking=False) is True
    browser_limiter.release()
    browser_limiter.release()

    print("✓ test_rate_limiter_and_concurrency_limiter passed")


def test_candidate_scheduler_end_to_end():
    """Verify CandidateScheduler scores, prioritizes, handles cooldowns & caching, and dispatches."""
    cfg = CandidateSchedulerConfig(min_candidate_score=0.55, llm_cooldown_seconds=10)
    scheduler = CandidateScheduler(config=cfg)

    event_id = uuid.uuid4()
    with SessionLocal() as session:
        _ensure_sport(session)
        evt = Event(
            id=event_id,
            source="fonbet",
            source_event_id=f"ext_{event_id.hex[:8]}",
            sport_code="tennis",
            participant_a_name="Alcaraz C.",
            participant_b_name="Sinner J.",
            scheduled_start_at=datetime.now(timezone.utc),
            status="prematch",
        )
        session.add(evt)
        session.commit()

        # 1. Evaluate and enqueue high candidate
        cand_high = CandidateItem(
            event_id=str(event_id),
            participant_a="Alcaraz C.",
            participant_b="Sinner J.",
            selection="player_a",
            odds=1.85,
            model_probability=0.68,
            market_probability=0.5405,
            edge=0.1395,
            confidence=0.85,
            model_version="test_v1",
        )
        item = scheduler.evaluate_and_enqueue(cand_high, snapshot_version="s1")
        assert item is not None
        assert scheduler.priority_queue.size() == 1

        # 2. Evaluate and reject low candidate
        cand_low = CandidateItem(
            event_id=str(uuid.uuid4()),
            participant_a="Player X",
            participant_b="Player Y",
            selection="player_x",
            odds=1.90,
            model_probability=0.51,
            market_probability=0.5263,
            edge=-0.0163,
            confidence=0.40,
            model_version="test_v1",
        )
        rejected_item = scheduler.evaluate_and_enqueue(cand_low)
        assert rejected_item is None
        assert scheduler.priority_queue.size() == 1

        # 3. Dispatch candidate
        pipeline = CombinedDecisionPipeline()
        mock_verdict = LLMStructuredVerdict(
            verdict="BET",
            market_type="match_winner",
            selection_id="player_a",
            confidence=0.90,
            stake_recommendation_fraction=0.015,
            summary="Approved by scheduler test",
            model_identifier="test-llm",
        )

        with patch.object(pipeline, "_query_llm", return_value=mock_verdict):
            popped = scheduler.priority_queue.pop()
            res = scheduler.dispatch_candidate(session, popped, pipeline)
            session.commit()

        assert res.status == "PROPOSED"
        assert scheduler.cooldown_manager.is_llm_in_cooldown(str(event_id)) is True

        # 4. Immediate second dispatch for same event should skip LLM due to cooldown
        item2 = scheduler.evaluate_and_enqueue(cand_high, snapshot_version="s1_repeat")
        res2 = scheduler.dispatch_candidate(session, item2, pipeline)
        session.commit()
        assert res2.status == "PROPOSED"
        assert "ML-Only" in res2.reason or res2.llm_decision_id is None

        # Verify status report
        report = scheduler.get_status_report()
        assert report.total_evaluated == 3
        assert report.total_dispatched == 2
        assert report.total_cooldown_skips == 1

    print("✓ test_candidate_scheduler_end_to_end passed")


def test_backend_scheduler_api_endpoints():
    """Verify backend API endpoints for /api/scheduler/*."""
    client = TestClient(app)

    # 1. Clear cooldowns
    resp_clear = client.post("/api/scheduler/cooldowns/clear")
    assert resp_clear.status_code == 200

    # 2. Evaluate & enqueue
    event_id = str(uuid.uuid4())
    with SessionLocal() as session:
        _ensure_sport(session)
        evt = Event(
            id=uuid.UUID(event_id),
            source="fonbet",
            source_event_id=f"ext_{event_id[:8]}",
            sport_code="tennis",
            participant_a_name="Zverev A.",
            participant_b_name="Rune H.",
            scheduled_start_at=datetime.now(timezone.utc),
            status="prematch",
        )
        session.add(evt)
        session.commit()

    payload = {
        "options": [
            {
                "event_id": event_id,
                "participant_a": "Zverev A.",
                "participant_b": "Rune H.",
                "selection": "player_a",
                "odds": 1.80,
                "model_probability": 0.70,
                "confidence": 0.85,
            },
            {
                "event_id": event_id,
                "participant_a": "Zverev A.",
                "participant_b": "Rune H.",
                "selection": "player_b",
                "odds": 2.10,
                "model_probability": 0.30,
                "confidence": 0.40,
            }
        ],
        "config": {
            "min_candidate_score": 0.55
        }
    }

    resp_eval = client.post("/api/scheduler/evaluate", json=payload)
    assert resp_eval.status_code == 200
    data = resp_eval.json()
    assert data["status"] == "success"
    assert data["total_evaluated"] == 2
    assert data["enqueued_count"] == 1
    assert data["rejected_count"] == 1

    # 3. Queue endpoint
    resp_q = client.get("/api/scheduler/queue")
    assert resp_q.status_code == 200
    queue_items = resp_q.json()
    assert len(queue_items) >= 1
    assert queue_items[0]["candidate"]["selection"] == "player_a"

    # 4. Dispatch endpoint
    resp_disp = client.post("/api/scheduler/dispatch?limit=5")
    assert resp_disp.status_code == 200
    disp_data = resp_disp.json()
    assert disp_data["status"] == "success"
    assert disp_data["dispatched_count"] >= 1

    # 5. Stats endpoint
    resp_stats = client.get("/api/scheduler/stats")
    assert resp_stats.status_code == 200
    stats = resp_stats.json()
    assert "queue_size" in stats
    assert "total_evaluated" in stats
    assert stats["total_dispatched"] >= 1

    print("✓ test_backend_scheduler_api_endpoints passed")


if __name__ == "__main__":
    test_candidate_scorer_math()
    test_priority_queue_ordering_and_capacity()
    test_per_event_cooldowns()
    test_snapshot_llm_cache()
    test_rate_limiter_and_concurrency_limiter()
    test_candidate_scheduler_end_to_end()
    test_backend_scheduler_api_endpoints()
    print("\n🎉 ALL PHASE 17 SCHEDULER & COST CONTROL TESTS PASSED SUCCESSFULLY!")
