"""Volleyball Feature Builder with rolling windows, set dynamics, and zero data leakage."""

import statistics
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from contracts import FeatureVector
from .base import (
    ROLLING_WINDOWS,
    calculate_line_drift,
    calculate_no_vig_probabilities,
    last_quoted_odds,
    calculate_odds_delta,
    calculate_odds_velocity,
    calculate_volatility,
)


class VolleyballFeatureBuilder:
    """
    Constructs deterministic numerical feature vector for live volleyball events.
    Enforces strict point-in-time cutoff: no observations beyond cutoff_time are ever accessed.
    """

    sport_code: str = "volleyball"
    feature_version: str = "1.0.0"

    def build_features(
        self,
        event_id: str | uuid.UUID,
        cutoff_time: datetime,
        event_snapshots: List[Any],
        odds_snapshots: List[Any],
    ) -> FeatureVector:
        """
        Builds feature vector strictly using snapshots with observed_at <= cutoff_time.
        Guarantee: Zero future data leakage!
        """
        if cutoff_time.tzinfo is None:
            cutoff_dt = cutoff_time.replace(tzinfo=timezone.utc)
        else:
            cutoff_dt = cutoff_time

        # 1. Strict point-in-time filter
        valid_states = [
            s for s in event_snapshots
            if (s.observed_at.replace(tzinfo=timezone.utc) if s.observed_at.tzinfo is None else s.observed_at) <= cutoff_dt
        ]
        valid_odds = [
            o for o in odds_snapshots
            if (o.observed_at.replace(tzinfo=timezone.utc) if o.observed_at.tzinfo is None else o.observed_at) <= cutoff_dt
        ]

        valid_states.sort(key=lambda s: s.observed_at)
        valid_odds.sort(key=lambda o: o.observed_at)

        features: Dict[str, float] = {}

        # 2. Extract latest odds at cutoff
        odds_t1_records = [o for o in valid_odds if getattr(o, "outcome", "") in ("team_a", "1", "home", "player_a")]
        odds_t2_records = [o for o in valid_odds if getattr(o, "outcome", "") in ("team_b", "2", "away", "player_b")]

        latest_t1_odds = last_quoted_odds(odds_t1_records)
        latest_t2_odds = last_quoted_odds(odds_t2_records)
        is_missing_odds = float(latest_t1_odds <= 1.0 or latest_t2_odds <= 1.0)

        features["current_odds_team_a"] = round(latest_t1_odds, 4)
        features["current_odds_team_b"] = round(latest_t2_odds, 4)
        features["is_missing_odds"] = is_missing_odds

        # 3. No-vig probabilities and margin
        no_vig_a, no_vig_b, margin = calculate_no_vig_probabilities(latest_t1_odds, latest_t2_odds)
        features["no_vig_prob_team_a"] = no_vig_a
        features["no_vig_prob_team_b"] = no_vig_b
        features["bookmaker_margin"] = margin

        # 4. Rolling window calculations (5s, 10s, 30s, 60s, 300s)
        for w in ROLLING_WINDOWS:
            window_start = cutoff_dt - timedelta(seconds=w)

            t1_in_window = [
                float(o.odds) for o in odds_t1_records
                if (o.observed_at.replace(tzinfo=timezone.utc) if o.observed_at.tzinfo is None else o.observed_at) >= window_start
            ]
            t2_in_window = [
                float(o.odds) for o in odds_t2_records
                if (o.observed_at.replace(tzinfo=timezone.utc) if o.observed_at.tzinfo is None else o.observed_at) >= window_start
            ]

            past_t1 = t1_in_window[0] if t1_in_window else latest_t1_odds
            delta_t1 = calculate_odds_delta(latest_t1_odds, past_t1)
            velocity_t1 = calculate_odds_velocity(delta_t1, w)
            volatility_t1 = calculate_volatility(t1_in_window)
            drift_t1 = calculate_line_drift(delta_t1)

            past_t2 = t2_in_window[0] if t2_in_window else latest_t2_odds
            delta_t2 = calculate_odds_delta(latest_t2_odds, past_t2)
            velocity_t2 = calculate_odds_velocity(delta_t2, w)
            volatility_t2 = calculate_volatility(t2_in_window)
            drift_t2 = calculate_line_drift(delta_t2)

            features[f"odds_delta_team_a_{w}s"] = delta_t1
            features[f"odds_velocity_team_a_{w}s"] = velocity_t1
            features[f"odds_volatility_team_a_{w}s"] = volatility_t1
            features[f"line_drift_team_a_{w}s"] = drift_t1

            features[f"odds_delta_team_b_{w}s"] = delta_t2
            features[f"odds_velocity_team_b_{w}s"] = velocity_t2
            features[f"odds_volatility_team_b_{w}s"] = volatility_t2
            features[f"line_drift_team_b_{w}s"] = drift_t2

        # 5. Live match scoring & state features
        if valid_states:
            latest_state = valid_states[-1]
            current_set = float(getattr(latest_state, "current_period", 1) or 1)
            score_detail = getattr(latest_state, "score_detail", {}) or {}

            sets_won_a = float(score_detail.get("sets_won_a", 0))
            sets_won_b = float(score_detail.get("sets_won_b", 0))
            sets_diff = sets_won_a - sets_won_b

            curr_score = score_detail.get("current_set_score", {})
            curr_pts_a = float(curr_score.get("points_a", 0))
            curr_pts_b = float(curr_score.get("points_b", 0))
            curr_pts_diff = curr_pts_a - curr_pts_b

            # Cumulative points
            volleyball_sets = score_detail.get("volleyball_sets", [])
            cumul_a = sum(s.get("points_a", 0) for s in volleyball_sets)
            cumul_b = sum(s.get("points_b", 0) for s in volleyball_sets)
            cumul_diff = float(cumul_a - cumul_b)

            is_tiebreak = 1.0 if (current_set == 5 or bool(score_detail.get("is_tiebreak_set"))) else 0.0
            is_match_pt = 1.0 if bool(score_detail.get("is_match_point")) else 0.0

            possession = getattr(latest_state, "possession", None)
            is_server_a = 1.0 if possession == "team_a" else 0.0
            is_server_b = 1.0 if possession == "team_b" else 0.0

            to_a = float(score_detail.get("timeouts_a", 0))
            to_b = float(score_detail.get("timeouts_b", 0))

            features["sets_won_team_a"] = sets_won_a
            features["sets_won_team_b"] = sets_won_b
            features["sets_differential"] = sets_diff
            features["current_set_number"] = current_set
            features["current_set_points_team_a"] = curr_pts_a
            features["current_set_points_team_b"] = curr_pts_b
            features["current_set_points_diff"] = curr_pts_diff
            features["cumulative_points_diff"] = cumul_diff
            features["is_tiebreak_set"] = is_tiebreak
            features["is_match_point"] = is_match_pt
            features["is_server_team_a"] = is_server_a
            features["is_server_team_b"] = is_server_b
            features["timeouts_differential"] = to_a - to_b
        else:
            features["sets_won_team_a"] = 0.0
            features["sets_won_team_b"] = 0.0
            features["sets_differential"] = 0.0
            features["current_set_number"] = 1.0
            features["current_set_points_team_a"] = 0.0
            features["current_set_points_team_b"] = 0.0
            features["current_set_points_diff"] = 0.0
            features["cumulative_points_diff"] = 0.0
            features["is_tiebreak_set"] = 0.0
            features["is_match_point"] = 0.0
            features["is_server_team_a"] = 0.0
            features["is_server_team_b"] = 0.0
            features["timeouts_differential"] = 0.0

        return FeatureVector(
            event_id=str(event_id),
            sport_code=self.sport_code,
            feature_version=self.feature_version,
            timestamp=cutoff_dt,
            features=features,
        )
