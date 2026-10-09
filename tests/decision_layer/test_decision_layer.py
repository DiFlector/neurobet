"""Test suite for Phase 16: ML + LLM Decision Layer.
Verifies candidate selection, cost control, ML-only mode, ML+LLM approval/rejection,
persistence to llm_decisions, BetProposal generation, strategy comparison, and backend API.
"""

import sys
import uuid
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from db.connection import SessionLocal
from db.models.events import Event
from db.models.sports import Sport
from db.models.predictions import LLMDecision
from db.models.betting import BetProposal
from contracts.decisions import (
    CandidateItem,
    DecisionPipelineConfig,
    LLMStructuredVerdict,
    LLMEvidenceItem,
)
from bankroll.decision_pipeline import (
    CandidateSelector,
    CombinedDecisionPipeline,
    LLMStrategyComparator,
)
from app.main import app


def _ensure_sport(session):
    sport = session.query(Sport).filter(Sport.code == "tennis").first()
    if not sport:
        sport = Sport(code="tennis", name="Tennis", is_primary=True)
        session.add(sport)
        session.flush()


def test_candidate_selector_shortlist_and_cost_control():
    """Verify CandidateSelector filters low-edge and low-confidence options without LLM calls."""
    config = DecisionPipelineConfig(min_edge=0.03, min_confidence=0.50)
    
    c_low_edge = CandidateItem(
        event_id=str(uuid.uuid4()),
        participant_a="Player A",
        participant_b="Player B",
        selection="player_a",
        odds=1.80,
        model_probability=0.56,
        market_probability=0.5555,
        edge=0.0045, # < 0.03
        confidence=0.70,
        model_version="test_v1",
    )
    
    c_low_conf = CandidateItem(
        event_id=str(uuid.uuid4()),
        participant_a="Player C",
        participant_b="Player D",
        selection="player_c",
        odds=2.00,
        model_probability=0.60,
        market_probability=0.50,
        edge=0.10, # > 0.03
        confidence=0.40, # < 0.50
        model_version="test_v1",
    )
    
    c_valid = CandidateItem(
        event_id=str(uuid.uuid4()),
        participant_a="Player E",
        participant_b="Player F",
        selection="player_e",
        odds=1.90,
        model_probability=0.62,
        market_probability=0.5263,
        edge=0.0937, # > 0.03
        confidence=0.65, # > 0.50
        model_version="test_v1",
    )
    
    shortlisted = CandidateSelector.shortlist_candidates([c_low_edge, c_low_conf, c_valid], config)
    
    assert len(shortlisted) == 1
    assert shortlisted[0].selection == "player_e"
    assert c_low_edge.is_shortlisted is False
    assert c_low_conf.is_shortlisted is False
    assert shortlisted[0].is_shortlisted is True

    # Also test evaluate_candidate directly
    c_evaluated = CandidateSelector.evaluate_candidate(
        event_id=str(uuid.uuid4()),
        sport_code="tennis",
        participant_a="Player E",
        participant_b="Player F",
        tournament=None,
        market="match_winner",
        selection="player_e",
        odds=1.90,
        model_probability=0.62,
        confidence=0.65,
        model_version="test_v1",
        min_edge=0.03,
        min_confidence=0.50,
    )
    assert c_evaluated.is_shortlisted is True
    print("✓ test_candidate_selector_shortlist_and_cost_control passed")


def test_ml_only_mode_execution():
    """Verify pure ML-only mode (llm_enabled=False) generates proposal without LLM intervention."""
    event_id = uuid.uuid4()
    with SessionLocal() as session:
        _ensure_sport(session)
        # Create event in DB for foreign key
        evt = Event(
            id=event_id,
            source="fonbet",
            source_event_id=f"ext_{event_id.hex[:8]}",
            sport_code="tennis",
            participant_a_name="Alcaraz C.",
            participant_b_name="Medvedev D.",
            scheduled_start_at=datetime.now(timezone.utc),
            status="prematch",
        )
        session.add(evt)
        session.commit()

        config = DecisionPipelineConfig(llm_enabled=False, max_stake_fraction=0.015)
        pipeline = CombinedDecisionPipeline(config=config)

        candidate = CandidateItem(
            event_id=str(event_id),
            participant_a="Alcaraz C.",
            participant_b="Medvedev D.",
            selection="player_a",
            odds=1.85,
            model_probability=0.65,
            market_probability=0.5405,
            edge=0.1095,
            confidence=0.72,
            model_version="tennis_v1",
            is_shortlisted=True,
        )

        res = pipeline.evaluate_candidate(session, candidate)
        session.commit()

        assert res.status == "PROPOSED"
        assert res.llm_decision_id is None
        assert res.proposal_id is not None
        assert "ML-Only Mode" in res.reason or "ML-Only strategy" in res.reason

        # Verify proposal persisted in DB
        proposal = session.query(BetProposal).filter(BetProposal.id == uuid.UUID(res.proposal_id)).first()
        assert proposal is not None
        assert proposal.event_id == event_id
        assert proposal.llm_decision_id is None
        assert float(proposal.suggested_stake) > 0.0

    print("✓ test_ml_only_mode_execution passed")


