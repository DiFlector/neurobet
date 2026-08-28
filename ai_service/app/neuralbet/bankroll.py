"""
Bankroll accounting for the neural bettor.

Two independent simulated cash accounts live in autobet_finished.db
(bankroll_accounts / bankroll_ledger / live_bets, created in backend/database.py):

  - "training": feeds the online-training loss (see model.py train_online). Its only
    purpose is to teach the network that going broke is catastrophic and staking
    everything on a coin-flip is bad, even if the coin-flip is individually +EV.
    Auto-resets to start_balance (with a heavy loss penalty) on ruin, per spec.

  - "live": the bot's real simulated betting activity, settled against actual resolved
    outcomes (see pipeline.py). Same ruin rule as "training": hitting 0 auto-resets the
    balance back to start_balance and bumps ruin_count. Completely independent from
    "training" otherwise — different balance, different bet history, never influences
    each other. A manual reset button in the admin panel is still available for either
    account (e.g. to change the starting balance), it's just not the only way back from 0.

Money rule for both accounts, per spec: a stake that loses is gone, period — the stake
itself is never refunded, win or lose (a win pays stake*coefficient, which already
includes the stake back; a loss returns nothing).
"""
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
import torch

from app.core.database import get_finished_connection, release_connection
from app.config import BACKEND_URL

# ---- Tunable knobs (env-overridable so they can be tuned without a code change) ----
START_BALANCE = float(os.getenv("BANKROLL_START_BALANCE", "1000.0"))
# Fractional-Kelly sizing (see allocate()). Fractions are of *equity*
# (spendable balance + locked-in-open-bets), not of unlocked cash alone.
# MIN is a hard floor on any placed stake; MAX is the per-position ceiling so
# strong edges can size above the floor. MAX_POSITIONS <= 0 = no per-round cap.
MIN_STAKE_FRACTION = float(os.getenv("BANKROLL_MIN_STAKE_FRACTION", "0.10"))
MAX_POSITION_FRACTION = float(os.getenv("BANKROLL_MAX_POSITION_FRACTION", "0.25"))
# Fraction of full Kelly actually staked. Full Kelly (bet exactly your edge) is
# provably too aggressive the moment the true edge is even slightly overestimated —
# and a model still mid-training overestimates its edge constantly. Quarter-Kelly is
# the standard conservative compromise: roughly half the variance of full Kelly for a
# small growth-rate cost.
KELLY_FRACTION = float(os.getenv("BANKROLL_KELLY_FRACTION", "0.25"))
MAX_POSITIONS = int(os.getenv("BANKROLL_MAX_POSITIONS", "0"))
RUIN_PENALTY = float(os.getenv("BANKROLL_RUIN_PENALTY", "5.0"))
BANKROLL_LOSS_WEIGHT = float(os.getenv("BANKROLL_LOSS_WEIGHT", "0.35"))
BANKROLL_LOSS_CLIP = float(os.getenv("BANKROLL_LOSS_CLIP", "3.0"))
BANKROLL_LOSS_DISABLED_PASSES = int(os.getenv("BANKROLL_LOSS_DISABLED_PASSES", "0"))

# A balance that's technically > 0 but too small to ever clear the min-equity stake
# again is functionally ruined — without this floor, float rounding can strand an
# account at e.g. 1e-6 forever (never exactly <= 0, so the auto-reset below never fires).
RUIN_THRESHOLD = 0.01

# Upper bound on balance/peak_balance — without this, unbounded compounding across many
# rounds/settlements can drift the DOUBLE PRECISION balance toward float overflow (inf),
# which then poisons every future round (inf - inf = NaN, rejected by the NOT NULL
# column). Every place that grows balance (apply_round_result, settle_stake) clamps to
# this ceiling; a reset also clamps its target start_balance so a bad manual reset can't
# start already past the cap.
MAX_BALANCE = float(os.getenv("BANKROLL_MAX_BALANCE", "1000000000000.0"))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Persistent account state
# ---------------------------------------------------------------------------

def _row_to_account(row) -> Dict[str, Any]:
    if row is None:
        return {
            "account": "", "balance": START_BALANCE, "start_balance": START_BALANCE,
            "peak_balance": START_BALANCE, "locked": 0.0, "rounds": 0, "bets_placed": 0,
            "wins": 0, "losses": 0, "total_staked": 0.0, "total_returned": 0.0,
            "ruin_count": 0, "is_ruined": 0, "updated_at": None,
        }
    return dict(row)


def get_account(account: str) -> Dict[str, Any]:
    conn = get_finished_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM bankroll_accounts WHERE account = %s", (account,))
    row = cur.fetchone()
    release_connection(conn)
    payload = _row_to_account(row)
    payload["account"] = account
    return payload


