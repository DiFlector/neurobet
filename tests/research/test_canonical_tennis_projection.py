from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.canonical_tennis_projection import choose_existing_match


def test_incremental_projection_merges_only_same_result_and_tournament():
    played_at = datetime(2026, 10, 20, tzinfo=timezone.utc)
    item = {
        "played_at": played_at,
        "gender": "m",
        "score": "6-4 7-6(5)",
        "tournament": "Vienna Open",
    }
    exact = SimpleNamespace(
        played_at=played_at,
        gender="m",
        score="6-4 7-6(5)",
        tournament="Erste Bank Open Vienna",
    )
    different_tournament = SimpleNamespace(
        played_at=played_at,
        gender="m",
        score="6-4 7-6(5)",
        tournament="Stockholm Open",
    )

    assert choose_existing_match([exact], item) is exact
    assert choose_existing_match([exact, exact], item) is False
    assert choose_existing_match([different_tournament], item) is False


def test_incremental_projection_refuses_score_corrections_as_duplicate_candidates():
    played_at = datetime(2026, 10, 20, tzinfo=timezone.utc)
    item = {"played_at": played_at, "gender": "w", "score": "6-3 6-2", "tournament": "Osaka"}
    prior = SimpleNamespace(
        played_at=played_at,
        gender="w",
        score="6-2 6-3",
        tournament="Osaka",
    )

    assert choose_existing_match([prior], item) is False


def test_score_key_ignores_tiebreak_annotations_on_either_side_of_set_score():
    from app.canonical_tennis_projection import _score_key

    assert _score_key("7-6(5) 6(7)-7 6-4") == _score_key("7-6 6-7 6-4")


def test_projection_fetches_candidates_in_one_batch_for_repeated_player_pairs():
    import uuid
    from app.canonical_tennis_projection import project_recent_tennis_explorer

    winner_id, loser_id = uuid.uuid4(), uuid.uuid4()
    players = [
        SimpleNamespace(id=winner_id, surname_key="winner", initial="a", gender="m",
                        source_ids={}, display_name="A Winner"),
        SimpleNamespace(id=loser_id, surname_key="loser", initial="b", gender="m",
                        source_ids={}, display_name="B Loser"),
    ]

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def scalars(self):
            return self

        def all(self):
            return self.rows

    class Session:
        def __init__(self):
            self.calls = 0
            self.added = []

        def execute(self, _statement):
            self.calls += 1
            return Result([] if "ext_tennis_canonical_player_aliases" in str(_statement)
                          else players if self.calls == 1 else [])

        def add(self, row):
            self.added.append(row)

        def flush(self):
            pass

    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    items = [{
        "source_match_id": f"te-{index}",
        "played_at": start + timedelta(days=5 * index),
        "available_at": start + timedelta(days=100),
        "gender": "m", "tour_level": "itf", "tournament": f"Event {index}",
        "winner_name": "A Winner", "loser_name": "B Loser",
        "winner_player_id": "winner-source", "loser_player_id": "loser-source",
        "score": "6-4 6-3", "best_of": 3,
    } for index in range(8)]
    session = Session()

    result = project_recent_tennis_explorer(session, items)

    assert session.calls == 7  # player resolution, source keys/pairs, and one bounded date lookup
    assert len(session.added) == 10  # eight matches and two durable source-ID aliases
    assert result == {"projected": 8, "merged": 0, "ambiguous": 0, "unlinked": 0, "unchanged": 0}


def test_tei_compound_surname_resolves_only_one_full_canonical_name_without_source_id_conflict():
    import uuid
    from app.canonical_tennis_projection import project_recent_tennis_explorer

    winner_id, loser_id = uuid.uuid4(), uuid.uuid4()
    winner = SimpleNamespace(id=winner_id, surname_key="ribera", initial="m", gender="w",
                             source_ids={"tml": "215453"}, display_name="Marina Bassols Ribera")
    loser = SimpleNamespace(id=loser_id, surname_key="cabrera", initial="l", gender="w",
                            source_ids={}, display_name="Leyla Cabrera")

    class Result:
        def __init__(self, rows): self.rows = rows
        def scalars(self): return self
        def all(self): return self.rows

    class Session:
        def __init__(self): self.calls = 0; self.added = []
        def execute(self, _statement):
            self.calls += 1
            return Result([] if "ext_tennis_canonical_player_aliases" in str(_statement)
                          else [winner, loser] if self.calls == 1 else [])
        def add(self, row): self.added.append(row)
        def flush(self): pass

    session = Session()
    result = project_recent_tennis_explorer(session, [{
        "source_match_id": "te-compound-surname", "played_at": datetime(2026, 10, 22, tzinfo=timezone.utc),
        "available_at": datetime(2026, 10, 24, tzinfo=timezone.utc), "gender": "w",
        "tour_level": "itf", "tournament": "Wrexham ITF", "winner_name": "Bassols Ribera M.",
        "loser_name": "Cabrera L.", "winner_player_id": "bassols-ribera",
        "loser_player_id": "cabrera-7a622", "score": "6-1 0-6 6-2", "best_of": 3,
    }])

    assert result == {"projected": 1, "merged": 0, "ambiguous": 0, "unlinked": 0, "unchanged": 0}
    match = next(row for row in session.added if hasattr(row, "canonical_key"))
    assert len(session.added) == 3  # two aliases and the canonical match
    assert match.winner_player_id == winner_id
    assert winner.source_ids["tennis_explorer"] == "bassols-ribera"


def test_te_compound_surname_does_not_override_existing_source_identity():
    from app.canonical_tennis_projection import _compound_surname_candidates

    conflict = SimpleNamespace(surname_key="ribera", initial="m", gender="w",
                               source_ids={"tennis_explorer": "different-provider-id"},
                               display_name="Marina Bassols Ribera")
    assert _compound_surname_candidates("bassols ribera", "m", "w", [conflict]) == []


