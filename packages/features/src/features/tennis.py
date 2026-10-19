"""Tennis Feature Builder with rolling windows, no-vig calculations, and zero data leakage."""

import bisect
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


def _empty_tape() -> Dict[str, float]:
    tape = {
        "tape_points_a": 0.0,
        "tape_points_b": 0.0,
        "tape_serve_won_a": 0.0,
        "tape_return_won_a": 0.0,
        "tape_serve_won_b": 0.0,
        "tape_return_won_b": 0.0,
        "tape_point_streak": 0.0,
        "tape_points_even": 0.0,
        "tape_current_set_points_a": 0.0,
        "tape_current_set_points_b": 0.0,
    }
    for index in range(1, 6):
        tape[f"paren_set_{index}_diff"] = 0.0
    return tape


class ScoreTape:
    """Point path built once as the replay walks forward. Same numbers as a full rescan."""

    def __init__(self, builder: "TennisFeatureBuilder"):
        self.builder = builder
        self.previous = None
        self.streak = 0
        self.current_set_points = [0.0, 0.0]
        self.base = _empty_tape()
        self.last_state = None
        self.seen = 0
        self._noted = 0
        self._state_times: List[datetime] = []
        self._state_flags: List[int] = []

    def note_states(self, states: List[Any]) -> None:
        for state in states[self._noted :]:
            self._state_times.append(_as_utc(state.observed_at))
            self._state_flags.append(1 if state.status in ("suspended", "paused", "delayed") else 0)
        self._noted = len(states)

    def suspensions(self, cutoff_dt: datetime, window_seconds: int) -> int:
        start = bisect.bisect_left(self._state_times, cutoff_dt - timedelta(seconds=window_seconds))
        return sum(self._state_flags[start:])

    def extend(self, states: List[Any]) -> None:
        for state in states[self.seen :]:
            self._add(state)
            self.last_state = state
        self.seen = len(states)

    def _add(self, state: Any) -> None:
        frame = self.builder._frame(state)
        previous = self.previous
        if previous is not None:
            winner = self.builder._point_winner(previous, frame)
            if winner is not None:
                server = previous["server"]
                if winner == "a":
                    self.base["tape_points_a"] += 1
                    self.current_set_points[0] += 1
                    if server == "player_a":
                        self.base["tape_serve_won_a"] += 1
                    elif server == "player_b":
                        self.base["tape_return_won_a"] += 1
                    self.streak = self.streak + 1 if self.streak > 0 else 1
                else:
                    self.base["tape_points_b"] += 1
                    self.current_set_points[1] += 1
                    if server == "player_b":
                        self.base["tape_serve_won_b"] += 1
                    elif server == "player_a":
                        self.base["tape_return_won_b"] += 1
                    self.streak = self.streak - 1 if self.streak < 0 else -1
            if frame["set_no"] != previous["set_no"]:
                self.current_set_points = [0.0, 0.0]
        self.previous = frame

    def as_features(self) -> Dict[str, float]:
        tape = dict(self.base)
        tape["tape_point_streak"] = float(self.streak)
        tape["tape_current_set_points_a"] = self.current_set_points[0]
        tape["tape_current_set_points_b"] = self.current_set_points[1]
        total = tape["tape_points_a"] + tape["tape_points_b"]
        tape["tape_points_even"] = 1.0 if total >= 4 and abs(tape["tape_points_a"] - tape["tape_points_b"]) <= 1 else 0.0
        if self.last_state is not None:
            lines = self.builder._set_lines(self.last_state)
            for index, line in enumerate(lines[:5], start=1):
                try:
                    tape[f"paren_set_{index}_diff"] = float(line.get("games_a") or 0) - float(line.get("games_b") or 0)
                except (TypeError, ValueError):
                    tape[f"paren_set_{index}_diff"] = 0.0
        return tape


