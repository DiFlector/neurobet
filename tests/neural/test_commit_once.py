"""One training decision per match: the second snapshot that repeats the same side."""

from app.commit import MIN_COMMIT_EDGE, committed_rows, confirming_index, first_commit_index, is_commit


NAMES = ["current_odds_player_a", "current_odds_player_b"]


def test_threshold_matches_live_gate():
    assert MIN_COMMIT_EDGE == 0.02
    assert is_commit(0.60, 0.40, 1.70, 2.20) is True
    assert is_commit(0.51, 0.49, 1.90, 1.90) is False


def test_confirming_snapshot_is_kept():
    match = [
        [1.50, 1.50],
        [2.00, 1.70],
        [1.85, 1.70],
    ]
    odds = [(1.50, 1.50), (2.00, 1.70), (1.85, 1.70)]
    probabilities = [(0.60, 0.40), (0.60, 0.40), (0.80, 0.20)]

    rows, labels = committed_rows([(match, 1)], lambda _rows: probabilities, NAMES)
    assert rows == [[1.85, 1.70]]
    assert labels == [1]
    assert confirming_index(probabilities, odds) == 2
    assert first_commit_index(probabilities, odds) == 2


def test_one_qualifying_snapshot_is_not_a_bet():
    match = [[2.00, 1.50]]
    rows, labels = committed_rows([(match, 1)], lambda _rows: [(0.60, 0.40)], NAMES)
    assert rows == []
    assert labels == []


def test_side_change_waits_for_the_next_agreement():
    match = [[1.70, 1.90], [1.90, 1.70], [1.95, 1.70]]
    probabilities = [(0.60, 0.40), (0.30, 0.70), (0.30, 0.70)]
    rows, _labels = committed_rows([(match, 0)], lambda _rows: probabilities, NAMES)
    assert rows == [[1.95, 1.70]]


def test_match_that_never_reaches_two_percent_is_omitted():
    weak = [[1.10, 1.10], [1.20, 1.20]]
    rows, labels = committed_rows([(weak, 0)], lambda _rows: [(0.55, 0.45)] * len(weak), NAMES)
    assert rows == []
    assert labels == []