def test_confirmed_alias_accepts_full_compound_surname_when_canonical_name_agrees():
    from app.canonical_tennis_projection import _confirmed_alias_name_matches

    canonical = SimpleNamespace(surname_key="taverna", display_name="Santiago Fa Rodriguez Taverna")
    assert _confirmed_alias_name_matches("rodriguez taverna", canonical)
    assert not _confirmed_alias_name_matches("rodriguez medina", canonical)


def test_only_reviewed_alias_can_bridge_provider_initial_convention_with_full_surname():
    from app.canonical_tennis_projection import _confirmed_alias_person_matches

    person = SimpleNamespace(
        display_name="Tomas Barrios Vera", surname_key="barrios vera", initial="t", gender="m"
    )
    reviewed = SimpleNamespace(evidence_json={"adjudication_id": "decision-1"})
    automatic = SimpleNamespace(evidence_json={"method": "unique_surname_initial_gender"})

    assert _confirmed_alias_person_matches(
        reviewed, person, surname="barrios vera", initial="m", gender="m"
    )
    assert not _confirmed_alias_person_matches(
        automatic, person, surname="barrios vera", initial="m", gender="m"
    )
    assert not _confirmed_alias_person_matches(
        reviewed, person, surname="barrios cordova", initial="m", gender="m"
    )


def test_tennis_explorer_projection_is_idempotent_by_source_match_id():
    import hashlib
    import uuid
    from app.canonical_tennis_projection import project_recent_tennis_explorer

    winner_id, loser_id = uuid.uuid4(), uuid.uuid4()
    winner = SimpleNamespace(id=winner_id, surname_key="winner", initial="a", gender="m",
                             source_ids={"tennis_explorer": "winner-source"}, display_name="A Winner")
    loser = SimpleNamespace(id=loser_id, surname_key="loser", initial="b", gender="m",
                            source_ids={"tennis_explorer": "loser-source"}, display_name="B Loser")
    source_id = "te-match-1"
    alias = f"tennis_explorer:{source_id}"
    prior = SimpleNamespace(
        canonical_key=hashlib.sha1(alias.encode()).hexdigest(), winner_player_id=winner_id,
        loser_player_id=loser_id, played_at=datetime(2026, 10, 22, tzinfo=timezone.utc),
        gender="m", score="6-4 7-6(5)", tournament="Vienna Open",
        stats={"sources": [alias]},
    )

    class Result:
        def __init__(self, rows): self.rows = rows
        def scalars(self): return self
        def all(self): return self.rows

    class Session:
        def __init__(self): self.calls = 0; self.added = []
        def execute(self, _statement):
            self.calls += 1
            if "ext_tennis_canonical_player_aliases" in str(_statement):
                return Result([])
            return Result([winner, loser] if self.calls == 1 else [prior])
        def add(self, row): self.added.append(row)
        def flush(self): pass

    session = Session()
    result = project_recent_tennis_explorer(session, [{
        "source_match_id": source_id, "played_at": prior.played_at,
        "available_at": datetime(2026, 10, 24, tzinfo=timezone.utc), "gender": "m",
        "tour_level": "atp", "tournament": "Vienna Open", "winner_name": "A Winner",
        "loser_name": "B Loser", "winner_player_id": "winner-source",
        "loser_player_id": "loser-source", "score": "6-4 7-6(5)", "best_of": 3,
    }])

    assert result == {"projected": 0, "merged": 0, "ambiguous": 0, "unlinked": 0, "unchanged": 1}
    assert session.calls == 5  # identity and pair lookups plus cross-source date candidates
    assert session.added == []


def test_tennis_explorer_corrects_existing_row_only_with_confirmed_source_aliases():
    import hashlib
    import uuid
    from app.canonical_tennis_projection import project_recent_tennis_explorer

    new_winner_id, new_loser_id = uuid.uuid4(), uuid.uuid4()
    old_winner_id, old_loser_id = uuid.uuid4(), uuid.uuid4()
    winner = SimpleNamespace(id=new_winner_id, surname_key="taverna", initial="s", gender="m",
        source_ids={"tml": "RH59"}, display_name="Santiago Fa Rodriguez Taverna")
    loser = SimpleNamespace(id=new_loser_id, surname_key="bueno", initial="g", gender="m",
        source_ids={"tml": "B0GR"}, display_name="Gonzalo Bueno")
    source_id = "3356201"
    source_key = hashlib.sha1(f"tennis_explorer:{source_id}".encode()).hexdigest()
    played = datetime(2026, 10, 22, tzinfo=timezone.utc)
    prior = SimpleNamespace(
        id=uuid.uuid4(), canonical_key=source_key, winner_player_id=old_winner_id,
        loser_player_id=old_loser_id, played_at=played, available_at=played,
        gender="m", score="6(12)-7 6-4 6-2", tournament="Cali challenger",
        duplicate_of_id=None, stats={"sources": [f"tennis_explorer:{source_id}"]},
    )
    confirmed = [
        SimpleNamespace(source_player_id="rodriguez-taverna", canonical_player_id=new_winner_id,
                        status="confirmed"),
        SimpleNamespace(source_player_id="bueno-01b7e", canonical_player_id=new_loser_id,
                        status="confirmed"),
    ]

    class Result:
        def __init__(self, rows): self.rows = rows
        def scalars(self): return self
        def all(self): return self.rows

    class Session:
        def __init__(self): self.calls = 0
        def execute(self, statement):
            self.calls += 1
            if "ext_tennis_canonical_player_aliases" in str(statement):
                return Result(confirmed)
            if self.calls in {1, 3}: return Result([winner, loser])
            if self.calls in {4, 5}: return Result([prior])
            return Result([])
        def flush(self): pass

    session = Session()
    result = project_recent_tennis_explorer(session, [{
        "source_match_id": source_id, "played_at": played, "available_at": played,
        "gender": "m", "tour_level": "challenger", "tournament": "Cali challenger",
        "winner_name": "Rodriguez Taverna S.", "loser_name": "Bueno G.",
        "winner_player_id": "rodriguez-taverna", "loser_player_id": "bueno-01b7e",
        "score": "6(12)-7 6-4 6-2",
    }])
    assert result["ambiguous"] == 0 and result["unlinked"] == 0
    assert prior.winner_player_id == new_winner_id and prior.loser_player_id == new_loser_id
    assert prior.stats["identity_corrections"][0]["method"] == "confirmed_source_alias_reprojection"


