"""One training decision per match: the first snapshot at or above the commit edge."""

from app.commit import MIN_COMMIT_EDGE, committed_rows, first_commit_index, is_commit


NAMES = ["current_odds_player_a", "current_odds_player_b"]


def test_threshold_matches_live_gate():
    assert MIN_COMMIT_EDGE == 0.02
    assert is_commit(0.60, 0.40, 1.70, 2.20) is True
    assert is_commit(0.51, 0.49, 1.90, 1.90) is False


def test_first_snapshot_at_two_percent_is_kept_and_later_ones_are_not():
    match = [
        [1.50, 1.50],
        [2.00, 1.50],
        [3.00, 1.20],
    ]

    def probabilities(_rows):
        return [(0.60, 0.40), (0.60, 0.40), (0.80, 0.20)]

    rows, labels = committed_rows([(match, 1)], probabilities, NAMES)
    assert rows == [[2.00, 1.50]]
    assert labels == [1]
    assert first_commit_index(probabilities(match), [(1.50, 1.50), (2.00, 1.50), (3.00, 1.20)]) == 1


def test_match_that_never_reaches_two_percent_is_omitted():
    weak = [[1.10, 1.10], [1.20, 1.20]]
    rows, labels = committed_rows([(weak, 0)], lambda _rows: [(0.55, 0.45)] * len(weak), NAMES)
    assert rows == []
    assert labels == []