def test_ml_llm_approval_and_persistence():
    """Verify ML+LLM approval (verdict=BET) persists LLMDecision and links to BetProposal."""
    event_id = uuid.uuid4()
    mock_verdict = LLMStructuredVerdict(
        verdict="BET",
        market_type="match_winner",
        selection_id="player_a",
        confidence=0.88,
        stake_recommendation_fraction=0.015,
        reason_codes=["MODEL_EDGE", "TACTICAL_ADVANTAGE"],
        summary="Positive head-to-head match-up on clay and player is in prime physical condition.",
        evidence=[
            LLMEvidenceItem(domain="tennis.com", note="Alcaraz won last 3 meetings on clay")
        ],
        model_identifier="qwen-2.5-3b-instruct",
    )

    with SessionLocal() as session:
        _ensure_sport(session)
        evt = Event(
            id=event_id,
            source="fonbet",
            source_event_id=f"ext_{event_id.hex[:8]}",
            sport_code="tennis",
            participant_a_name="Alcaraz C.",
            participant_b_name="Zverev A.",
            scheduled_start_at=datetime.now(timezone.utc),
            status="prematch",
        )
        session.add(evt)
        session.commit()

        config = DecisionPipelineConfig(llm_enabled=True)
        pipeline = CombinedDecisionPipeline(config=config)

        candidate = CandidateItem(
            event_id=str(event_id),
            participant_a="Alcaraz C.",
            participant_b="Zverev A.",
            selection="player_a",
            odds=1.75,
            model_probability=0.68,
            market_probability=0.5714,
            edge=0.1086,
            confidence=0.80,
            model_version="tennis_v1",
            is_shortlisted=True,
        )

        with patch.object(pipeline, "_query_llm", return_value=mock_verdict):
            res = pipeline.evaluate_candidate(session, candidate)
            session.commit()

        assert res.status == "PROPOSED"
        assert res.llm_verdict == "BET"
        assert res.llm_decision_id is not None
        assert res.proposal_id is not None

        # Verify LLMDecision record in DB
        llm_dec = session.query(LLMDecision).filter(LLMDecision.id == uuid.UUID(res.llm_decision_id)).first()
        assert llm_dec is not None
        assert llm_dec.event_id == event_id
        assert llm_dec.verdict == "BET"
        assert llm_dec.raw_json is not None
        assert llm_dec.raw_json["model_identifier"] == "qwen-2.5-3b-instruct"

        # Verify BetProposal links to LLMDecision
        prop = session.query(BetProposal).filter(BetProposal.id == uuid.UUID(res.proposal_id)).first()
        assert prop is not None
        assert prop.llm_decision_id == llm_dec.id

    print("✓ test_ml_llm_approval_and_persistence passed")


def test_ml_llm_qualitative_risk_rejection():
    """Verify LLM rejecting bet (verdict=NO_BET due to injury risk) suppresses BetProposal."""
    event_id = uuid.uuid4()
    mock_verdict = LLMStructuredVerdict(
        verdict="NO_BET",
        market_type="match_winner",
        selection_id="player_a",
        confidence=0.30,
        stake_recommendation_fraction=0.0,
        reason_codes=["INJURY_RISK", "ABDOMINAL_STRAIN"],
        summary="Player showed signs of acute abdominal strain during yesterday practice session.",
        evidence=[
            LLMEvidenceItem(domain="sports-news.com", note="Player reported discomfort in practice")
        ],
        model_identifier="qwen-2.5-3b-instruct",
    )

    with SessionLocal() as session:
        _ensure_sport(session)
        evt = Event(
            id=event_id,
            source="fonbet",
            source_event_id=f"ext_{event_id.hex[:8]}",
            sport_code="tennis",
            participant_a_name="Djokovic N.",
            participant_b_name="Rune H.",
            scheduled_start_at=datetime.now(timezone.utc),
            status="prematch",
        )
        session.add(evt)
        session.commit()

        config = DecisionPipelineConfig(llm_enabled=True)
        pipeline = CombinedDecisionPipeline(config=config)

        candidate = CandidateItem(
            event_id=str(event_id),
            participant_a="Djokovic N.",
            participant_b="Rune H.",
            selection="player_a",
            odds=1.55,
            model_probability=0.74,
            market_probability=0.6451,
            edge=0.0949,
            confidence=0.82,
            model_version="tennis_v1",
            is_shortlisted=True,
        )

        with patch.object(pipeline, "_query_llm", return_value=mock_verdict):
            res = pipeline.evaluate_candidate(session, candidate)
            session.commit()

        assert res.status == "LLM_REJECTED"
        assert res.llm_verdict == "NO_BET"
        assert res.llm_decision_id is not None
        assert res.proposal_id is None
        assert "INJURY_RISK" in res.reason

        # Proposal must NOT exist
        proposals = session.query(BetProposal).filter(
            BetProposal.event_id == event_id,
            BetProposal.llm_decision_id == uuid.UUID(res.llm_decision_id)
        ).all()
        assert len(proposals) == 0

    print("✓ test_ml_llm_qualitative_risk_rejection passed")


