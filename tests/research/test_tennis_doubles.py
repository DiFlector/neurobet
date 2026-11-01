import pytest

from app.tennis_doubles import (
    canonical_pair, classify_discipline, classify_doubles_discipline,
    event_discipline_source_facts, parse_doubles_side,
    symmetric_pair_features, validate_scoring_rules, confirm_roster_player,
    confirmed_provider_roster,
)


def test_team_display_is_only_split_into_search_candidates():
    assert parse_doubles_side("Surname, A. / Surname, B.") == ("Surname, A.", "Surname, B.")
    assert parse_doubles_side("Same / Same") is None
    assert parse_doubles_side("One / Two / Three") is None
    assert parse_doubles_side("Singles") is None


def test_discipline_requires_explicit_doubles_source_context():
    assert classify_discipline("WTA Doubles") == "womens_doubles"
    assert classify_discipline("ATP doubles") == "mens_doubles"
    assert classify_discipline("Mixed Doubles") == "mixed_doubles"
    assert classify_discipline("Jane / John", "unknown event") == "unknown"
    assert classify_discipline("Doubles") == "unknown_doubles"


def test_discipline_facts_use_league_and_official_metadata_not_tournament_attribute():
    from types import SimpleNamespace
    event = SimpleNamespace(metadata_json={"fonbet_official_result": {"gender": "women"}})
    assert classify_discipline(*event_discipline_source_facts(event, "WTA Doubles")) == "womens_doubles"
    event.metadata_json = {}
    assert classify_discipline(*event_discipline_source_facts(event, None)) == "unknown"
    assert classify_doubles_discipline(*event_discipline_source_facts(event, None)) == "unknown_doubles"


def test_pair_identity_and_features_ignore_partner_order():
    assert canonical_pair(("b", "a")) == canonical_pair(("a", "b")) == ("a", "b")
    assert symmetric_pair_features([1200, 1400]) == symmetric_pair_features([1400, 1200])
    assert symmetric_pair_features([None, 1400]) == {
        "mean": 1400.0, "spread": None, "observed_count": 1,
    }


def test_scoring_rules_require_explicit_versioned_fields():
    rules = {"version": "provider:fixture-v1", "sets_to_win": 2, "advantage": False,
             "set_tiebreak": {"target": 7}, "match_tiebreak": {"target": 10}}
    assert validate_scoring_rules(rules) == rules
    with pytest.raises(ValueError):
        validate_scoring_rules({"sets_to_win": 2})


def test_identity_confirmation_creates_a_new_four_player_roster_state():
    original = {"A": [{"participant_id": "p1", "identity_confirmed": False},
                       {"participant_id": "p2", "identity_confirmed": False}],
                "B": [{"participant_id": "p3", "identity_confirmed": False},
                       {"participant_id": "p4", "identity_confirmed": False}]}
    first, count, matched = confirm_roster_player(original, "p1", "c1")
    assert matched and count == 1
    assert original["A"][0]["identity_confirmed"] is False
    for participant, canonical in (("p2", "c2"), ("p3", "c3"), ("p4", "c4")):
        first, count, matched = confirm_roster_player(first, participant, canonical)
        assert matched
    assert count == 4
    assert not confirm_roster_player(first, "unknown", "c5")[2]


def test_provider_roster_requires_four_distinct_confirmed_ids_and_keeps_sides():
    roster = {side: [{"participant_id": f"{side}{i}", "canonical_player_id": f"c{side}{i}",
                      "identity_confirmed": True} for i in (1, 2)] for side in ("A", "B")}
    ids = {"A1": "fs1", "A2": "fs2", "B1": "fs3", "B2": "fs4"}
    assert confirmed_provider_roster(roster, ids.get) == {
        "A": ("fs1", "fs2"), "B": ("fs3", "fs4")}
    assert confirmed_provider_roster(roster, lambda _: "same") is None
    roster["B"][1]["identity_confirmed"] = False
    assert confirmed_provider_roster(roster, ids.get) is None
