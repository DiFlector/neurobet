"""Tennis stake-gate search over one recorded decision tape.

The checkpoint is scored once. Every later combination only replays the
confirmation and paper bankroll, so the search cannot see the final matches.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from ..confirmation import next_watch_action
from tennis_adapter.policy import TennisPolicy, windows_allow
from .bankroll import ReplayBankroll

MIN_BETS = 30
MAX_DRAWDOWN = 2500.0
EDGE_GRID = (0.02, 0.03, 0.04, 0.05, 0.06)
PROBABILITY_GRID = (0.40, 0.45, 0.50, 0.55, 0.60)
CONFIRMATION_GRID = (2, 3, 4)
STALENESS_GRID = (5.0, 10.0, 15.0)
SHORT_STARTS = (1.20, 1.30, 1.40)
MID_STARTS = (1.40, 1.50, 1.60)
MATCH_ENDS = (2.00, 2.20, 2.40)
RANGE_LO = (1.20, 1.30, 1.40, 1.50, 1.60)
RANGE_HI = (1.80, 2.00, 2.20, 2.40)
RANGE_PREFERENCE = 0.02


def price_windows() -> List[Dict[str, float]]:
    """Non-empty tennis windows. Upper bounds stay exclusive and do not overlap."""
    found = []
    for short_lo in SHORT_STARTS:
        short_hi = 1.40 if short_lo < 1.40 else 1.50
        for mid_lo in MID_STARTS:
            if mid_lo < short_hi:
                continue
            mid_hi = 1.80
            if mid_lo >= mid_hi:
                continue
            for match_hi in MATCH_ENDS:
                if match_hi <= mid_hi:
                    continue
                found.append(
                    {
                        "short_odds_lo": short_lo,
                        "short_odds_hi": short_hi,
                        "mid_odds_lo": mid_lo,
                        "mid_odds_hi": mid_hi,
                        "match_odds_hi": match_hi,
                    }
                )
    return found


def iter_gate_configs() -> Iterable[Dict[str, Any]]:
    for windows in price_windows():
        for edge in EDGE_GRID:
            for probability in PROBABILITY_GRID:
                for confirmations in CONFIRMATION_GRID:
                    for staleness in STALENESS_GRID:
                        yield {
                            **windows,
                            "min_edge": edge,
                            "min_probability": probability,
                            "confirmations": confirmations,
                            "max_odds_staleness_sec": staleness,
                            "observe_set": 1,
                        }


def range_windows() -> List[Dict[str, float]]:
    """One inclusive lower bound and one exclusive upper bound."""
    found = []
    for low in RANGE_LO:
        for high in RANGE_HI:
            if high <= low + 0.15:
                continue
            found.append({"min_odds": low, "max_odds": high})
    return found


def iter_range_gate_configs() -> Iterable[Dict[str, Any]]:
    for window in range_windows():
        for edge in EDGE_GRID:
            for probability in PROBABILITY_GRID:
                for confirmations in CONFIRMATION_GRID:
                    for staleness in STALENESS_GRID:
                        yield {
                            **window,
                            "odds_gate_mode": "range",
                            "min_edge": edge,
                            "min_probability": probability,
                            "confirmations": confirmations,
                            "max_odds_staleness_sec": staleness,
                            "observe_set": 1,
                        }


def prefer_gate(range_row: Optional[Dict[str, Any]], live_row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Keep the single odds range unless the three windows are clearly better."""
    if range_row is None:
        return live_row
    if live_row is None or live_row.get("roi") is None:
        return range_row
    if range_row.get("roi") is None:
        return live_row
    live_drawdown = float(live_row.get("max_drawdown") or 0.0)
    range_drawdown = float(range_row.get("max_drawdown") or 0.0)
    if float(live_row["roi"]) >= float(range_row["roi"]) + RANGE_PREFERENCE and live_drawdown <= range_drawdown:
        return live_row
    return range_row


