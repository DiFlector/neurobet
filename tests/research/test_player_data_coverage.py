from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

from app.player_data_coverage import (
    cache_eligible_history_sources, canonical_source_name, coverage_is_fresh, enabled_sources_for_capability,
    partial_coverage_is_usable, partial_history_is_fresh,
    profile_needs_history_refresh,
)
from app.player_profiles import (
    _canonical_player_surname_keys,
    _canonical_tml_side_is_winner,
    _api_tennis_serve_counts,
    _consistent_serve_counts,
    _is_singles_archive_player,
    _profile_has_canonical_source_id,
    _tml_serve_counts,
    has_unmaterialized_canonical_results,
)


def test_history_coverage_is_fresh_until_seven_day_boundary():
    now = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)

    assert coverage_is_fresh(now - timedelta(days=7), as_of=now,
                             status="available", complete=True)
    assert not coverage_is_fresh(now - timedelta(days=7, seconds=1), as_of=now,
                                 status="available", complete=True)


def test_source_capability_gates_are_independent_and_default_to_current_behavior(monkeypatch):
    flag = "TENNIS_SOURCE_TENNIS_EXPLORER_MATCH_HISTORY_ENABLED"
    monkeypatch.delenv(flag, raising=False)
    assert "tennis_explorer" in enabled_sources_for_capability("match_history")

    monkeypatch.setenv(flag, "false")
    history_sources = enabled_sources_for_capability("match_history")
    assert "tennis_explorer" not in history_sources
    assert "tennis_my_life" in history_sources
    assert "tennis_my_life" in enabled_sources_for_capability("serve_statistics")
    assert "tennis_explorer" not in enabled_sources_for_capability("serve_statistics")
    assert canonical_source_name("tml") == "tennis_my_life"
    assert "tennis_my_life" == canonical_source_name(" TML ")

    monkeypatch.setenv(flag, "yes")
    assert "tennis_explorer" in enabled_sources_for_capability("match_history")


def test_disabled_history_source_cannot_satisfy_cache_refresh_gate(monkeypatch):
    monkeypatch.setenv("TENNIS_SOURCE_TENNIS_EXPLORER_MATCH_HISTORY_ENABLED", "false")
    complete_sources, partial_sources = cache_eligible_history_sources()
    assert "tennis_explorer" not in complete_sources
    assert "tennis_explorer" not in partial_sources
    assert "tennis_my_life" in complete_sources
    assert "api_tennis" in partial_sources


def test_partial_failed_and_future_coverage_are_not_usable():
    now = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)

    assert not coverage_is_fresh(now, as_of=now, status="partial", complete=False)
    assert not coverage_is_fresh(now, as_of=now, status="error", complete=True)
    assert not coverage_is_fresh(now + timedelta(seconds=1), as_of=now,
                                 status="available", complete=True)


def test_fresh_tennis_explorer_partial_history_is_usable_without_claiming_completeness():
    now = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
    assert partial_history_is_fresh(now - timedelta(days=7), as_of=now,
                                    status="partial", complete=False, source="tennis_explorer")
    assert not partial_history_is_fresh(now - timedelta(days=7, seconds=1), as_of=now,
                                        status="partial", complete=False, source="tennis_explorer")
    assert not partial_history_is_fresh(now, as_of=now,
                                        status="partial", complete=True, source="tennis_explorer")
    assert not partial_history_is_fresh(now, as_of=now,
                                        status="partial", complete=False, source="flashscore")
    assert not partial_history_is_fresh(now + timedelta(seconds=1), as_of=now,
                                        status="partial", complete=False, source="tennis_explorer")


def test_recent_api_tennis_archive_history_is_partial_only():
    now = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
    assert partial_history_is_fresh(now, as_of=now, status="partial", complete=False,
                                    source="api_tennis")
    assert not partial_history_is_fresh(now, as_of=now, status="available", complete=True,
                                        source="api_tennis")
    row = SimpleNamespace(source="api_tennis", status="partial", complete=False,
                          checked_at=now, evidence_json={"usable_for_metrics": True})
    assert partial_coverage_is_usable(row, as_of=now)
    row.evidence_json = {"usable_for_metrics": False}
    assert not partial_coverage_is_usable(row, as_of=now)


