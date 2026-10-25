"""Temporal split, calibration holdout, rating cutoff, and the tennis Markov model."""

from datetime import datetime, timedelta, timezone

import numpy as np

from app.compare.calibration import ProbabilityCalibrator
from app.compare.candidates.markov import match_win
from app.compare.candidates.rating import HistoryMatch, RatingBook
from app.compare.load import load_external_prematch, load_history
from app.compare.rows import CompareRow
from app.compare.splits import assert_disjoint, split_matches
from app.models import TemporalCalibratedEstimator, build_logistic_regression_pipeline


def _row(event_id, start, label=1):
    return CompareRow(
        event_id=event_id,
        observed_at=start,
        started_at=start,
        finished_at=start + timedelta(hours=2),
        phase="prematch",
        label=label,
        odds_a=1.8,
        odds_b=2.0,
        market_prob_a=2.0 / 3.8,
        player_a="a",
        player_b="b",
    )


def test_canonical_match_loaders_exclude_rows_linked_as_cross_source_duplicates():
    class Result:
        def fetchall(self):
            return []

    class Session:
        def __init__(self):
            self.statements = []

        def execute(self, statement, *_args):
            self.statements.append(str(statement))
            return Result()

    session = Session()
    assert load_history(session, {}) == []
    canonical_history_query = next(
        statement for statement in session.statements
        if "FROM ext_tennis_canonical_matches" in statement
    )
    assert "duplicate_of_id IS NULL" in canonical_history_query

    session = Session()
    assert load_external_prematch(session) == []
    assert "duplicate_of_id IS NULL" in session.statements[0]


def test_split_keeps_matches_apart():
    origin = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for index in range(12):
        start = origin + timedelta(days=index)
        rows.append(_row(f"m{index}", start, label=index % 2))
        rows.append(_row(f"m{index}", start + timedelta(minutes=10), label=index % 2))
    train, validation, test = split_matches(rows, embargo=timedelta(0))
    assert_disjoint(train, validation, test)
    assert train and validation and test
    assert max(row.started_at for row in train) < min(row.started_at for row in validation)
    assert max(row.started_at for row in validation) < min(row.started_at for row in test)


def test_calibrator_rejects_test_matches():
    calibrator = ProbabilityCalibrator("sigmoid")
    try:
        calibrator.fit(
            np.asarray([0.2, 0.8, 0.3, 0.7, 0.4, 0.6, 0.25, 0.75]),
            np.asarray([0, 1, 0, 1, 0, 1, 0, 1]),
            ["a", "b", "c", "d", "e", "f", "g", "h"],
            {"e"},
        )
    except RuntimeError as exc:
        assert "теста" in str(exc)
    else:
        raise AssertionError("calibrator accepted a test match")


def test_isotonic_skipped_on_few_matches():
    calibrator = ProbabilityCalibrator("isotonic")
    labels = np.asarray([0, 1] * 20)
    probs = np.clip(labels * 0.6 + 0.2, 0.05, 0.95)
    calibrator.fit(probs, labels, [f"m{i}" for i in range(40)], set())
    assert calibrator.method == "none"
    assert calibrator.skipped_reason


def test_rating_hides_future_matches():
    moment = datetime(2026, 6, 1, tzinfo=timezone.utc)
    history = [
        HistoryMatch(
            available_at=moment,
            winner="Alice",
            loser="Bob",
            surface="hard",
            tour_level="atp",
        )
    ]
    book = RatingBook(history)
    before = book.lookup("Alice", moment - timedelta(days=1), "hard", "atp")
    after = book.lookup("Alice", moment + timedelta(minutes=1), "hard", "atp")
    assert before["seen"] is False
    assert after["seen"] is True
    assert after["elo"] > before["elo"]