def choose_gates(rows: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Highest ROI, then smaller drawdown, then more bets. Weak samples are dropped."""
    eligible = [
        row
        for row in rows
        if int(row.get("bets") or 0) >= MIN_BETS
        and int(row.get("bankruptcies") or 0) == 0
        and float(row.get("max_drawdown") or 0.0) <= MAX_DRAWDOWN
        and row.get("roi") is not None
    ]
    if not eligible:
        return None
    return min(
        eligible,
        key=lambda row: (
            -float(row["roi"]),
            float(row["max_drawdown"]),
            -int(row["bets"]),
            json.dumps(row["params"], sort_keys=True),
        ),
    )


def simulate_gates(
    candidates: Sequence[Dict[str, Any]],
    params: Dict[str, Any],
    initial_bankroll: float = 5000.0,
    fixed_stake: float = 100.0,
    bankruptcy_threshold: float = 100.0,
) -> Dict[str, Any]:
    """Apply one gate set to recorded sides. This does not call the model."""
    policy = TennisPolicy(
        min_probability=float(params["min_probability"]),
        short_odds_lo=float(params.get("short_odds_lo") or 1.20),
        short_odds_hi=float(params.get("short_odds_hi") or 1.40),
        mid_odds_lo=float(params.get("mid_odds_lo") or 1.60),
        mid_odds_hi=float(params.get("mid_odds_hi") or 1.80),
        match_odds_hi=float(params.get("match_odds_hi") or 2.20),
        min_edge=float(params["min_edge"]),
        confirmations=int(params["confirmations"]),
        observe_set=int(params.get("observe_set") or 1),
        match_gate_mode="live",
        set_gate_mode="live",
        match_min_probability=float(params["min_probability"]),
        set_min_probability=float(params["min_probability"]),
        match_min_edge=float(params["min_edge"]),
        set_min_edge=float(params["min_edge"]),
    )
    bank = ReplayBankroll(initial_bankroll, fixed_stake, bankruptcy_threshold)
    watches: Dict[tuple, Dict[str, Any]] = {}
    confirmed = set()
    bets = []
    counts: Dict[str, int] = {}
    current_event = None
    open_bets: List[Dict[str, Any]] = []

    def settle(when: datetime) -> None:
        for bet in list(open_bets):
            winner = bet["winner"] or "void"
            bank.settle_bet(bet["bet_id"], winner, when)
        open_bets.clear()
        bank.note_equity()

    for row in candidates:
        event_id = str(row["event_id"])
        if current_event is not None and event_id != current_event:
            settle(_stamp(row["snapshot_at"]))
        current_event = event_id
        market = str(row["market"])
        key = (event_id, market)
        if key in confirmed:
            counts["DUPLICATE_MARKET"] = counts.get("DUPLICATE_MARKET", 0) + 1
            continue
        side = str(row.get("side") or "") or None
        probability = float(row.get("probability") or 0.0)
        odds = float(row.get("odds") or 0.0)
        edge = float(row.get("edge") or 0.0)
        eligible, reason = (False, "NO_SIDE")
        if side and edge < float(params["min_edge"]):
            side = None
        if side:
            family = "match_winner" if market == "match_winner" else "set_winner"
            if str(params.get("odds_gate_mode") or "live") == "range":
                eligible, reason = _range_allows(probability, odds, params)
            else:
                eligible, reason = windows_allow(probability, odds, family, policy)
        action, detail = next_watch_action(
            watches.get(key),
            str(row["snapshot_at"]),
            side,
            eligible,
            reason,
            required=policy.confirmations,
        )
        watches[key] = detail
        if action != "commit" or not side:
            if side and not eligible and reason:
                counts[reason] = counts.get(reason, 0) + 1
            continue
        placed = _try_fill(row, bank, policy, float(params["max_odds_staleness_sec"]), fixed_stake, counts)
        if placed is None:
            continue
        confirmed.add(key)
        open_bets.append(placed)
        bets.append(placed["record"])
        bank.note_equity()
    if current_event is not None:
        settle(datetime.now(timezone.utc))
    bank.note_equity()
    wins = sum(1 for bet in bets if bet["status"] == "WON")
    losses = sum(1 for bet in bets if bet["status"] == "LOST")
    voids = sum(1 for bet in bets if bet["status"] == "VOID")
    turnover = round(sum(float(bet["stake"]) for bet in bets if bet["status"] != "VOID"), 2)
    pnl = round(sum(float(bet["net_profit"]) for bet in bets), 2)
    return {
        "params": params,
        "matches_seen": len({str(row["event_id"]) for row in candidates}),
        "bets": len(bets),
        "wins": wins,
        "losses": losses,
        "voids": voids,
        "win_rate": round(wins / (wins + losses), 4) if wins + losses else None,
        "turnover": turnover,
        "net_pnl": pnl,
        "roi": round(pnl / turnover, 4) if turnover else None,
        "final_balance": round(float(bank.current_balance), 2),
        "max_drawdown": round(float(bank.max_drawdown), 2),
        "bankruptcies": int(bank.bankruptcies),
        "rejection_counts": counts,
    }


def _range_allows(probability: float, odds: float, params: Dict[str, Any]) -> tuple:
    if float(probability) < float(params["min_probability"]):
        return False, "LOW_PROBABILITY"
    price = float(odds)
    if price < float(params["min_odds"]):
        return False, "ODDS_TOO_SHORT"
    if price >= float(params["max_odds"]):
        return False, "ODDS_TOO_LONG"
    return True, ""


def _try_fill(
    row: Dict[str, Any],
    bank: ReplayBankroll,
    policy: TennisPolicy,
    max_staleness: float,
    stake: float,
    counts: Dict[str, int],
):
    code = str(row.get("fill_code") or "")
    staleness = row.get("staleness_sec")
    if code:
        counts[code] = counts.get(code, 0) + 1
        return None
    if staleness is None or float(staleness) > max_staleness:
        counts["STALE_ODDS"] = counts.get("STALE_ODDS", 0) + 1
        return None
    execution_odds = float(row.get("fill_odds") or 0.0)
    if execution_odds <= 1.0:
        counts["INVALID_ODDS"] = counts.get("INVALID_ODDS", 0) + 1
        return None
    probability = float(row["probability"])
    effective = probability * execution_odds - 1.0
    if effective < policy.min_edge:
        counts["PRICE_SLIPPED"] = counts.get("PRICE_SLIPPED", 0) + 1
        return None
    moment = _stamp(row["snapshot_at"]) + timedelta(seconds=2)
    bank.ensure_solvent(moment)
    bet_id = f"{row['event_id']}|{row['market']}|{row['snapshot_at']}"
    placed = bank.place_bet(
        bet_id=bet_id,
        event_id=str(row["event_id"]),
        target_outcome=str(row["side"]),
        placed_at=moment,
        odds=execution_odds,
        stake=stake,
        probability=probability,
        edge=effective,
    )
    if placed is None:
        counts["INSUFFICIENT_FUNDS"] = counts.get("INSUFFICIENT_FUNDS", 0) + 1
        return None
    winner = str(row.get("winner") or "")
    status = "VOID" if winner in ("", "void") else "WON" if winner == str(row["side"]) else "LOST"
    profit = 0.0 if status == "VOID" else round(stake * execution_odds - stake, 2) if status == "WON" else -stake
    return {
        "bet_id": bet_id,
        "winner": "void" if status == "VOID" else winner,
        "record": {
            "status": status,
            "stake": stake,
            "net_profit": profit,
        },
    }


def _stamp(value: str) -> datetime:
    moment = datetime.fromisoformat(str(value))
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)
