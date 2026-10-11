"""
Shadow Mode Engine for Neurobet.

Implements parallel model inference coordination, prediction drift measurement
(Population Stability Index across 10 bins & Wasserstein distance),
counterfactual betting performance segmented by odds and edge brackets,
and strict promotion gating.
"""

from datetime import datetime, timedelta, timezone
import logging
import math
from typing import Any, Dict, List, Optional, Tuple

from contracts.predictions import FeatureVector, MLPrediction
from contracts.shadow import (
    ShadowConfig,
    ShadowPredictionPair,
    DriftAnalysisReport,
    SegmentPerformance,
    ShadowEvaluationReport,
)

logger = logging.getLogger("neural.shadow")


class ShadowEngine:
    """
    Coordinates active and shadow models.
    Executes parallel inference, calculates drift and segment performance,
    and enforces promotion constraints.
    """

    def __init__(
        self,
        active_predictor: Optional[Any] = None,
        shadow_predictor: Optional[Any] = None,
        config: Optional[ShadowConfig] = None,
    ):
        self.active_predictor = active_predictor
        self.shadow_predictor = shadow_predictor
        self.config = config or ShadowConfig()
        self.pairs_history: List[ShadowPredictionPair] = []
        self.ground_truth_history: Dict[str, str] = {}  # event_id -> actual_winner

    def evaluate_parallel(
        self,
        feature_vector: FeatureVector,
        odds_snapshot: Dict[str, float],
        eval_time: Optional[datetime] = None,
    ) -> ShadowPredictionPair:
        """
        Runs both active and shadow models concurrently on the same feature vector
        and identical odds snapshot.
        Guarantees that shadow prediction has is_shadow=True.
        """
        eval_time = eval_time or datetime.now(timezone.utc)

        # Active prediction (is_shadow=False)
        active_pred: MLPrediction = self.active_predictor.predict(
            features=feature_vector,
            odds_snapshot=odds_snapshot,
            is_shadow=False,
        )

        # Shadow prediction (is_shadow=True)
        shadow_pred: MLPrediction = self.shadow_predictor.predict(
            features=feature_vector,
            odds_snapshot=odds_snapshot,
            is_shadow=True,
        )

        # Force is_shadow flag integrity using immutable copy
        if active_pred.is_shadow:
            active_pred = active_pred.model_copy(update={"is_shadow": False})
        if not shadow_pred.is_shadow:
            shadow_pred = shadow_pred.model_copy(update={"is_shadow": True})

        # Calculate differential metrics
        prob_diff = round(
            abs(active_pred.outcomes[0].model_probability - shadow_pred.outcomes[0].model_probability),
            4,
        )
        act_best_edge = max((o.edge for o in active_pred.outcomes), default=0.0)
        shd_best_edge = max((o.edge for o in shadow_pred.outcomes), default=0.0)
        edge_diff = round(
            abs(act_best_edge - shd_best_edge),
            4,
        )
        outcomes_match = (active_pred.best_outcome == shadow_pred.best_outcome)

        pair = ShadowPredictionPair(
            event_id=feature_vector.event_id,
            timestamp=eval_time,
            active_prediction=active_pred,
            shadow_prediction=shadow_pred,
            prob_diff_outcome_a=prob_diff,
            outcomes_match=outcomes_match,
            edge_diff=edge_diff,
        )
        self.pairs_history.append(pair)
        return pair

    def register_ground_truth(self, event_id: str, actual_winner: str) -> None:
        """Records ground truth winner for counterfactual evaluation."""
        self.ground_truth_history[event_id] = actual_winner

    @classmethod
    def compute_drift(cls, pairs: List[ShadowPredictionPair]) -> DriftAnalysisReport:
        """
        Computes Population Stability Index (PSI), Wasserstein distance,
        and outcome agreement rate between Active and Shadow probability distributions.
        Uses 10 bins on [0.0, 1.0] with smoothing factor epsilon = 1e-4.
        """
        if not pairs:
            return DriftAnalysisReport(
                probability_drift_psi=0.0,
                wasserstein_distance=0.0,
                mean_prob_shift=0.0,
                agreement_rate=1.0,
                drift_status="STABLE",
                psi_by_bin={},
            )

        probs_active = [p.active_prediction.outcomes[0].model_probability for p in pairs]
        probs_shadow = [p.shadow_prediction.outcomes[0].model_probability for p in pairs]

        n = len(pairs)
        agreement_count = sum(1 for p in pairs if p.outcomes_match)
        agreement_rate = round(agreement_count / n, 4)

        # Wasserstein distance on 1D empirical distributions
        diffs = [s - a for s, a in zip(probs_shadow, probs_active)]
        wasserstein_dist = round(sum(abs(d) for d in diffs) / n, 4)
        mean_shift = round(sum(diffs) / n, 4)

        # 10-bin Population Stability Index (PSI)
        bins = [round(i * 0.1, 4) for i in range(11)]
        psi_total = 0.0
        psi_by_bin: Dict[str, float] = {}

        eps = 1e-4  # Smoothing factor
        for b in range(10):
            b_low = bins[b]
            b_high = bins[b + 1]
            bin_name = f"[{b_low:.1f}, {b_high:.1f})"

            if b < 9:
                count_act = sum(1 for p in probs_active if b_low <= p < b_high)
                count_shd = sum(1 for p in probs_shadow if b_low <= p < b_high)
            else:
                count_act = sum(1 for p in probs_active if b_low <= p <= b_high)
                count_shd = sum(1 for p in probs_shadow if b_low <= p <= b_high)

            frac_act = max(eps, float(count_act) / n)
            frac_shd = max(eps, float(count_shd) / n)

            psi_bin = (frac_shd - frac_act) * math.log(frac_shd / frac_act)
            psi_total += psi_bin
            psi_by_bin[bin_name] = round(float(psi_bin), 4)

        psi_total = round(float(psi_total), 4)

        # PSI thresholding
        if psi_total < 0.10:
            drift_status = "STABLE"
        elif psi_total < 0.25:
            drift_status = "MODERATE_DRIFT"
        else:
            drift_status = "SEVERE_DRIFT"

        return DriftAnalysisReport(
            probability_drift_psi=psi_total,
            wasserstein_distance=wasserstein_dist,
            mean_prob_shift=mean_shift,
            agreement_rate=agreement_rate,
            drift_status=drift_status,
            psi_by_bin=psi_by_bin,
        )

    @classmethod
    def compute_segment_performance(
        cls,
        pairs: List[ShadowPredictionPair],
        ground_truth_map: Dict[str, str],
        stake_per_bet: float = 1000.0,
    ) -> Tuple[Dict[str, SegmentPerformance], Dict[str, SegmentPerformance]]:
        """
        Computes counterfactual betting simulation for shadow model
        partitioned across odds segments and edge segments.
        """
        odds_bins = {
            "1.01 - 1.50": lambda o: 1.01 <= o < 1.50,
            "1.50 - 2.00": lambda o: 1.50 <= o < 2.00,
            "2.00 - 3.00": lambda o: 2.00 <= o < 3.00,
            "3.00+": lambda o: o >= 3.00,
        }

        edge_bins = {
            "2% - 5% edge": lambda e: 0.02 <= e < 0.05,
            "5% - 10% edge": lambda e: 0.05 <= e < 0.10,
            "10%+ edge": lambda e: e >= 0.10,
        }

        def new_stats():
            return {
                "evaluations_count": 0,
                "counterfactual_bets_count": 0,
                "counterfactual_turnover": 0.0,
                "counterfactual_pnl": 0.0,
                "brier_sum": 0.0,
                "log_loss_sum": 0.0,
            }

        odds_stats = {k: new_stats() for k in odds_bins}
        edge_stats = {k: new_stats() for k in edge_bins}

        for pair in pairs:
            shd = pair.shadow_prediction
            evt_id = pair.event_id
            actual_winner = ground_truth_map.get(evt_id)

            best_outcome = shd.best_outcome
            best_opt = next((o for o in shd.outcomes if o.outcome == best_outcome), shd.outcomes[0])
            cand_odds = best_opt.bookmaker_odds
            cand_edge = best_opt.edge

            matched_odds_seg = None
            for seg_name, predicate in odds_bins.items():
                if predicate(cand_odds):
                    matched_odds_seg = seg_name
                    break

            matched_edge_seg = None
            for seg_name, predicate in edge_bins.items():
                if predicate(cand_edge):
                    matched_edge_seg = seg_name
                    break

            def update_stats(st: Dict[str, Any]):
                st["evaluations_count"] += 1
                if shd.has_positive_edge:
                    st["counterfactual_bets_count"] += 1
                    st["counterfactual_turnover"] += stake_per_bet
                    if actual_winner:
                        won = (best_outcome == actual_winner)
                        profit = (stake_per_bet * (cand_odds - 1.0)) if won else (-stake_per_bet)
                        st["counterfactual_pnl"] += profit

                        y_true = 1 if (actual_winner == "player_a") else 0
                        prob_a = shd.outcomes[0].model_probability
                        st["brier_sum"] += (prob_a - y_true) ** 2
                        safe_p = max(1e-6, min(1.0 - 1e-6, prob_a if y_true == 1 else (1.0 - prob_a)))
                        st["log_loss_sum"] += -math.log(safe_p)

            if matched_odds_seg:
                update_stats(odds_stats[matched_odds_seg])
            if matched_edge_seg:
                update_stats(edge_stats[matched_edge_seg])

        def build_segments(stats_dict: Dict[str, Dict[str, Any]]) -> Dict[str, SegmentPerformance]:
            res = {}
            for k, st in stats_dict.items():
                b_cnt = st["counterfactual_bets_count"]
                turnover = round(st["counterfactual_turnover"], 2)
                pnl = round(st["counterfactual_pnl"], 2)
                roi = round(pnl / turnover, 4) if turnover > 0 else 0.0
                win_rate = round(max(0.0, (pnl + turnover) / (turnover * 2.0)), 4) if turnover > 0 else 0.0
                brier = round(st["brier_sum"] / b_cnt, 4) if b_cnt > 0 else 0.0
                lloss = round(st["log_loss_sum"] / b_cnt, 4) if b_cnt > 0 else 0.0

                res[k] = SegmentPerformance(
                    segment_name=k,
                    evaluations_count=st["evaluations_count"],
                    counterfactual_bets_count=b_cnt,
                    counterfactual_turnover=turnover,
                    counterfactual_pnl=pnl,
                    counterfactual_roi=roi,
                    counterfactual_win_rate=win_rate,
                    brier_score=brier,
                    log_loss=lloss,
                )
            return res

        odds_results = build_segments(odds_stats)
        edge_results = build_segments(edge_stats)
        return odds_results, edge_results

    def generate_evaluation_report(
        self,
        shadow_start_time: datetime,
        shadow_end_time: datetime,
        stake_per_bet: float = 1000.0,
    ) -> ShadowEvaluationReport:
        """
        Builds complete ShadowEvaluationReport comparing active and shadow models.
        """
        duration = shadow_end_time - shadow_start_time
        duration_hours = max(0.0, duration.total_seconds() / 3600.0)
        duration_days = round(duration_hours / 24.0, 2)

        total_evals = len(self.pairs_history)

        # Active betting summary
        active_bets_count = 0
        active_turnover = 0.0
        active_pnl = 0.0
        active_brier_sum = 0.0
        active_logloss_sum = 0.0

        # Shadow counterfactual betting summary
        shadow_bets_count = 0
        shadow_turnover = 0.0
        shadow_pnl = 0.0
        shadow_brier_sum = 0.0
        shadow_logloss_sum = 0.0

        for pair in self.pairs_history:
            actual_winner = self.ground_truth_history.get(pair.event_id)

            # Active evaluation
            act = pair.active_prediction
            if act.has_positive_edge:
                active_bets_count += 1
                active_turnover += stake_per_bet
                if actual_winner:
                    act_won = (act.best_outcome == actual_winner)
                    act_odds = next((o.bookmaker_odds for o in act.outcomes if o.outcome == act.best_outcome), 2.0)
                    active_pnl += (stake_per_bet * (act_odds - 1.0)) if act_won else (-stake_per_bet)

                    y_true = 1 if (actual_winner == "player_a") else 0
                    p_a = act.outcomes[0].model_probability
                    active_brier_sum += (p_a - y_true) ** 2
                    safe_p = max(1e-6, min(1.0 - 1e-6, p_a if y_true == 1 else (1.0 - p_a)))
                    active_logloss_sum += -math.log(safe_p)

            # Shadow evaluation
            shd = pair.shadow_prediction
            if shd.has_positive_edge:
                shadow_bets_count += 1
                shadow_turnover += stake_per_bet
                if actual_winner:
                    shd_won = (shd.best_outcome == actual_winner)
                    shd_odds = next((o.bookmaker_odds for o in shd.outcomes if o.outcome == shd.best_outcome), 2.0)
                    shadow_pnl += (stake_per_bet * (shd_odds - 1.0)) if shd_won else (-stake_per_bet)

                    y_true = 1 if (actual_winner == "player_a") else 0
                    p_a = shd.outcomes[0].model_probability
                    shadow_brier_sum += (p_a - y_true) ** 2
                    safe_p = max(1e-6, min(1.0 - 1e-6, p_a if y_true == 1 else (1.0 - p_a)))
                    shadow_logloss_sum += -math.log(safe_p)

        active_roi = round(active_pnl / active_turnover, 4) if active_turnover > 0 else 0.0
        shadow_roi = round(shadow_pnl / shadow_turnover, 4) if shadow_turnover > 0 else 0.0

        active_brier = round(active_brier_sum / max(1, active_bets_count), 4)
        shadow_brier = round(shadow_brier_sum / max(1, shadow_bets_count), 4)
        active_log_loss = round(active_logloss_sum / max(1, active_bets_count), 4)
        shadow_log_loss = round(shadow_logloss_sum / max(1, shadow_bets_count), 4)

        # Drift analysis
        drift = self.compute_drift(self.pairs_history)

        # Segment breakdown
        odds_perf, edge_perf = self.compute_segment_performance(
            pairs=self.pairs_history,
            ground_truth_map=self.ground_truth_history,
            stake_per_bet=stake_per_bet,
        )

        # Promotion Gate Evaluation
        rejection_reasons = []
        if duration_days < self.config.min_shadow_period_days:
            rejection_reasons.append(
                f"INSUFFICIENT_SHADOW_DURATION: observed {duration_days:.1f} days, required at least {self.config.min_shadow_period_days} days."
            )

        if total_evals < self.config.min_shadow_evaluations:
            rejection_reasons.append(
                f"INSUFFICIENT_EVALUATIONS: observed {total_evals} evaluations, required at least {self.config.min_shadow_evaluations}."
            )

        if drift.probability_drift_psi > self.config.max_acceptable_drift_psi:
            rejection_reasons.append(
                f"EXCESSIVE_PREDICTION_DRIFT: probability PSI ({drift.probability_drift_psi:.4f}) exceeds threshold ({self.config.max_acceptable_drift_psi:.4f})."
            )

        if shadow_roi < self.config.min_shadow_roi:
            rejection_reasons.append(
                f"SUB_PAR_COUNTERFACTUAL_ROI: shadow ROI ({shadow_roi*100:.2f}%) is below minimum requirement ({self.config.min_shadow_roi*100:.2f}%)."
            )

        brier_delta = shadow_brier - active_brier
        if brier_delta > self.config.max_acceptable_brier_delta:
            rejection_reasons.append(
                f"CALIBRATION_DEGRADATION: shadow Brier score delta ({brier_delta:+.4f}) exceeds max allowed ({self.config.max_acceptable_brier_delta:.4f})."
            )

        meets_criteria = (len(rejection_reasons) == 0)
        if meets_criteria:
            verdict = "PROMOTION_APPROVED: Candidate model satisfies all shadow period, drift, and performance requirements."
        else:
            verdict = f"PROMOTION_BLOCKED: Candidate failed {len(rejection_reasons)} mandatory criteria: {'; '.join(rejection_reasons)}"

        return ShadowEvaluationReport(
            id=f"shd_rep_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
            active_model_version=self.config.active_model_version,
            shadow_model_version=self.config.shadow_model_version,
            sport_code=self.config.sport_code,
            market=self.config.market,
            shadow_period_start=shadow_start_time,
            shadow_period_end=shadow_end_time,
            shadow_duration_hours=round(duration_hours, 2),
            shadow_duration_days=duration_days,
            total_evaluations=total_evals,
            active_total_bets=active_bets_count,
            shadow_counterfactual_bets=shadow_bets_count,
            active_pnl=round(active_pnl, 2),
            shadow_counterfactual_pnl=round(shadow_pnl, 2),
            active_roi=active_roi,
            shadow_counterfactual_roi=shadow_roi,
            active_brier_score=active_brier,
            shadow_brier_score=shadow_brier,
            active_log_loss=active_log_loss,
            shadow_log_loss=shadow_log_loss,
            drift_analysis=drift,
            performance_by_odds_segment=odds_perf,
            performance_by_edge_segment=edge_perf,
            meets_promotion_criteria=meets_criteria,
            promotion_rejection_reasons=rejection_reasons,
            verdict=verdict,
            created_at=datetime.now(timezone.utc),
        )
