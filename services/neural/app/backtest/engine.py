from datetime import datetime, timedelta
import logging
from typing import List, Dict, Any, Optional, Tuple
import numpy as np

from .config import BacktestConfig
from .temporal import WalkForwardSplitter
from .execution import (
    ExecutionSimulator,
    OrderExecutionResult,
    SimulatedOddsSnapshot,
    logit_clv,
    no_vig_probability,
    quote_as_of,
)
from .bankroll import BacktestBankroll, SimulatedBet
from .metrics import BacktestMetricsCalculator, BacktestSummary, bootstrap_interval
from ..commit import confirming_index
from ..strategy import stake_gate
from ..models import build_gradient_boosting_pipeline, build_logistic_regression_pipeline
from ..metrics import compute_classification_metrics
from db import SessionLocal
from db.models.ml_registry import ExperimentResult


logger = logging.getLogger("neural.backtest")


class BacktestSample:
    """A single chronological decision point in the backtester."""
    def __init__(
        self,
        event_id: str,
        decision_timestamp: datetime,
        feature_vector: np.ndarray,
        p1_odds: float,
        p2_odds: float,
        odds_snapshots: List[SimulatedOddsSnapshot],
        actual_winner: str,  # "p1" or "p2" or "void"
        finished_at: Optional[datetime] = None,
        market: str = "match_winner",
        research_published_at: Optional[datetime] = None,
        research_retrieved_at: Optional[datetime] = None,
        adverse_fatigue_injury_p1: bool = False,
        adverse_fatigue_injury_p2: bool = False,
        research_summary: Optional[str] = None,
    ):
        self.event_id = event_id
        self.decision_timestamp = decision_timestamp
        self.feature_vector = feature_vector
        self.p1_odds = p1_odds
        self.p2_odds = p2_odds
        self.odds_snapshots = odds_snapshots
        self.actual_winner = actual_winner
        self.finished_at = finished_at
        self.market = market
        self.research_published_at = research_published_at
        self.research_retrieved_at = research_retrieved_at
        self.adverse_fatigue_injury_p1 = adverse_fatigue_injury_p1
        self.adverse_fatigue_injury_p2 = adverse_fatigue_injury_p2
        self.research_summary = research_summary
        # Label: 1 if p1 won, 0 if p2 won
        self.label = 1 if actual_winner == "p1" else 0


def split_by_event(samples: List[BacktestSample], n_splits: int) -> List[Tuple[np.ndarray, np.ndarray]]:
    """Expanding folds that never put two snapshots of one match on different sides."""
    events: List[str] = []
    buckets: Dict[str, List[int]] = {}
    for index, sample in enumerate(samples):
        if sample.event_id not in buckets:
            events.append(sample.event_id)
            buckets[sample.event_id] = []
        buckets[sample.event_id].append(index)
    if len(events) < (n_splits + 1) * 2:
        raise ValueError(
            f"Нужно минимум {(n_splits + 1) * 2} матчей для {n_splits} фолдов, сейчас {len(events)}."
        )
    chunk = len(events) // (n_splits + 1)
    splits = []
    for fold in range(n_splits):
        test_start = (fold + 1) * chunk
        test_end = (fold + 2) * chunk if fold < n_splits - 1 else len(events)
        train_idx = [index for event_id in events[:test_start] for index in buckets[event_id]]
        test_idx = [index for event_id in events[test_start:test_end] for index in buckets[event_id]]
        splits.append((np.asarray(train_idx, dtype=int), np.asarray(test_idx, dtype=int)))
    return splits


def confirming_bet_indices(
    model,
    samples: List[BacktestSample],
    test_idx,
    min_edge: float,
    min_interval_sec: float = 0.0,
    required: int = 2,
    sport_code: str = "",
) -> set:
    """Sample positions where this fitted model confirms the same side twice."""
    groups: Dict[str, List[int]] = {}
    for idx in test_idx:
        groups.setdefault(samples[int(idx)].event_id, []).append(int(idx))
    allowed = set()
    for indices in groups.values():
        if len(indices) < 2:
            continue
        probabilities = []
        odds = []
        for idx in indices:
            sample = samples[idx]
            raw = model.predict_proba(np.asarray(sample.feature_vector, dtype=float).reshape(1, -1))[0]
            classes = [int(item) for item in getattr(model, "classes_", [0, 1])]
            prob_a = float(raw[classes.index(1)]) if 1 in classes else float(raw[-1])
            probabilities.append((prob_a, 1.0 - prob_a))
            odds.append((float(sample.p1_odds), float(sample.p2_odds)))
        times = [samples[idx].decision_timestamp for idx in indices]
        chosen = confirming_index(
            probabilities,
            odds,
            min_edge,
            times,
            min_interval_sec,
            required,
            str(samples[indices[0]].market or "match_winner"),
            sport_code,
        )
        if chosen is not None:
            allowed.add(indices[chosen])
    return allowed