class MarketQuotes:
    """Quotes of one market, appended in time order. Replay reuses this across cutoffs."""

    def __init__(self):
        self.seen = 0
        self.quotes: List[Any] = []
        self.side_a: List[Any] = []
        self.side_b: List[Any] = []
        self.times_a: List[datetime] = []
        self.times_b: List[datetime] = []

    def extend(self, odds_snapshots: List[Any], cutoff_dt: datetime, market_type: str, line_value: Optional[int]) -> None:
        for row in odds_snapshots[self.seen :]:
            if _as_utc(row.observed_at) > cutoff_dt:
                break
            self.seen += 1
            market = getattr(row, "market_type", None)
            if market_type == "match_winner":
                if market not in (None, "", "match_winner"):
                    continue
            else:
                if market != market_type:
                    continue
                raw_line = getattr(row, "line_value", None)
                if raw_line is None or line_value is None:
                    continue
                try:
                    if int(float(raw_line)) != int(line_value):
                        continue
                except (TypeError, ValueError):
                    continue
            self.quotes.append(row)
            outcome = getattr(row, "outcome", None)
            moment = _as_utc(row.observed_at)
            if outcome in ("player_a", "1", "p1"):
                self.side_a.append(row)
                self.times_a.append(moment)
            elif outcome in ("player_b", "2", "p2"):
                self.side_b.append(row)
                self.times_b.append(moment)


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _state_parts(state: Any) -> tuple:
    detail = state.score_detail if isinstance(getattr(state, "score_detail", None), dict) else {}
    sport = state.sport_state if isinstance(getattr(state, "sport_state", None), dict) else {}
    stats = sport.get("stats") if isinstance(sport.get("stats"), dict) else {}
    if not stats and isinstance(detail.get("stats"), dict):
        stats = detail["stats"]
    raw_lines = stats.get("set_lines") or detail.get("set_lines") or []
    lines = [line for line in raw_lines if isinstance(line, dict)]
    return detail, sport, stats, lines


def games_complete(games_a: Any, games_b: Any) -> bool:
    """A set line that already has a winner. 6-6 is a tiebreak still in progress."""
    left = int(_as_float(games_a))
    right = int(_as_float(games_b))
    if left == 6 and right == 6:
        return False
    if max(left, right) >= 6 and abs(left - right) >= 2:
        return True
    return max(left, right) == 7 and min(left, right) in (5, 6)


def closed_set_winner(games_a: Any, games_b: Any) -> Optional[str]:
    """Winner of a finished set: 6 with a two-game margin, or 7-5 / 7-6."""
    try:
        left = int(games_a)
        right = int(games_b)
    except (TypeError, ValueError):
        return None
    if left == right:
        return None
    high, low = max(left, right), min(left, right)
    if high < 6 or high > 7:
        return None
    if high == 6 and high - low < 2:
        return None
    if high == 7 and low < 5:
        return None
    return "player_a" if left > right else "player_b"


def is_one_set_lead(sets_a: Any, sets_b: Any) -> bool:
    """1:0 and 0:1 are not a finished tennis match."""
    try:
        left = int(sets_a)
        right = int(sets_b)
    except (TypeError, ValueError):
        return False
    return (left, right) in ((1, 0), (0, 1))


def score_board(state: Any) -> Dict[str, Any]:
    """Sets, the live game line, and points from the stored snapshot shape.

    Live snapshots keep sets and games under stats.set_lines and points under
    current_game. Flat keys from older tests stay as a fallback.
    """
    detail, _sport, stats, lines = _state_parts(state)
    live = next((line for line in lines if line.get("live")), None)
    closed = [line for line in lines if line is not live]
    if live is None and lines:
        live = lines[-1]
        closed = lines[:-1]

    sets_a = stats.get("sets_a", detail.get("sets_a"))
    sets_b = stats.get("sets_b", detail.get("sets_b"))
    if sets_a is None or sets_b is None:
        sets_a = sum(1 for line in closed if _as_float(line.get("games_a")) > _as_float(line.get("games_b")))
        sets_b = sum(1 for line in closed if _as_float(line.get("games_b")) > _as_float(line.get("games_a")))
    else:
        sets_a = _as_float(sets_a)
        sets_b = _as_float(sets_b)

    if live is not None:
        games_a = _as_float(live.get("games_a"))
        games_b = _as_float(live.get("games_b"))
    else:
        games_a = _as_float(detail.get("current_game_a"))
        games_b = _as_float(detail.get("current_game_b"))

    game = detail.get("current_game") if isinstance(detail.get("current_game"), dict) else {}
    points_a = str(game.get("points_a", detail.get("points_a", "0")))
    points_b = str(game.get("points_b", detail.get("points_b", "0")))
    current_set = int(
        _as_float(
            getattr(state, "current_period", None)
            or detail.get("current_period")
            or detail.get("current_set")
            or 1
        )
    )
    return {
        "sets_a": sets_a,
        "sets_b": sets_b,
        "games_a": games_a,
        "games_b": games_b,
        "points_a": points_a,
        "points_b": points_b,
        "current_set": current_set,
        "lines": lines,
        "tiebreak": bool(game.get("is_tiebreak")) or (games_a == 6.0 and games_b == 6.0),
    }