def test_tennis_abstract_projects_only_with_unique_opponent_and_normalizes_winner_score():
    import uuid
    from app.canonical_tennis_projection import project_recent_tennis_abstract

    target_id, opponent_id = uuid.uuid4(), uuid.uuid4()
    target = SimpleNamespace(id=target_id, gender="m", source_ids={"tennis_abstract": "TargetSlug"},
                             surname_key="target", initial="t", display_name="Target Player")
    opponent = SimpleNamespace(id=opponent_id, gender="m", source_ids={},
                               surname_key="opponent", initial="o", display_name="Opponent O")

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def scalars(self):
            return self

        def all(self):
            return self.rows

    class Session:
        def __init__(self):
            self.added = []
            self.calls = 0

        def get(self, _model, _identity):
            return target

        def execute(self, _statement):
            self.calls += 1
            return Result([opponent] if self.calls == 1 else [])

        def add(self, row):
            self.added.append(row)

        def flush(self):
            pass

    session = Session()
    result = project_recent_tennis_abstract(session, [{
        "source_match_id": "ta-row-1", "played_at": "2026-10-20T00:00:00+00:00",
        "available_at": "2026-10-24T00:00:00+00:00", "player_won": False,
        "opponent_name": "Opponent O", "winner_name": "Opponent O",
        "loser_name": "Player", "score": "4-6 3-6", "tournament": "Test Open",
        "round_name": "QF", "surface": "hard",
    }], target_player_id=target_id, source_player_id="TargetSlug")

    assert result == {"projected": 1, "merged": 0, "ambiguous": 0, "unlinked": 0, "unchanged": 0}
    [match] = session.added
    assert match.winner_player_id == opponent_id
    assert match.loser_player_id == target_id
    assert match.score == "6-4 6-3"
    assert match.stats["history_scope"] == "recent_fragment_not_full_career"


def test_tennis_abstract_refuses_ambiguous_opponent_identity():
    import uuid
    from app.canonical_tennis_projection import project_recent_tennis_abstract

    target_id = uuid.uuid4()
    target = SimpleNamespace(id=target_id, gender="m", source_ids={"tennis_abstract": "TargetSlug"},
                             surname_key="target", initial="t", display_name="Target Player")
    first = SimpleNamespace(id=uuid.uuid4(), gender="m", surname_key="opponent", initial="o")
    second = SimpleNamespace(id=uuid.uuid4(), gender="m", surname_key="opponent", initial="o")

    class Result:
        def scalars(self): return self
        def all(self): return [first, second]

    class Session:
        def get(self, _model, _identity): return target
        def execute(self, _statement): return Result()
        def add(self, _row): raise AssertionError("ambiguous match must not be projected")
        def flush(self): pass

    result = project_recent_tennis_abstract(Session(), [{
        "source_match_id": "ta-row-2", "played_at": "2026-10-20T00:00:00+00:00",
        "available_at": "2026-10-24T00:00:00+00:00", "player_won": True,
        "opponent_name": "Opponent O", "score": "6-4 6-3",
        "tournament": "Test Open", "round_name": "QF", "surface": "hard",
    }], target_player_id=target_id, source_player_id="TargetSlug")
    assert result["unlinked"] == 1
    assert result["projected"] == 0


def test_tennis_abstract_matcher_flags_score_or_date_drift_instead_of_duplicating():
    from app.canonical_tennis_projection import _choose_tennis_abstract_existing_match

    played_at = datetime(2026, 10, 20, tzinfo=timezone.utc)
    item = {"played_at": played_at, "gender": "m", "score": "6-4 7-6(5)",
            "tournament": "Vienna Open"}
    reversed_score = SimpleNamespace(
        played_at=played_at, gender="m", score="4-6 6-7(5)",
        tournament="Erste Bank Open Vienna",
    )
    drifted_date = SimpleNamespace(
        played_at=played_at + timedelta(days=12), gender="m", score="4-6 6-7(5)",
        tournament="Erste Bank Open Vienna",
    )

    assert _choose_tennis_abstract_existing_match([reversed_score], item) is reversed_score
    assert _choose_tennis_abstract_existing_match([drifted_date], item) is False
    assert _choose_tennis_abstract_existing_match([reversed_score, reversed_score], item) is False


def test_tennis_abstract_score_is_canonical_winner_first_after_result_validation():
    from app.canonical_tennis_projection import _validated_player_score

    assert _validated_player_score("4-6 3-6", target_won=False) == "6-4 6-3"
    assert _validated_player_score("4-6 3-6", target_won=True) == ""


def test_api_tennis_projection_links_by_provider_ids_and_projects_retrospective_result():
    import uuid
    from app.canonical_tennis_projection import project_api_tennis_results

    winner_id, loser_id = uuid.uuid4(), uuid.uuid4()
    winner = SimpleNamespace(id=winner_id, surname_key="winner", initial="a", gender="m",
                             source_ids={"api_tennis": "api-w"}, display_name="A Winner")
    loser = SimpleNamespace(id=loser_id, surname_key="loser", initial="b", gender="m",
                            source_ids={"api_tennis": "api-l"}, display_name="B Loser")

    class Result:
        def __init__(self, rows): self.rows = rows
        def scalars(self): return self
        def all(self): return self.rows

    class Session:
        def __init__(self): self.calls = 0; self.added = []
        def execute(self, _statement):
            self.calls += 1
            if "ext_tennis_canonical_player_aliases" in str(_statement):
                return Result([])
            if self.calls == 4: return Result([])
            return Result([winner, loser] if self.calls <= 2 else [])
        def add(self, row): self.added.append(row)
        def flush(self): pass

    played = datetime(2026, 10, 22, tzinfo=timezone.utc)
    available = datetime(2026, 10, 24, tzinfo=timezone.utc)
    item = {
        "source_match_id": "api-match-1", "played_at": played, "available_at": available,
        "tour_level": "atp", "tournament": "Vienna Open", "round_name": "Final",
        "gender": "m", "winner_name": "A Winner", "loser_name": "B Loser",
        "winner_player_id": "api-w", "loser_player_id": "api-l", "score": "6-4 7-6",
    }
    session = Session()
    result = project_api_tennis_results(session, [item])

    assert result == {"projected": 1, "merged": 0, "ambiguous": 0, "unlinked": 0, "unchanged": 0}
    assert session.calls == 7
    [match] = session.added
    assert match.winner_player_id == winner_id and match.loser_player_id == loser_id
    assert match.score == "6-4 7-6"
    assert match.best_of == 0
    assert match.stats == {
        "sources": ["api_tennis:api-match-1"], "raw_levels": ["atp"],
        "retrospective": True, "best_of_known": False,
    }


