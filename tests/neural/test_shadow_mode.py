"""
Unit and integration tests for Phase 25: Shadow Mode.

Tests:
1. Shadow predictions flag emission (is_shadow=True vs is_shadow=False).
2. Bet Manager strict rejection of shadow proposals (SHADOW_MODEL_CANNOT_BET).
3. Drift analysis (10-bin PSI, Wasserstein distance, probability shift).
4. Counterfactual performance calculation segmented by odds and edge buckets.
5. Strict promotion gate evaluation (duration, evaluations count, drift PSI, ROI, Brier calibration).
6. Backend REST API endpoints for shadow monitoring and gated promotion.
"""

import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

paths = [
    "/srv/neurobet/packages/contracts/src",
    "/srv/neurobet/packages/features/src",
    "/srv/neurobet/packages/db/src",
    "/srv/neurobet/packages/sports-core/src",
    "/srv/neurobet/packages/bankroll/src",
    "/srv/neurobet/services/neural",
    "/srv/neurobet/services/backend",
    "/srv/neurobet/services/bet-manager",
    "/app/packages/contracts/src",
    "/app/packages/features/src",
    "/app/packages/db/src",
    "/app/packages/sports-core/src",
    "/app/packages/bankroll/src",
    "/app",
]
for p in paths:
    if p not in sys.path:
        sys.path.insert(0, p)

from contracts import (
    FeatureVector,
    OutcomePrediction,
    MLPrediction,
    ShadowConfig,
    ShadowPredictionPair,
    DriftAnalysisReport,
    SegmentPerformance,
    ShadowEvaluationReport,
    ShadowEngine,
    BetProposal,
)
try:
    from bankroll import BetValidator
except ImportError:
    try:
        from app.validator import BetValidator
    except ImportError:
        from bankroll.validator import BetValidator

try:
    import tennis_adapter
except ImportError:
    pass


def create_dummy_prediction(
    event_id: str,
    p_a: float,
    odds_a: float = 2.0,
    odds_b: float = 2.0,
    is_shadow: bool = False,
    model_version: str = "v1.0",
) -> MLPrediction:
    p_b = round(1.0 - p_a, 4)
    edge_a = round(p_a * odds_a - 1.0, 4)
    edge_b = round(p_b * odds_b - 1.0, 4)

    fair_odds_a = round(1.0 / max(0.001, p_a), 4)
    fair_odds_b = round(1.0 / max(0.001, p_b), 4)
    ev_a = round(p_a * (odds_a - 1.0) - (1.0 - p_a), 4)
    ev_b = round(p_b * (odds_b - 1.0) - (1.0 - p_b), 4)

    outcomes = [
        OutcomePrediction(
            outcome="player_a",
            model_probability=p_a,
            bookmaker_odds=odds_a,
            fair_odds=fair_odds_a,
            edge=edge_a,
            expected_value=ev_a,
            confidence=0.85,
        ),
        OutcomePrediction(
            outcome="player_b",
            model_probability=p_b,
            bookmaker_odds=odds_b,
            fair_odds=fair_odds_b,
            edge=edge_b,
            expected_value=ev_b,
            confidence=0.85,
        ),
    ]
    best_outcome = "player_a" if p_a >= p_b else "player_b"
    best_edge = max(edge_a, edge_b)

    return MLPrediction(
        prediction_id=f"pred_{event_id}_{'shadow' if is_shadow else 'active'}",
        event_id=event_id,
        model_version=model_version,
        sport_code="tennis",
        market="match_winner",
        outcomes=outcomes,
        best_outcome=best_outcome,
        has_positive_edge=(best_edge > 0.0),
        is_shadow=is_shadow,
    )


def test_shadow_config_defaults():
    config = ShadowConfig()
    assert config.min_shadow_period_days == 7.0
    assert config.min_shadow_evaluations == 100
    assert config.max_acceptable_drift_psi == 0.25
    assert config.min_shadow_roi == 0.0
    assert config.max_acceptable_brier_delta == 0.02