def reset_account(account: str, start_balance: Optional[float] = None) -> Dict[str, Any]:
    """Manual reset — the only way the 'live' account can ever come back from ruin."""
    sb = min(start_balance if start_balance is not None else START_BALANCE, MAX_BALANCE)
    conn = get_finished_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO bankroll_accounts (account, balance, start_balance, peak_balance, updated_at)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT(account) DO UPDATE SET
            balance = excluded.balance, start_balance = excluded.start_balance,
            peak_balance = excluded.peak_balance, locked = 0, is_ruined = 0,
            updated_at = excluded.updated_at;
    """, (account, sb, sb, sb, now_iso()))
    conn.commit()
    release_connection(conn)
    return get_account(account)


def apply_round_result(
    account: str, staked: float, returned: float, bets_count: int, wins: int, losses: int
) -> Dict[str, Any]:
    """
    Settle one round of betting against a persistent account: subtract what was staked,
    add back what came back (winning payouts only — losing stakes never return), track
    the running peak, and handle ruin. Both accounts use the same rule: balance <= 0
    auto-resets to start_balance and bumps ruin_count (the loss-side penalty for the
    training account is applied separately in model.py, this just does the bookkeeping).
    `is_ruined` is kept as a transient "just hit zero this round" flag for the UI/logs,
    not a standing lock — the balance is already back to start_balance by the time this
    returns, so a fresh round can bet again immediately.
    """
    conn = get_finished_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM bankroll_accounts WHERE account = %s", (account,))
    acc = _row_to_account(cur.fetchone())
    acc["account"] = account
    balance_before = acc["balance"]
    balance_after = balance_before - staked + returned
    ruined = balance_after <= RUIN_THRESHOLD
    ruin_count = acc["ruin_count"]
    is_ruined = 0

    if ruined:
        ruin_count += 1
        balance_after = acc["start_balance"]
        is_ruined = 1

    balance_after = min(balance_after, MAX_BALANCE)
    peak = max(acc["peak_balance"], balance_after)
    round_no = acc["rounds"] + 1

    cur.execute("""
        UPDATE bankroll_accounts SET
            balance = %s, peak_balance = %s, rounds = %s, bets_placed = bets_placed + %s,
            wins = wins + %s, losses = losses + %s, total_staked = total_staked + %s,
            total_returned = total_returned + %s, ruin_count = %s, is_ruined = %s, updated_at = %s
        WHERE account = %s
    """, (
        balance_after, peak, round_no, bets_count, wins, losses, staked, returned,
        ruin_count, is_ruined, now_iso(), account,
    ))
    cur.execute("""
        INSERT INTO bankroll_ledger
            (account, round_no, balance_before, balance_after, staked, returned, bets_count, ruined, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (account, round_no, balance_before, balance_after, staked, returned, bets_count, int(ruined), now_iso()))
    conn.commit()
    release_connection(conn)
    return get_account(account)