def test_api_tennis_projection_merges_match_into_unique_established_player_pair():
    import uuid
    from app.canonical_tennis_projection import project_api_tennis_results

    api_winner, api_loser = uuid.uuid4(), uuid.uuid4()
    canonical_winner, canonical_loser = uuid.uuid4(), uuid.uuid4()
    api_people = [
        SimpleNamespace(id=api_winner, surname_key="winner", initial="a", gender="unknown",
                        source_ids={"api_tennis": "api-w"}, display_name="A Winner"),
        SimpleNamespace(id=api_loser, surname_key="loser", initial="b", gender="unknown",
                        source_ids={"api_tennis": "api-l"}, display_name="B Loser"),
    ]
    established_people = [
        SimpleNamespace(id=canonical_winner, surname_key="winner", initial="a", gender="m",
                        source_ids={"tml": "tml-w"}, display_name="A Winner Full"),
        SimpleNamespace(id=canonical_loser, surname_key="loser", initial="b", gender="m",
                        source_ids={"sackmann_sqlite": "db-l"}, display_name="B Loser Full"),
    ]
    played = datetime(2026, 10, 22, tzinfo=timezone.utc)
    existing_match = SimpleNamespace(
        id=uuid.uuid4(), canonical_key="existing-canonical-key",
        winner_player_id=canonical_winner, loser_player_id=canonical_loser,
        played_at=played, gender="m", score="6-4 7-6(5)", tournament="Vienna Open",
        stats={"sources": ["tml:match-1"]},
    )

    class Result:
        def __init__(self, rows): self.rows = rows
        def scalars(self): return self
        def all(self): return self.rows

    class Session:
        def __init__(self): self.calls = 0; self.added = []
        def execute(self, _statement):
            self.calls += 1
            if "ext_tennis_canonical_player_aliases" in str(_statement):
                return Result([])
            if self.calls == 4: return Result([])  # no existing API-linked row to correct
            if self.calls == 1: return Result(api_people)
            if self.calls == 2: return Result(api_people + established_people)
            if self.calls == 5: return Result([])  # no prior API canonical-key row
            if self.calls == 6: return Result([existing_match])
            return Result([])  # duplicate reconciliation finds no additional row
        def add(self, row): self.added.append(row)
        def flush(self): pass

    session = Session()
    result = project_api_tennis_results(session, [{
        "source_match_id": "api-match-1", "played_at": played,
        "available_at": datetime(2026, 10, 24, tzinfo=timezone.utc),
        "tour_level": "atp", "tournament": "Vienna Open", "round_name": "Final",
        "gender": "m", "winner_name": "A Winner", "loser_name": "B Loser",
        "winner_player_id": "api-w", "loser_player_id": "api-l", "score": "6-4 7-6",
    }])

    assert result == {"projected": 0, "merged": 1, "ambiguous": 0, "unlinked": 0, "unchanged": 0}
    assert session.added == []
    assert existing_match.stats["sources"] == ["api_tennis:api-match-1", "tml:match-1"]


def test_api_projection_links_duplicate_to_established_match_and_preserves_missing_facts():
    import uuid
    from app.canonical_tennis_projection import project_api_tennis_results

    api_winner, api_loser = uuid.uuid4(), uuid.uuid4()
    tml_winner, tml_loser = uuid.uuid4(), uuid.uuid4()
    api_people = [
        SimpleNamespace(id=api_winner, surname_key="winner", initial="a", gender="m",
                        source_ids={"api_tennis": "api-w"}, display_name="A Winner"),
        SimpleNamespace(id=api_loser, surname_key="loser", initial="b", gender="m",
                        source_ids={"api_tennis": "api-l"}, display_name="B Loser"),
    ]
    played = datetime(2026, 10, 22, tzinfo=timezone.utc)
    established_match = SimpleNamespace(
        id=uuid.uuid4(), canonical_key="tml-established-match",
        winner_player_id=tml_winner, loser_player_id=tml_loser,
        played_at=played, available_at=played + timedelta(days=1),
        gender="m", winner_name="A Winner", loser_name="B Loser",
        score="6-4 6-3", tournament="Vienna Open", surface="hard",
        best_of=3, round_name="Final", winner_rank=18, loser_rank=27,
        odds_winner=None, odds_loser=None,
        serve_won_winner=0.71, serve_won_loser=0.62,
        return_won_winner=None, return_won_loser=None,
        odds_source=None, stats_source="tennis_my_life",
        duplicate_of_id=None,
        stats={"sources": ["tennis_my_life:tml-match-1"]},
    )

    class Result:
        def __init__(self, rows): self.rows = rows
        def scalars(self): return self
        def all(self): return self.rows

    class Session:
        def __init__(self): self.calls = 0; self.added = []
        def execute(self, _statement):
            self.calls += 1
            if self.calls in {1, 2}: return Result(api_people)
            if self.calls in {3, 4}: return Result([])
            return Result([established_match])
        def add(self, row): self.added.append(row)
        def flush(self): pass

    session = Session()
    result = project_api_tennis_results(session, [{
        "source_match_id": "api-match-1", "played_at": played,
        "available_at": played + timedelta(days=2),
        "tour_level": "atp", "tournament": "Vienna Open", "round_name": "Final",
        "gender": "m", "winner_name": "A Winner", "loser_name": "B Loser",
        "winner_player_id": "api-w", "loser_player_id": "api-l", "score": "6-4 6-3",
    }])

    assert result == {"projected": 1, "merged": 1, "ambiguous": 0, "unlinked": 0, "unchanged": 0}
    [api_duplicate] = session.added
    assert api_duplicate.duplicate_of_id == established_match.id
    assert established_match.stats["sources"] == [
        "api_tennis:api-match-1", "tennis_my_life:tml-match-1",
    ]
    assert established_match.stats["duplicate_rows"][0]["canonical_key"] == api_duplicate.canonical_key
    assert established_match.serve_won_winner == 0.71
    assert established_match.winner_rank == 18