def test_projected_tennis_abstract_history_can_be_partial_but_not_complete():
    now = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
    assert partial_history_is_fresh(now, as_of=now, status="partial", complete=False,
                                    source="tennis_abstract")
    assert not partial_history_is_fresh(now, as_of=now, status="available", complete=True,
                                        source="tennis_abstract")
    row = SimpleNamespace(source="tennis_abstract", status="partial", complete=False,
                          checked_at=now, evidence_json={"usable_for_metrics": True})
    assert partial_coverage_is_usable(row, as_of=now)


def test_partial_coverage_requires_explicit_metric_approval():
    now = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
    row = SimpleNamespace(
        checked_at=now, status="partial", complete=False, source="tennis_explorer",
        evidence_json={"usable_for_metrics": True},
    )
    assert partial_coverage_is_usable(row, as_of=now)
    row.evidence_json = {}
    assert not partial_coverage_is_usable(row, as_of=now)


def test_fresh_partial_history_prevents_per_event_flashscore_refresh():
    now = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
    for source in ("api_tennis", "tennis_explorer", "tennis_abstract"):
        partial = SimpleNamespace(source=source, status="partial", complete=False,
                                  checked_at=now, evidence_json={"usable_for_metrics": True})
        assert not profile_needs_history_refresh(
            metrics_version=1, metrics_available_at=now, as_of=now,
            complete_coverage=None, partial_coverage=partial,
            flashscore_last_success_at=None, identity_confirmation_required=False,
            attempt_recent=False,
        )
    assert profile_needs_history_refresh(
        metrics_version=1, metrics_available_at=now, as_of=now,
        complete_coverage=None, partial_coverage=None,
        flashscore_last_success_at=None, identity_confirmation_required=False,
        attempt_recent=False,
    )


def test_disabled_flashscore_history_does_not_count_as_fresh_coverage(monkeypatch):
    now = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
    monkeypatch.setenv("TENNIS_SOURCE_FLASHSCORE_MATCH_HISTORY_ENABLED", "false")
    assert profile_needs_history_refresh(
        metrics_version=1, metrics_available_at=now, as_of=now,
        complete_coverage=None, partial_coverage=None,
        flashscore_last_success_at=now, identity_confirmation_required=False,
        attempt_recent=False,
    )

    monkeypatch.setenv("TENNIS_SOURCE_FLASHSCORE_MATCH_HISTORY_ENABLED", "true")
    assert not profile_needs_history_refresh(
        metrics_version=1, metrics_available_at=now, as_of=now,
        complete_coverage=None, partial_coverage=None,
        flashscore_last_success_at=now, identity_confirmation_required=False,
        attempt_recent=False,
    )


def test_newer_unusable_partial_snapshot_does_not_hide_older_usable_history():
    from app.player_data_coverage import fresh_partial_history_coverage

    now = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
    usable = SimpleNamespace(
        source="tennis_explorer", status="partial", complete=False,
        checked_at=now - timedelta(days=3),
        evidence_json={"usable_for_metrics": True},
    )
    newer_unusable = SimpleNamespace(
        source="api_tennis", status="partial", complete=False,
        checked_at=now - timedelta(hours=2),
        evidence_json={"usable_for_metrics": False},
    )

    class Result:
        def scalars(self):
            return self

        def all(self):
            return [newer_unusable, usable]

    class Session:
        def execute(self, _statement):
            return Result()

    assert fresh_partial_history_coverage(Session(), uuid.uuid4(), as_of=now) is usable


def test_api_tennis_coverage_requires_recent_imported_source_row_and_metrics_materialization():
    from app.player_profiles import _record_api_tennis_partial_coverage

    now = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
    canonical_id = uuid.uuid4()
    loser_id = uuid.uuid4()
    participant = SimpleNamespace(id=uuid.uuid4())
    profile = SimpleNamespace(metrics_version=1, history_match_count=17,
                              metrics_available_at=now)
    person = SimpleNamespace(id=canonical_id, source_ids={})
    canonical_match = (
        "canonical-match-1", now - timedelta(days=2),
        {"sources": ["api_tennis:source-match-1"]},
        canonical_id, loser_id, "Player A", "Player B",
    )
    source_row = SimpleNamespace(
        source_match_id="source-match-1", available_at=now - timedelta(hours=1),
        serve_won_winner=0.7, serve_won_loser=None,
        winner_player_id="p-api", loser_player_id="opponent-api",
        winner_name="Player A", loser_name="Player B",
    )

    class Result:
        def __init__(self, rows): self.rows = rows
        def all(self): return self.rows
        def scalars(self): return self
    class Session:
        def __init__(self): self.added = []
        def execute(self, statement):
            sql = str(statement)
            return Result([canonical_match] if "ext_tennis_canonical_matches" in sql else [source_row])
        def get(self, *_args): return None
        def add(self, row): self.added.append(row)
        def flush(self): pass

    session = Session()
    assert _record_api_tennis_partial_coverage(session, participant, profile, person, now)
    [coverage] = session.added
    assert coverage.source == "api_tennis"
    assert coverage.status == "partial" and coverage.complete is False
    assert coverage.checked_at == source_row.available_at
    assert coverage.source_watermark == canonical_match[1]
    assert coverage.evidence_json["usable_for_metrics"] is True
    assert coverage.evidence_json["complete_career_history"] is False
    assert coverage.evidence_json["serve_statistics_available"] == 1


