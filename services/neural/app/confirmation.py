"""Shared two-snapshot confirmation used by live inference and historical replay."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


def next_watch_action(
    detail: Optional[Dict[str, Any]],
    snapshot_at: str,
    side: Optional[str],
    eligible: bool,
    reason: str,
    required: int = 2,
) -> Tuple[str, Dict[str, Any]]:
    """Decide whether this snapshot is unchanged, a watch, or a commit.

    A side is committed only after `required` distinct snapshots name it and the
    current snapshot passes the stake gate. The default of two snapshots is the
    live rule.
    """
    previous = dict(detail or {})
    if previous.get("snapshot_at") == snapshot_at:
        same_reason = previous.get("decision_reason") == reason
        same_gate = bool(previous.get("decision_eligible", True)) == bool(eligible)
        if same_reason and same_gate:
            return "unchanged", previous
        previous["decision_eligible"] = bool(eligible)
        previous["decision_reason"] = reason
        previous["side"] = side
        return "refresh", previous

    prior_side = previous.get("side")
    prior_count = int(previous.get("agree_count") or (1 if prior_side else 0))
    if side and prior_side and side == prior_side:
        count = prior_count + 1
    else:
        count = 1 if side else 0
    updated = {
        "watch": True,
        "snapshot_at": snapshot_at,
        "side": side,
        "decision_eligible": bool(eligible),
        "decision_reason": reason,
        "agree_count": count,
    }
    if side and prior_side and side == prior_side and eligible and count >= int(required):
        return "commit", updated
    return "watch", updated