def test_api_projection_relinks_existing_api_only_duplicate_to_established_canonical_match():
    import hashlib
    import uuid
    from app.canonical_tennis_projection import project_api_tennis_results

    api_winner_id, api_loser_id = uuid.uuid4(), uuid.uuid4()
    tml_winner_id, tml_loser_id = uuid.uuid4(), uuid.uuid4()
    api_winner = SimpleNamespace(id=api_winner_id, surname_key="rodriguez taverna", initial="s",
        gender="m", source_ids={"api_tennis": "361"}, display_name="S. Rodriguez Taverna")
    api_loser = SimpleNamespace(id=api_loser_id, surname_key="bueno", initial="g",
        gender="m", source_ids={"api_tennis": "9209"}, display_name="G. Bueno")
    tml_winner = SimpleNamespace(id=tml_winner_id, surname_key="taverna", initial="s",
        gender="m", source_ids={"tml": "RH59"}, display_name="Santiago Fa Rodriguez Taverna")
    tml_loser = SimpleNamespace(id=tml_loser_id, surname_key="bueno", initial="g",
        gender="m", source_ids={"tml": "B0GR"}, display_name="Gonzalo Bueno")
    played = datetime(2026, 10, 22, 16, 45, tzinfo=timezone.utc)
    old_key = hashlib.sha1(b"api_tennis:12173096").hexdigest()
    old_api_row = SimpleNamespace(
        id=uuid.uuid4(), canonical_key=old_key,
        winner_player_id=api_winner_id, loser_player_id=api_loser_id,
        played_at=played, gender="m", score="6-7 6-4 6-2", tournament="Cali",
        duplicate_of_id=None, stats={"sources": ["api_tennis:12173096"]},
    )
    established_row = SimpleNamespace(
        id=uuid.uuid4(), canonical_key="tennis_explorer:3356201",
        winner_player_id=tml_winner_id, loser_player_id=tml_loser_id,
        played_at=played, gender="m", score="6(12)-7 6-4 6-2", tournament="Cali challenger",
        duplicate_of_id=None, stats={"sources": ["tennis_explorer:3356201"]},
    )
    reviewed_alias = SimpleNamespace(
        source_player_id="361", canonical_player_id=tml_winner_id,
        status="confirmed", evidence_json={"adjudication_id": "reviewed-decision"},
    )

    class Result:
        def __init__(self, rows): self.rows = rows
        def scalars(self): return self
        def all(self): return self.rows

    class Session:
        def __init__(self): self.calls = 0
        def execute(self, _statement):
            self.calls += 1
            if "ext_tennis_canonical_player_aliases" in str(_statement):
                return Result([reviewed_alias])
            if self.calls in {1, 2, 4}: return Result([api_winner, api_loser, tml_winner, tml_loser])
            if self.calls == 5: return Result([])
            if self.calls == 6: return Result([old_api_row])
            if self.calls == 7: return Result([established_row])
            return Result([])
        def flush(self): pass

    item = {
        "source_match_id": "12173096", "played_at": played,
        "available_at": played, "tour_level": "challenger", "tournament": "Cali",
        "gender": "m", "winner_name": "S. Rodriguez Taverna", "loser_name": "G. Bueno",
        "winner_player_id": "361", "loser_player_id": "9209", "score": "6-7 6-4 6-2",
    }
    result = project_api_tennis_results(Session(), [item])

    assert result["ambiguous"] == 0 and result["unlinked"] == 0
    assert old_api_row.duplicate_of_id == established_row.id
    assert old_api_row.stats["duplicate_of"] == str(established_row.id)
    assert "api_tennis:12173096" in established_row.stats["sources"]
    assert established_row.stats["duplicate_rows"][0]["canonical_match_id"] == str(old_api_row.id)


