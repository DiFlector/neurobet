"""Football Feature Builder with rolling windows, match dynamics, and zero data leakage."""

import statistics
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from contracts import FeatureVector
from .base import (
    ROLLING_WINDOWS,
    calculate_line_drift,
    last_quoted_odds,
    calculate_odds_delta,
    calculate_odds_velocity,
    calculate_volatility,
)


class FootballFeatureBuilder:
    """
    Constructs deterministic numerical feature vector for live football events.
    Enforces strict point-in-time cutoff: no observations beyond cutoff_time are ever accessed.
    """

    sport_code: str = "football"
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
        odds_x_records = [o for o in valid_odds if getattr(o, "outcome", "") in ("draw", "x", "X")]
        odds_t2_records = [o for o in valid_odds if getattr(o, "outcome", "") in ("team_b", "2", "away", "player_b")]

        latest_t1_odds = last_quoted_odds(odds_t1_records)
        latest_x_odds = last_quoted_odds(odds_x_records)
        latest_t2_odds = last_quoted_odds(odds_t2_records)
        is_missing_odds = float(latest_t1_odds <= 1.0 or latest_t2_odds <= 1.0)

        features["current_odds_team_a"] = round(latest_t1_odds, 4)
        features["current_odds_draw"] = round(latest_x_odds, 4)
        features["current_odds_team_b"] = round(latest_t2_odds, 4)
        features["is_missing_odds"] = is_missing_odds

        # 3. No-vig probabilities and margin. A missing side contributes nothing.
        prob_1 = 1.0 / latest_t1_odds if latest_t1_odds > 1.0 else 0.0
        prob_x = 1.0 / latest_x_odds if latest_x_odds > 1.0 else 0.0
        prob_2 = 1.0 / latest_t2_odds if latest_t2_odds > 1.0 else 0.0
        total_implied = prob_1 + prob_x + prob_2
        margin = max(0.0, total_implied - 1.0)

        if total_implied > 0 and latest_t1_odds > 1.0 and latest_t2_odds > 1.0:
            no_vig_a = round(prob_1 / total_implied, 4)
            no_vig_x = round(prob_x / total_implied, 4)
            no_vig_b = round(prob_2 / total_implied, 4)
        else:
            no_vig_a, no_vig_x, no_vig_b = 0.5, 0.0, 0.5
            margin = 0.0

        features["no_vig_prob_team_a"] = no_vig_a
        features["no_vig_prob_draw"] = no_vig_x
        features["no_vig_prob_team_b"] = no_vig_b
        features["bookmaker_margin"] = round(margin, 4)

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

        # 5. Football match state features
        if valid_states:
            latest_state = valid_states[-1]
            score_detail = getattr(latest_state, "score_detail", {}) or getattr(latest_state, "stats", {}) or {}

            score_a = float(getattr(latest_state, "score_a", 0) or score_detail.get("score_a", 0))
            score_b = float(getattr(latest_state, "score_b", 0) or score_detail.get("score_b", 0))
            goal_diff = score_a - score_b
            total_goals = score_a + score_b

            curr_period = float(getattr(latest_state, "current_period", 1) or score_detail.get("current_period", 1))
            minute = float(score_detail.get("minute", 45.0 if curr_period == 1 else 90.0))

            # Cards
            yellow = score_detail.get("yellow_cards", {})
            yellow_a = float(yellow.get("team_a", 0))
            yellow_b = float(yellow.get("team_b", 0))
            yellow_diff = yellow_a - yellow_b

            red = score_detail.get("red_cards", {})
            red_a = float(red.get("team_a", 0))
            red_b = float(red.get("team_b", 0))
            red_diff = red_a - red_b
            has_red = 1.0 if (red_a > 0 or red_b > 0) else 0.0

            # Corners
            corners = score_detail.get("corners", {})
            corners_a = float(corners.get("team_a", 0))
            corners_b = float(corners.get("team_b", 0))
            corners_diff = corners_a - corners_b

            # Shots on goal
            shots = score_detail.get("shots_on_goal", {})
            shots_a = float(shots.get("team_a", 0))
            shots_b = float(shots.get("team_b", 0))
            shots_diff = shots_a - shots_b
            shots_ratio = round((shots_a + 1.0) / (shots_a + shots_b + 2.0), 4)

            # Possession
            poss = score_detail.get("possession_pct", {})
            poss_a = float(poss.get("team_a", 50.0))
            poss_b = float(poss.get("team_b", 50.0))

            # Expected goals (xG)
            xg = score_detail.get("xg", {})
            xg_a = float(xg.get("team_a", 0.0))
            xg_b = float(xg.get("team_b", 0.0))
            xg_diff = round(xg_a - xg_b, 4)

            # Clutch time: 2nd half minute >= 75 and 1-goal diff or tie
            is_clutch = 1.0 if (minute >= 75.0 and abs(goal_diff) <= 1.0) else 0.0
            is_second_half = 1.0 if curr_period >= 2 else 0.0
            is_et = 1.0 if (curr_period == 3 or bool(score_detail.get("is_extra_time"))) else 0.0
            is_pen = 1.0 if (curr_period >= 4 or bool(score_detail.get("is_penalties"))) else 0.0

            features["score_team_a"] = score_a
            features["score_team_b"] = score_b
            features["goal_differential"] = goal_diff
            features["total_goals"] = total_goals
            features["current_period"] = curr_period
            features["match_minute"] = minute
            features["is_second_half"] = is_second_half
            features["is_extra_time"] = is_et
            features["is_penalties"] = is_pen
            features["yellow_cards_team_a"] = yellow_a
            features["yellow_cards_team_b"] = yellow_b
            features["yellow_cards_diff"] = yellow_diff
            features["red_cards_team_a"] = red_a
            features["red_cards_team_b"] = red_b
            features["red_cards_diff"] = red_diff
            features["has_red_card"] = has_red
            features["corners_team_a"] = corners_a
            features["corners_team_b"] = corners_b
            features["corners_differential"] = corners_diff
            features["shots_team_a"] = shots_a
            features["shots_team_b"] = shots_b
            features["shots_differential"] = shots_diff
            features["shots_ratio"] = shots_ratio
            features["possession_team_a"] = poss_a
            features["possession_team_b"] = poss_b
            features["xg_team_a"] = xg_a
            features["xg_team_b"] = xg_b
            features["xg_differential"] = xg_diff
            features["is_clutch_time"] = is_clutch
        else:
            features["score_team_a"] = 0.0
            features["score_team_b"] = 0.0
            features["goal_differential"] = 0.0
            features["total_goals"] = 0.0
            features["current_period"] = 1.0
            features["match_minute"] = 0.0
            features["is_second_half"] = 0.0
            features["is_extra_time"] = 0.0
            features["is_penalties"] = 0.0
            features["yellow_cards_team_a"] = 0.0
            features["yellow_cards_team_b"] = 0.0
            features["yellow_cards_diff"] = 0.0
            features["red_cards_team_a"] = 0.0
            features["red_cards_team_b"] = 0.0
            features["red_cards_diff"] = 0.0
            features["has_red_card"] = 0.0
            features["corners_team_a"] = 0.0
            features["corners_team_b"] = 0.0
            features["corners_differential"] = 0.0
            features["shots_team_a"] = 0.0
            features["shots_team_b"] = 0.0
            features["shots_differential"] = 0.0
            features["shots_ratio"] = 0.5
            features["possession_team_a"] = 50.0
            features["possession_team_b"] = 50.0
            features["xg_team_a"] = 0.0
            features["xg_team_b"] = 0.0
            features["xg_differential"] = 0.0
            features["is_clutch_time"] = 0.0

        return FeatureVector(
            event_id=str(event_id),
            sport_code=self.sport_code,
            feature_version=self.feature_version,
            timestamp=cutoff_dt,
            features=features,
        )