def test_parallel_predictions_execution():
    """Verify ShadowEngine runs both models in parallel and enforces is_shadow flags."""
    mock_active = MagicMock()
    mock_shadow = MagicMock()

    mock_active.predict.return_value = create_dummy_prediction("evt_1", p_a=0.60, is_shadow=False, model_version="v1.0")
    mock_shadow.predict.return_value = create_dummy_prediction("evt_1", p_a=0.65, is_shadow=True, model_version="v2.0-shadow")

    engine = ShadowEngine(active_predictor=mock_active, shadow_predictor=mock_shadow)

    features = FeatureVector(
        event_id="evt_1",
        sport_code="tennis",
        features={"player_a_rank": 15, "player_b_rank": 25},
        timestamp=datetime.now(timezone.utc),
    )

    pair = engine.evaluate_parallel(feature_vector=features, odds_snapshot={"player_a": 2.0, "player_b": 2.0})

    assert pair.active_prediction.is_shadow is False
    assert pair.shadow_prediction.is_shadow is True
    assert pair.prob_diff_outcome_a == 0.05
    assert pair.outcomes_match is True
    assert len(engine.pairs_history) == 1


def test_bet_manager_strictly_rejects_shadow_proposal():
    """Verify BetValidator rejects any proposal marked with is_shadow=True."""
    validator = BetValidator()

    # Active proposal should pass shadow gate
    active_proposal = BetProposal(
        proposal_id="prop_act_1",
        event_id="evt_real_1",
        sport_code="tennis",
        market="match_winner",
        outcome="player_a",
        selection_id="sel_a",
        bookmaker_odds=1.95,
        fair_odds=1.54,
        model_probability=0.65,
        edge=0.2675,
        suggested_stake=500.0,
        ml_prediction_id="pred_1",
        is_shadow=False,
    )
    res_active = validator.validate_proposal(active_proposal)
    assert res_active.is_valid is True

    # Shadow proposal must be rejected immediately
    shadow_proposal = BetProposal(
        proposal_id="prop_shd_1",
        event_id="evt_real_1",
        sport_code="tennis",
        market="match_winner",
        outcome="player_a",
        selection_id="sel_a",
        bookmaker_odds=1.95,
        fair_odds=1.54,
        model_probability=0.65,
        edge=0.2675,
        suggested_stake=500.0,
        ml_prediction_id="pred_1",
        is_shadow=True,
    )
    res_shadow = validator.validate_proposal(shadow_proposal)
    assert res_shadow.is_valid is False
    assert res_shadow.rejection_reason == "SHADOW_MODEL_CANNOT_BET"
    assert "cannot generate real or virtual bets" in res_shadow.rejection_details


def test_drift_calculation_psi_and_wasserstein():
    """Verify 10-bin PSI and Wasserstein distance under stable vs severe drift scenarios."""
    # Case 1: Identical distributions (Zero drift)
    pairs_identical = [
        ShadowPredictionPair(
            event_id=f"evt_{i}",
            timestamp=datetime.now(timezone.utc),
            active_prediction=create_dummy_prediction(f"evt_{i}", p_a=0.55, is_shadow=False),
            shadow_prediction=create_dummy_prediction(f"evt_{i}", p_a=0.55, is_shadow=True),
            prob_diff_outcome_a=0.0,
            outcomes_match=True,
            edge_diff=0.0,
        )
        for i in range(50)
    ]
    report_stable = ShadowEngine.compute_drift(pairs_identical)
    assert report_stable.probability_drift_psi == 0.0
    assert report_stable.wasserstein_distance == 0.0
    assert report_stable.drift_status == "STABLE"
    assert report_stable.agreement_rate == 1.0
    assert len(report_stable.psi_by_bin) == 10

    # Case 2: Divergent distributions (Severe drift)
    pairs_divergent = [
        ShadowPredictionPair(
            event_id=f"evt_{i}",
            timestamp=datetime.now(timezone.utc),
            active_prediction=create_dummy_prediction(f"evt_{i}", p_a=0.20, is_shadow=False),
            shadow_prediction=create_dummy_prediction(f"evt_{i}", p_a=0.85, is_shadow=True),
            prob_diff_outcome_a=0.65,
            outcomes_match=False,
            edge_diff=1.30,
        )
        for i in range(100)
    ]
    report_divergent = ShadowEngine.compute_drift(pairs_divergent)
    assert report_divergent.probability_drift_psi > 0.25
    assert report_divergent.wasserstein_distance == 0.65
    assert report_divergent.drift_status == "SEVERE_DRIFT"
    assert report_divergent.agreement_rate == 0.0


