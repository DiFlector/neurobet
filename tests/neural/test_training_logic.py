"""Training rules: labels, missing prices, and the model that selects the decision row."""

import sys
from datetime import datetime, timedelta, timezone

import numpy as np

for path in (
    "/srv/neurobet/packages/contracts/src",
    "/srv/neurobet/packages/db/src",
    "/srv/neurobet/packages/features/src",
    "/srv/neurobet/services/neural",
):
    if path not in sys.path:
        sys.path.insert(0, path)

from app.commit import is_commit, side_odds, side_odds_from_row
from app.dataset import (
    MatchSnapshots,
    _context_as_of,
    choose_canonical_events,
    chronological_folds,
    collapse_repeated_states,
    decision_scores,
    line_is_trainable,
    match_training_exclusion,
    match_winner_label,
    thin_match,
)
from features.football import FootballFeatureBuilder
from features.tennis import TennisFeatureBuilder


class _Decision:
    def __init__(self, created_at, raw_json):
        self.created_at = created_at
        self.raw_json = raw_json


class _Selector:
    """Returns P(side A) from the first column. This is the model the caller passes in."""

    classes_ = np.array([0, 1])

    def predict_proba(self, rows):
        probs = []
        for row in rows:
            prob_a = 0.80 if float(row[0]) >= 2.0 else 0.40
            probs.append((1.0 - prob_a, prob_a))
        return np.asarray(probs, dtype=float)


def test_draw_is_unlabeled_and_unknown_winner_uses_the_score():
    assert match_winner_label("draw", "A", "B", {"score_a": 1, "score_b": 1}, "football") is None
    assert match_winner_label("player_a", "A", "B", {}, "tennis") == 1
    assert match_winner_label("player_b", "A", "B", {}, "tennis") == 0
    assert match_winner_label("A", "A", "B", {}, "tennis") == 1
    assert match_winner_label("B", "A", "B", {}, "tennis") == 0
    assert match_winner_label("mystery", "A", "B", {"sets_a": 2, "sets_b": 0}, "tennis") == 1
    assert match_winner_label("mystery", "A", "B", {"sets_a": 1, "sets_b": 1}, "tennis") is None
    assert match_winner_label(None, "A", "B", {"score_a": 0, "score_b": 2}, "hockey") == 0
    assert match_winner_label(None, "A", "B", {"score_a": 1, "score_b": 1}, "football") is None


def test_missing_quotes_cannot_commit():
    now = datetime.now(timezone.utc)
    tennis = TennisFeatureBuilder().build_features("e", now, [], []).features
    football = FootballFeatureBuilder().build_features("e", now, [], []).features
    assert tennis["current_odds_player_a"] == 0.0
    assert tennis["current_odds_player_b"] == 0.0
    assert tennis["is_missing_odds"] == 1.0
    assert football["current_odds_team_a"] == 0.0
    assert football["current_odds_team_b"] == 0.0
    assert football["current_odds_draw"] == 0.0
    assert side_odds(tennis) == (0.0, 0.0)
    assert is_commit(0.9, 0.1, *side_odds(tennis)) is False

    names = ["current_odds_team_a", "current_odds_team_b", "is_missing_odds"]
    assert side_odds_from_row([2.10, 3.50, 1.0], names) == (0.0, 0.0)


def test_context_after_the_snapshot_is_ignored():
    cutoff = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    decisions = [
        _Decision(cutoff - timedelta(minutes=5), {"context_side_a": 0.2, "injury_risk_score": 0.1, "fatigue_risk_score": 0.0, "news_risk_score": 0.0}),
        _Decision(cutoff + timedelta(minutes=5), {"context_side_a": 0.9, "injury_risk_score": 0.8, "fatigue_risk_score": 0.7, "news_risk_score": 0.6}),
    ]
    scores = _context_as_of(decisions, cutoff)
    assert scores["context_side_a"] == 0.2
    assert scores["injury_risk_score"] == 0.1


def test_walk_forward_keeps_a_match_on_one_side():
    mode, folds = chronological_folds(20, n_splits=4)
    assert mode == "walk_forward"
    assert len(folds) == 4
    for train_index, test_index in folds:
        assert set(train_index).isdisjoint(test_index)
        assert max(train_index) < min(test_index)

    mode, folds = chronological_folds(6, n_splits=4)
    assert mode == "holdout"
    train_index, test_index = folds[0]
    assert set(train_index).isdisjoint(test_index)
    assert max(train_index) < min(test_index)


def test_long_match_keeps_its_first_and_last_snapshot():
    rows = [[float(index), 2.0, 0.0] for index in range(40)]
    moments = [datetime(2026, 6, 1, tzinfo=timezone.utc) + timedelta(minutes=index) for index in range(40)]
    match = MatchSnapshots("m", moments[0], 1, rows, moments)
    thinned = thin_match(match, limit=12)
    assert len(thinned.rows) == 12
    assert thinned.rows[0][0] == 0.0
    assert thinned.rows[-1][0] == 39.0


