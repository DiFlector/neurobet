"""One tennis pass: set markets and the match verdict that follows from them."""

from __future__ import annotations

from typing import Dict, Optional

from tennis_adapter.policy import load_tennis_policy, set_is_observation_only

MIN_EDGE = load_tennis_policy().min_edge


def is_v2(version_tag: Optional[str]) -> bool:
    return str(version_tag or "").startswith("tennis_v2")


def checkpoint_scores_sets(checkpoint: Optional[dict]) -> bool:
    """A set line scores sets from its features. The assigned match line stays on the match."""
    data = checkpoint or {}
    names = {str(item) for item in (data.get("feature_names") or [])}
    capable = str(data.get("kind") or "") == "composite" or {"market_is_set", "set_index"} <= names or is_v2(data.get("version_tag"))
    from tennis_adapter.policy import _assigned, load_tennis_policy

    policy = load_tennis_policy()
    model_name = str(data.get("model_name") or "")
    match_name = _assigned(policy.match_model)[0]
    set_name = _assigned(policy.set_model)[0]
    if model_name and model_name == set_name:
        return True
    if model_name and model_name == match_name:
        return False
    return capable


def sets_to_win(best_of: int) -> int:
    size = 5 if int(best_of) >= 5 else 3
    return size // 2 + 1


def set_is_committable(number: int, current_set: int, finished: Dict[int, str], best_of: int = 3) -> bool:
    """A quoted set can be committed only while it is the set actually being played."""
    try:
        target = int(number)
        playing = int(current_set)
    except (TypeError, ValueError):
        return False
    if target < 1 or target in finished or target > playing:
        return False
    limit = 5 if int(best_of) >= 5 else 3
    if target > limit:
        return False
    need = sets_to_win(best_of)
    won_a = sum(1 for winner in finished.values() if winner == "player_a")
    won_b = sum(1 for winner in finished.values() if winner == "player_b")
    return won_a < need and won_b < need


def match_verdict(
    finished: Dict[int, str],
    open_sides: Dict[int, Optional[str]],
    points_even: bool,
    match_edge_side: Optional[str],
    match_edge: float,
    best_of: int = 3,
    min_edge: Optional[float] = None,
) -> Optional[str]:
    """
    Side of the match, or None while the set picture is contested.

    finished includes sets that closed before a verdict was placed.
    open_sides are quoted sets still awaiting or holding a confirmed side.
    """
    need = sets_to_win(best_of)
    won_a = sum(1 for winner in finished.values() if winner == "player_a")
    won_b = sum(1 for winner in finished.values() if winner == "player_b")
    if points_even and won_a == won_b:
        return None
    confirmed = [side for side in open_sides.values() if side]
    if any(side == "player_a" for side in confirmed) and any(side == "player_b" for side in confirmed):
        return None
    add_a = sum(1 for side in confirmed if side == "player_a")
    add_b = sum(1 for side in confirmed if side == "player_b")
    unfinished = any(side is None for side in open_sides.values())
    if won_a >= need and won_b < need:
        side = "player_a"
    elif won_b >= need and won_a < need:
        side = "player_b"
    elif unfinished or not confirmed:
        return None
    elif won_a + add_a >= need and won_b + add_b < need:
        side = "player_a"
    elif won_b + add_b >= need and won_a + add_a < need:
        side = "player_b"
    else:
        return None
    threshold = float(min_edge) if min_edge is not None else float(load_tennis_policy().min_edge)
    if match_edge_side != side or match_edge < min(0.03, threshold):
        return None
    return side