def test_api_projection_continues_reconciliation_from_existing_duplicate_survivor():
    import hashlib
    import uuid
    from app.canonical_tennis_projection import project_api_tennis_results

    winner_id, loser_id = uuid.uuid4(), uuid.uuid4()
    winner = SimpleNamespace(id=winner_id, surname_key="winner", initial="a", gender="m",
        source_ids={"api_tennis": "api-w", "tml": "tml-w"}, display_name="A Winner")
    loser = SimpleNamespace(id=loser_id, surname_key="loser", initial="b", gender="m",
        source_ids={"api_tennis": "api-l", "tml": "tml-l"}, display_name="B Loser")
    played = datetime(2026, 10, 22, tzinfo=timezone.utc)
    source_ref = "api_tennis:legacy-match"
    survivor = SimpleNamespace(
        id=uuid.uuid4(), canonical_key="tml-survivor", duplicate_of_id=None,
        winner_player_id=winner_id, loser_player_id=loser_id,
        played_at=played, available_at=played, gender="m", score="6-4 6-3",
        tournament="Vienna Open", winner_name="A Winner", loser_name="B Loser",
        stats={"sources": ["tennis_my_life:tml-match"]},
    )
    old_key = hashlib.sha1(source_ref.encode()).hexdigest()
    existing_api_row = SimpleNamespace(
        id=uuid.uuid4(), canonical_key=old_key, duplicate_of_id=survivor.id,
        winner_player_id=winner_id, loser_player_id=loser_id,
        played_at=played, available_at=played, gender="m", score="6-4 6-3",
        tournament="Vienna Open", winner_name="A Winner", loser_name="B Loser",
        stats={"sources": [source_ref], "duplicate_of": str(survivor.id)},
    )
    flashscore_row = SimpleNamespace(
        id=uuid.uuid4(), canonical_key="flashscore-row", duplicate_of_id=None,
        winner_player_id=winner_id, loser_player_id=loser_id,
        played_at=played, available_at=played, gender="male", score="6-4 6-3",
        tournament="Vienna Open", winner_name="A Winner", loser_name="B Loser",
        stats={"sources": [source_ref, "flashscore:match"]},
    )

    class Result:
        def __init__(self, rows): self.rows = rows
        def scalars(self): return self
        def all(self): return self.rows

    class Session:
        def __init__(self): self.calls = 0
        def execute(self, statement):
            self.calls += 1
            if "ext_tennis_canonical_player_aliases" in str(statement): return Result([])
            if self.calls == 1: return Result([winner, loser])
            if self.calls == 2: return Result([winner, loser])
            if self.calls == 4: return Result([])  # no rows need identity correction
            if self.calls == 5: return Result([existing_api_row])
            if self.calls == 6: return Result([survivor, flashscore_row])
            if self.calls == 7: return Result([survivor, existing_api_row, flashscore_row])
            return Result([])  # descendants of the redundant row
        def flush(self): pass

    result = project_api_tennis_results(Session(), [{
        "source_match_id": "legacy-match", "played_at": played,
        "available_at": played, "tour_level": "atp", "tournament": "Vienna Open",
        "gender": "m", "winner_name": "A Winner", "loser_name": "B Loser",
        "winner_player_id": "api-w", "loser_player_id": "api-l", "score": "6-4 6-3",
    }])

    assert result["ambiguous"] == 0 and result["unlinked"] == 0
    assert existing_api_row.duplicate_of_id == survivor.id
    assert flashscore_row.duplicate_of_id == survivor.id
    assert "flashscore:match" in survivor.stats["sources"]


def test_api_tennis_projection_keeps_conflicts_and_ambiguous_player_ids_unlinked():
    import uuid
    from app.canonical_tennis_projection import project_api_tennis_results

    winner_id, loser_id = uuid.uuid4(), uuid.uuid4()
    winner = SimpleNamespace(id=winner_id, surname_key="winner", initial="a", gender="m",
                             source_ids={"api_tennis": "api-w"}, display_name="A Winner")
    loser = SimpleNamespace(id=loser_id, surname_key="loser", initial="b", gender="m",
                            source_ids={"api_tennis": "api-l"}, display_name="B Loser")
    existing = SimpleNamespace(
        id=uuid.uuid4(), winner_player_id=winner_id, loser_player_id=loser_id,
        played_at=datetime(2026, 10, 22, tzinfo=timezone.utc), gender="m",
        score="6-3 6-4", tournament="Vienna Open", stats={"sources": ["tml:old"]},
    )

    class Result:
        def __init__(self, rows): self.rows = rows
        def scalars(self): return self
        def all(self): return self.rows

    class Session:
        def __init__(self, exact): self.calls = 0; self.added = []; self.exact = exact
        def execute(self, _statement):
            self.calls += 1
            if "ext_tennis_canonical_player_aliases" in str(_statement):
                return Result([])
            if self.calls == 4: return Result([])  # no existing API-linked row to correct
            if self.calls == 1: return Result(self.exact)
            if self.calls == 2: return Result([winner, loser])
            if self.calls == 5: return Result([])
            return Result([existing])
        def add(self, row): self.added.append(row)
        def flush(self): pass

    item = {
        "source_match_id": "api-match-conflict", "played_at": existing.played_at,
        "available_at": datetime(2026, 10, 24, tzinfo=timezone.utc), "tour_level": "atp",
        "tournament": "Vienna Open", "gender": "m", "winner_name": "A Winner",
        "loser_name": "B Loser", "winner_player_id": "api-w", "loser_player_id": "api-l",
        "score": "6-4 6-3",
    }
    conflict_session = Session([winner, loser])
    conflict = project_api_tennis_results(conflict_session, [item])
    assert conflict["ambiguous"] == 1 and conflict_session.added == []

    ambiguous_session = Session([winner, SimpleNamespace(**vars(winner)), loser])
    ambiguous = project_api_tennis_results(ambiguous_session, [item])
    assert ambiguous["unlinked"] == 1 and ambiguous_session.added == []


def test_api_tennis_projection_enriches_legacy_set_summary_only_for_exact_result_identity():
    import hashlib
    import uuid
    from app.canonical_tennis_projection import project_api_tennis_results

    winner_id, loser_id = uuid.uuid4(), uuid.uuid4()
    winner = SimpleNamespace(id=winner_id, surname_key="winner", initial="a", gender="m",
                             source_ids={"api_tennis": "api-w"}, display_name="A Winner")
    loser = SimpleNamespace(id=loser_id, surname_key="loser", initial="b", gender="m",
                            source_ids={"api_tennis": "api-l"}, display_name="B Loser")
    legacy = SimpleNamespace(
        id=uuid.uuid4(), winner_player_id=winner_id, loser_player_id=loser_id,
        played_at=datetime(2026, 10, 22, tzinfo=timezone.utc), gender="m",
        score="0 - 2", tournament="W100 Wrexham",
        canonical_key=hashlib.sha1(b"api_tennis:api-match-enrich").hexdigest(),
        stats={"sources": ["api_tennis:api-match-enrich", "tml:old"]},
    )

    class Result:
        def __init__(self, rows): self.rows = rows
        def scalars(self): return self
        def all(self): return self.rows

    class Session:
        def __init__(self): self.calls = 0
        def execute(self, _statement):
            self.calls += 1
            if "ext_tennis_canonical_player_aliases" in str(_statement):
                return Result([])
            if self.calls == 4: return Result([])  # no existing API-linked row to correct
            if self.calls == 1: return Result([winner, loser])
            if self.calls == 2: return Result([winner, loser])
            if self.calls == 5: return Result([])
            if self.calls == 6: return Result([legacy])
            if self.calls >= 7: return Result([])
            return Result([legacy])
        def flush(self): pass

    item = {
        "source_match_id": "api-match-enrich", "played_at": legacy.played_at,
        "available_at": datetime(2026, 10, 24, tzinfo=timezone.utc), "tour_level": "wta",
        "tournament": "W100 Wrexham", "gender": "m", "winner_name": "A Winner",
        "loser_name": "B Loser", "winner_player_id": "api-w", "loser_player_id": "api-l",
        "score": "6-3 6-2",
    }

    result = project_api_tennis_results(Session(), [item])

    assert result == {"projected": 0, "merged": 1, "ambiguous": 0, "unlinked": 0, "unchanged": 0}
    assert legacy.score == "6-3 6-2"
    assert legacy.stats["sources"] == ["api_tennis:api-match-enrich", "tml:old"]
    assert legacy.stats["result_score_sources"] == ["api_tennis"]


