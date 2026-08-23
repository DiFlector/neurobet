"""Outcome calls, stake band, 1X2 prior, and coeff-steam p nudge."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))

from neurobet_filters import (  # noqa: E402
    DRAW_FACTOR_ID,
    MIN_BET_COEFF,
    W1_FACTOR_ID,
    W2_FACTOR_ID,
    adjust_p_for_coeff_move,
    coeff_ok_for_stake,
    outcome_will_win,
    outcome_will_win_sql,
    score_1x2_prior,
)


def test_outside_band_is_not_auto_loss():
    # No p / no prior → skip (None), never a recorded miss.
    assert outcome_will_win(0, 1.02) is None
    assert outcome_will_win(0, MIN_BET_COEFF - 0.01) is None
    assert outcome_will_win(None, 1.02) is None
    assert outcome_will_win(0, 1.70) is None
    # Short with high p is a win call; low p is «скорее всего не победит».
    assert outcome_will_win(0, 1.02, win_probability=0.96) == 1
    assert outcome_will_win(0, 1.02, win_probability=0.30) == 0
    assert outcome_will_win(1, 1.70, win_probability=0.70) == 1


def test_sql_is_skip_not_ev_loss():
    sql = outcome_will_win_sql()
    assert "ELSE NULL END)" in sql
    assert "ELSE h.predicted_win END" not in sql
    assert str(int(W1_FACTOR_ID)) in sql
    assert f"< {float(MIN_BET_COEFF):g}" not in sql


def test_coeff_ok_for_stake_band_and_high_p_tail():
    assert coeff_ok_for_stake(1.05) is False
    assert coeff_ok_for_stake(1.20) is True
    assert coeff_ok_for_stake(2.00) is True
    assert coeff_ok_for_stake(2.20, 0.89) is False
    assert coeff_ok_for_stake(2.20, 0.90) is True
    assert coeff_ok_for_stake(2.20, 90.0) is True
    assert coeff_ok_for_stake(2.60, 0.99) is False


def test_score_1x2_prior_leader_and_tied():
    assert score_1x2_prior(W1_FACTOR_ID, 1, 0) == 1
    assert score_1x2_prior(DRAW_FACTOR_ID, 1, 0) == 0
    assert score_1x2_prior(W2_FACTOR_ID, 1, 0) == 0
    assert score_1x2_prior(W1_FACTOR_ID, 0, 0) is None
    assert score_1x2_prior(W2_FACTOR_ID, 0, 2) == 1
    assert score_1x2_prior(930, 1, 0) is None


def test_outcome_will_win_score_prior_overrides_ev():
    assert outcome_will_win(
        1, 1.80, factor_id=W2_FACTOR_ID, score_1=1, score_2=0, win_probability=0.70,
    ) == 0
    assert outcome_will_win(
        0, 1.02, factor_id=W1_FACTOR_ID, score_1=1, score_2=0, win_probability=0.96,
    ) == 1
    assert outcome_will_win(
        0, 1.80, factor_id=W1_FACTOR_ID, score_1=0, score_2=0, win_probability=0.55,
    ) == 1


def test_adjust_p_for_coeff_move_steam():
    base = 0.50
    # Price shortened 1.80 → 1.50 → p up.
    up = adjust_p_for_coeff_move(base, 1.50, 1.80)
    assert up > base
    # Price drifted out 1.50 → 1.80 → p down.
    down = adjust_p_for_coeff_move(base, 1.80, 1.50)
    assert down < base
    # No initial → unchanged (aside from clip).
    assert abs(adjust_p_for_coeff_move(base, 1.50, None) - base) < 1e-9


def test_falling_coeff_can_flip_will_win():
    # 48% with shortening price can cross 50%.
    assert outcome_will_win(
        0, 1.50, win_probability=0.48, initial_coefficient=1.80,
    ) == 1
    assert outcome_will_win(
        0, 1.80, win_probability=0.52, initial_coefficient=1.50,
    ) == 0


if __name__ == "__main__":
    test_outside_band_is_not_auto_loss()
    test_sql_is_skip_not_ev_loss()
    test_coeff_ok_for_stake_band_and_high_p_tail()
    test_score_1x2_prior_leader_and_tied()
    test_outcome_will_win_score_prior_overrides_ev()
    test_adjust_p_for_coeff_move_steam()
    test_falling_coeff_can_flip_will_win()
    print("ok")
