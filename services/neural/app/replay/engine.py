"""Point-in-time replay of one saved checkpoint over finished matches."""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from contracts import FeatureVector

from ..backtest.config import BacktestConfig
from ..backtest.execution import ExecutionSimulator, SimulatedOddsSnapshot
from ..confirmation import next_watch_action
from ..context_gate import NEUTRAL_CONTEXT, apply_context_features, context_side_from_event
from ..dataset import _context_as_of
from ..set_match import match_verdict, set_is_committable, set_is_observation_only
from tennis_adapter.policy import TennisPolicy, market_limits_from_mapping, model_commits_market, resolve_replay_policy, windows_allow
from .bankroll import ReplayBankroll

logger = logging.getLogger("neural.replay")

GATES_APPLIED = [
    "point_in_time_features",
    "stored_context_or_neutral",
    "confirmation",
    "stake_gate",
    "configured_edge_and_probability",
    "execution_latency_staleness_slippage",
    "duplicate_market",
    "fixed_stake_bankroll",
    "bankruptcy_reset",
]

_SIDE_A = {"player_a", "team_a", "home", "1", "p1", "a"}
_SIDE_B = {"player_b", "team_b", "away", "2", "p2", "b"}


def _bet_stats(bets: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    wins = sum(1 for bet in bets if bet["status"] == "WON")
    losses = sum(1 for bet in bets if bet["status"] == "LOST")
    voids = sum(1 for bet in bets if bet["status"] == "VOID")
    settled = wins + losses
    turnover = round(sum(float(bet["stake"]) for bet in bets if bet["status"] != "VOID"), 2)
    net = round(sum(float(bet["net_profit"]) for bet in bets), 2)
    return {
        "bets": len(bets),
        "wins": wins,
        "losses": losses,
        "voids": voids,
        "win_rate": round(wins / settled, 4) if settled else None,
        "turnover": turnover,
        "net_pnl": net,
        "roi": round(net / turnover, 4) if turnover else None,
    }


def _set_sort(market: str) -> tuple:
    tail = str(market).split(":")[-1]
    return (0, int(tail)) if tail.isdigit() else (1, tail)


def _as_utc(moment: datetime) -> datetime:
    tz = moment.tzinfo
    if tz is None:
        return moment.replace(tzinfo=timezone.utc)
    if tz is timezone.utc or moment.utcoffset() == timedelta(0):
        return moment
    return moment.astimezone(timezone.utc)


def _bump(counts: Counter, code: str) -> None:
    counts[code] += 1


def odds_allowed(
    probability: float,
    odds: float,
    market: str,
    config: Dict[str, Any],
    sport_code: str = "",
) -> tuple[bool, str]:
    """Tennis live windows from the run config, or an explicit range the operator typed."""
    if str(sport_code or "").strip().lower() != "tennis":
        return True, ""
    price = float(odds)
    limits = market_limits_from_mapping(market, config)
    if limits["mode"] == "range":
        if float(probability) < float(limits["min_probability"]):
            return False, "LOW_PROBABILITY"
        if price < float(limits["min_odds"]):
            return False, "ODDS_TOO_SHORT"
        if price >= float(limits["max_odds"]):
            return False, "ODDS_TOO_LONG"
        return True, ""
    resolved = resolve_replay_policy(config)
    policy_keys = TennisPolicy().as_dict().keys()
    policy = TennisPolicy(**{key: resolved[key] for key in policy_keys if key in resolved})
    return windows_allow(probability, price, market, policy)


def _prices(rows: Sequence[Any], cutoff: datetime, market_type: str, line_value: Optional[float] = None) -> Optional[tuple[float, float]]:
    limit = _as_utc(cutoff)
    found: Dict[str, float] = {}
    for row in rows:
        if _as_utc(row.observed_at) > limit:
            continue
        if str(getattr(row, "market_type", "") or "") != market_type:
            continue
        if line_value is not None and int(float(row.line_value or 0)) != int(line_value):
            continue
        outcome = str(getattr(row, "outcome", "") or "").lower()
        try:
            price = float(row.odds)
        except (TypeError, ValueError):
            continue
        if price <= 1.0:
            continue
        if outcome in _SIDE_A:
            found["a"] = price
        elif outcome in _SIDE_B:
            found["b"] = price
    if "a" not in found or "b" not in found:
        return None
    return found["a"], found["b"]


def _latest_match_prices(rows: Sequence[Any], cutoff: datetime) -> Optional[tuple[float, float]]:
    priced = _prices(rows, cutoff, "match_winner")
    if priced:
        return priced
    limit = _as_utc(cutoff)
    found: Dict[str, float] = {}
    for row in rows:
        if _as_utc(row.observed_at) > limit:
            continue
        if str(getattr(row, "market_type", "") or "") not in ("", "match_winner"):
            continue
        outcome = str(getattr(row, "outcome", "") or "").lower()
        try:
            price = float(row.odds)
        except (TypeError, ValueError):
            continue
        if price <= 1.0:
            continue
        if outcome in _SIDE_A:
            found["a"] = price
        elif outcome in _SIDE_B:
            found["b"] = price
    if "a" not in found or "b" not in found:
        return None
    return found["a"], found["b"]


class PriceBook:
    """Latest match and set prices while the replay walks the tape forward."""

    def __init__(self) -> None:
        self.seen = 0
        self.strict: Dict[str, float] = {}
        self.any_side: Dict[str, float] = {}
        self.sets: Dict[int, Dict[str, float]] = {}

    def extend(self, rows: Sequence[Any], cutoff: datetime) -> None:
        limit = _as_utc(cutoff)
        for row in rows[self.seen :]:
            if _as_utc(row.observed_at) > limit:
                break
            self.seen += 1
            market = str(getattr(row, "market_type", "") or "")
            outcome = str(getattr(row, "outcome", "") or "").lower()
            side = "a" if outcome in _SIDE_A else "b" if outcome in _SIDE_B else ""
            if not side:
                continue
            try:
                price = float(row.odds)
            except (TypeError, ValueError):
                continue
            if price <= 1.0:
                continue
            if market == "set_winner":
                if row.line_value is None:
                    continue
                self.sets.setdefault(int(float(row.line_value)), {})[side] = price
            elif market in ("", "match_winner"):
                self.any_side[side] = price
                if market == "match_winner":
                    self.strict[side] = price

    def match_prices(self) -> Optional[tuple[float, float]]:
        found = self.strict if "a" in self.strict and "b" in self.strict else self.any_side
        if "a" not in found or "b" not in found:
            return None
        return found["a"], found["b"]

    def set_prices(self) -> Dict[int, tuple[float, float]]:
        priced = {}
        for number, sides in self.sets.items():
            if "a" in sides and "b" in sides:
                priced[number] = (sides["a"], sides["b"])
        return priced


def _execution_quotes(rows: Sequence[Any], market_type: str, line_value: Optional[float]) -> List[SimulatedOddsSnapshot]:
    grouped: Dict[datetime, Dict[str, float]] = {}
    suspended: Dict[datetime, bool] = {}
    for row in rows:
        if str(getattr(row, "market_type", "") or "") != market_type:
            continue
        if line_value is not None and int(float(row.line_value or 0)) != int(line_value):
            continue
        moment = _as_utc(row.observed_at)
        slot = grouped.setdefault(moment, {})
        outcome = str(getattr(row, "outcome", "") or "").lower()
        try:
            price = float(row.odds)
        except (TypeError, ValueError):
            continue
        if outcome in _SIDE_A:
            slot["a"] = price
        elif outcome in _SIDE_B:
            slot["b"] = price
        status = str(getattr(row, "status", "") or "").lower()
        suspended[moment] = suspended.get(moment, False) or status in ("suspended", "closed")
    quotes = []
    for moment, slot in grouped.items():
        if "a" not in slot or "b" not in slot:
            continue
        quotes.append(
            SimulatedOddsSnapshot(
                snapshot_id=moment.isoformat(),
                observed_at=moment,
                p1_odds=slot["a"],
                p2_odds=slot["b"],
                is_suspended=suspended.get(moment, False),
            )
        )
    return quotes


def _fill_preview(cutoff: datetime, quotes: Sequence[Any], latency_sec: float) -> Dict[str, Any]:
    """Quote that would be available after latency, before staleness and slippage gates."""
    moment = _as_utc(cutoff)
    execution_time = moment + timedelta(seconds=float(latency_sec))
    available = [item for item in quotes if _as_utc(item.observed_at) <= execution_time]
    empty = {"code": "NO_ODDS_SNAPSHOT", "odds_a": 0.0, "odds_b": 0.0, "staleness_sec": None}
    if not available:
        return empty
    latest = max(available, key=lambda item: _as_utc(item.observed_at))
    if latest.is_suspended:
        return {"code": "MARKET_SUSPENDED", "odds_a": 0.0, "odds_b": 0.0, "staleness_sec": None}
    staleness = (execution_time - _as_utc(latest.observed_at)).total_seconds()
    return {
        "code": "",
        "odds_a": float(latest.p1_odds),
        "odds_b": float(latest.p2_odds),
        "staleness_sec": staleness,
    }


def _side_token(outcome: str) -> str:
    return "p1" if str(outcome).endswith("_a") or str(outcome) in _SIDE_A else "p2"


def _finished_sets(state) -> Dict[int, str]:
    """Sets already closed on this snapshot. The stored final list is not used."""
    if state is None:
        return {}
    from features.tennis import score_board
    from tennis_adapter.adapter import closed_set_winners, final_set_winners

    board = score_board(state)
    sets_a = int(board["sets_a"])
    sets_b = int(board["sets_b"])
    period = int(board["current_set"] or 1)
    best_of = 5 if max(sets_a + sets_b, period) >= 4 else 3
    need = best_of // 2 + 1
    status = str(getattr(state, "status", "") or "").lower()
    match_done = status in ("finished", "completed", "ended") or sets_a >= need or sets_b >= need
    winners = final_set_winners(board["lines"]) if match_done else closed_set_winners(board["lines"])
    return {int(key): winner for key, winner in winners.items() if winner in ("player_a", "player_b")}


def _winner_token(event: Any, market: str) -> Optional[str]:
    from ..journal import _winner_for_market

    winner = _winner_for_market(event, market)
    if winner == "":
        return "void"
    return winner


class ReplayRunner:
    """Replays one predictor. The caller supplies matches already limited to the requested tail."""

    def __init__(self, predictor, config: Dict[str, Any], sport_code: str):
        self.predictor = predictor
        self.config = dict(config)
        if sport_code == "tennis":
            self.config.update(resolve_replay_policy(config))
        self.sport_code = sport_code
        self.counts: Counter = Counter()
        self.bets: List[Dict[str, Any]] = []
        self.bankroll = ReplayBankroll(
            float(config["initial_bankroll"]),
            float(config["fixed_stake"]),
            float(config["bankruptcy_threshold"]),
        )
        self.execution = ExecutionSimulator(
            BacktestConfig(
                sport_code=sport_code,
                min_edge=float(config["min_edge"]),
                execution_latency_sec=float(config["execution_latency_sec"]),
                max_odds_staleness_sec=float(config["max_odds_staleness_sec"]),
                allow_slippage=bool(config["allow_slippage"]),
                initial_bankroll=float(config["initial_bankroll"]),
                fixed_stake=float(config["fixed_stake"]),
                stake_strategy="FIXED",
                max_stake_cap=0.5,
                max_exposure=1.0,
            )
        )
        self._watches: Dict[tuple, Dict[str, Any]] = {}
        self._confirmed: set[tuple] = set()
        self._observed: Dict[tuple, str] = {}
        self.candidates: List[Dict[str, Any]] = []
        self._funnel: Dict[str, Dict[str, set]] = {
            stage: {"match": set(), "set": set()} for stage in ("data", "candidate", "confirmation", "execution")
        }

    def run(self, matches: Sequence[Any], feature_builder, on_match=None) -> Dict[str, Any]:
        ordered = sorted(matches, key=lambda item: _as_utc(item.started_at))
        total = len(ordered)
        for index, match in enumerate(ordered, start=1):
            if getattr(match, "odds", None) or getattr(match, "cached_rows", None):
                self._funnel["data"]["match"].add(str(match.event_id))
            self._replay_match(match, feature_builder)
            self._settle_match(match, _as_utc(match.started_at) + timedelta(days=2))
            if on_match is not None:
                on_match(index, total)
        self.bankroll.note_equity()
        return self.summary(total)

    def summary(self, matches_seen: int) -> Dict[str, Any]:
        version = str((self.predictor.checkpoint_data or {}).get("version_tag") or "")
        name = str((self.predictor.checkpoint_data or {}).get("model_name") or "")
        enabled = self._enabled_markets()
        totals = _bet_stats(self.bets)
        by_market: Dict[str, Any] = {}
        if "match_winner" in enabled:
            by_market["match_winner"] = _bet_stats([bet for bet in self.bets if bet["market"] == "match_winner"])
        if "set_winner" in enabled:
            set_bets = [bet for bet in self.bets if str(bet["market"]).startswith("set_winner")]
            set_keys = sorted({str(bet["market"]) for bet in set_bets}, key=_set_sort)
            if not set_keys:
                by_market["set_winner"] = _bet_stats([])
            for key in set_keys:
                by_market[key] = _bet_stats([bet for bet in set_bets if bet["market"] == key])
        import hashlib
        import json
        from tennis_adapter.policy import TennisPolicy

        policy_fields = list(TennisPolicy().as_dict().keys())
        applied_policy = {key: self.config.get(key) for key in policy_fields}
        applied_policy["match_decision_mode"] = self.config.get("match_decision_mode", "set_confirmed")
        policy_hash = hashlib.sha256(json.dumps(applied_policy, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        funnel = {
            "available_matches": matches_seen,
            "eligible_matches": len(self._funnel["data"]["match"]),
            "by_market": {
                market: {
                    "eligible_matches": len(self._funnel["data"][market]),
                    "candidate_matches": len(self._funnel["candidate"][market]),
                    "confirmed_matches": len(self._funnel["confirmation"][market]),
                    "executed_matches": len(self._funnel["execution"][market]),
                }
                for market in ("match", "set")
            },
            "candidate_slices": int(self.counts.get("CANDIDATE_SLICES", 0)),
            "confirmed_slices": int(self.counts.get("CONFIRMED_SLICES", 0)),
            "executed_slices": len(self.bets),
        }
        return {
            "model_name": name,
            "version_tag": version,
            "sport_code": self.sport_code,
            "markets": sorted(enabled),
            "matches_seen": matches_seen,
            **totals,
            "initial_bankroll": float(self.config["initial_bankroll"]),
            "final_balance": round(float(self.bankroll.current_balance), 2),
            "min_balance": round(float(self.bankroll.min_balance), 2),
            "max_drawdown": round(float(self.bankroll.max_drawdown), 2),
            "bankruptcies": int(self.bankroll.bankruptcies),
            "rejection_counts": dict(self.counts),
            "applied_policy": applied_policy,
            "policy_sha256": policy_hash,
            "decision_funnel": funnel,
            "gates_applied": list(GATES_APPLIED),
            "by_market": by_market,
            "bet_records": self.bets,
        }

    def _stakes_set(self, number: int) -> bool:
        from tennis_adapter.policy import stake_set_numbers

        raw = self.config.get("stake_sets")
        if raw:
            values = raw if isinstance(raw, (list, tuple, set)) else str(raw).replace(",", " ").split()
            return int(number) in {int(item) for item in values}
        allowed = stake_set_numbers()
        if not allowed:
            return True
        return int(number) in allowed

    def _enabled_markets(self) -> set[str]:
        raw = self.config.get("markets")
        if isinstance(raw, list) and raw:
            return {str(item) for item in raw if str(item) in ("match_winner", "set_winner")}
        from ..set_match import checkpoint_scores_sets

        if self.sport_code == "tennis" and checkpoint_scores_sets(self.predictor.checkpoint_data):
            return {"match_winner", "set_winner"}
        return {"match_winner"}

    def _replay_match(self, match, feature_builder) -> None:
        if getattr(match, "cached_rows", None):
            self._replay_cached(match)
            return
        states = list(match.states)
        odds = list(match.odds)
        cutoffs = [state.observed_at for state in states] or [_as_utc(match.started_at)]
        tape = None
        match_line = None
        self._set_lines = {}
        self._book = PriceBook()
        if self.sport_code == "tennis" and type(feature_builder).__name__ == "TennisFeatureBuilder":
            from features.tennis import MarketQuotes, ScoreTape

            tape = ScoreTape(feature_builder)
            match_line = MarketQuotes()
        state_at = 0
        odds_at = 0
        for cutoff in cutoffs:
            moment = _as_utc(cutoff)
            if tape is None:
                visible_states = [row for row in states if _as_utc(row.observed_at) <= moment]
                visible_odds = [row for row in odds if _as_utc(row.observed_at) <= moment]
            else:
                while state_at < len(states) and _as_utc(states[state_at].observed_at) <= moment:
                    state_at += 1
                while odds_at < len(odds) and _as_utc(odds[odds_at].observed_at) <= moment:
                    odds_at += 1
                visible_states = states[:state_at]
                visible_odds = odds[:odds_at]
            self._book.extend(visible_odds, moment)
            if not visible_odds:
                _bump(self.counts, "NO_ODDS")
                continue
            try:
                if tape is None:
                    vector = feature_builder.build_features(
                        event_id=str(match.event_id),
                        cutoff_time=moment,
                        event_snapshots=visible_states,
                        odds_snapshots=visible_odds,
                    )
                else:
                    vector = feature_builder.build_features(
                        event_id=str(match.event_id),
                        cutoff_time=moment,
                        event_snapshots=visible_states,
                        odds_snapshots=visible_odds,
                        tape=tape,
                        line=match_line,
                    )
            except Exception:
                _bump(self.counts, "FEATURE_BUILD")
                continue
            if vector.feature_cutoff_timestamp is not None and _as_utc(vector.feature_cutoff_timestamp) > moment:
                raise RuntimeError("feature cutoff is later than the decision")
            context = _context_as_of(match.decisions, moment) if match.decisions else dict(NEUTRAL_CONTEXT)
            saved_side = context_side_from_event(getattr(match, "event", None))
            if saved_side is not None:
                context["context_side_a"] = saved_side
            vector.features.update(context)
            if match.history_features is not None:
                vector.features.update(match.history_features(moment))
            apply_context_features(vector, self.predictor, context)
            state = visible_states[-1] if visible_states else None
            self._score_cutoff(match, vector, visible_odds, moment, state)

    def _replay_cached(self, match) -> None:
        """Score the saved training rows. Quotes for execution still come from the odds table."""
        names = list(match.feature_names)
        odds = list(match.odds)
        odds_at = 0
        version = str((self.predictor.checkpoint_data or {}).get("version_tag") or "")
        line = str((self.predictor.checkpoint_data or {}).get("model_name") or "")
        self._set_lines = {}
        self._book = PriceBook()
        enabled = self._enabled_markets()
        for index, row in enumerate(match.cached_rows):
            moment = _as_utc(match.cached_times[index])
            while odds_at < len(odds) and _as_utc(odds[odds_at].observed_at) <= moment:
                odds_at += 1
            visible = odds[:odds_at]
            self._book.extend(visible, moment)
            market = str(match.cached_markets[index] or "match_winner")
            family = "set_winner" if market.startswith("set_winner:") else market
            if family not in enabled:
                continue
            features = {name: float(row[position]) for position, name in enumerate(names)}
            # Cached training tapes predate the v4 market discriminator. The row's
            # market key is authoritative for this replay, and must be restored
            # before inference so v4 can select the correct market head.
            is_set_market = market.startswith("set_winner:")
            features["market_is_set"] = 1.0 if is_set_market else 0.0
            line_value = int(market.split(":", 1)[1]) if is_set_market else None

            # Execution prices always come from the odds visible at this cutoff,
            # never from the other market's cached feature row.
            prices = (
                self._book.set_prices().get(line_value)
                if line_value is not None
                else self._book.match_prices()
            )
            odds_a, odds_b = prices if prices is not None else (0.0, 0.0)
            features["market_odds_a"] = float(odds_a)
            features["market_odds_b"] = float(odds_b)
            vector = FeatureVector(
                event_id=str(match.event_id),
                sport_code=self.sport_code,
                feature_version=str(match.feature_version),
                feature_cutoff_timestamp=moment,
                features=features,
            )
            observe = False
            if is_set_market:
                if not self._stakes_set(line_value):
                    continue
                observe = set_is_observation_only(line_value, self.sport_code, self.config.get("observe_set")) or not model_commits_market(
                    line, version, market
                )
            self._consider(match, vector, market, odds_a, odds_b, moment, visible, line_value, observe=observe)

    def _score_cutoff(self, match, vector: FeatureVector, odds_rows, cutoff: datetime, state=None) -> None:
        version = str((self.predictor.checkpoint_data or {}).get("version_tag") or "")
        line = str((self.predictor.checkpoint_data or {}).get("model_name") or "")
        enabled = self._enabled_markets()
        from ..set_match import checkpoint_scores_sets

        scores_sets = "set_winner" in enabled and self.sport_code == "tennis" and checkpoint_scores_sets(self.predictor.checkpoint_data)
        wants_match = "match_winner" in enabled
        if not scores_sets:
            if not wants_match:
                return
            prices = self._book.match_prices()
            if prices is None:
                _bump(self.counts, "NO_ODDS")
                return
            vector.features["market_is_set"] = 0.0
            vector.features["market_odds_a"] = float(prices[0])
            vector.features["market_odds_b"] = float(prices[1])
            self._consider(match, vector, "match_winner", prices[0], prices[1], cutoff, odds_rows, None)
            return
        from features import registry as feature_registry

        original = dict(vector.features)
        prices = self._book.set_prices()
        if prices:
            self._funnel["data"]["set"].add(str(match.event_id))
        finished = _finished_sets(state)
        current_set = int(float(original.get("current_set") or 1))
        best_of = 5 if max([current_set, *finished.keys(), *prices.keys()], default=1) >= 4 else 3
        open_sides: Dict[int, Optional[str]] = {}
        builder = feature_registry.get("tennis")
        for number, (set_a, set_b) in prices.items():
            if number in finished or not set_is_committable(number, current_set, finished, best_of):
                continue
            if not self._stakes_set(number):
                continue
            market = f"set_winner:{number}"
            observe = set_is_observation_only(number, self.sport_code, self.config.get("observe_set")) or not model_commits_market(
                line, version, market
            )
            watch_key = (str(match.event_id), market)
            if observe and watch_key in self._observed:
                open_sides[number] = self._observed[watch_key]
                continue
            if (match.event_id, market) in self._confirmed:
                open_sides[number] = self._confirmed_side(match.event_id, market)
                continue
            vector.features.clear()
            vector.features.update(original)
            if builder is not None and hasattr(builder, "apply_market_line"):
                cursor = self._set_lines.get(number)
                if cursor is None and type(builder).__name__ == "TennisFeatureBuilder":
                    from features.tennis import MarketQuotes

                    cursor = MarketQuotes()
                    self._set_lines[number] = cursor
                builder.apply_market_line(
                    vector.features,
                    odds_rows,
                    cutoff,
                    market_type="set_winner",
                    line_value=number,
                    cursor=cursor,
                )
            vector.features["market_is_set"] = 1.0
            vector.features["set_index"] = float(number)
            vector.features["set_has_started"] = 1.0 if current_set >= number else 0.0
            vector.features["market_odds_a"] = set_a
            vector.features["market_odds_b"] = set_b
            vector.features["match_wait"] = 0.0
            self._consider(match, vector, market, set_a, set_b, cutoff, odds_rows, number, observe=observe)
            open_sides[number] = self._observed.get(watch_key) if observe else self._confirmed_side(match.event_id, market)
        if wants_match and (match.event_id, "match_winner") not in self._confirmed:
            vector.features.clear()
            vector.features.update(original)
            match_prices = self._book.match_prices()
            if match_prices is not None:
                vector.features["market_is_set"] = 0.0
                vector.features["match_wait"] = 0.0
                probe = self.predictor.predict(
                    vector,
                    odds_a=match_prices[0],
                    odds_b=match_prices[1],
                    is_shadow=False,
                )
                edge_side = probe.best_outcome
                edge_value = 0.0
                if edge_side:
                    chosen = next((item for item in probe.outcomes if item.outcome == edge_side), None)
                    edge_value = float(chosen.edge) if chosen else 0.0
                if self.config.get("match_decision_mode", "set_confirmed") == "independent":
                    allowed = edge_side if current_set >= 2 else None
                else:
                    allowed = match_verdict(
                        finished,
                        open_sides,
                        bool(original.get("tape_points_even")),
                        edge_side,
                        edge_value,
                        best_of,
                    )
                vector.features["match_wait"] = 0.0 if allowed else 1.0
                self._consider(
                    match,
                    vector,
                    "match_winner",
                    match_prices[0],
                    match_prices[1],
                    cutoff,
                    odds_rows,
                    None,
                    observe=not model_commits_market(line, version, "match_winner"),
                )
        vector.features.clear()
        vector.features.update(original)

    def _confirmed_side(self, event_id: str, market: str) -> Optional[str]:
        for bet in reversed(self.bets):
            if bet["event_id"] == str(event_id) and bet["market"] == market:
                return bet["outcome"]
        return None

    def _consider(self, match, vector, market: str, odds_a: float, odds_b: float, cutoff: datetime, odds_rows, line_value, observe: bool = False) -> None:
        family = "set" if str(market).startswith("set_winner") else "match"
        _bump(self.counts, "SNAPSHOTS_SEEN")
        key = (str(match.event_id), market)
        if key in self._confirmed:
            _bump(self.counts, "DUPLICATE_MARKET")
            return
        if float((vector.features or {}).get("match_wait") or 0.0) >= 0.5:
            _bump(self.counts, "MATCH_WAIT")
            return
        snapshot_at = cutoff.isoformat()
        composite = str((self.predictor.checkpoint_data or {}).get("kind") or "") == "composite"
        if composite and hasattr(self.predictor, "component_votes"):
            self._consider_composite(match, vector, market, odds_a, odds_b, cutoff, odds_rows, line_value, snapshot_at, observe)
            return
        limits = market_limits_from_mapping(market if market == "match_winner" else "set_winner", self.config) if self.sport_code == "tennis" else {"min_edge": float(self.config["min_edge"]), "confirmations": int(self.config["confirmations"])}
        min_edge = float(limits["min_edge"])
        result = self.predictor.predict(vector, odds_a=odds_a, odds_b=odds_b, is_shadow=False, min_edge_threshold=min_edge)
        if self.config.get("record_candidates"):
            self._record_candidate(match, market, cutoff, odds_rows, line_value, result)
        side = result.best_outcome if result.has_positive_edge else None
        chosen = next((item for item in (result.outcomes or []) if item.outcome == side), None)
        eligible, reason = (False, "NO_SIDE")
        if chosen is not None:
            eligible, reason = odds_allowed(
                chosen.model_probability,
                chosen.bookmaker_odds,
                market if market == "match_winner" else "set_winner",
                self.config,
                self.sport_code,
            )
            if eligible and float(chosen.edge) < min_edge:
                eligible, reason = False, "LOW_EDGE"
            if eligible:
                self._funnel["candidate"][family].add(str(match.event_id))
                _bump(self.counts, "CANDIDATE_SLICES")
        watch_key = key
        action, detail = next_watch_action(
            self._watches.get(watch_key),
            snapshot_at,
            side,
            eligible,
            reason,
            required=int(limits["confirmations"]),
        )
        self._watches[watch_key] = detail
        if action != "commit" or chosen is None:
            if side and not eligible and reason:
                _bump(self.counts, reason)
            return
        self._funnel["confirmation"][family].add(str(match.event_id))
        _bump(self.counts, "CONFIRMED_SLICES")
        if observe:
            self._observed[(str(match.event_id), market)] = str(chosen.outcome)
            return
        self._place(match, market, chosen, cutoff, odds_rows, "match_winner" if market == "match_winner" else "set_winner", line_value)

    def _consider_composite(self, match, vector, market, odds_a, odds_b, cutoff, odds_rows, line_value, snapshot_at, observe: bool = False) -> None:
        from ..composite_vote import advance_components

        key = (str(match.event_id), market)
        votes = self.predictor.component_votes(vector, odds_a, odds_b, market)
        if float((vector.features or {}).get("match_wait") or 0.0) >= 0.5:
            votes = [{**item, "side": None, "reason": item.get("reason") or "MATCH_WAIT"} for item in votes]
        previous = self._watches.get(key) or {}
        if previous.get("snapshot_at") == snapshot_at:
            return
        state = advance_components((previous.get("composite") or {}).get("components"), votes)
        side = state.get("side") if state.get("selected") else None
        self._watches[key] = {"snapshot_at": snapshot_at, "side": side, "composite": state, "watch": True}
        if not state.get("selected") or not side:
            _bump(self.counts, str(state.get("phase") or "COMPOSITE_WAIT"))
            return
        members = [item for item in state["components"] if item.get("confirmed_side") == side]
        if len(members) < 3:
            return
        probability = sum(float(item["probability"]) for item in members) / len(members)
        odds = sum(float(item["odds"]) for item in members) / len(members)
        edge = sum(float(item["edge"]) for item in members) / len(members)
        chosen = type("Side", (), {})()
        chosen.outcome = side
        chosen.model_probability = probability
        chosen.bookmaker_odds = odds
        chosen.edge = edge
        eligible, reason = odds_allowed(
            probability,
            odds,
            market if market == "match_winner" else "set_winner",
            self.config,
            self.sport_code,
        )
        if not eligible or edge < float(self.config["min_edge"]):
            _bump(self.counts, reason or "LOW_EDGE")
            return
        if observe:
            self._observed[(str(match.event_id), market)] = str(side)
            return
        self._place(match, market, chosen, cutoff, odds_rows, "match_winner" if market == "match_winner" else "set_winner", line_value)

    def _record_candidate(self, match, market: str, cutoff: datetime, odds_rows, line_value, result) -> None:
        """Store the side and the later quote. Gate search replays this tape without the model."""
        outcomes = list(result.outcomes or [])
        chosen = max(outcomes, key=lambda item: float(item.edge)) if outcomes else None
        side = ""
        probability = 0.0
        odds = 0.0
        edge = 0.0
        if chosen is not None and float(chosen.edge) >= 0.0:
            side = str(chosen.outcome)
            probability = float(chosen.model_probability)
            odds = float(chosen.bookmaker_odds)
            edge = float(chosen.edge)
        market_type = "match_winner" if market == "match_winner" else "set_winner"
        preview = _fill_preview(cutoff, _execution_quotes(odds_rows, market_type, line_value), float(self.config.get("execution_latency_sec") or 0.0))
        fill_odds = 0.0
        if side:
            fill_odds = float(preview["odds_a"] if _side_token(side) == "p1" else preview["odds_b"])
        winner = _winner_token(match.event, market) or ""
        self.candidates.append(
            {
                "event_id": str(match.event_id),
                "market": market,
                "snapshot_at": _as_utc(cutoff).isoformat(),
                "side": side,
                "probability": probability,
                "odds": odds,
                "edge": edge,
                "winner": "" if winner is None else str(winner),
                "fill_code": preview["code"],
                "fill_odds": fill_odds,
                "staleness_sec": preview["staleness_sec"],
            }
        )

    def _place(self, match, market: str, chosen, cutoff: datetime, odds_rows, market_type: str, line_value) -> None:
        if self.sport_code == "tennis":
            limits = market_limits_from_mapping(market_type, self.config)
            self.execution.config.min_edge = float(limits["min_edge"])
        quotes = _execution_quotes(odds_rows, market_type, line_value)
        fill = self.execution.execute_order(
            cutoff,
            float(chosen.model_probability),
            _side_token(chosen.outcome),
            float(chosen.bookmaker_odds),
            quotes,
        )
        if not fill.is_executed:
            _bump(self.counts, fill.rejection_code or "NOT_EXECUTED")
            return
        self.bankroll.ensure_solvent(cutoff)
        market_key = f"{match.event_id}|{market}|{cutoff.isoformat()}"
        placed = self.bankroll.place_bet(
            bet_id=market_key,
            event_id=str(match.event_id),
            target_outcome=str(chosen.outcome),
            placed_at=fill.execution_timestamp,
            odds=float(fill.execution_odds),
            stake=float(self.config["fixed_stake"]),
            probability=float(chosen.model_probability),
            edge=float(chosen.model_probability) * float(fill.execution_odds) - 1.0,
        )
        if placed is None:
            _bump(self.counts, "INSUFFICIENT_FUNDS")
            return
        family = "set" if str(market).startswith("set_winner") else "match"
        self._funnel["execution"][family].add(str(match.event_id))
        self._confirmed.add((str(match.event_id), market))
        self.bets.append(
            {
                "event_id": str(match.event_id),
                "market": market,
                "outcome": str(chosen.outcome),
                "placed_at": fill.execution_timestamp.isoformat(),
                "odds": float(fill.execution_odds),
                "stake": float(self.config["fixed_stake"]),
                "probability": round(float(chosen.model_probability), 4),
                "edge": round(float(chosen.model_probability) * float(fill.execution_odds) - 1.0, 4),
                "status": "PENDING",
                "payout": 0.0,
                "net_profit": 0.0,
            }
        )
        self.bankroll.note_equity()

    def _settle_match(self, match, when: datetime) -> None:
        for bet in list(self.bankroll.active_bets.values()):
            if bet.event_id != str(match.event_id):
                continue
            market = str(bet.bet_id).split("|", 2)[1]
            winner = _winner_token(match.event, market)
            actual = "void" if winner in (None, "void") else winner
            self.bankroll.settle_bet(bet.bet_id, actual, when)
            for row in self.bets:
                if row["event_id"] != bet.event_id or row["market"] != market or row["status"] != "PENDING":
                    continue
                if actual == "void":
                    row["status"] = "VOID"
                    row["payout"] = float(bet.stake)
                    row["net_profit"] = 0.0
                elif actual == bet.target_outcome:
                    row["status"] = "WON"
                    row["payout"] = round(float(bet.stake) * float(bet.odds), 2)
                    row["net_profit"] = round(row["payout"] - float(bet.stake), 2)
                else:
                    row["status"] = "LOST"
                    row["payout"] = 0.0
                    row["net_profit"] = -float(bet.stake)
        self.bankroll.note_equity()
