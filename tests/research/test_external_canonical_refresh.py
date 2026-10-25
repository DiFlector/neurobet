import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from build_canonical_tennis import (
    identity_of,
    is_singles_match,
    preserve_existing_match_ids,
    preserve_existing_player_ids,
)
from link_tennis_players import canonical_match_dates_query


def _row(canonical_key, sources):
    return (str(uuid.uuid4()), canonical_key, *([None] * 23), {"sources": sources})


def test_rebuild_keeps_existing_match_identity_when_new_source_joins():
    existing_id = str(uuid.uuid4())
    existing_rows = [(existing_id, "old-key", ["sackmann_sqlite:123"])]
    rebuilt = [_row("new-digest", ["sackmann_sqlite:123", "tml:2026-1-2"])]

    result = preserve_existing_match_ids(rebuilt, existing_rows)

    assert result[0][0] == existing_id
    assert result[0][1] == "old-key"
    assert result[0][25]["sources"] == ["sackmann_sqlite:123", "tml:2026-1-2"]


def test_rebuild_does_not_choose_between_ambiguous_old_matches():
    first_id, second_id = str(uuid.uuid4()), str(uuid.uuid4())
    existing_rows = [
        (first_id, "first", ["sackmann_sqlite:123"]),
        (second_id, "second", ["ten:456"]),
    ]
    rebuilt = [_row("new-digest", ["sackmann_sqlite:123", "ten:456"])]

    result = preserve_existing_match_ids(rebuilt, existing_rows)

    assert result[0][0] != first_id
    assert result[0][0] != second_id
    assert result[0][1] == "new-digest"


def test_split_rebuild_does_not_overwrite_one_old_match_twice():
    existing_id = str(uuid.uuid4())
    existing_rows = [(existing_id, "old-key", ["sackmann_sqlite:123"])]
    rebuilt = [
        _row("left-digest", ["sackmann_sqlite:123"]),
        _row("right-digest", ["sackmann_sqlite:123"]),
    ]

    result = preserve_existing_match_ids(rebuilt, existing_rows)

    assert sum(row[0] == existing_id for row in result) == 1
    assert len({row[1] for row in result}) == 2


def test_rebuild_ignores_aliases_on_retained_duplicate_rows():
    survivor_id, duplicate_id = str(uuid.uuid4()), str(uuid.uuid4())
    existing_rows = [
        # The duplicate can appear first in a database scan and still must not
        # steal the alias already merged onto the survivor.
        (duplicate_id, "old-duplicate-key", ["api_tennis:api-1"], survivor_id),
        (survivor_id, "old-survivor-key", ["tml:tml-1", "api_tennis:api-1"], None),
    ]
    rebuilt = [_row("new-group-key", ["tml:tml-1", "api_tennis:api-1"])]

    result = preserve_existing_match_ids(rebuilt, existing_rows)

    assert result[0][0] == survivor_id
    assert result[0][1] == "old-survivor-key"


def test_rebuild_does_not_reuse_an_alias_owned_only_by_a_duplicate_row():
    survivor_id, duplicate_id = str(uuid.uuid4()), str(uuid.uuid4())
    generated = str(uuid.uuid4())
    rebuilt = [(generated, "new-key", *([None] * 23), {"sources": ["api_tennis:api-2"]})]

    result = preserve_existing_match_ids(
        rebuilt,
        [(duplicate_id, "old-duplicate-key", ["api_tennis:api-2"], survivor_id)],
    )

    assert result[0][0] == generated
    assert result[0][0] != duplicate_id


def test_player_activity_windows_exclude_retained_duplicate_matches():
    query = canonical_match_dates_query().lower()

    assert query.count("duplicate_of_id is null") == 2


def test_new_source_alias_preserves_unique_existing_player_id():
    existing_id = str(uuid.uuid4())
    players = {"person:one": {"id": str(uuid.uuid4()), "source_ids": {"tml": "p-1", "ten": "p-9"}}}

    result = preserve_existing_player_ids(players, [(existing_id, {"tml": "p-1"})])

    assert result["person:one"]["id"] == existing_id


def test_source_gender_aliases_create_the_same_player_identity():
    assert identity_of("Alexander Bublik", "BK92", "tml", "m") == identity_of("Alexander Bublik", "302417", "api_tennis", "male")