def _thinned_indices(samples: List[BacktestSample], indices, limit: int = 12) -> List[int]:
    """Keep an even spread of each match, including its first and last snapshot."""
    groups: Dict[str, List[int]] = {}
    for idx in indices:
        groups.setdefault(samples[int(idx)].event_id, []).append(int(idx))
    chosen: List[int] = []
    for group in groups.values():
        if len(group) <= limit:
            chosen.extend(group)
            continue
        last = None
        for slot in range(limit):
            pick = group[int(round(slot * (len(group) - 1) / (limit - 1)))]
            if pick != last:
                chosen.append(pick)
                last = pick
    return chosen


class WalkForwardBacktester:
    """
    Orchestrates the entire walk-forward backtesting loop:
    1. Splits dataset chronologically with purge/embargo.
    2. Trains calibrated ML baseline on training fold.
    3. Evaluates model sequentially on out-of-sample test fold.
    4. Simulates order execution with network latency, staleness, and slippage checks.
    5. Manages bankroll, exposure, Kelly sizing, and virtual settlements.
    6. Produces comprehensive P&L, ROI, Drawdown, and bucketed analytics.
    7. Persists experiment results to PostgreSQL.
    """

    def __init__(self, config: Optional[BacktestConfig] = None):
        self.config = config or BacktestConfig()
        self.splitter = WalkForwardSplitter(
            n_splits=self.config.n_splits,
            split_mode=self.config.split_mode,
            purge_window_sec=self.config.purge_window_sec,
        )
        self.execution_sim = ExecutionSimulator(self.config)
        self.bankroll = BacktestBankroll(self.config)

    def run(
        self,
        samples: List[BacktestSample],
        model_type: str = "gradient_boosting",
        save_to_db: bool = True,
    ) -> BacktestSummary:
        """
        Execute walk-forward backtest across all folds.
        
        Args:
            samples: List of BacktestSample sorted chronologically.
            model_type: "gradient_boosting" or "logistic_regression".
            save_to_db: Whether to record results to experiment_results table.
        """
        if not samples:
            raise ValueError("No samples provided for backtesting.")

        # Ensure strict chronological sorting
        samples = sorted(samples, key=lambda s: s.decision_timestamp)
        timestamps = [s.decision_timestamp for s in samples]

        if self.config.group_by_event:
            splits = split_by_event(samples, self.config.n_splits)
        else:
            splits = self.splitter.split(timestamps)
        logger.info(f"Starting Walk-Forward Backtest with {len(splits)} folds on {len(samples)} samples.")

        total_orders_considered = 0
        total_orders_rejected = 0
        rejection_counts: Dict[str, int] = {}
        all_test_y_true: List[int] = []
        all_test_y_prob: List[float] = []

        # Error analysis tracking for LLM
        total_candidates_evaluated = 0
        veto_count = 0
        approval_count = 0
        true_positives = 0
        false_positives = 0
        true_negatives = 0
        false_negatives = 0
        avoided_loss_pnl = 0.0
        missed_profit_pnl = 0.0

        for fold_idx, (train_idx, test_idx) in enumerate(splits):
            logger.info(f"--- Fold {fold_idx + 1}/{len(splits)}: Train={len(train_idx)}, Test={len(test_idx)} ---")

            # 1. Prepare training matrix. Long matches are thinned so one timeline cannot dominate.
            fit_idx = _thinned_indices(samples, train_idx) if self.config.group_by_event else train_idx
            X_train = np.array([samples[i].feature_vector for i in fit_idx], dtype=np.float32)
            y_train = np.array([samples[i].label for i in fit_idx], dtype=np.int32)

            # 2. Train baseline model on train fold
            if model_type == "logistic_regression":
                model = build_logistic_regression_pipeline(random_state=self.config.random_seed)
            else:
                model = build_gradient_boosting_pipeline(random_state=self.config.random_seed)

            model.fit(X_train, y_train)
            bet_indices = (
                confirming_bet_indices(
                    model,
                    samples,
                    test_idx,
                    self.config.min_edge,
                    self.config.min_confirm_interval_sec,
                    self.config.confirmations,
                    self.config.sport_code,
                )
                if self.config.confirm_side
                else {int(idx) for idx in test_idx}
            )

            # 3. Step sequentially through out-of-sample test fold
            for idx in test_idx:
                sample = samples[idx]
                self.bankroll.release_due(sample.decision_timestamp)
                feat = sample.feature_vector.reshape(1, -1)

                # Predict probabilities
                probs = model.predict_proba(feat)[0]
                prob_p1 = float(probs[1])
                prob_p2 = float(probs[0])

                all_test_y_true.append(sample.label)
                all_test_y_prob.append(prob_p1)
                if int(idx) not in bet_indices:
                    continue

                # Check edge for P1 and P2
                edge_p1 = (prob_p1 * sample.p1_odds) - 1.0
                edge_p2 = (prob_p2 * sample.p2_odds) - 1.0

                # Select best betting candidate
                target_outcome = None
                target_prob = 0.0
                target_odds = 0.0
                target_edge = 0.0

                if edge_p1 >= self.config.min_edge and stake_gate(prob_p1, sample.p1_odds, sample.market, self.config.sport_code)[0]:
                    target_outcome = "p1"
                    target_prob = prob_p1
                    target_odds = sample.p1_odds
                    target_edge = edge_p1

                elif edge_p2 >= self.config.min_edge and stake_gate(prob_p2, sample.p2_odds, sample.market, self.config.sport_code)[0]:
                    target_outcome = "p2"
                    target_prob = prob_p2
                    target_odds = sample.p2_odds
                    target_edge = edge_p2

                if not target_outcome:
                    continue  # No profitable edge identified

                total_candidates_evaluated += 1

                # LLM qualitative reasoning & veto logic (if enabled)
                llm_verdict = None
                if self.config.use_llm:
                    is_vetoed = False
                    if target_outcome == "p1" and sample.adverse_fatigue_injury_p1:
                        is_vetoed = True
                    elif target_outcome == "p2" and sample.adverse_fatigue_injury_p2:
                        is_vetoed = True

                    if is_vetoed:
                        veto_count += 1
                        llm_verdict = "VETO"
                        hypo_won = (sample.actual_winner == target_outcome)
                        hypo_stake = self.bankroll.calculate_stake(
                            probability=target_prob,
                            odds=target_odds,
                        )
                        if hypo_won:
                            false_negatives += 1
                            missed_profit_pnl += hypo_stake * (target_odds - 1.0)
                        else:
                            true_negatives += 1
                            avoided_loss_pnl += hypo_stake
                        continue
                    else:
                        approval_count += 1
                        llm_verdict = "APPROVED"

                total_orders_considered += 1

                # 4. Realistic execution simulation (latency, as-of odds, staleness, suspension)
                exec_result: OrderExecutionResult = self.execution_sim.execute_order(
                    decision_time=sample.decision_timestamp,
                    model_probability=target_prob,
                    target_outcome=target_outcome,
                    decision_odds=target_odds,
                    odds_snapshots=sample.odds_snapshots,
                )

                if not exec_result.is_executed:
                    total_orders_rejected += 1
                    code = exec_result.rejection_code or "REJECTED"
                    rejection_counts[code] = rejection_counts.get(code, 0) + 1
                    logger.debug(f"Order rejected: {exec_result.rejection_code}")
                    continue

                # 5. Position Sizing & Virtual Bankroll Placement
                stake = self.bankroll.calculate_stake(
                    probability=target_prob,
                    odds=exec_result.execution_odds,
                )

                if stake <= 0:
                    continue

                age_sec = None
                fresh_bucket = "none"
                if sample.research_published_at:
                    age_sec = max(0.0, (sample.decision_timestamp - sample.research_published_at).total_seconds())
                    if age_sec <= 3600:
                        fresh_bucket = "<1h"
                    elif age_sec <= 21600:
                        fresh_bucket = "1-6h"
                    else:
                        fresh_bucket = ">6h"

                bet_id = f"bt_bet_{sample.event_id}_{target_outcome}_{len(self.bankroll.settled_bets) + len(self.bankroll.active_bets)}"
                bet = self.bankroll.place_bet(
                    bet_id=bet_id,
                    event_id=sample.event_id,
                    target_outcome=target_outcome,
                    placed_at=exec_result.execution_timestamp,
                    odds=exec_result.execution_odds,
                    stake=stake,
                    probability=target_prob,
                    edge=exec_result.effective_edge,
                    research_age_seconds=age_sec,
                    research_freshness_bucket=fresh_bucket,
                    llm_verdict=llm_verdict,
                )

                if not bet:
                    total_orders_rejected += 1
                    continue

                # 6. Hold the stake until the recorded finish. Samples without one close after 45 minutes.
                match_end_time = sample.finished_at or (sample.decision_timestamp + timedelta(minutes=45))
                if match_end_time < exec_result.execution_timestamp:
                    match_end_time = exec_result.execution_timestamp
                executed = quote_as_of(sample.odds_snapshots, exec_result.execution_timestamp)
                closed = quote_as_of(sample.odds_snapshots, match_end_time)
                if executed and closed:
                    bet.clv = logit_clv(
                        no_vig_probability(closed.p1_odds, closed.p2_odds, target_outcome),
                        no_vig_probability(executed.p1_odds, executed.p2_odds, target_outcome),
                    )
                if self.config.use_llm:
                    self.bankroll.settle_bet(bet.bet_id, sample.actual_winner, match_end_time)
                else:
                    bet.pending_winner = sample.actual_winner
                    bet.due_at = match_end_time

                if self.config.use_llm and bet.status in ("WON", "LOST"):
                    if bet.status == "WON":
                        true_positives += 1
                    elif bet.status == "LOST":
                        false_positives += 1

        horizon = max(
            (sample.finished_at or sample.decision_timestamp) for sample in samples
        )
        self.bankroll.release_due(horizon + timedelta(days=1))

        # 7. Calculate statistical probabilistic metrics and calibration curve
        stat_metrics = {}
        calibration_curve_data = {}
        if all_test_y_true and all_test_y_prob:
            y_true_arr = np.array(all_test_y_true, dtype=int)
            y_prob_arr = np.array(all_test_y_prob, dtype=float)
            stat_metrics = compute_classification_metrics(y_true_arr, y_prob_arr)

            # Compute 10-bin calibration curve
            bins = np.linspace(0.0, 1.0, 11)
            bin_indices = np.digitize(y_prob_arr, bins) - 1
            calib_points = []
            for b in range(10):
                mask = bin_indices == b
                b_count = int(np.sum(mask))
                if b_count > 0:
                    b_true = float(np.mean(y_true_arr[mask]))
                    b_pred = float(np.mean(y_prob_arr[mask]))
                else:
                    b_true = 0.0
                    b_pred = float((bins[b] + bins[b + 1]) / 2.0)
                calib_points.append({
                    "bin_index": b,
                    "bin_range": f"[{bins[b]:.2f}, {bins[b+1]:.2f})",
                    "mean_predicted_prob": round(b_pred, 4),
                    "fraction_positives": round(b_true, 4),
                    "sample_count": b_count,
                })

            calibration_curve_data = {
                "n_bins": 10,
                "points": calib_points,
                "expected_calibration_error": stat_metrics.get("expected_calibration_error", 0.0),
                "brier_score": stat_metrics.get("brier_score", 0.0),
                "log_loss": stat_metrics.get("log_loss", 0.0),
            }

        # 8. Compute error analysis metrics
        veto_precision = (true_negatives / (true_negatives + false_negatives)) if (true_negatives + false_negatives) > 0 else 0.0
        approval_precision = (true_positives / (true_positives + false_positives)) if (true_positives + false_positives) > 0 else 0.0
        net_veto_value_pnl = avoided_loss_pnl - missed_profit_pnl

        error_analysis_data = {
            "total_candidates_evaluated": total_candidates_evaluated,
            "veto_count": veto_count,
            "approval_count": approval_count,
            "true_positives": true_positives,
            "false_positives": false_positives,
            "true_negatives": true_negatives,
            "false_negatives": false_negatives,
            "veto_precision": round(veto_precision, 4),
            "approval_precision": round(approval_precision, 4),
            "avoided_loss_pnl": round(avoided_loss_pnl, 2),
            "missed_profit_pnl": round(missed_profit_pnl, 2),
            "net_veto_value_pnl": round(net_veto_value_pnl, 2),
        }

        by_event: Dict[str, List[SimulatedBet]] = {}
        for settled in self.bankroll.settled_bets:
            by_event.setdefault(settled.event_id, []).append(settled)
        event_roi = []
        event_clv = []
        for bets in by_event.values():
            turnover = sum(bet.stake for bet in bets)
            if turnover > 0:
                event_roi.append(sum(bet.net_profit for bet in bets) / turnover)
            clv_values = [bet.clv for bet in bets if bet.clv is not None]
            if clv_values:
                event_clv.append(sum(clv_values) / len(clv_values))
        roi_mean, roi_low, roi_high = bootstrap_interval(event_roi, seed=self.config.random_seed)
        clv_mean, clv_low, clv_high = bootstrap_interval(event_clv, seed=self.config.random_seed)
        execution_stats = {
            "mean_clv": clv_mean,
            "roi_low": roi_low,
            "roi_high": roi_high,
            "clv_low": clv_low,
            "clv_high": clv_high,
            "rejected_stale": rejection_counts.get("STALE_ODDS", 0),
            "event_roi": roi_mean,
        }
        error_analysis_data["rejections"] = rejection_counts

        # 9. Calculate complete summary metrics
        summary = BacktestMetricsCalculator.compute_summary(
            initial_bankroll=self.bankroll.initial_balance,
            final_bankroll=self.bankroll.current_balance,
            settled_bets=self.bankroll.settled_bets,
            equity_curve=self.bankroll.equity_curve,
            peak_exposure=self.bankroll.peak_exposure,
            statistical_metrics=stat_metrics,
            calibration_curve=calibration_curve_data,
            error_analysis=error_analysis_data,
            execution_stats=execution_stats,
        )

        logger.info(
            f"Backtest Complete! Final Balance: {summary.final_bankroll:.2f} RUB "
            f"| P&L: {summary.net_pnl:+.2f} RUB | ROI: {summary.roi*100:.2f}% "
            f"| WinRate: {summary.win_rate*100:.1f}% | MaxDrawdown: {summary.max_drawdown_pct*100:.2f}% "
            f"| LogLoss: {summary.log_loss:.4f} | Brier: {summary.brier_score:.4f} | ECE: {summary.expected_calibration_error:.4f}"
        )

        # 10. Save results to database if requested
        if save_to_db:
            self._save_experiment_result(summary, model_type)

        return summary

    def _save_experiment_result(self, summary: BacktestSummary, model_type: str) -> None:
        """Record experiment in PostgreSQL experiment_results table."""
        try:
            with SessionLocal() as db:
                exp_name = getattr(self.config, "experiment_name", None) or f"WalkForward_{self.config.sport_code}_{model_type}_{self.config.stake_strategy}"
                strat_dict = self.config.model_dump()
                strat_dict["config"] = self.config.model_dump()
                strat_dict["statistical_metrics"] = {
                    "log_loss": summary.log_loss,
                    "brier_score": summary.brier_score,
                    "roc_auc": summary.roc_auc,
                    "expected_calibration_error": summary.expected_calibration_error,
                }
                strat_dict["calibration_curve"] = summary.calibration_curve
                strat_dict["odds_buckets"] = {k: v.__dict__ for k, v in summary.odds_buckets.items()}
                strat_dict["edge_buckets"] = {k: v.__dict__ for k, v in summary.edge_buckets.items()}
                strat_dict["freshness_buckets"] = {k: v.__dict__ for k, v in summary.freshness_buckets.items()}
                strat_dict["error_analysis"] = summary.error_analysis

                exp = ExperimentResult(
                    name=exp_name,
                    strategy_config=strat_dict,
                    backtest_pnl=float(summary.net_pnl),
                    backtest_roi=float(summary.roi),
                    win_rate=float(summary.win_rate),
                    max_drawdown=float(summary.max_drawdown_pct),
                )

                db.add(exp)
                db.commit()
                logger.info(f"Saved ExperimentResult to database with ID: {exp.id}")
        except Exception as e:
            logger.warning(f"Could not persist ExperimentResult to database: {e}")