def match_is_decided(state: Any) -> bool:
    """True when one side already has the sets required to win this match."""
    board = score_board(state)
    sets_a = board["sets_a"]
    sets_b = board["sets_b"]
    current = board["current_set"]
    best_of_five = current >= 4 or (sets_a >= 2 and sets_b >= 2) or len(board["lines"]) >= 4
    need = 3 if best_of_five else 2
    if max(sets_a, sets_b) < need:
        return False
    # A new set has started, so the previous tally did not end the match.
    if current > sets_a + sets_b:
        return False
    return True


def set_is_open(state: Any, number: int) -> bool:
    """True while this set is still being played or has not started."""
    board = score_board(state)
    if board["current_set"] > number:
        return False
    lines = board["lines"]
    if 0 <= number - 1 < len(lines):
        line = lines[number - 1]
        if not line.get("live") and number < len(lines):
            return False
        if games_complete(line.get("games_a"), line.get("games_b")):
            return False
    return True


class TennisFeatureBuilder:
    """
    Constructs deterministic numerical feature vector for live tennis events.
    Enforces strict point-in-time cutoff: no observations beyond cutoff_time are ever accessed.
    """

    sport_code: str = "tennis"
    feature_version: str = "1.1.0"

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
        tape: Optional[ScoreTape] = None,
        line: Optional[MarketQuotes] = None,
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

        # Replay passes a growing prefix and keeps the tape and the match line.
        # A live call still filters and sorts, so a future row cannot get in.
        if tape is None:
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
        else:
            valid_states = event_snapshots
            valid_odds = odds_snapshots

        features: Dict[str, float] = {}
        self.apply_market_line(features, valid_odds, cutoff_dt, market_type="match_winner", cursor=line)

        # Suspension count uses the score timeline, not the quoted market.
        if tape is None:
            for w in ROLLING_WINDOWS:
                window_start = cutoff_dt - timedelta(seconds=w)
                states_in_window = [
                    s for s in valid_states
                    if _as_utc(s.observed_at) >= window_start
                ]
                suspensions = sum(1 for s in states_in_window if s.status in ("suspended", "paused", "delayed"))
                features[f"suspension_frequency_{w}s"] = float(suspensions)
        else:
            tape.note_states(valid_states)
            for w in ROLLING_WINDOWS:
                features[f"suspension_frequency_{w}s"] = float(tape.suspensions(cutoff_dt, w))

        # 5. Tennis Score Differentials & Context
        latest_state = valid_states[-1] if valid_states else None
        if latest_state:
            features["is_missing_state"] = 0.0
            score_detail = latest_state.score_detail or {}
            sport_state = latest_state.sport_state or {}
            board = score_board(latest_state)
            games_a = board["games_a"]
            games_b = board["games_b"]
            features["current_set"] = float(board["current_set"])
            features["sets_diff"] = board["sets_a"] - board["sets_b"]
            features["games_diff"] = games_a - games_b
            features["total_games_current_set"] = games_a + games_b
            pts_a_val = self.POINT_MAPPING.get(board["points_a"], 0.0)
            pts_b_val = self.POINT_MAPPING.get(board["points_b"], 0.0)
            features["points_diff"] = pts_a_val - pts_b_val

            # Server
            server = sport_state.get("server") or score_detail.get("server")
            features["is_server_a"] = 1.0 if server == "player_a" else 0.0
            features["is_server_b"] = 1.0 if server == "player_b" else 0.0
            features["is_missing_server"] = 1.0 if not server else 0.0

            # Break point & tiebreak
            features["is_break_point"] = 1.0 if sport_state.get("is_break_point") or score_detail.get("is_break_point") else 0.0
            features["is_tiebreak"] = 1.0 if board["tiebreak"] else 0.0

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

        if tape is None:
            features.update(self._score_tape(valid_states))
        else:
            tape.extend(valid_states)
            features.update(tape.as_features())

        # Mean reversion indicator: pre-match favorite trailing in games/sets
        p_fav_a = features.get("opening_prob_player_a", features.get("no_vig_prob_player_a", 0.50))
        games_d = float(features.get("games_diff", 0.0))
        sets_d = float(features.get("sets_diff", 0.0))
        fav_trailing = 0.0
        if p_fav_a > 0.50:
            deficit_a = max(0.0, -games_d) + 2.0 * max(0.0, -sets_d)
            fav_trailing = (p_fav_a - 0.50) * deficit_a
        elif p_fav_a < 0.50:
            deficit_b = max(0.0, games_d) + 2.0 * max(0.0, sets_d)
            fav_trailing = -((0.50 - p_fav_a) * deficit_b)
        features["favorite_trailing"] = round(float(fav_trailing), 4)

        return FeatureVector(
            event_id=str(event_id),
            sport_code=self.sport_code,
            feature_version=self.feature_version,
            feature_cutoff_timestamp=cutoff_dt,
            features=features,
        )

    def apply_market_line(
        self,
        features: Dict[str, float],
        odds_snapshots: List[Any],
        cutoff_time: datetime,
        market_type: str = "match_winner",
        line_value: Optional[int] = None,
        cursor: Optional[MarketQuotes] = None,
    ) -> None:
        """Overwrite the line block with quotes of one market at or before the cutoff.

        A row without market_type counts as the match line, so older tests stay valid.
        Set rows must pass market_type='set_winner' and the set number.
        """
        cutoff_dt = _as_utc(cutoff_time)
        if cursor is not None:
            cursor.extend(odds_snapshots, cutoff_dt, market_type, line_value)
            side_a = cursor.side_a
            side_b = cursor.side_b
            times_a = cursor.times_a
            times_b = cursor.times_b
        else:
            quotes = []
            for row in odds_snapshots:
                if _as_utc(row.observed_at) > cutoff_dt:
                    continue
                market = getattr(row, "market_type", None)
                if market_type == "match_winner":
                    if market not in (None, "", "match_winner"):
                        continue
                else:
                    if market != market_type:
                        continue
                    raw_line = getattr(row, "line_value", None)
                    if raw_line is None or line_value is None:
                        continue
                    try:
                        if int(float(raw_line)) != int(line_value):
                            continue
                    except (TypeError, ValueError):
                        continue
                quotes.append(row)
            quotes.sort(key=lambda row: row.observed_at)
            side_a = [row for row in quotes if row.outcome in ("player_a", "1", "p1")]
            side_b = [row for row in quotes if row.outcome in ("player_b", "2", "p2")]
            times_a = None
            times_b = None

        latest_a = last_quoted_odds(side_a)
        latest_b = last_quoted_odds(side_b)
        open_a = float(side_a[0].odds) if side_a else latest_a
        open_b = float(side_b[0].odds) if side_b else latest_b
        features["current_odds_player_a"] = round(latest_a, 4)
        features["current_odds_player_b"] = round(latest_b, 4)
        features["is_missing_odds"] = float(latest_a <= 1.0 or latest_b <= 1.0)
        no_vig_a, no_vig_b, margin = calculate_no_vig_probabilities(latest_a, latest_b)
        open_prob_a, _, _ = calculate_no_vig_probabilities(open_a, open_b)
        features["no_vig_prob_player_a"] = no_vig_a
        features["no_vig_prob_player_b"] = no_vig_b
        features["opening_prob_player_a"] = open_prob_a
        features["bookmaker_margin"] = margin

        for window in ROLLING_WINDOWS:
            window_start = cutoff_dt - timedelta(seconds=window)
            if times_a is None:
                prices_a = [float(row.odds) for row in side_a if _as_utc(row.observed_at) >= window_start]
                prices_b = [float(row.odds) for row in side_b if _as_utc(row.observed_at) >= window_start]
            else:
                prices_a = [float(row.odds) for row in side_a[bisect.bisect_left(times_a, window_start) :]]
                prices_b = [float(row.odds) for row in side_b[bisect.bisect_left(times_b, window_start) :]]
            past_a = prices_a[0] if prices_a else latest_a
            past_b = prices_b[0] if prices_b else latest_b
            delta_a = calculate_odds_delta(latest_a, past_a)
            delta_b = calculate_odds_delta(latest_b, past_b)
            features[f"odds_delta_player_a_{window}s"] = delta_a
            features[f"odds_velocity_player_a_{window}s"] = calculate_odds_velocity(delta_a, window)
            features[f"odds_volatility_player_a_{window}s"] = calculate_volatility(prices_a)
            features[f"line_drift_player_a_{window}s"] = calculate_line_drift(delta_a)
            features[f"odds_delta_player_b_{window}s"] = delta_b
            features[f"odds_velocity_player_b_{window}s"] = calculate_odds_velocity(delta_b, window)
            features[f"odds_volatility_player_b_{window}s"] = calculate_volatility(prices_b)
            features[f"line_drift_player_b_{window}s"] = calculate_line_drift(delta_b)
            features[f"is_missing_odds_window_{window}s"] = float(len(prices_a) < 1)

    def _score_tape(self, states: List[Any]) -> Dict[str, float]:
        """Point-by-point path and the parenthesis lines of every set, up to this cutoff."""
        cursor = ScoreTape(self)
        cursor.extend(states)
        return cursor.as_features()

    def _frame(self, state: Any) -> Dict[str, Any]:
        detail = state.score_detail if isinstance(state.score_detail, dict) else {}
        sport = state.sport_state if isinstance(state.sport_state, dict) else {}
        game = detail.get("current_game") if isinstance(detail.get("current_game"), dict) else {}
        points_a = self.POINT_MAPPING.get(str(game.get("points_a", detail.get("points_a", "0"))), 0.0)
        points_b = self.POINT_MAPPING.get(str(game.get("points_b", detail.get("points_b", "0"))), 0.0)
        lines = self._set_lines(state)
        live = next((line for line in lines if line.get("live")), lines[-1] if lines else {})
        try:
            games_a = float(live.get("games_a") if live else detail.get("current_game_a") or 0)
            games_b = float(live.get("games_b") if live else detail.get("current_game_b") or 0)
        except (TypeError, ValueError):
            games_a, games_b = 0.0, 0.0
        return {
            "set_no": int(float(getattr(state, "current_period", None) or detail.get("current_period") or detail.get("current_set") or 1)),
            "games_a": games_a,
            "games_b": games_b,
            "points_a": points_a,
            "points_b": points_b,
            "server": sport.get("server") or detail.get("server"),
        }

    def _point_winner(self, previous: Dict[str, Any], frame: Dict[str, Any]) -> Optional[str]:
        if frame["set_no"] == previous["set_no"] and frame["games_a"] == previous["games_a"] and frame["games_b"] == previous["games_b"]:
            if frame["points_a"] == previous["points_a"] + 1 and frame["points_b"] == previous["points_b"]:
                return "a"
            if frame["points_b"] == previous["points_b"] + 1 and frame["points_a"] == previous["points_a"]:
                return "b"
            return None
        if frame["set_no"] == previous["set_no"] and frame["games_a"] == previous["games_a"] + 1:
            return "a"
        if frame["set_no"] == previous["set_no"] and frame["games_b"] == previous["games_b"] + 1:
            return "b"
        return None

    def _set_lines(self, state: Any) -> List[Dict[str, Any]]:
        _detail, _sport, _stats, lines = _state_parts(state)
        return lines