def test_counterfactual_segment_performance():
    """Verify counterfactual performance across odds and edge brackets."""
    pairs = [
        # Bracket 1.01-1.50 odds, edge 2%-5%
        ShadowPredictionPair(
            event_id="evt_odds_low",
            timestamp=datetime.now(timezone.utc),
            active_prediction=create_dummy_prediction("evt_odds_low", p_a=0.75, odds_a=1.40),
            shadow_prediction=create_dummy_prediction("evt_odds_low", p_a=0.74, odds_a=1.40, is_shadow=True),  # edge = 0.74*1.4-1 = 0.036 (3.6%)
            prob_diff_outcome_a=0.01,
            outcomes_match=True,
            edge_diff=0.014,
        ),
        # Bracket 2.00-3.00 odds, edge 10%+
        ShadowPredictionPair(
            event_id="evt_odds_high",
            timestamp=datetime.now(timezone.utc),
            active_prediction=create_dummy_prediction("evt_odds_high", p_a=0.55, odds_a=2.20),
            shadow_prediction=create_dummy_prediction("evt_odds_high", p_a=0.60, odds_a=2.20, is_shadow=True),  # edge = 0.60*2.2-1 = 0.32 (32%)
            prob_diff_outcome_a=0.05,
            outcomes_match=True,
            edge_diff=0.11,
        ),
    ]
    ground_truth = {
        "evt_odds_low": "player_a",   # Won: profit = 1000 * 0.40 = +400
        "evt_odds_high": "player_b",  # Lost: profit = -1000
    }

    odds_perf, edge_perf = ShadowEngine.compute_segment_performance(pairs, ground_truth, stake_per_bet=1000.0)

    # Check 1.01-1.50 bracket
    seg_low = odds_perf["1.01 - 1.50"]
    assert seg_low.evaluations_count == 1
    assert seg_low.counterfactual_bets_count == 1
    assert seg_low.counterfactual_pnl == 400.0
    assert seg_low.counterfactual_roi == 0.40

    # Check 2.00-3.00 bracket
    seg_high = odds_perf["2.00 - 3.00"]
    assert seg_high.evaluations_count == 1
    assert seg_high.counterfactual_bets_count == 1
    assert seg_high.counterfactual_pnl == -1000.0
    assert seg_high.counterfactual_roi == -1.0

    # Check edge bracket 2%-5%
    seg_edge_low = edge_perf["2% - 5% edge"]
    assert seg_edge_low.counterfactual_bets_count == 1
    assert seg_edge_low.counterfactual_pnl == 400.0

    # Check edge bracket 10%+
    seg_edge_high = edge_perf["10%+ edge"]
    assert seg_edge_high.counterfactual_bets_count == 1
    assert seg_edge_high.counterfactual_pnl == -1000.0


def test_promotion_gating_scenarios():
    """Verify promotion gate strictly blocks or approves candidate models."""
    now = datetime.now(timezone.utc)

    # 1. Blocked: Insufficient duration (< 7 days)
    engine = ShadowEngine(
        config=ShadowConfig(
            min_shadow_period_days=7.0,
            min_shadow_evaluations=10,
        )
    )
    for i in range(15):
        pair = ShadowPredictionPair(
            event_id=f"e_{i}",
            timestamp=now,
            active_prediction=create_dummy_prediction(f"e_{i}", p_a=0.60),
            shadow_prediction=create_dummy_prediction(f"e_{i}", p_a=0.61, is_shadow=True),
            prob_diff_outcome_a=0.01,
            outcomes_match=True,
            edge_diff=0.02,
        )
        engine.pairs_history.append(pair)
        engine.register_ground_truth(f"e_{i}", "player_a")

    rep_short = engine.generate_evaluation_report(
        shadow_start_time=now - timedelta(days=2),  # Only 2 days
        shadow_end_time=now,
    )
    assert rep_short.meets_promotion_criteria is False
    assert any("INSUFFICIENT_SHADOW_DURATION" in r for r in rep_short.promotion_rejection_reasons)

    # 2. Blocked: Insufficient evaluations (< 100)
    rep_few_evals = engine.generate_evaluation_report(
        shadow_start_time=now - timedelta(days=10),
        shadow_end_time=now,
    )
    # Default requires 100 evals, here we have 15
    engine.config = engine.config.model_copy(update={"min_shadow_evaluations": 100})
    rep_few_evals = engine.generate_evaluation_report(
        shadow_start_time=now - timedelta(days=10),
        shadow_end_time=now,
    )
    assert rep_few_evals.meets_promotion_criteria is False
    assert any("INSUFFICIENT_EVALUATIONS" in r for r in rep_few_evals.promotion_rejection_reasons)

    # 3. Blocked: Excessive drift
    engine_drift = ShadowEngine(
        config=ShadowConfig(
            min_shadow_period_days=5.0,
            min_shadow_evaluations=10,
            max_acceptable_drift_psi=0.15,
        )
    )
    for i in range(20):
        pair = ShadowPredictionPair(
            event_id=f"e_{i}",
            timestamp=now,
            active_prediction=create_dummy_prediction(f"e_{i}", p_a=0.15),
            shadow_prediction=create_dummy_prediction(f"e_{i}", p_a=0.85, is_shadow=True),
            prob_diff_outcome_a=0.70,
            outcomes_match=False,
            edge_diff=1.40,
        )
        engine_drift.pairs_history.append(pair)
        engine_drift.register_ground_truth(f"e_{i}", "player_a")

    rep_drift = engine_drift.generate_evaluation_report(
        shadow_start_time=now - timedelta(days=6),
        shadow_end_time=now,
    )
    assert rep_drift.meets_promotion_criteria is False
    assert any("EXCESSIVE_PREDICTION_DRIFT" in r for r in rep_drift.promotion_rejection_reasons)

    # 4. Approved: Satisfies all 5 promotion criteria
    engine_approved = ShadowEngine(
        config=ShadowConfig(
            min_shadow_period_days=7.0,
            min_shadow_evaluations=50,
            max_acceptable_drift_psi=0.25,
            min_shadow_roi=0.0,
            max_acceptable_brier_delta=0.02,
        )
    )
    for i in range(60):
        pair = ShadowPredictionPair(
            event_id=f"e_app_{i}",
            timestamp=now,
            active_prediction=create_dummy_prediction(f"e_app_{i}", p_a=0.60, odds_a=2.0),
            shadow_prediction=create_dummy_prediction(f"e_app_{i}", p_a=0.62, odds_a=2.0, is_shadow=True),
            prob_diff_outcome_a=0.02,
            outcomes_match=True,
            edge_diff=0.04,
        )
        engine_approved.pairs_history.append(pair)
        engine_approved.register_ground_truth(f"e_app_{i}", "player_a")  # Wins, generating positive ROI

    rep_approved = engine_approved.generate_evaluation_report(
        shadow_start_time=now - timedelta(days=8),  # 8 days >= 7 days
        shadow_end_time=now,
    )
    assert rep_approved.meets_promotion_criteria is True, f"Failed reasons: {rep_approved.promotion_rejection_reasons}"
    assert len(rep_approved.promotion_rejection_reasons) == 0
    assert "PROMOTION_APPROVED" in rep_approved.verdict