def test_tennis_explorer_player_ids_are_namespaced_stable_aliases():
    assert identity_of("Daniil Medvedev", "medvedev-daniil-51347", "tennis_explorer", "m") == identity_of(
        "Medvedev D.", "MEDVEDEV-DAN-1", "tennis_explorer", "male"
    )


def test_tennis_explorer_result_can_join_existing_match_by_names_date_and_score():
    from datetime import datetime, timezone
    from app.compare.canonical import RawMatch, cluster_matches

    day = datetime(2026, 10, 20, tzinfo=timezone.utc)
    tml = RawMatch(
        source="tml", source_match_id="tml-1", played_at=day, available_at=day,
        tour_level="atp", tournament="Vienna", surface="hard", best_of=3,
        round_name="R32", gender="m", winner_name="Daniil Medvedev",
        loser_name="Stefanos Tsitsipas", winner_player_id="p1", loser_player_id="p2",
        score="6-4 6-3",
    )
    explorer = RawMatch(
        source="tennis_explorer", source_match_id="te-1", played_at=day, available_at=day,
        tour_level="atp", tournament="Vienna", surface="unknown", best_of=3,
        round_name="", gender="m", winner_name="Medvedev D.", loser_name="Tsitsipas S.",
        winner_player_id="medvedev-daniil", loser_player_id="tsitsipas-stefanos",
        score="6-4 6-3",
    )

    groups = cluster_matches([tml, explorer])

    assert len(groups) == 1
    assert {item.source for item in groups[0]} == {"tml", "tennis_explorer"}


def test_incremental_te_projection_requires_unambiguous_match_evidence():
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from app.canonical_tennis_projection import choose_existing_match

    item = {
        "played_at": datetime(2026, 10, 20, tzinfo=timezone.utc),
        "gender": "m", "score": "6-4 7-6(5)", "tournament": "Vienna Open",
    }
    matching = SimpleNamespace(
        played_at=item["played_at"], gender="m", score="6-4 7-6(5)", tournament="Erste Bank Open Vienna",
    )
    conflicting = SimpleNamespace(
        played_at=item["played_at"], gender="m", score="6-3 6-4", tournament="Vienna Open",
    )

    assert choose_existing_match([matching], item) is matching
    assert choose_existing_match([conflicting], item) is False
    assert choose_existing_match([matching, matching], item) is False


def test_api_alias_does_not_replace_a_stable_tml_player_id_after_name_merge():
    stable_tml_id, earlier_api_id = str(uuid.uuid4()), str(uuid.uuid4())
    players = {"person:one": {"id": str(uuid.uuid4()), "source_ids": {"tml": "p-1", "api_tennis": "p-2"}}}

    result = preserve_existing_player_ids(players, [
        (stable_tml_id, {"tml": "p-1"}),
        (earlier_api_id, {"api_tennis": "p-2"}),
    ])

    assert result["person:one"]["id"] == stable_tml_id


def test_ambiguous_existing_player_alias_does_not_merge_identities():
    first_id, second_id = str(uuid.uuid4()), str(uuid.uuid4())
    generated_id = str(uuid.uuid4())
    players = {"person:one": {"id": generated_id, "source_ids": {"tml": "p-1"}}}

    result = preserve_existing_player_ids(players, [
        (first_id, {"tml": "p-1"}), (second_id, {"tml": "p-1"}),
    ])

    assert result["person:one"]["id"] == generated_id


def test_duplicate_current_source_identity_is_not_assigned_by_iteration_order():
    existing_id = str(uuid.uuid4())
    players = {
        "person:one": {"id": str(uuid.uuid4()), "source_ids": {"tml": "p-1"}},
        "person:two": {"id": str(uuid.uuid4()), "source_ids": {"tml": "p-1"}},
    }

    result = preserve_existing_player_ids(players, [(existing_id, {"tml": "p-1"})])

    assert all(player["id"] != existing_id for player in result.values())


def test_api_tennis_doubles_remain_out_of_singles_projection():
    assert is_singles_match("api_tennis", "Daniil Medvedev", "Jannik Sinner", {"event_type": "ATP Singles"})
    assert not is_singles_match("api_tennis", "Player A / Player B", "Player C / Player D", {"event_type": "Doubles"})
    assert not is_singles_match("api_tennis", "Player A", "Player B", {"event_type": "WTA Doubles"})
    assert not is_singles_match("tml", "Player A / Player B", "Player C / Player D", {})