def test_markov_symmetry_and_match_point():
    even = match_win(0, 0, 0, 0, 0, 0, 0.62, 0.62, "")
    assert abs(even - 0.5) < 0.02
    ahead = match_win(1, 0, 5, 0, 3, 0, 0.65, 0.65, "a", sets_to_win=2)
    behind = match_win(0, 1, 0, 5, 0, 3, 0.65, 0.65, "b", sets_to_win=2)
    assert ahead > 0.85
    assert ahead > behind
    closer = match_win(1, 0, 3, 3, 0, 0, 0.65, 0.65, "a", sets_to_win=2)
    assert ahead > closer > behind


def test_same_seed_same_split():
    origin = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = [_row(f"m{index}", origin + timedelta(days=index), index % 2) for index in range(10)]
    first = split_matches(rows, embargo=timedelta(0))
    second = split_matches(rows, embargo=timedelta(0))
    assert [row.event_id for row in first[2]] == [row.event_id for row in second[2]]


def test_temporal_calibration_uses_later_rows():
    model = build_logistic_regression_pipeline(C=1.0, calibration_method="sigmoid")
    assert isinstance(model, TemporalCalibratedEstimator)
    rng = np.random.default_rng(1)
    rows = rng.normal(size=(40, 4))
    labels = np.array([0, 1] * 20)
    model.fit(rows, labels)
    probs = model.predict_proba(rows)
    assert probs.shape == (40, 2)
    assert model.calibrated_ is True


def test_two_spellings_merge():
    from datetime import datetime, timedelta, timezone

    from app.compare.canonical import RawMatch, cluster_matches

    day = datetime(2024, 6, 1, tzinfo=timezone.utc)
    sackmann = RawMatch(
        source="sackmann_sqlite",
        source_match_id="2024-1-1",
        played_at=day,
        available_at=day + timedelta(days=7),
        tour_level="atp",
        tournament="Halle",
        surface="Hard",
        best_of=3,
        round_name="F",
        gender="m",
        winner_name="Daniil Medvedev",
        loser_name="Jannik Sinner",
        winner_player_id="1",
        loser_player_id="2",
        score="6-4 6-3",
    )
    tml = RawMatch(
        source="tml",
        source_match_id="2024-1-1-b",
        played_at=day,
        available_at=day + timedelta(days=7),
        tour_level="atp",
        tournament="Halle",
        surface="hard",
        best_of=3,
        round_name="F",
        gender="m",
        winner_name="Medvedev D.",
        loser_name="Sinner J.",
        winner_player_id="1",
        loser_player_id="2",
        score="6-4, 6-3",
    )
    api_tennis = RawMatch(
        source="api_tennis",
        source_match_id="api-2024-1",
        played_at=day,
        available_at=day + timedelta(days=8),
        tour_level="ATP Singles",
        tournament="Halle",
        surface="unknown",
        best_of=3,
        round_name="F",
        gender="m",
        winner_name="Daniil Medvedev",
        loser_name="Jannik Sinner",
        winner_player_id="api-player-1",
        loser_player_id="api-player-2",
        score="2-0",
    )
    odds = RawMatch(
        source="atp_odds",
        source_match_id="odds-1",
        played_at=day + timedelta(days=3),
        available_at=day + timedelta(days=4),
        tour_level="ATP250",
        tournament="Halle",
        surface="hard",
        best_of=3,
        round_name="F",
        gender="m",
        winner_name="Medvedev D.",
        loser_name="Sinner J.",
        score="",
        odds_winner=1.7,
        odds_loser=2.1,
    )
    groups = cluster_matches([sackmann, tml, odds, api_tennis])
    assert len(groups) == 1
    assert len(groups[0]) == 4
    tml.winner_player_id = "100"
    tml.loser_player_id = "200"
    sackmann.winner_player_id = "900"
    sackmann.loser_player_id = "800"
    regrouped = cluster_matches([sackmann, tml])
    assert len(regrouped) == 1
    full = RawMatch(
        source="tml",
        source_match_id="ao",
        played_at=day,
        available_at=day + timedelta(days=7),
        tour_level="atp",
        tournament="Australian Open",
        surface="hard",
        best_of=5,
        round_name="R64",
        gender="m",
        winner_name="Alexei Popyrin",
        loser_name="Chun-Hsin Tseng",
        winner_player_id="P09Z",
        loser_player_id="T1",
        score="4-6 7-6(5) 6-7(5) 7-6(4) 6-1",
    )
    short = RawMatch(
        source="ten",
        source_match_id="ten-ao",
        played_at=day + timedelta(days=1),
        available_at=day + timedelta(days=2),
        tour_level="atp",
        tournament="Australian Open ATP",
        surface="hard",
        best_of=5,
        round_name="R64",
        gender="m",
        winner_name="Popyrin A.",
        loser_name="Tseng C. H.",
        score="2-3",
        odds_winner=1.8,
        odds_loser=2.0,
    )
    assert len(cluster_matches([full, short])) == 1


