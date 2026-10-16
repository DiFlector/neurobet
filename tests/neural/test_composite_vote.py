"""Composite agreement: three lines, one visible decision."""

from app.composite_predictor import eligible_side
from app.composite_vote import advance_components


def _vote(component_id, side, reason=""):
    return {
        "id": component_id,
        "label": component_id,
        "side": side,
        "probability": 0.62,
        "odds": 1.7,
        "edge": 0.05,
        "reason": reason,
    }


def test_first_snapshot_is_not_a_choice():
    state = advance_components([], [
        _vote("logistic", "player_a"),
        _vote("boosting", "player_a"),
        _vote("residual_mlp", "player_a"),
    ])
    assert state["selected"] is False
    assert state["display_phase"] == "0/3"
    assert state["agreement_count"] == 0


def test_second_snapshot_selects_only_when_all_three_match():
    first = advance_components([], [
        _vote("logistic", "player_a"),
        _vote("boosting", "player_a"),
        _vote("residual_mlp", "player_b"),
    ])
    second = advance_components(first["components"], [
        _vote("logistic", "player_a"),
        _vote("boosting", "player_a"),
        _vote("residual_mlp", "player_b"),
    ])
    assert second["selected"] is False
    assert second["display_phase"] == "2/3"
    assert second["side"] == "player_a"

    agreed = advance_components(first["components"], [
        _vote("logistic", "player_a"),
        _vote("boosting", "player_a"),
        _vote("residual_mlp", "player_a"),
    ])
    assert agreed["selected"] is False
    assert agreed["agreement_count"] == 2


def test_unanimous_second_snapshot_is_chosen():
    first = advance_components([], [
        _vote("logistic", "player_a"),
        _vote("boosting", "player_a"),
        _vote("residual_mlp", "player_a"),
    ])
    second = advance_components(first["components"], [
        _vote("logistic", "player_a"),
        _vote("boosting", "player_a"),
        _vote("residual_mlp", "player_a"),
    ])
    assert second["selected"] is True
    assert second["display_phase"] == "Выбрано"
    assert second["phase"] == "3/3"
    assert second["side"] == "player_a"
    assert all(item["confirmed"] for item in second["components"])


def test_side_change_breaks_confirmation():
    first = advance_components([], [_vote("logistic", "player_a"), _vote("boosting", "player_a"), _vote("residual_mlp", "player_a")])
    flipped = advance_components(first["components"], [
        _vote("logistic", "player_b"),
        _vote("boosting", "player_a"),
        _vote("residual_mlp", "player_a"),
    ])
    assert flipped["selected"] is False
    assert flipped["agreement_count"] == 2


def test_missing_edge_or_closed_odds_window_has_no_side():
    short = eligible_side(0.95, 1.10, 6.0, "match_winner")
    assert short["side"] is None
    assert short["reason"] == "ODDS_TOO_SHORT"
    set_long = eligible_side(0.38, 2.20, 1.70, "set_winner:1")
    assert set_long["side"] == "player_b"
    outside = eligible_side(0.70, 1.50, 2.80, "match_winner")
    assert outside["side"] is None
    assert outside["reason"] == "ODDS_OUT_OF_WINDOW"


if __name__ == "__main__":
    test_first_snapshot_is_not_a_choice()
    test_second_snapshot_selects_only_when_all_three_match()
    test_unanimous_second_snapshot_is_chosen()
    test_side_change_breaks_confirmation()
    test_missing_edge_or_closed_odds_window_has_no_side()
    print("test_composite_vote: OK")