def lock_stake(account: str, amount: float) -> None:
    """Debits `amount` from balance into `locked` when a live bet is opened. A stake in
    `locked` is money that's already left the spendable balance but hasn't been decided
    yet — it only returns (in part or in full) when settle_stake resolves it."""
    conn = get_finished_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE bankroll_accounts SET
            balance = balance - %s, locked = locked + %s, bets_placed = bets_placed + 1,
            total_staked = total_staked + %s, updated_at = %s
        WHERE account = %s
    """, (amount, amount, amount, now_iso(), account))
    conn.commit()
    release_connection(conn)


def settle_stake(account: str, stake: float, payout: float, outcome: str) -> Dict[str, Any]:
    """
    Resolves one previously-locked live stake. `payout` is stake*coefficient on a win,
    0 on a loss (the stake is gone for good — never refunded), or `stake` itself on a
    void/ungradable outcome (the only case money comes back untouched). `outcome` is
    one of "win"/"loss"/"void", used only for the wins/losses counters.
    """
    conn = get_finished_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM bankroll_accounts WHERE account = %s", (account,))
    acc = _row_to_account(cur.fetchone())
    acc["account"] = account
    balance_before = acc["balance"]
    balance_after = balance_before + payout
    locked_after = max(acc["locked"] - stake, 0.0)
    ruined = balance_after <= RUIN_THRESHOLD
    ruin_count = acc["ruin_count"]
    is_ruined = 0

    if ruined:
        ruin_count += 1
        balance_after = acc["start_balance"]
        locked_after = 0.0
        is_ruined = 1

    balance_after = min(balance_after, MAX_BALANCE)
    peak = max(acc["peak_balance"], balance_after)
    cur.execute("""
        UPDATE bankroll_accounts SET
            balance = %s, locked = %s, peak_balance = %s, total_returned = total_returned + %s,
            wins = wins + %s, losses = losses + %s, ruin_count = %s, is_ruined = %s, updated_at = %s
        WHERE account = %s
    """, (
        balance_after, locked_after, peak, payout,
        int(outcome == "win"), int(outcome == "loss"),
        ruin_count, is_ruined, now_iso(), account,
    ))
    cur.execute("""
        INSERT INTO bankroll_ledger
            (account, round_no, balance_before, balance_after, staked, returned, bets_count, ruined, created_at)
        VALUES (%s, NULL, %s, %s, %s, %s, 1, %s, %s)
    """, (account, balance_before, balance_after, stake, payout, int(ruined), now_iso()))
    conn.commit()
    release_connection(conn)
    return get_account(account)


def get_ledger(account: str, limit: int = 200) -> List[Dict[str, Any]]:
    conn = get_finished_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM bankroll_ledger WHERE account = %s ORDER BY id DESC LIMIT %s",
        (account, limit),
    )
    rows = cur.fetchall()
    release_connection(conn)
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# "live" account — owned by backend now (backend/database.py: get_live_account,
# place_live_bet_candidates, settle_live_bets). This service only proposes candidates
# and reads its balance back over HTTP, so backend can re-validate market freshness
# against its own live data at the exact moment a bet would be written — see the plan
# note in backend/database.py for why that matters (a market a bookmaker has pulled or
# replaced can otherwise sit "live" in a stale local view indefinitely).
# ---------------------------------------------------------------------------

def fetch_live_bankroll() -> Dict[str, float]:
    """Spendable balance, locked-in-open-bets, and equity (= balance + locked)."""
    try:
        with httpx.Client(timeout=10.0) as client:
            res = client.get(f"{BACKEND_URL}/api/internal/live-bankroll")
            res.raise_for_status()
            acc = res.json()["account"]
            balance = float(acc.get("balance") or 0.0)
            locked = float(acc.get("locked") or 0.0)
            return {"balance": balance, "locked": locked, "equity": balance + locked}
    except Exception:
        return {"balance": 0.0, "locked": 0.0, "equity": 0.0}


def fetch_live_balance() -> float:
    return fetch_live_bankroll()["balance"]


def submit_live_bet_candidates(candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    """candidates: [{event_id, factor_id, market_prefix, parameter, label, match_name,
    coefficient, stake_fraction, win_probability}, ...]. Returns backend's verdict —
    {"placed": [...], "skipped": [{"candidate":..., "reason":...}, ...]}."""
    if not candidates:
        return {"placed": [], "skipped": []}
    try:
        with httpx.Client(timeout=15.0) as client:
            res = client.post(f"{BACKEND_URL}/api/internal/live-bets", json={"candidates": candidates})
            res.raise_for_status()
            data = res.json()
            return {"placed": data.get("placed", []), "skipped": data.get("skipped", [])}
    except Exception:
        return {"placed": [], "skipped": [{"candidate": c, "reason": "backend_unreachable"} for c in candidates]}


# ---------------------------------------------------------------------------
# Differentiable allocation / settlement — used inside the training loss
# ---------------------------------------------------------------------------

def allocate(win_probs: torch.Tensor, coefficients: torch.Tensor, stake_logits: torch.Tensor) -> torch.Tensor:
    """
    Turns a round's candidates into independent fractions-of-equity to risk on each.
    Full Kelly f* = (p*c - 1) / (c - 1); we take KELLY_FRACTION of that, capped at
    MAX_POSITION_FRACTION, then map strength into [MIN_STAKE_FRACTION, MAX_POSITION_FRACTION]
    so every placed stake is at least the equity floor and stronger edges size up toward
    the ceiling. Stake-head sigmoid only scales within that band (cannot go below the floor).
    MAX_POSITIONS <= 0 disables the per-round count cap.
    """
    edge = win_probs * coefficients - 1.0
    denom = torch.clamp(coefficients - 1.0, min=1e-6)
    full_kelly = torch.clamp(edge / denom, min=0.0)
    capped_kelly = torch.clamp(KELLY_FRACTION * full_kelly, max=MAX_POSITION_FRACTION)

    has_edge = full_kelly.detach() > 0
    # Strength in [0, 1]: how far capped-Kelly sits toward the ceiling, times stake head.
    kelly_unit = capped_kelly / max(MAX_POSITION_FRACTION, 1e-6)
    strength = torch.clamp(kelly_unit, 0.0, 1.0) * torch.sigmoid(stake_logits)
    span = max(MAX_POSITION_FRACTION - MIN_STAKE_FRACTION, 0.0)
    fractions = torch.where(
        has_edge,
        MIN_STAKE_FRACTION + span * strength,
        torch.zeros_like(capped_kelly),
    )

    keep = has_edge
    if MAX_POSITIONS > 0 and int(keep.sum().item()) > MAX_POSITIONS:
        topk = torch.topk(fractions.detach(), MAX_POSITIONS).indices
        mask = torch.zeros_like(fractions, dtype=torch.bool)
        mask[topk] = True
        keep = keep & mask
    return fractions * keep.float()


def settle(fractions: torch.Tensor, coefficients: torch.Tensor, wins: torch.Tensor) -> torch.Tensor:
    """
    gain = fraction of bank remaining after the round: money never staked, plus payouts
    on winners, minus stakes lost outright on losers (a lost stake is never returned).
    `wins` must be the true historical outcome (0/1), not a model prediction — this is
    "what would actually have happened to real money," used only to shape the loss.
    """
    return 1.0 - fractions.sum() + (fractions * coefficients * wins).sum()
