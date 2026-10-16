"""Basketball Feature Builder with rolling windows, pace calculations, and zero data leakage."""

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


class BasketballFeatureBuilder:
    """
    Constructs deterministic numerical feature vector for live basketball events.
    Enforces strict point-in-time cutoff: no observations beyond cutoff_time are ever accessed.
    """

    sport_code: str = "basketball"
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
            current_quarter = float(getattr(latest_state, "current_period", 1) or 1)
            clock_sec = float(getattr(latest_state, "match_clock_seconds", 0) or 0)
            score_detail = getattr(latest_state, "score_detail", {}) or {}

            quarters_summary = score_detail.get("quarters_summary", [])
            pts_a = sum(q.get("a", 0) for q in quarters_summary)
            pts_b = sum(q.get("b", 0) for q in quarters_summary)

            # If quarters summary not present, check score_str or fallback
            if pts_a == 0 and pts_b == 0:
                raw_score = str(getattr(latest_state, "score", "") or "")
                import re
                m = re.search(r"(\d+)[\:\-](\d+)", raw_score)
                if m:
                    pts_a, pts_b = int(m.group(1)), int(m.group(2))

            curr_q_diff = 0.0
            if quarters_summary:
                last_q = quarters_summary[-1]
                curr_q_diff = float(last_q.get("a", 0) - last_q.get("b", 0))

            point_margin = float(pts_a - pts_b)
            total_pts = float(pts_a + pts_b)

            # Implied game pace: projected total points over 40-minute regulation game
            elapsed_minutes = max(1.0, (current_quarter - 1) * 10.0 + min(10.0, clock_sec / 60.0))
            projected_pace = round((total_pts / elapsed_minutes) * 40.0, 1)

            fouls_a = float(score_detail.get("fouls_a", 0))
            fouls_b = float(score_detail.get("fouls_b", 0))

            features["current_quarter"] = current_quarter
            features["match_clock_seconds"] = clock_sec
            features["points_team_a"] = float(pts_a)
            features["points_team_b"] = float(pts_b)
            features["point_differential"] = point_margin
            features["total_points_scored"] = total_pts
            features["current_quarter_diff"] = curr_q_diff
            features["implied_game_pace"] = projected_pace
            features["fouls_differential"] = fouls_a - fouls_b
            features["is_second_half"] = 1.0 if current_quarter >= 3 else 0.0
            features["is_crunch_time"] = 1.0 if (current_quarter >= 4 and abs(point_margin) <= 5.0) else 0.0
        else:
            features["current_quarter"] = 1.0
            features["match_clock_seconds"] = 0.0
            features["points_team_a"] = 0.0
            features["points_team_b"] = 0.0
            features["point_differential"] = 0.0
            features["total_points_scored"] = 0.0
            features["current_quarter_diff"] = 0.0
            features["implied_game_pace"] = 160.0
            features["fouls_differential"] = 0.0
            features["is_second_half"] = 0.0
            features["is_crunch_time"] = 0.0

        return FeatureVector(
            event_id=str(event_id),
            sport_code=self.sport_code,
            feature_version=self.feature_version,
            timestamp=cutoff_dt,
            features=features,
        )
