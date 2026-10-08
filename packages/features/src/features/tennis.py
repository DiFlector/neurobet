"""Tennis Feature Builder with rolling windows, no-vig calculations, and zero data leakage."""

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


class TennisFeatureBuilder:
    """
    Constructs deterministic numerical feature vector for live tennis events.
    Enforces strict point-in-time cutoff: no observations beyond cutoff_time are ever accessed.
    """

    sport_code: str = "tennis"
    feature_version: str = "1.0.0"

    POINT_MAPPING = {
        "0": 0.0,
        "15": 1.0,
        "30": 2.0,
        "40": 3.0,
        "AD": 4.0,
    }

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
        # Ensure timezone-aware UTC comparison
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

        # Sort chronologically
        valid_states.sort(key=lambda s: s.observed_at)
        valid_odds.sort(key=lambda o: o.observed_at)

        features: Dict[str, float] = {}

        # 2. Extract latest odds at cutoff
        odds_p1_records = [o for o in valid_odds if o.outcome in ("player_a", "1", "p1")]
        odds_p2_records = [o for o in valid_odds if o.outcome in ("player_b", "2", "p2")]

        latest_p1_odds = float(odds_p1_records[-1].odds) if odds_p1_records else 1.85
        latest_p2_odds = float(odds_p2_records[-1].odds) if odds_p2_records else 1.95
        is_missing_odds = float(not bool(odds_p1_records and odds_p2_records))

        features["current_odds_player_a"] = round(latest_p1_odds, 4)
        features["current_odds_player_b"] = round(latest_p2_odds, 4)
        features["is_missing_odds"] = is_missing_odds

        # 3. No-vig probabilities and market margin
        no_vig_a, no_vig_b, margin = calculate_no_vig_probabilities(latest_p1_odds, latest_p2_odds)
        features["no_vig_prob_player_a"] = no_vig_a
        features["no_vig_prob_player_b"] = no_vig_b
        features["bookmaker_margin"] = margin

        # 4. Rolling window calculations (5s, 10s, 30s, 60s, 300s)
        for w in ROLLING_WINDOWS:
            window_start = cutoff_dt - timedelta(seconds=w)

            # Filter snapshots in window [cutoff - w, cutoff]
            p1_in_window = [
                float(o.odds) for o in odds_p1_records
                if (o.observed_at.replace(tzinfo=timezone.utc) if o.observed_at.tzinfo is None else o.observed_at) >= window_start
            ]
            p2_in_window = [
                float(o.odds) for o in odds_p2_records
                if (o.observed_at.replace(tzinfo=timezone.utc) if o.observed_at.tzinfo is None else o.observed_at) >= window_start
            ]

            # Delta & Velocity for player_a
            past_p1 = p1_in_window[0] if p1_in_window else latest_p1_odds
            delta_p1 = calculate_odds_delta(latest_p1_odds, past_p1)
            velocity_p1 = calculate_odds_velocity(delta_p1, w)
            volatility_p1 = calculate_volatility(p1_in_window)
            drift_p1 = calculate_line_drift(delta_p1)

            # Delta & Velocity for player_b
            past_p2 = p2_in_window[0] if p2_in_window else latest_p2_odds
            delta_p2 = calculate_odds_delta(latest_p2_odds, past_p2)
            velocity_p2 = calculate_odds_velocity(delta_p2, w)
            volatility_p2 = calculate_volatility(p2_in_window)
            drift_p2 = calculate_line_drift(delta_p2)

            features[f"odds_delta_player_a_{w}s"] = delta_p1
            features[f"odds_velocity_player_a_{w}s"] = velocity_p1
            features[f"odds_volatility_player_a_{w}s"] = volatility_p1
            features[f"line_drift_player_a_{w}s"] = drift_p1

            features[f"odds_delta_player_b_{w}s"] = delta_p2
            features[f"odds_velocity_player_b_{w}s"] = velocity_p2
            features[f"odds_volatility_player_b_{w}s"] = volatility_p2
            features[f"line_drift_player_b_{w}s"] = drift_p2

            # Suspension count in window
            states_in_window = [
                s for s in valid_states
                if (s.observed_at.replace(tzinfo=timezone.utc) if s.observed_at.tzinfo is None else s.observed_at) >= window_start
            ]
            suspensions = sum(1 for s in states_in_window if s.status in ("suspended", "paused", "delayed"))
            features[f"suspension_frequency_{w}s"] = float(suspensions)
            features[f"is_missing_odds_window_{w}s"] = float(len(p1_in_window) < 1)

        # 5. Tennis Score Differentials & Context
        latest_state = valid_states[-1] if valid_states else None
        if latest_state:
            features["is_missing_state"] = 0.0
            score_detail = latest_state.score_detail or {}
            sport_state = latest_state.sport_state or {}

            # Sets
            curr_period = float(latest_state.current_period or 1)
            sets_a = float(score_detail.get("sets_a", 0))
            sets_b = float(score_detail.get("sets_b", 0))
            features["current_set"] = curr_period
            features["sets_diff"] = sets_a - sets_b

            # Games in current set
            games_a = float(score_detail.get("current_game_a", 0))
            games_b = float(score_detail.get("current_game_b", 0))
            features["games_diff"] = games_a - games_b
            features["total_games_current_set"] = games_a + games_b

            # Points
            pts_a_str = str(score_detail.get("points_a", "0"))
            pts_b_str = str(score_detail.get("points_b", "0"))
            pts_a_val = self.POINT_MAPPING.get(pts_a_str, 0.0)
            pts_b_val = self.POINT_MAPPING.get(pts_b_str, 0.0)
            features["points_diff"] = pts_a_val - pts_b_val

            # Server
            server = sport_state.get("server") or score_detail.get("server")
            features["is_server_a"] = 1.0 if server == "player_a" else 0.0
            features["is_server_b"] = 1.0 if server == "player_b" else 0.0
            features["is_missing_server"] = 1.0 if not server else 0.0

            # Break point & tiebreak
            features["is_break_point"] = 1.0 if sport_state.get("is_break_point") else 0.0
            features["is_tiebreak"] = 1.0 if (games_a == 6.0 and games_b == 6.0) else 0.0

            # Match clock
            elapsed = float(getattr(latest_state, "match_clock_seconds", None) or 0)
            if elapsed == 0 and valid_states:
                # Approximate from first snapshot
                elapsed = max(0.0, (latest_state.observed_at - valid_states[0].observed_at).total_seconds())
            features["elapsed_match_seconds"] = elapsed

            # Tennis match statistics
            stats = sport_state.get("stats", {})
            features["aces_diff"] = float(stats.get("aces_a", 0)) - float(stats.get("aces_b", 0))
            features["double_faults_diff"] = float(stats.get("double_faults_a", 0)) - float(stats.get("double_faults_b", 0))
            features["is_missing_stats"] = 0.0 if stats else 1.0
        else:
            features["is_missing_state"] = 1.0
            features["current_set"] = 1.0
            features["sets_diff"] = 0.0
            features["games_diff"] = 0.0
            features["total_games_current_set"] = 0.0
            features["points_diff"] = 0.0
            features["is_server_a"] = 0.0
            features["is_server_b"] = 0.0
            features["is_missing_server"] = 1.0
            features["is_break_point"] = 0.0
            features["is_tiebreak"] = 0.0
            features["elapsed_match_seconds"] = 0.0
            features["aces_diff"] = 0.0
            features["double_faults_diff"] = 0.0
            features["is_missing_stats"] = 1.0

        return FeatureVector(
            event_id=str(event_id),
            sport_code=self.sport_code,
            feature_version=self.feature_version,
            feature_cutoff_timestamp=cutoff_dt,
            features=features,
        )