class _Event:
    def __init__(self, event_id, source_event_id, odds_count_hint=0, last_seen_at=None):
        self.id = event_id
        self.source_event_id = source_event_id
        self.last_seen_at = last_seen_at


class _State:
    def __init__(self, observed_at, score):
        self.observed_at = observed_at
        self.score = score
        self.current_period = 1
        self.score_detail = {}
        self.sport_state = {}


class _Quote:
    def __init__(self, observed_at, outcome, odds, market_type="match_winner", line_value=None):
        self.observed_at = observed_at
        self.outcome = outcome
        self.odds = odds
        self.market_type = market_type
        self.line_value = line_value


def test_duplicate_source_keeps_the_richer_line():
    earlier = datetime(2026, 6, 1, tzinfo=timezone.utc)
    later = earlier + timedelta(hours=2)
    thin = _Event("a", "100", last_seen_at=later)
    rich = _Event("b", "100", last_seen_at=earlier)
    other = _Event("c", "200", last_seen_at=earlier)
    kept, dropped = choose_canonical_events([thin, rich, other], {"a": 1, "b": 8, "c": 3})
    assert dropped == 1
    assert [event.id for event in kept] == ["b", "c"]


def test_conflicting_labels_and_retirements_stay_out():
    agreed = match_training_exclusion(
        "tennis",
        metadata={"winner": "player_a"},
        score={"sets_a": 2, "sets_b": 0},
        participant_a_name="A",
        participant_b_name="B",
    )
    conflict = match_training_exclusion(
        "tennis",
        metadata={"winner": "player_a", "fonbet_official_result": {"winner": "player_b", "status": "FINISHED"}},
        score={"sets_a": 0, "sets_b": 2},
        participant_a_name="A",
        participant_b_name="B",
    )
    retired = match_training_exclusion(
        "tennis",
        metadata={"fonbet_official_result": {"winner": "player_a", "status": "RETIRED", "is_retired": True}},
        score={"sets_a": 1, "sets_b": 0, "is_retired": True},
    )
    assert agreed is None
    assert conflict == "label_conflict"
    assert retired == "retired_or_walkover"


def test_extreme_prices_and_repeated_states_drop():
    assert line_is_trainable(1.5, 2.6) is True
    assert line_is_trainable(120.0, 1.05) is False
    assert line_is_trainable(1.01, 1.01) is False
    moment = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    states = [
        _State(moment, "1:0 (6-4)"),
        _State(moment + timedelta(seconds=5), "1:0 (6-4)"),
        _State(moment + timedelta(seconds=10), "1:1 (6-4 4-6)"),
    ]
    quotes = [
        _Quote(moment, "player_a", 1.8),
        _Quote(moment, "player_b", 2.0),
    ]
    kept, dropped = collapse_repeated_states(states, quotes)
    assert dropped == 1
    assert len(kept) == 2
    assert kept[-1].score == "1:1 (6-4 4-6)"


def test_epoch_holdout_split_keeps_matches_whole():
    from app.epoch_fit import split_holdout

    matches = [
        MatchSnapshots(f"m{index}", datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=index), index % 2, [[1.0]], [datetime(2026, 1, 1, tzinfo=timezone.utc)])
        for index in range(12)
    ]
    trainable, holdout = split_holdout(matches, 5)
    assert [match.event_id for match in holdout] == ["m7", "m8", "m9", "m10", "m11"]
    assert set(match.event_id for match in trainable).isdisjoint(match.event_id for match in holdout)


def test_decision_row_is_chosen_by_the_given_model():
    names = ["current_odds_player_a", "current_odds_player_b", "is_missing_odds"]
    match = MatchSnapshots(
        event_id="m1",
        started_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        label=1,
        rows=[
            [1.4, 2.8, 0.0],
            [2.2, 1.7, 0.0],
            [2.4, 1.6, 0.0],
        ],
        observed_at=[
            datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
            datetime(2026, 6, 1, 12, 1, tzinfo=timezone.utc),
            datetime(2026, 6, 1, 12, 2, tzinfo=timezone.utc),
        ],
    )
    labels, probs = decision_scores(_Selector(), [match], names)
    assert labels.tolist() == [1]
    assert probs.tolist() == [0.8]


if __name__ == "__main__":
    test_draw_is_unlabeled_and_unknown_winner_uses_the_score()
    test_missing_quotes_cannot_commit()
    test_context_after_the_snapshot_is_ignored()
    test_walk_forward_keeps_a_match_on_one_side()
    test_long_match_keeps_its_first_and_last_snapshot()
    test_duplicate_source_keeps_the_richer_line()
    test_conflicting_labels_and_retirements_stay_out()
    test_extreme_prices_and_repeated_states_drop()
    test_epoch_holdout_split_keeps_matches_whole()
    test_decision_row_is_chosen_by_the_given_model()
    print("test_training_logic: OK")