def test_api_result_set_summary_matches_only_a_detailed_score_with_same_set_counts():
    import uuid
    from app.canonical_tennis_projection import _choose_api_tennis_match

    winner_id, loser_id = uuid.uuid4(), uuid.uuid4()
    candidate = SimpleNamespace(
        winner_player_id=winner_id, loser_player_id=loser_id,
        played_at=datetime(2026, 10, 23, tzinfo=timezone.utc), gender="m",
        score="6-3 6-4", tournament="Cali challenger",
    )
    item = {
        "played_at": candidate.played_at, "gender": "m", "tournament": "Cali",
        "score": "2 - 0",
    }
    assert _choose_api_tennis_match(
        [candidate], item, SimpleNamespace(id=winner_id), SimpleNamespace(id=loser_id)
    ) is candidate

    item["score"] = "2 - 1"
    assert _choose_api_tennis_match(
        [candidate], item, SimpleNamespace(id=winner_id), SimpleNamespace(id=loser_id)
    ) is False


def test_api_projection_corrects_only_exact_source_linked_row_with_confirmed_aliases():
    import uuid
    from app.canonical_tennis_projection import _correct_api_linked_match_identities

    old_winner, old_loser = uuid.uuid4(), uuid.uuid4()
    winner, loser = SimpleNamespace(id=uuid.uuid4(), display_name="Santiago Fa Rodriguez Taverna"), \
        SimpleNamespace(id=uuid.uuid4(), display_name="Felipe De Dios")
    row = SimpleNamespace(
        winner_name="S. Rodriguez Taverna", loser_name="F. De Dios",
        winner_player_id=old_winner, loser_player_id=old_loser,
        played_at=datetime(2026, 10, 18, tzinfo=timezone.utc), gender="male",
        score="6-1 6-1", tournament="Cali Challenger",
        stats={"sources": ["api_tennis:12172169", "flashscore:nFKsiQt2"]},
    )

    class Result:
        def scalars(self): return self
        def all(self): return [row]

    class Session:
        def execute(self, _statement): return Result()

    item = {
        "source_match_id": "12172169", "played_at": row.played_at,
        "gender": "m", "tournament": "Cali", "score": "6-1 6-1",
        "winner_name": "S. Rodriguez Taverna", "loser_name": "F. De Dios",
        "winner_player_id": "361", "loser_player_id": "38056",
    }
    _correct_api_linked_match_identities(Session(), [(item, winner, loser)])

    assert (row.winner_player_id, row.loser_player_id) == (winner.id, loser.id)
    assert (row.winner_name, row.loser_name) == (winner.display_name, loser.display_name)
    assert row.stats["identity_corrections"][0]["previous_canonical_player_ids"] == [
        str(old_winner), str(old_loser),
    ]
    assert row.stats["identity_corrections"][0]["source_match_id"] == "12172169"


def test_api_match_selection_breaks_duplicate_ties_by_exact_date_then_archive_source():
    import uuid
    from app.canonical_tennis_projection import _choose_api_tennis_match

    winner_id, loser_id = uuid.uuid4(), uuid.uuid4()
    played = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
    item = {
        "played_at": played, "gender": "m", "tournament": "Cali",
        "score": "7-6 6-7 7-6",
    }
    tml_week_row = SimpleNamespace(
        winner_player_id=winner_id, loser_player_id=loser_id,
        played_at=played - timedelta(days=1), gender="m", score="7-6 6-7 7-6",
        tournament="Cali Challenger", stats={"sources": ["tennis_my_life:week"]},
    )
    te_exact_day_row = SimpleNamespace(
        winner_player_id=winner_id, loser_player_id=loser_id,
        played_at=played, gender="m", score="7-6(6) 6(6)-7 7-6(4)",
        tournament="Cali Challenger", stats={"sources": ["tennis_explorer:exact"]},
    )
    flashscore_exact_day_row = SimpleNamespace(
        winner_player_id=winner_id, loser_player_id=loser_id,
        played_at=played, gender="m", score="7-6 6-7 7-6",
        tournament="Cali Challenger", stats={"sources": ["flashscore:exact"]},
    )
    winner, loser = SimpleNamespace(id=winner_id), SimpleNamespace(id=loser_id)

    # Exact provider match date chooses TE over an earlier week-level TML date.
    assert _choose_api_tennis_match(
        [tml_week_row, te_exact_day_row], item, winner, loser
    ) is te_exact_day_row
    # When dates tie, a uniquely established archive row beats a Flashscore row.
    assert _choose_api_tennis_match(
        [flashscore_exact_day_row, te_exact_day_row], item, winner, loser
    ) is te_exact_day_row


def test_tournament_tokens_normalize_case_before_comparison():
    from app.canonical_tennis_projection import _tournament_tokens

    assert _tournament_tokens("Cali Challenger") == {"cali"}
    assert _tournament_tokens("Vienna Open") == {"vienna"}