def test_provider_player_id_namespaces_do_not_merge_unrelated_players():
    from datetime import datetime, timezone

    from app.compare.canonical import RawMatch, cluster_matches

    day = datetime(2024, 6, 1, tzinfo=timezone.utc)
    source_a = RawMatch(
        source="tml", source_match_id="tml-1", played_at=day, available_at=day,
        tour_level="atp", tournament="Halle", surface="grass", best_of=3,
        round_name="F", gender="m", winner_name="Daniil Medvedev", loser_name="Jannik Sinner",
        winner_player_id="1", loser_player_id="2", score="6-4 6-3",
    )
    source_b = RawMatch(
        source="api_tennis", source_match_id="api-1", played_at=day, available_at=day,
        tour_level="atp", tournament="Halle", surface="grass", best_of=3,
        round_name="F", gender="m", winner_name="Carlos Alcaraz", loser_name="Novak Djokovic",
        winner_player_id="1", loser_player_id="2", score="6-4 6-3",
    )
    groups = cluster_matches([source_a, source_b])

    assert len(groups) == 2


def test_residual_shift_is_bounded():
    from app.compare.candidates.ensemble import logit
    from app.compare.candidates.residual import ResidualCandidate

    origin = datetime(2024, 1, 1, tzinfo=timezone.utc)
    rows = []
    for index in range(80):
        label = index % 2
        row = _row(f"m{index}", origin + timedelta(days=index), label)
        row.market_prob_a = 0.5
        row.elo_diff = 220.0 if label else -220.0
        rows.append(row)
    model = ResidualCandidate("logistic")
    model.fit(rows[:60], np.full(60, 0.5))
    predicted = model.predict(rows[60:], np.full(20, 0.5))
    assert float(np.std(predicted)) > 0.02
    shift = np.abs(logit(predicted) - logit(np.full(len(predicted), 0.5)))
    assert float(shift.max()) <= 0.35 + 1e-6


def test_markov_does_not_return_zero_or_one():
    finished = match_win(2, 0, 0, 0, 0, 0, 0.65, 0.65, "a", sets_to_win=2)
    assert 0.02 <= finished <= 0.98
    assert finished not in (0.0, 1.0)


def test_choose_link_keeps_one_player():
    from tennis_adapter.external_names import choose_link

    assert choose_link([("a", 0.92), ("b", 0.75)], []) is None
    assert choose_link([("a", 0.92)], []) == ("a", 0.92)
    assert choose_link([], [("a", 0.92)]) == ("a", 0.6)


if __name__ == "__main__":
    test_split_keeps_matches_apart()
    test_calibrator_rejects_test_matches()
    test_isotonic_skipped_on_few_matches()
    test_rating_hides_future_matches()
    test_markov_symmetry_and_match_point()
    test_same_seed_same_split()
    test_temporal_calibration_uses_later_rows()
    test_two_spellings_merge()
    test_residual_shift_is_bounded()
    test_markov_does_not_return_zero_or_one()
    test_choose_link_keeps_one_player()
    print("test_model_comparison: OK")
