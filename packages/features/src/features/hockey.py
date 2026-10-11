"""Hockey Feature Builder with rolling windows, period dynamics, and zero data leakage."""

import statistics
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from contracts import FeatureVector
from .base import (
    ROLLING_WINDOWS,
    calculate_line_drift,
    calculate_no_vig_probabilities,
    calculate_odds_delta,
    calculate_odds_velocity,
    calculate_volatility,
)


class HockeyFeatureBuilder:
    """
    Constructs deterministic numerical feature vector for live hockey events.
    Enforces strict point-in-time cutoff: no observations beyond cutoff_time are ever accessed.
    """

    sport_code: str = "hockey"
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

        latest_t1_odds = float(odds_t1_records[-1].odds) if odds_t1_records else 1.90
        latest_t2_odds = float(odds_t2_records[-1].odds) if odds_t2_records else 1.90
        is_missing_odds = float(not bool(odds_t1_records and odds_t2_records))

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

        # 5. Hockey match state features
        if valid_states:
            latest_state = valid_states[-1]
            score_detail = getattr(latest_state, "score_detail", {}) or getattr(latest_state, "stats", {}) or {}

            score_a = float(getattr(latest_state, "score_a", 0) or score_detail.get("score_a", 0))
            score_b = float(getattr(latest_state, "score_b", 0) or score_detail.get("score_b", 0))
            goal_diff = score_a - score_b
            total_goals = score_a + score_b

            curr_period = float(getattr(latest_state, "current_period", 1) or score_detail.get("current_period", 1))

            # Shots on goal
            shots = score_detail.get("shots_on_goal", {})
            shots_a = float(shots.get("team_a", 0))
            shots_b = float(shots.get("team_b", 0))
            shots_diff = shots_a - shots_b
            shots_ratio = round((shots_a + 1.0) / (shots_a + shots_b + 2.0), 4)

            # Powerplay
            pp = score_detail.get("powerplay", {})
            is_pp = 1.0 if bool(pp.get("is_active")) else 0.0
            pp_team = pp.get("team")
            pp_a = 1.0 if (is_pp and pp_team in ("team_a", "home", "1")) else 0.0
            pp_b = 1.0 if (is_pp and pp_team in ("team_b", "away", "2")) else 0.0

            # Empty net
            en_a = 1.0 if bool(score_detail.get("empty_net_a")) else 0.0
            en_b = 1.0 if bool(score_detail.get("empty_net_b")) else 0.0

            # Overtime / Shootout
            is_ot = 1.0 if (curr_period == 4 or bool(score_detail.get("is_overtime"))) else 0.0
            is_so = 1.0 if (curr_period >= 5 or bool(score_detail.get("is_shootout"))) else 0.0

            # Clutch time: 3rd period, 1-goal diff or tie
            is_clutch = 1.0 if (curr_period == 3 and abs(goal_diff) <= 1.0) else 0.0

            features["score_team_a"] = score_a
            features["score_team_b"] = score_b
            features["goal_differential"] = goal_diff
            features["total_goals"] = total_goals
            features["current_period"] = curr_period
            features["is_overtime"] = is_ot
            features["is_shootout"] = is_so
            features["shots_team_a"] = shots_a
            features["shots_team_b"] = shots_b
            features["shots_differential"] = shots_diff
            features["shots_ratio"] = shots_ratio
            features["is_powerplay"] = is_pp
            features["is_powerplay_team_a"] = pp_a
            features["is_powerplay_team_b"] = pp_b
            features["is_empty_net_team_a"] = en_a
            features["is_empty_net_team_b"] = en_b
            features["is_clutch_time"] = is_clutch
        else:
            features["score_team_a"] = 0.0
            features["score_team_b"] = 0.0
            features["goal_differential"] = 0.0
            features["total_goals"] = 0.0
            features["current_period"] = 1.0
            features["is_overtime"] = 0.0
            features["is_shootout"] = 0.0
            features["shots_team_a"] = 0.0
            features["shots_team_b"] = 0.0
            features["shots_differential"] = 0.0
            features["shots_ratio"] = 0.5
            features["is_powerplay"] = 0.0
            features["is_powerplay_team_a"] = 0.0
            features["is_powerplay_team_b"] = 0.0
            features["is_empty_net_team_a"] = 0.0
            features["is_empty_net_team_b"] = 0.0
            features["is_clutch_time"] = 0.0

        return FeatureVector(
            event_id=str(event_id),
            sport_code=self.sport_code,
            feature_version=self.feature_version,
            timestamp=cutoff_dt,
            features=features,
        )