def test_profile_coverage_records_all_sources_even_when_tml_is_complete(monkeypatch):
    from app import player_profiles

    calls = []
    monkeypatch.setattr(player_profiles, "_record_tml_history_coverage",
                        lambda *_args: calls.append("tml") or True)
    monkeypatch.setattr(player_profiles, "_record_tennis_explorer_partial_coverage",
                        lambda *_args: calls.append("tennis_explorer") or True)
    monkeypatch.setattr(player_profiles, "_record_tennis_explorer_recent_results_coverage",
                        lambda *_args: calls.append("tennis_explorer_recent") or False)
    monkeypatch.setattr(player_profiles, "_record_api_tennis_partial_coverage",
                        lambda *_args: calls.append("api_tennis") or True)
    monkeypatch.setattr(player_profiles, "_record_tennis_abstract_partial_coverage",
                        lambda *_args: calls.append("tennis_abstract") or False)

    status = player_profiles._record_profile_source_coverage(
        object(), object(), object(), object(), datetime.now(timezone.utc)
    )
    assert calls == ["tml", "tennis_explorer", "tennis_explorer_recent", "api_tennis", "tennis_abstract"]
    assert status == ("available", None)


def test_materialized_serve_stats_get_independent_partial_source_coverage():
    from types import SimpleNamespace
    from app.player_profiles import _record_materialized_serve_statistics_coverage

    now = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
    participant = SimpleNamespace(id=uuid.uuid4())
    facts = [
        SimpleNamespace(source_match_id="tml-1", played_at=now - timedelta(days=2),
                        serve_points=100, serve_points_won=61,
                        source_payload={"serve_stats_source": "tml",
                                        "serve_stats_available_at": (now - timedelta(hours=1)).isoformat()}),
        SimpleNamespace(source_match_id="api-1", played_at=now - timedelta(days=3),
                        serve_points=90, serve_points_won=55,
                        source_payload={"serve_stats_source": "api_tennis",
                                        "serve_stats_available_at": (now - timedelta(hours=2)).isoformat()}),
        SimpleNamespace(source_match_id="stale-1", played_at=now - timedelta(days=40),
                        serve_points=80, serve_points_won=47,
                        source_payload={"serve_stats_source": "tml",
                                        "serve_stats_available_at": (now - timedelta(days=8)).isoformat()}),
        SimpleNamespace(source_match_id="unknown-1", played_at=now - timedelta(days=1),
                        serve_points=None, serve_points_won=None,
                        source_payload={"serve_stats_source": "tml",
                                        "serve_stats_available_at": now.isoformat()}),
    ]

    class Session:
        def __init__(self): self.added = []
        def get(self, *_args): return None
        def add(self, row): self.added.append(row)
        def flush(self): pass

    session = Session()
    _record_materialized_serve_statistics_coverage(session, participant, facts, now)

    assert {row.source for row in session.added} == {"tennis_my_life", "api_tennis"}
    assert all(row.capability == "serve_statistics" for row in session.added)
    assert all(row.status == "partial" and row.complete is False for row in session.added)
    by_source = {row.source: row for row in session.added}
    assert by_source["tennis_my_life"].evidence_json["matches_with_explicit_stats"] == 1
    assert by_source["api_tennis"].evidence_json["usable_for_metrics"] is True


