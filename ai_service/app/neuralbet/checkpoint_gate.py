"""Online GRU checkpoint accept/reject (no torch).

Floor is applied to the *attempted* Brier only. If incoming weights have already
drifted above last-accepted + tolerance, a probe that beats incoming (and clears
the min best_epoch) is allowed through so catch-up cannot deadlock.

Recovery is *same-pin only*. A val-pin refresh changes the Brier yardstick
(incoming jumps because the held-out slice changed, not because the GRU
drifted). Comparing that incoming to a last-accepted floor from the previous
pin looks like "already over floor" and would accept a worse model. Callers
must pass same_val_pin=False on a pin change; the gate then rejects so the
caller can rebase the floor onto the current weights' Brier on the new pin.
"""
from __future__ import annotations

from typing import Optional, Tuple


def decide_online_checkpoint(
    *,
    attempted_brier: float,
    incoming_brier: float,
    last_accepted: Optional[float],
    floor_tol: float,
    brier_eps: float,
    min_best_epoch: int,
    best_epoch: Optional[int],
    attempted_win_bce: Optional[float] = None,
    incoming_win_bce: Optional[float] = None,
    same_val_pin: bool = True,
) -> Tuple[bool, Optional[str]]:
    # Pin change is not model drift — check before best_epoch so a refresh pass
    # that also memorized the batch still rebases the floor instead of falling
    # through to recovery on the next pass.
    if not same_val_pin:
        return False, "val_pin_refresh"

    if (
        min_best_epoch > 0
        and best_epoch is not None
        and int(best_epoch) < min_best_epoch
    ):
        return False, "best_epoch"

    incoming_over = last_accepted is not None and incoming_brier > last_accepted + floor_tol
    attempted_over = last_accepted is not None and attempted_brier > last_accepted + floor_tol
    improved = attempted_brier < incoming_brier - brier_eps
    tie_bce = (
        attempted_brier <= incoming_brier + brier_eps
        and attempted_win_bce is not None
        and incoming_win_bce is not None
        and attempted_win_bce < incoming_win_bce - 1e-4
    )

    if attempted_over and not incoming_over:
        return False, "floor"
    if attempted_over and incoming_over:
        if improved:
            return True, None
        return False, "floor"
    if improved or tie_bce:
        return True, None
    return False, "incoming"