def test_strategy_comparator_metrics():
    """Verify StrategyComparator computes incremental PnL and false positive reduction correctly."""
    evaluations = [
        # Candidate 1: Both placed, won
        {"ml_only": True, "llm_accepted": True, "actual_outcome": "WON", "odds": 2.0, "stake": 100.0},
        # Candidate 2: Both placed, lost
        {"ml_only": True, "llm_accepted": True, "actual_outcome": "LOST", "odds": 1.9, "stake": 100.0},
        # Candidate 3: LLM rejected due to injury; turned out to be a LOSS (False positive prevented!)
        {"ml_only": True, "llm_accepted": False, "actual_outcome": "LOST", "odds": 1.8, "stake": 100.0},
        # Candidate 4: LLM rejected; turned out to be a WIN (opportunity missed)
        {"ml_only": True, "llm_accepted": False, "actual_outcome": "WON", "odds": 2.2, "stake": 100.0},
    ]

    report = LLMStrategyComparator.compare(evaluations)

    # ML-Only:
    # 1: +100
    # 2: -100
    # 3: -100
    # 4: +120
    # ML total pnl = +20 on 400 stake => ROI = +5.0%
    assert report.total_candidates == 4
    assert report.ml_only_bets_count == 4
    assert report.ml_llm_bets_count == 2
    assert report.llm_filtered_count == 2
    assert report.ml_only_pnl == 20.0

    # ML+LLM:
    # 1: +100
    # 2: -100
    # ML+LLM total pnl = 0 on 200 stake => ROI = 0.0%
    assert report.ml_llm_pnl == 0.0

    # False positive reduction: 1 loss out of 2 filtered = 50.0%
    assert report.false_positive_reduction_rate == 0.5
    print("✓ test_strategy_comparator_metrics passed")


def test_backend_decision_api_endpoints():
    """Verify backend API endpoints for decisions evaluation, history, and strategy comparison."""
    client = TestClient(app)

    # 1. Evaluate endpoint
    event_id = str(uuid.uuid4())
    with SessionLocal() as session:
        _ensure_sport(session)
        evt = Event(
            id=uuid.UUID(event_id),
            source="fonbet",
            source_event_id=f"ext_{event_id[:8]}",
            sport_code="tennis",
            participant_a_name="Sinner J.",
            participant_b_name="Fritz T.",
            scheduled_start_at=datetime.now(timezone.utc),
            status="prematch",
        )
        session.add(evt)
        session.commit()

    eval_payload = {
        "options": [
            {
                "event_id": event_id,
                "participant_a": "Sinner J.",
                "participant_b": "Fritz T.",
                "selection": "player_a",
                "odds": 1.70,
                "model_probability": 0.68,
                "confidence": 0.75,
            },
            {
                "event_id": event_id,
                "participant_a": "Sinner J.",
                "participant_b": "Fritz T.",
                "selection": "player_b",
                "odds": 2.20,
                "model_probability": 0.32, # edge < 0
                "confidence": 0.45,
            }
        ],
        "config": {
            "llm_enabled": False, # ML only test
            "min_edge": 0.03,
            "min_confidence": 0.50,
        }
    }

    resp = client.post("/api/decisions/evaluate", json=eval_payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["total_candidates"] == 2
    assert data["shortlisted_count"] == 1
    assert len(data["decisions"]) == 2
    assert data["decisions"][0]["status"] == "PROPOSED"
    assert data["decisions"][1]["status"] == "FILTERED_OUT"

    # 2. History endpoint
    resp_hist = client.get("/api/decisions/history")
    assert resp_hist.status_code == 200
    assert isinstance(resp_hist.json(), list)

    # 3. Strategy comparison endpoint
    resp_comp = client.post("/api/decisions/strategy-comparison", json={"evaluations": []})
    assert resp_comp.status_code == 200
    comp_data = resp_comp.json()
    assert "total_candidates" in comp_data
    assert "incremental_pnl" in comp_data

    print("✓ test_backend_decision_api_endpoints passed")


if __name__ == "__main__":
    test_candidate_selector_shortlist_and_cost_control()
    test_ml_only_mode_execution()
    test_ml_llm_approval_and_persistence()
    test_ml_llm_qualitative_risk_rejection()
    test_strategy_comparator_metrics()
    test_backend_decision_api_endpoints()
    print("\n🎉 ALL PHASE 16 DECISION LAYER TESTS PASSED SUCCESSFULLY!")