def test_late_serve_statistics_append_a_new_point_in_time_fact_version():
    from types import SimpleNamespace
    from app.player_profiles import _canonical_fact_available_at, _canonical_fact_version_needed

    match_available = datetime(2026, 10, 1, tzinfo=timezone.utc)
    stats_available = datetime(2026, 10, 20, tzinfo=timezone.utc)
    imported_at = datetime(2026, 10, 24, tzinfo=timezone.utc)
    prior = SimpleNamespace(serve_points=None, serve_points_won=None)

    assert _canonical_fact_version_needed(None, None)  # initial result is usable without serve data
    assert not _canonical_fact_version_needed(prior, None)  # unchanged no-stat observation adds no version
    assert _canonical_fact_version_needed(prior, (100, 62))  # late explicit counts create a version
    available_at = _canonical_fact_available_at(
        match_available, stats_available, has_previous_version=True, imported_at=imported_at,
    )
    assert available_at == imported_at
    assert available_at > stats_available > match_available


def test_fresh_api_tennis_and_tennis_abstract_results_trigger_lazy_metric_materialization():
    from app.player_profiles import has_unmaterialized_external_results

    participant_id = uuid.uuid4()
    canonical_id = uuid.uuid4()
    person = SimpleNamespace(id=canonical_id, display_name="A Player")
    match_rows = [("canonical-api", {"sources": ["api_tennis:match-1", "tennis_abstract:ta-1"]})]

    class Result:
        def __init__(self, rows): self.rows = rows
        def all(self): return self.rows
        def scalars(self): return self

    class Session:
        def __init__(self, fact_keys=()): self.calls = 0; self.fact_keys = list(fact_keys)
        def get(self, _model, _key): return person
        def execute(self, _statement):
            self.calls += 1
            if self.calls == 1: return Result(match_rows)
            if self.calls == 2:
                return Result([SimpleNamespace(source="api_tennis", source_match_id="match-1")])
            return Result(self.fact_keys)

    participant = SimpleNamespace(id=participant_id)
    profile = SimpleNamespace(canonical_player_id=canonical_id)

    assert has_unmaterialized_external_results(Session(), participant, profile)
    assert not has_unmaterialized_external_results(
        Session(fact_keys=["canonical-api"]), participant, profile
    )


def test_metrics_materialized_during_request_allow_only_small_timestamp_skew():
    now = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
    partial = SimpleNamespace(source="tennis_explorer", status="partial", complete=False,
                              checked_at=now, evidence_json={"usable_for_metrics": True})
    args = dict(metrics_version=1, as_of=now, complete_coverage=None,
                partial_coverage=partial, flashscore_last_success_at=None,
                identity_confirmation_required=False, attempt_recent=False)
    assert not profile_needs_history_refresh(metrics_available_at=now + timedelta(seconds=5), **args)
    assert profile_needs_history_refresh(metrics_available_at=now + timedelta(seconds=6), **args)
    assert profile_needs_history_refresh(metrics_available_at=now + timedelta(days=8), **args)


def test_existing_canonical_link_requires_matching_archived_source_id():
    class Result:
        def all(self):
            return []

    class Session:
        def execute(self, _statement):
            return Result()

    canonical_id = uuid.uuid4()
    person = SimpleNamespace(id=canonical_id, source_ids={"tml": "BK92"})
    profile = SimpleNamespace(
        canonical_player_id=canonical_id,
        source_identities={"archive:tml": "BK92"},
    )
    assert _profile_has_canonical_source_id(Session(), profile, person)

    profile.source_identities = {"archive:tml": "another-player"}
    assert not _profile_has_canonical_source_id(Session(), profile, person)


def test_pair_display_name_is_not_a_singles_archive_candidate():
    assert _is_singles_archive_player(SimpleNamespace(display_name="Alexander Bublik"))
    assert not _is_singles_archive_player(SimpleNamespace(display_name="Andreeva/ Bublik"))


def test_russian_transliteration_finds_final_e_archive_name_candidate():
    assert "brengl" in _canonical_player_surname_keys("Бренгл М")


def test_russian_transliteration_finds_c_k_variant_without_auto_linking():
    # Candidate lookup must reach the source spelling; the caller still
    # requires a unique, independently verified full-name match.
    assert "cretu" in _canonical_player_surname_keys("Крету Ч")


def test_russian_name_keys_reach_common_spanish_and_dutch_spellings():
    from app.tennis_flashscore import _player_name_matches

    cerundolo_keys = _canonical_player_surname_keys("Серундоло Ф")
    griekspoor_keys = _canonical_player_surname_keys("Грикспор Т")
    assert "cerundolo" in cerundolo_keys
    assert "griekspoor" in griekspoor_keys
    # Key expansion only finds indexed rows; the existing full-name matcher
    # still validates each candidate before a canonical profile is selected.
    assert _player_name_matches("Серундоло Ф", "Francisco Cerundolo")
    assert _player_name_matches("Грикспор Т", "Tallon Griekspoor")