def test_backend_shadow_endpoints_mock():
    """Verify backend FastAPI endpoints for shadow models."""
    try:
        from services.backend.app.main import app
    except ImportError:
        from app.main import app
    from fastapi.testclient import TestClient

    client = TestClient(app)

    # 1. GET /api/models/shadow
    res_status = client.get("/api/models/shadow?sport_code=tennis")
    assert res_status.status_code == 200
    data = res_status.json()
    assert "sport_code" in data
    assert "metrics" in data

    # 2. GET /api/models/shadow/report
    res_report = client.get("/api/models/shadow/report?sport_code=tennis&min_shadow_period_days=1.0&min_shadow_evaluations=10")
    assert res_report.status_code == 200
    report_data = res_report.json()
    assert "drift_analysis" in report_data
    assert "performance_by_odds_segment" in report_data
    assert "performance_by_edge_segment" in report_data
    assert "meets_promotion_criteria" in report_data

    # 3. POST /api/models/shadow/register with unknown model -> 404
    res_reg_unknown = client.post("/api/models/shadow/register", json={"version_tag": "non-existent-tag"})
    assert res_reg_unknown.status_code == 404

    # 4. POST /api/models/shadow/promote with unknown model -> 404
    res_prom_unknown = client.post("/api/models/shadow/promote", json={"shadow_version_tag": "non-existent-tag"})
    assert res_prom_unknown.status_code == 404
    print("✓ test_backend_shadow_endpoints_mock passed")


if __name__ == "__main__":
    print("Running Phase 25 Shadow Mode Test Suite...")
    test_shadow_config_defaults()
    print("✓ test_shadow_config_defaults passed")
    test_parallel_predictions_execution()
    print("✓ test_parallel_predictions_execution passed")
    test_bet_manager_strictly_rejects_shadow_proposal()
    print("✓ test_bet_manager_strictly_rejects_shadow_proposal passed")
    test_drift_calculation_psi_and_wasserstein()
    print("✓ test_drift_calculation_psi_and_wasserstein passed")
    test_counterfactual_segment_performance()
    print("✓ test_counterfactual_segment_performance passed")
    test_promotion_gating_scenarios()
    print("✓ test_promotion_gating_scenarios passed")
    test_backend_shadow_endpoints_mock()
    print("\n🎉 ALL PHASE 25 SHADOW MODE TESTS PASSED SUCCESSFULLY!")