def test_api_tennis_projection_does_not_enrich_legacy_summary_with_conflicting_winner():
    import uuid
    from app.canonical_tennis_projection import _choose_api_tennis_match

    winner_id, loser_id = uuid.uuid4(), uuid.uuid4()
    existing = SimpleNamespace(
        winner_player_id=loser_id, loser_player_id=winner_id,
        played_at=datetime(2026, 10, 22, tzinfo=timezone.utc), gender="m",
        score="0 - 2", tournament="W100 Wrexham",
    )
    item = {"played_at": existing.played_at, "gender": "m", "tournament": "W100 Wrexham", "score": "6-3 6-2"}

    assert _choose_api_tennis_match([existing], item,
                                    SimpleNamespace(id=winner_id),
                                    SimpleNamespace(id=loser_id)) is False


def test_cross_source_duplicate_uses_exact_shared_id_across_weekly_date_drift_and_flattens_children():
    import uuid
    from app.canonical_tennis_projection import _reconcile_cross_source_duplicates

    winner_id, loser_id = uuid.uuid4(), uuid.uuid4()

    def match(match_id, day, sources, *, duplicate_of_id=None):
        return SimpleNamespace(
            id=match_id, canonical_key=f"match-{match_id}",
            played_at=datetime(2026, 10, day, tzinfo=timezone.utc),
            gender="w", score="6-2 2-6 6-3", tournament="Osaka WTA",
            winner_name="Mary Stoiana", loser_name="Moyuka Uchijima",
            winner_player_id=winner_id, loser_player_id=loser_id,
            duplicate_of_id=duplicate_of_id, stats={"sources": sources},
            surface="hard", best_of=3, round_name="R32",
            winner_rank=None, loser_rank=None, odds_winner=None, odds_loser=None,
            serve_won_winner=None, serve_won_loser=None,
            return_won_winner=None, return_won_loser=None,
            stats_source=None, odds_source=None,
        )

    api_target = match(uuid.uuid4(), 22, ["api_tennis:12172767"])
    tml_survivor = match(uuid.uuid4(), 19, [
        "tennis_my_life:tml-wta-2026-405-23-269265", "api_tennis:12172767",
    ])
    api_child = match(uuid.uuid4(), 22, ["api_tennis:12172767"], duplicate_of_id=api_target.id)

    class Result:
        def __init__(self, rows): self.rows = rows
        def scalars(self): return self
        def all(self): return self.rows

    class Session:
        def __init__(self): self.calls = 0
        def execute(self, _statement):
            self.calls += 1
            return Result([tml_survivor] if self.calls == 1 else [api_child])

    item = {
        "gender": "w", "score": "6-2 2-6 6-3", "tournament": "Osaka WTA",
        "winner_name": "Mary Stoiana", "loser_name": "Moyuka Uchijima",
    }
    linked = _reconcile_cross_source_duplicates(
        Session(), "api_tennis", {"12172767": (api_target, item)}
    )

    assert linked == 1
    assert api_target.duplicate_of_id == tml_survivor.id
    assert api_child.duplicate_of_id == tml_survivor.id
    assert api_child.stats["duplicate_of"] == str(tml_survivor.id)
    assert "api_tennis:12172767" in tml_survivor.stats["sources"]
    assert tml_survivor.stats["source_date_conflicts"] == [{
        "shared_source_ids": ["api_tennis:12172767"],
        "survivor_date": "2026-10-19", "duplicate_date": "2026-10-22",
        "delta_days": 3,
    }]


def test_cross_source_duplicate_does_not_use_shared_id_when_canonical_sides_disagree():
    from app.canonical_tennis_projection import _same_cross_source_match

    played = datetime(2026, 10, 22, tzinfo=timezone.utc)
    target = SimpleNamespace(
        id="target", duplicate_of_id=None, played_at=played, gender="w",
        score="6-2 2-6 6-3", tournament="Osaka WTA",
        winner_name="Mary Stoiana", loser_name="Moyuka Uchijima",
        winner_player_id="winner-a", loser_player_id="loser-a",
        stats={"sources": ["api_tennis:12172767"]},
    )
    conflicting = SimpleNamespace(
        id="other", duplicate_of_id=None, played_at=played - timedelta(days=3),
        gender="w", score="6-2 2-6 6-3", tournament="Osaka WTA",
        winner_name="Mary Stoiana", loser_name="Moyuka Uchijima",
        winner_player_id="winner-b", loser_player_id="loser-b",
        stats={"sources": ["api_tennis:12172767"]},
    )
    item = {
        "gender": "w", "score": target.score, "tournament": target.tournament,
        "winner_name": target.winner_name, "loser_name": target.loser_name,
    }

    assert not _same_cross_source_match(item, target, conflicting)


def test_cross_source_duplicate_accepts_compound_name_format_when_canonical_ids_agree():
    from app.canonical_tennis_projection import _same_cross_source_match

    played = datetime(2026, 10, 18, tzinfo=timezone.utc)
    target = SimpleNamespace(
        id="target", duplicate_of_id=None, played_at=played, gender="m",
        score="6-1 6-1", tournament="Cali Challenger",
        winner_name="S. Rodriguez Taverna", loser_name="F. De Dios",
        winner_player_id="canonical-rodriguez", loser_player_id="canonical-de-dios",
        stats={"sources": ["api_tennis:12172169", "tennis_explorer:3353661"]},
    )
    candidate = SimpleNamespace(
        id="candidate", duplicate_of_id=None, played_at=played, gender="male",
        score="6-1 6-1", tournament="Cali",
        winner_name="Santiago Fa Rodriguez Taverna", loser_name="Felipe De Dios",
        winner_player_id="canonical-rodriguez", loser_player_id="canonical-de-dios",
        stats={"sources": ["api_tennis:12172169", "flashscore:match"]},
    )
    item = {
        "gender": "m", "score": target.score, "tournament": "Cali",
        "winner_name": target.winner_name, "loser_name": target.loser_name,
    }

    assert _same_cross_source_match(item, target, candidate)