def test_french_phonetic_archive_match_requires_independent_ids_country_and_initial():
    from app.player_profiles import _french_archive_identity_match

    candidate = SimpleNamespace(
        surname_key="jacquemot", initial="e", ioc="FRA", gender="w",
        source_ids={"tml": "221039", "tennis_explorer": "jacquemot"},
    )
    assert _french_archive_identity_match("Жакмо Э", candidate)
    assert not _french_archive_identity_match("Жакмо А", candidate)
    assert not _french_archive_identity_match("Жакмо Э", SimpleNamespace(
        **{**candidate.__dict__, "ioc": "BEL"}
    ))
    assert not _french_archive_identity_match("Жакмо Э", SimpleNamespace(
        **{**candidate.__dict__, "source_ids": {"tml": "221039"}}
    ))


def test_new_canonical_result_is_materialized_once_without_requiring_new_coverage():
    assert has_unmaterialized_canonical_results(["te-match", "shared-match"], ["shared-match"])
    assert not has_unmaterialized_canonical_results(["te-match"], ["te-match"])
    assert not has_unmaterialized_canonical_results([], [])


def test_tml_serve_counts_require_explicit_consistent_winner_or_loser_counts():
    stats = {"w_svpt": "71", "w_1stWon": "48", "w_2ndWon": "11",
             "l_svpt": "64", "l_1stWon": "36", "l_2ndWon": "9"}
    assert _tml_serve_counts(stats, is_winner=True) == (71, 59)
    assert _tml_serve_counts(stats, is_winner=False) == (64, 45)
    assert _tml_serve_counts({"w_svpt": "71", "w_1stWon": "48"}, is_winner=True) is None
    assert _tml_serve_counts({"w_svpt": "2.5", "w_1stWon": "1", "w_2ndWon": "1"},
                             is_winner=True) is None
    assert _tml_serve_counts({"w_svpt": "10", "w_1stWon": "8", "w_2ndWon": "3"},
                             is_winner=True) is None


def test_tml_serve_side_mapping_handles_reversed_players_and_rejects_unmatched_names():
    assert _canonical_tml_side_is_winner(
        "Player A", "Player B", "Player A", "Player B", participant_is_winner=True
    ) is True
    assert _canonical_tml_side_is_winner(
        "Player A", "Player B", "Player B", "Player A", participant_is_winner=True
    ) is False
    assert _canonical_tml_side_is_winner(
        "Player A", "Player B", "Player B", "Player A", participant_is_winner=False
    ) is True
    assert _canonical_tml_side_is_winner(
        "Player A", "Player B", "Player C", "Player D", participant_is_winner=True
    ) is None
    assert _canonical_tml_side_is_winner(
        "Lorenzo Musetti", "Luca Van Assche", "L. Musetti", "L. van Assche",
        participant_is_winner=True,
    ) is True
    assert _canonical_tml_side_is_winner(
        "Lorenzo Musetti", "Luca Van Assche", "L. van Assche", "L. Musetti",
        participant_is_winner=True,
    ) is False


def test_api_tennis_serve_counts_use_explicit_match_period_won_and_total():
    stats = {"statistics": [
        {"stat_period": "MATCH", "stat_name": "Service points won", "player_key": "p1",
         "stat_won": "42", "stat_total": "61"},
        {"stat_period": "1ST SET", "stat_name": "Service points won", "player_key": "p1",
         "stat_won": "20", "stat_total": "30"},
    ]}
    assert _api_tennis_serve_counts(stats, provider_player_id="p1") == (61, 42)
    assert _api_tennis_serve_counts(stats, provider_player_id="p2") is None
    assert _api_tennis_serve_counts(
        {"statistics": [{"stat_period": "match", "stat_name": "service points won",
                         "player_key": "p1", "stat_won": "8", "stat_total": "7"}]},
        provider_player_id="p1",
    ) is None


def test_serve_counts_are_left_unknown_when_sources_disagree():
    assert _consistent_serve_counts([((61, 42), "tml"), ((61, 42), "api_tennis")]) == (61, 42)
    assert _consistent_serve_counts([((61, 42), "tml"), ((60, 41), "api_tennis")]) is None
    assert _consistent_serve_counts([]) is None