def generate_synthetic_backtest_samples(
    n_events: int = 200,
    random_seed: int = 42,
) -> List[BacktestSample]:
    """
    Generates realistic, chronologically ordered synthetic tennis events
    with odds time-series, suspensions, features, qualitative research packets, and true outcomes.
    Used for deterministic backtesting validation and unit tests.
    """
    rng = np.random.RandomState(random_seed)
    base_time = datetime(2026, 6, 1, 10, 0, 0)
    samples = []

    for i in range(n_events):
        event_time = base_time + timedelta(minutes=15 * i)
        event_id = f"evt_sim_{i:04d}"

        # True latent probability for player 1 winning
        true_p1 = rng.beta(2.5, 2.5)

        # Generate qualitative research publication timestamps
        freshness_rand = rng.rand()
        if freshness_rand < 0.30:
            # < 1 hour old
            research_pub = event_time - timedelta(minutes=int(rng.uniform(10, 50)))
        elif freshness_rand < 0.65:
            # 1 to 6 hours old
            research_pub = event_time - timedelta(hours=int(rng.uniform(1.5, 5.5)))
        elif freshness_rand < 0.85:
            # > 6 hours old
            research_pub = event_time - timedelta(hours=int(rng.uniform(7, 24)))
        else:
            # No external research available
            research_pub = None

        research_retrieved = event_time - timedelta(minutes=2) if research_pub else None

        # Contextual risk factors (fatigue / minor injury)
        has_adverse_p1 = (rng.rand() < 0.12)
        has_adverse_p2 = (rng.rand() < 0.12) if not has_adverse_p1 else False

        # True probability shifts according to contextual fatigue / injury
        if has_adverse_p1:
            true_p1 = max(0.08, true_p1 - 0.28)
        elif has_adverse_p2:
            true_p1 = min(0.92, true_p1 + 0.28)

        actual_winner = "p1" if rng.rand() < true_p1 else "p2"

        # Generate odds with slight bookmaker margin (overround ~ 5%)
        # Add random noise to create realistic value / edge opportunities
        est_p1 = np.clip(true_p1 + rng.normal(0, 0.08), 0.15, 0.85)
        est_p2 = 1.0 - est_p1
        p1_odds = round(max(1.05, 1.0 / (est_p1 * 1.05)), 2)
        p2_odds = round(max(1.05, 1.0 / (est_p2 * 1.05)), 2)

        # Create 10 continuous odds snapshots spaced 5 seconds apart
        snapshots = []
        for s_idx in range(10):
            snap_time = event_time - timedelta(seconds=(9 - s_idx) * 5)
            # Drift odds slightly over time
            drift = rng.normal(0, 0.02)
            snap_p1 = round(max(1.02, p1_odds + drift), 2)
            snap_p2 = round(max(1.02, p2_odds - drift), 2)
            is_susp = (rng.rand() < 0.04)  # 4% chance of temporary suspension

            snapshots.append(
                SimulatedOddsSnapshot(
                    snapshot_id=f"snap_{event_id}_{s_idx}",
                    observed_at=snap_time,
                    p1_odds=snap_p1,
                    p2_odds=snap_p2,
                    is_suspended=is_susp,
                )
            )

        # Generate 43 synthetic features correlated with true_p1
        features = np.zeros(43, dtype=np.float32)
        features[0] = p1_odds
        features[1] = p2_odds
        features[2] = true_p1 + rng.normal(0, 0.1)  # score diff proxy
        features[3] = rng.choice([-1.0, 0.0, 1.0])   # drift indicator
        features[4:] = rng.normal(0, 1.0, size=39)   # rolling features

        samples.append(
            BacktestSample(
                event_id=event_id,
                decision_timestamp=event_time,
                feature_vector=features,
                p1_odds=p1_odds,
                p2_odds=p2_odds,
                odds_snapshots=snapshots,
                actual_winner=actual_winner,
                research_published_at=research_pub,
                research_retrieved_at=research_retrieved,
                adverse_fatigue_injury_p1=has_adverse_p1,
                adverse_fatigue_injury_p2=has_adverse_p2,
            )
        )

    return samples
