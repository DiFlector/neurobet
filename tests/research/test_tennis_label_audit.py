from datetime import datetime, timedelta, timezone
import re
import hashlib
import json
import sys
from types import SimpleNamespace
from uuid import UUID
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))
sys.path.insert(0, str(_ROOT / "scripts" / "research"))
from tennis_label_audit import (_utc_result_time, _validate_feature_availability,
                                _latest_identity_link_as_of,
                                _temporal_precedence, _causal_training_membership,
                                _walk_forward_admission, _window_summary,
                                _official_winner, _official_set_scores, _snapshot_set_number,
                                _missing_feature_values, ODDS_FREE_V2_FEATURE_NAMES,
                                _source_point_key, _snapshot_matches_source_point,
                                _point_events_for_alignment, _validated_terminal_game,
                                _snapshot_game_state,
                                _history_versions_by_id,
                                _training_gate_status, _tour, _tour_source,
                                _external_tour_level, _resolve_result_availability,
                                _result_score_snapshot_evidence, _score_token_pattern,
                                _summarize_feature_availability,
                                _snapshot_search_lower_bound, _write_training_examples,
                                _write_snapshot_source_coverage,
                                _summarize_snapshot_source_coverage,
                                _materialize_odds_free_feature_values,
                                _doubles_subdiscipline,
                                _read_saved_snapshot_cohort, _indexed_result_snapshot_evidence,
                                _select_pre_result_snapshot, _event_state_source_evidence)


def test_identity_resolution_uses_latest_decision_available_at_snapshot():
    cutoff = datetime(2026, 10, 27, 7, 17, tzinfo=timezone.utc)
    candidate = SimpleNamespace(id="1", status="candidate", available_at=cutoff - timedelta(days=6))
    confirmed = SimpleNamespace(id="2", status="confirmed", available_at=cutoff - timedelta(days=5))
    future_revocation = SimpleNamespace(id="3", status="revoked", available_at=cutoff + timedelta(seconds=1))

    assert _latest_identity_link_as_of(
        [candidate, confirmed, future_revocation], cutoff) is confirmed


def test_doubles_subdiscipline_uses_explicit_source_facts_and_keeps_unknown():
    event = SimpleNamespace(metadata_json={"fonbet_official_result": {"discipline": "Mixed Doubles"}})
    assert _doubles_subdiscipline(event, "ITF") == "mixed_doubles"
    event.metadata_json = {"fonbet_official_result": {"gender": "women"}}
    assert _doubles_subdiscipline(event, "WTA Doubles") == "womens_doubles"
    event.metadata_json = {}
    assert _doubles_subdiscipline(event, "Doubles") == "unknown_doubles"


def test_match_tiebreak_terminal_score_is_not_emitted_as_game_or_set():
    item = {"confirmed": True, "winner": 0, "quality": "terminal_from_tiebreak_result",
            "set": 3, "game_score": [10, 8]}
    assert _validated_terminal_game(item, True, [(6, 4), (4, 6), (10, 8)]) is None


def test_representative_snapshot_prefers_timely_evidence_before_result_only():
    at = datetime(2026, 10, 26, 10, tzinfo=timezone.utc)
    rows = [SimpleNamespace(id=i, observed_at=at + timedelta(minutes=i), status="live", score="0:0")
            for i in range(4)]
    def check(row):
        return {"missing_sources": ["event_format"] if row.id == 0 else []}
    assert _select_pre_result_snapshot(rows, feature_check=check,
                                      label_available_at=rows[2].observed_at).id == 1
    rows[1].score = "2:0 (6-4, 6-3)"
    assert _select_pre_result_snapshot(rows, feature_check=check,
        label_available_at=rows[2].observed_at, final_score=rows[1].score).id == 0
    assert _select_pre_result_snapshot(rows, feature_check=check, label_available_at=at) is None


def test_archived_event_state_witness_preserves_its_original_available_time():
    witness_at = datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc)
    snapshot_at = witness_at + timedelta(seconds=2)
    key = f"fonbet/tennis/{witness_at.date()}/aaaa/{'a' * 64}.json"
    row = SimpleNamespace(
        raw_evidence_id=UUID(int=3), raw_evidence_available_at=witness_at,
        raw_hash="a" * 64, raw_object_key=key,
        raw_evidence_hash="b" * 64, raw_evidence_json={"parser_version": "test-v1"},
    )
    evidence = _event_state_source_evidence(row, UUID(int=2), snapshot_at)
    assert evidence["source"] == "fonbet_archived_live_snapshot"
    assert evidence["available_at"] == witness_at.isoformat()
    assert evidence["raw_object_key"] == key
    assert evidence["parser_version"] == "test-v1"


def test_saved_snapshot_cohort_is_only_a_deduplicated_primary_key_index(tmp_path):
    event_id, snapshot_id = UUID(int=1), UUID(int=2)
    at = "2026-10-26T10:00:00+00:00"
    row = {"fonbet_event_id": str(event_id), "snapshot_id": str(snapshot_id),
           "snapshot_observed_at": at, "feature_values": {"forged": 123}}
    path = tmp_path / "examples.jsonl"
    path.write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n")
    cohort = _read_saved_snapshot_cohort(str(path))
    assert cohort["examples"] == 2
    assert cohort["event_ids"] == {event_id}
    assert len(cohort["keys"]) == 1
    assert cohort["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert "feature_values" not in cohort


def test_saved_snapshot_cohort_rejects_conflicting_event_for_one_snapshot(tmp_path):
    import pytest
    row = {"fonbet_event_id": str(UUID(int=1)), "snapshot_id": str(UUID(int=2)),
           "snapshot_observed_at": "2026-10-26T10:00:00+00:00"}
    path = tmp_path / "examples.jsonl"
    path.write_text(json.dumps(row) + "\n" + json.dumps({**row, "fonbet_event_id": str(UUID(int=3))}))
    with pytest.raises(ValueError, match="conflicting_event"):
        _read_saved_snapshot_cohort(str(path))


def test_indexed_result_time_requires_exact_saved_final_score():
    event_id = UUID(int=1)
    at = datetime(2026, 10, 26, 10, tzinfo=timezone.utc)
    event = SimpleNamespace(metadata_json={"fonbet_official_result": {"final_score": "2:0 (6-4, 6-3)"}})
    row = SimpleNamespace(id=UUID(int=2), event_id=event_id, observed_at=at,
                          status="live", score="2:0 (6-4, 6-3)")
    evidence = _indexed_result_snapshot_evidence({event_id: event}, [row])
    assert evidence[event_id]["observed_at"] == at
    row.score = "1:0 (6-4, 5-3)"
    assert _indexed_result_snapshot_evidence({event_id: event}, [row]) == {}


def test_result_witness_recovers_earlier_receipt_even_with_late_import_time():
    event_id = UUID(int=1)
    at = datetime(2026, 10, 13, 10, tzinfo=timezone.utc)
    event = SimpleNamespace(metadata_json={"fonbet_official_result": {
        "final_score": "2:0 (6-4, 6-3)", "available_at": "2026-10-21T12:00:00Z"}})
    def row(number, observed, score="2-0 (6-4, 6-3)"):
        return SimpleNamespace(id=UUID(int=number), event_id=event_id,
                               observed_at=observed, score=score, status="live")
    witnesses = [row(4, at+timedelta(hours=1)), row(2, at),
                 row(3, at-timedelta(hours=1), "1-0 (6-4, 5-3)"),
                 row(5, at+timedelta(days=20))]
    evidence = _indexed_result_snapshot_evidence({event_id: event}, witnesses,
                                                cutoff=at+timedelta(days=1))
    assert evidence[event_id]["snapshot_id"] == str(UUID(int=2))
    assert _resolve_result_availability(_utc_result_time(event), evidence[event_id]) == (
        at, "fonbet_exact_final_score_snapshot")
    assert _indexed_result_snapshot_evidence({event_id: event}, [witnesses[-1]], cutoff=at) == {}


def test_official_match_labels_require_a_consistent_definitive_score():
    valid = {
        "status": "FINISHED", "winner": "player_a", "final_score": "2:0 (6-4, 6-3)",
    }
    cases = [
        ("finished", valid, {"set_winners": {"1": "player_a", "2": "player_a"}}, (0, "confirmed")),
        ("live", valid, {}, (None, "official_result_not_finished")),
        ("finished", {**valid, "is_walkover": True}, {}, (None, "walkover_or_retirement")),
        ("finished", {**valid, "is_retired": True}, {}, (None, "walkover_or_retirement")),
        ("finished", {**valid, "winner": "player_b"}, {}, (None, "winner_disagrees_with_score")),
        ("finished", {**valid, "final_score": "2:1 (6-4, 6-3)"}, {}, (None, "match_score_disagrees_with_sets")),
        ("finished", valid, {"set_winners": {"1": "player_b", "2": "player_a"}},
         (None, "set_winner_disagrees_with_score")),
    ]
    for event_status, official, metadata, expected in cases:
        event = SimpleNamespace(status=event_status, metadata_json={"fonbet_official_result": official, **metadata})
        assert _official_winner(event) == expected


def test_training_examples_manifest_hashes_serialized_file_bytes(tmp_path):
    rows = [{"feature": "surface_unknown", "value": True}, {"unicode": "подача"}]
    manifest = _write_training_examples(rows, str(tmp_path))
    payload = (tmp_path / manifest["path"].split("/")[-1]).read_bytes()
    assert manifest["sha256"] == hashlib.sha256(payload).hexdigest()
    assert manifest["logical_digest"] == hashlib.sha256("\n".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) for row in rows
    ).encode("utf-8")).hexdigest()
    assert manifest["sha256"] != manifest["logical_digest"]


def test_audit_materializes_ordered_odds_free_values_from_snapshot_cutoff():
    cutoff = datetime(2026, 10, 26, 11, tzinfo=timezone.utc)
    event = SimpleNamespace(metadata_json={
        "best_of": 3, "best_of_available_at": "2026-10-26T10:00:00Z",
        "surface": "clay", "surface_available_at": "2026-10-26T10:00:00Z",
    })
    snapshot = SimpleNamespace(
        score_detail={"stats": {"sets_a": 1, "sets_b": 0,
                                "set_lines": [{"games_a": 3, "games_b": 2, "live": True}]},
                      "current_game": {"points_a": "30", "points_b": "15"},
                      "server": "player_a"},
        sport_state={},
    )
    player_metrics = {
        "a": {"elo": {"overall": 1600}, "recent": {"days_90": {"wins": 8, "matches": 10}},
              "overall": {"matches": 30}},
        "b": {"elo": {"overall": 1400}, "recent": {"days_90": {"wins": 2, "matches": 10}},
              "overall": {"matches": 20}},
    }

    values = _materialize_odds_free_feature_values(event, snapshot, cutoff, player_metrics,
                                                    {"a": (100, 70), "b": (100, 60)})

    assert values["elo_diff"] == 200
    assert values["sets_to_win"] == 2
    assert values["match_format_known"] == 1.0
    assert values["points_a"] == 2
    assert values["server_a"] == 1.0
    assert not any(name.startswith("surface_") for name in values)
    assert values["serve_probability_a"] == (70 + 0.62 * 200) / 300
    assert "odds_a" not in values


def test_audit_does_not_fill_unobserved_or_late_format():
    cutoff = datetime(2026, 10, 26, 11, tzinfo=timezone.utc)
    event = SimpleNamespace(metadata_json={"best_of": 5, "best_of_available_at": "2026-10-26T12:00:00Z"})
    snapshot = SimpleNamespace(score_detail={}, sport_state={})

    values = _materialize_odds_free_feature_values(event, snapshot, cutoff, {}, {})

    assert values["sets_to_win"] == 0
    assert values["match_format_known"] == 0.0


def test_tour_uses_confirmed_flashscore_category_only_as_report_metadata():
    assert _tour("local event", external_tour="itf") == "itf"
    assert _tour("local event", external_tour="challenger") == "challenger"
    assert _tour("local event", external_tour="atp") == "atp"
    assert _tour("local event") == "unknown"
    assert _tour_source("local event", external_tour="itf") == "flashscore_match_archive"
    assert _tour_source("ATP 250", external_tour="itf") == "flashscore_match_archive"
    assert _tour_source("ATP 250", {"tour": "wta"}, "itf") == "fonbet_metadata"


def test_external_mixed_and_female_challenger_categories_use_match_gender():
    assert _external_tour_level("atp_wta", "male") == "atp"
    assert _external_tour_level("atp_wta", "female") == "wta"
    assert _external_tour_level("atp_wta", "unknown") == "unknown"
    assert _external_tour_level("challenger", "female") == "wta"
    assert _external_tour_level("challenger", "male") == "challenger"
    assert _external_tour_level("itf", "female", "J300 Girls") == "junior"
    assert _external_tour_level("itf", "male", "U18 Boys") == "junior"
    assert _external_tour_level("challenger", "male", "Menorca") == "challenger"
    assert _external_tour_level("atp_wta", "unknown", "ATP Cup") == "atp"


def test_junior_markers_are_classified_before_itf_and_gender_names():
    assert _tour("J300 Boys") == "junior"
    assert _tour("ITF U18 Girls") == "junior"
    assert _tour("ITF M15 Antalya") == "itf"


def test_window_minimum_counts_only_matches_with_target_labels():
    ids = [UUID(int=value) for value in range(1, 11)]
    labels = {ids[0]: {0}, ids[1]: {1}}
    summary = _window_summary(ids, {}, labels, required=3)
    assert summary["candidate_matches"] == 10
    assert summary["matches"] == 2
    assert summary["class_a"] == 1
    assert summary["class_b"] == 1
    assert summary["both_classes"] is True
    assert summary["meets_match_minimum"] is False


def test_result_time_uses_earlier_exact_observation_and_never_scheduled_time():
    observed = datetime(2026, 10, 13, 4, 45, tzinfo=timezone.utc)
    backfill = datetime(2026, 10, 21, 4, 30, tzinfo=timezone.utc)
    assert _resolve_result_availability(backfill, {"observed_at": observed}) == (
        observed, "fonbet_exact_final_score_snapshot")
    assert _resolve_result_availability(backfill, None) == (
        backfill, "fonbet_official_result_metadata")
    assert _resolve_result_availability(None, None) == (None, None)


def test_snapshot_score_filter_accepts_punctuation_variants_but_requires_exact_tokens():
    pattern = _score_token_pattern((0, 2, 5, 7, 3, 6))
    assert re.fullmatch(pattern, "0:2 (5-7 3-6)")
    assert re.fullmatch(pattern, "0-2 (5-7, 3-6)")
    assert not re.fullmatch(pattern, "0-2 (5-7, 3-6, 6-0)")
    assert not re.fullmatch(pattern, "0-2 (5-7, 3-5)")


def test_scheduled_start_is_not_used_as_result_availability():
    event = SimpleNamespace(
        metadata_json={"fonbet_official_result": {}},
        scheduled_start_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    assert _utc_result_time(event) is None


def test_snapshot_lookup_uses_first_seen_only_as_a_safe_query_bound():
    first_seen = datetime(2026, 10, 13, 4, 0)
    event = SimpleNamespace(first_seen_at=first_seen)
    assert _snapshot_search_lower_bound(event) == first_seen.replace(tzinfo=timezone.utc)
    assert _snapshot_search_lower_bound(SimpleNamespace(first_seen_at=None)) is None


def test_result_snapshot_search_prefers_latest_indexed_rows_and_checks_exact_score():
    event_ids = [UUID(int=101), UUID(int=102)]
    first_seen = datetime(2026, 10, 20, tzinfo=timezone.utc)
    rows = [
        SimpleNamespace(event_id=event_id, id=f"snapshot-{i}", observed_at=first_seen + timedelta(hours=i),
                        status="finished", score="0-2 (2-6, 2-6)")
        for i, event_id in enumerate(event_ids)
    ]

    class Result:
        def __init__(self, values): self.values = values
        def all(self): return self.values
        def yield_per(self, _size): return iter(self.values)

    class Session:
        def __init__(self): self.calls = []
        def execute(self, statement, params=None):
            self.calls.append((str(statement), params))
            return Result(rows)

    events = {
        event_id: SimpleNamespace(
            first_seen_at=first_seen,
            metadata_json={"fonbet_official_result": {"final_score": "0:2 (2-6 2-6)"}},
        )
        for event_id in event_ids
    }
    session = Session()
    evidence, count = _result_score_snapshot_evidence(session, events)

    assert count == 2
    assert evidence[event_ids[0]]["snapshot_id"] == "snapshot-0"
    assert evidence[event_ids[1]]["snapshot_id"] == "snapshot-1"
    assert evidence[event_ids[0]]["observed_at"] == first_seen
    assert len(session.calls) == 1
    lookup, params = session.calls[0]
    assert "JOIN LATERAL" in lookup
    assert "event_id = requested.event_id" in lookup
    assert "ORDER BY observed_at DESC, id DESC" in lookup
    assert "score_pattern" not in lookup
    assert params["event_ids"] == event_ids
    assert params["lower_bounds"] == [first_seen, first_seen]


def test_result_snapshot_search_bounds_database_batches_and_covers_every_event():
    event_ids = [UUID(int=1000 + i) for i in range(205)]
    first_seen = datetime(2026, 10, 20, tzinfo=timezone.utc)

    class Result:
        def __init__(self, values): self.values = values
        def yield_per(self, _size): return iter(self.values)

    class Session:
        def __init__(self): self.calls = []
        def execute(self, statement, params=None):
            self.calls.append((str(statement), params))
            rows = [SimpleNamespace(event_id=event_id, id=f"snapshot-{event_id.int}",
                                    observed_at=first_seen, status="finished",
                                    score="0:2 (2-6 2-6)")
                    for event_id in params["event_ids"]]
            return Result(rows)

    events = {
        event_id: SimpleNamespace(
            first_seen_at=first_seen,
            metadata_json={"fonbet_official_result": {"final_score": "0:2 (2-6 2-6)"}},
        )
        for event_id in event_ids
    }
    session = Session()
    evidence, count = _result_score_snapshot_evidence(session, events)

    assert count == len(event_ids)
    assert set(evidence) == set(event_ids)
    assert [len(params["event_ids"]) for _, params in session.calls] == [100, 100, 5]
    assert max(len(params["event_ids"]) for _, params in session.calls) == 100


def test_result_snapshot_search_falls_back_to_history_only_for_latest_mismatches():
    event_id = UUID(int=103)
    first_seen = datetime(2026, 10, 20, tzinfo=timezone.utc)
    latest = SimpleNamespace(event_id=event_id, id="latest", observed_at=first_seen + timedelta(hours=2),
                             status="finished", score="1:2 (2-6 2-6)")
    historical = SimpleNamespace(event_id=event_id, id="prior-exact", observed_at=first_seen + timedelta(hours=1),
                                 status="live", score="0-2 (2-6, 2-6)")

    class Result:
        def __init__(self, values): self.values = values
        def all(self): return self.values
        def yield_per(self, _size): return iter(self.values)

    class Session:
        def __init__(self): self.calls = []
        def execute(self, statement, params=None):
            self.calls.append((str(statement), params))
            return Result([latest] if len(self.calls) == 1 else [historical])

    event = SimpleNamespace(first_seen_at=first_seen,
        metadata_json={"fonbet_official_result": {"final_score": "0:2 (2-6 2-6)"}})
    session = Session()
    evidence, count = _result_score_snapshot_evidence(session, {event_id: event})

    assert count == 1
    assert evidence[event_id]["snapshot_id"] == "prior-exact"
    assert len(session.calls) == 2
    assert "score_pattern" in session.calls[1][0]
    assert session.calls[1][1]["event_ids"] == [event_id]


def test_result_snapshot_search_skips_events_with_official_availability_time():
    event_id = UUID(int=104)
    first_seen = datetime(2026, 10, 20, tzinfo=timezone.utc)
    official_available_at = "2026-10-21T12:00:00+00:00"
    class Session:
        def __init__(self): self.calls = []
        def execute(self, statement, params=None):
            self.calls.append((str(statement), params))

    event = SimpleNamespace(first_seen_at=first_seen, metadata_json={
        "fonbet_official_result": {
            "final_score": "0:2 (2-6 2-6)", "available_at": official_available_at,
        },
    })
    session = Session()
    evidence, count = _result_score_snapshot_evidence(session, {event_id: event})

    assert evidence == {}
    assert count == 0
    assert session.calls == []
    assert _resolve_result_availability(_utc_result_time(event), evidence.get(event_id)) == (
        datetime.fromisoformat(official_available_at), "fonbet_official_result_metadata",
    )


def test_result_snapshot_history_fallback_uses_small_batches_for_unknown_times():
    event_ids = [UUID(int=2000 + i) for i in range(25)]
    first_seen = datetime(2026, 10, 20, tzinfo=timezone.utc)

    class Result:
        def __init__(self, values): self.values = values
        def yield_per(self, _size): return iter(self.values)

    class Session:
        def __init__(self): self.calls = []
        def execute(self, statement, params=None):
            self.calls.append((str(statement), params))
            score = "0:2 (2-6 2-6)" if "score_pattern" in str(statement) else "1:2 (6-4 2-6 0-6)"
            rows = [SimpleNamespace(event_id=event_id, id=f"snapshot-{event_id.int}",
                                    observed_at=first_seen + timedelta(hours=1),
                                    status="finished", score=score)
                    for event_id in params["event_ids"]]
            return Result(rows)

    events = {
        event_id: SimpleNamespace(
            first_seen_at=first_seen,
            metadata_json={"fonbet_official_result": {"final_score": "0:2 (2-6 2-6)"}},
        )
        for event_id in event_ids
    }
    session = Session()
    evidence, count = _result_score_snapshot_evidence(session, events)

    assert count == len(event_ids)
    assert set(evidence) == set(event_ids)
    assert [len(params["event_ids"]) for _, params in session.calls] == [25, 10, 10, 5]


def test_official_result_availability_uses_persisted_source_observation():
    available_at = "2026-10-20T12:34:56+00:00"
    event = SimpleNamespace(metadata_json={"fonbet_official_result": {"available_at": available_at}})
    assert _utc_result_time(event) == datetime.fromisoformat(available_at)


def test_unknown_source_server_does_not_discard_a_unique_score_transition():
    item = {"set": 1, "state_before": ["30", "15"], "game_score": [2, 3], "server": None,
            "tie_break": False}
    signature = _source_point_key(item, external_home_is_a=True)
    snapshot = (1, (2, 3), (2, 1), 0, False)
    assert signature == (1, (2, 3), (2, 1), None, False)
    assert _snapshot_matches_source_point(snapshot, signature) is True


def test_known_source_server_must_agree_and_duplicate_states_remain_ambiguous():
    item = {"set": 1, "state_before": ["30", "15"], "game_score": [2, 3], "server": 0,
            "tie_break": False}
    signature = _source_point_key(item, external_home_is_a=True)
    assert _snapshot_matches_source_point((1, (2, 3), (2, 1), 0, False), signature) is True
    assert _snapshot_matches_source_point((1, (2, 3), (2, 1), 1, False), signature) is False


def test_official_set_scores_are_accepted_without_external_pbp():
    event = SimpleNamespace(status="finished", metadata_json={"fonbet_official_result": {
        "status": "FINISHED", "winner": "player_a", "final_score": "2:0 (6-4, 7-6)",
    }})
    assert _official_set_scores(event) == ([(6, 4), (7, 6)], "confirmed")


def test_intermediate_game_result_need_not_equal_the_finished_set():
    terminal = {"set": 1, "game_score": [3, 2], "confirmed": True, "winner": 0,
                "quality": "terminal_from_game_result"}
    assert _validated_terminal_game(terminal, True, [(6, 4)]) == (1, (2, 2), 0, False)
    assert _validated_terminal_game(terminal, False, [(4, 6)]) == (1, (2, 2), 1, False)
    assert _validated_terminal_game({**terminal, "set": 2}, True, [(6, 4)]) is None
    assert _validated_terminal_game({**terminal, "game_score": [6, 3]}, True, [(6, 4)]) is None
    assert _validated_terminal_game({**terminal, "game_score": [3, 5]}, True, [(6, 4)]) is None


def test_flashscore_points_align_to_the_game_before_its_confirmed_result():
    point = {"set": 1, "game_score": [3, 2], "state_before": ["30", "15"],
             "winner": 0, "server": 0, "confirmed": True, "quality": "confirmed"}
    terminal = {"set": 1, "game_score": [3, 2], "state_after": {"game_result": [3, 2]},
                "winner": 0, "confirmed": True, "quality": "terminal_from_game_result"}
    prepared = _point_events_for_alignment([point, terminal], "flashscore-point-history-v7")
    signature = _source_point_key(prepared[0], True)
    assert signature == (1, (2, 2), (2, 1), 0, False)
    assert _snapshot_matches_source_point((1, (2, 2), (2, 1), 0, False), signature)
    assert not _snapshot_matches_source_point((1, (3, 2), (2, 1), 0, False), signature)
    assert _source_point_key(prepared[0], False) == (1, (2, 2), (1, 2), 1, False)
    assert "game_score_before" not in point
    assert _source_point_key(_point_events_for_alignment([point], "flashscore-point-history-v7")[0], True) is None
    conflict = {**terminal, "winner": 1}
    assert _source_point_key(_point_events_for_alignment([point, terminal, conflict],
                                                       "flashscore-point-history-v7")[0], True) is None


def test_game_label_alignment_does_not_require_point_fields():
    snapshot = SimpleNamespace(current_period=1, sport_state={}, score_detail={
        "stats": {"set_lines": [{"games_a": 2, "games_b": 2, "live": True}]}})
    assert _snapshot_game_state(snapshot) == (1, (2, 2))
    snapshot.current_period = 2
    assert _snapshot_game_state(snapshot) is None


def test_parser_to_audit_round_trip_uses_pre_game_fonbet_state():
    from tennis_adapter.point_history import PARSER_VERSION, parse_point_history_html
    html = '''<div class="matchHistoryRowWrapper"><div class="sectionHeader">Point by point - Set 1</div>
      <div class="matchHistoryRow"><div class="matchHistoryRow__scoreBox"><span>1</span><span>0</span></div></div>
      <div class="matchHistoryRow__fifteens"><div class="matchHistoryRow__fifteen">15:0</div>
      <div class="matchHistoryRow__fifteen">30:0</div><div class="matchHistoryRow__fifteen">40:0</div>
      <div class="matchHistoryRow__gameResult">GAME 1-0</div></div></div>'''
    parsed = parse_point_history_html(html)
    assert parsed["events"][0]["game_score"] == [1, 0]
    prepared = _point_events_for_alignment(parsed["events"], PARSER_VERSION)
    signature = _source_point_key(prepared[0], True)
    assert _snapshot_matches_source_point((1, (0, 0), (0, 0), None, False), signature)
    assert not _snapshot_matches_source_point((1, (1, 0), (0, 0), None, False), signature)


def test_history_pk_pages_preserve_asof_order_and_reject_missing_evidence():
    import pytest
    ids = [UUID(int=i) for i in (1, 2, 3)]
    responses = [[SimpleNamespace(id=ids[1]), SimpleNamespace(id=ids[0])],
                 [SimpleNamespace(id=ids[2])]]
    class Query:
        def filter(self, _): return self
        def all(self): return responses.pop(0)
    class Session:
        def query(self, _): return Query()
    assert [row.id for row in _history_versions_by_id(Session(), ids, batch_size=2)] == ids
    responses[:] = [[]]
    with pytest.raises(ValueError, match="history_version_primary_key_missing"):
        list(_history_versions_by_id(Session(), ids[:1]))


def test_official_set_scores_reject_retirement_and_inconsistent_winner():
    retired = SimpleNamespace(status="finished", metadata_json={"fonbet_official_result": {
        "status": "FINISHED", "winner": "player_a", "is_retired": True,
        "final_score": "1:0 (6-4, 2-1)",
    }})
    assert _official_set_scores(retired) == (None, "walkover_or_retirement")
    mismatch = SimpleNamespace(status="finished", metadata_json={"fonbet_official_result": {
        "status": "FINISHED", "winner": "player_a", "final_score": "2:0 (6-4, 2-6, 6-3)",
    }})
    assert _official_set_scores(mismatch) == (None, "match_score_disagrees_with_sets")


def test_set_number_can_be_read_without_point_state():
    snapshot = SimpleNamespace(current_period=None,
        score_detail={"stats": {"set_lines": [{"live": False}, {"live": True}]}},
        sport_state={})
    assert _snapshot_set_number(snapshot) == 2


def test_feature_materialization_must_contain_every_contract_value():
    values = {name: 0.0 for name in ODDS_FREE_V2_FEATURE_NAMES}
    assert _missing_feature_values(values) == []
    values.pop("points_a")
    assert _missing_feature_values(values) == ["points_a"]
    assert len(_missing_feature_values(None)) == len(ODDS_FREE_V2_FEATURE_NAMES)


def test_feature_availability_fails_closed_for_missing_or_future_sources():
    cutoff = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
    complete = {name: cutoff.isoformat() for name in (
        "player_metrics", "serve_metrics_a", "serve_metrics_b", "event_state", "event_format")}
    assert _validate_feature_availability(cutoff, complete)["certified"] is True
    complete["serve_metrics_a"] = (cutoff + timedelta(seconds=1)).isoformat()
    result = _validate_feature_availability(cutoff, complete)
    assert result["certified"] is False
    assert result["future_sources"] == ["serve_metrics_a"]
    complete.pop("event_format")
    result = _validate_feature_availability(cutoff, complete)
    assert result["missing_sources"] == []


def test_explicit_contract_default_is_causal_without_inventing_source_timestamp():
    cutoff = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
    complete = {name: cutoff.isoformat() for name in (
        "player_metrics", "serve_metrics_a", "serve_metrics_b", "event_state", "event_format")}
    complete["serve_metrics_a"] = None
    result = _validate_feature_availability(cutoff, complete, {"serve_metrics_a"})
    assert result["certified"] is True
    assert result["contract_defaulted_sources"] == ["serve_metrics_a"]
    assert result["missing_sources"] == []


def test_unknown_event_format_is_optional_but_late_known_format_fails():
    cutoff = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
    sources = {name: cutoff.isoformat() for name in (
        "player_metrics", "serve_metrics_a", "serve_metrics_b", "event_state")}
    result = _validate_feature_availability(cutoff, sources, {"event_format"})
    assert result["certified"] is True
    assert result["missing_sources"] == []
    late = _validate_feature_availability(cutoff, {**sources, "event_format": (cutoff + timedelta(seconds=1)).isoformat()})
    assert late["certified"] is False
    assert late["future_sources"] == ["event_format"]


def test_feature_availability_summary_distinguishes_observed_values_from_contract_defaults():
    summary = _summarize_feature_availability([
        {"certified": True, "contract_defaulted_sources": []},
        {"certified": True, "contract_defaulted_sources": ["serve_metrics_a"]},
        {"certified": False, "contract_defaulted_sources": []},
    ])
    assert summary == {
        "checked_examples": 3,
        "certified_examples": 2,
        "uncertified_examples": 1,
        "fully_source_observed_examples": 1,
        "contract_defaulted_examples": 1,
        "allowed_player_prior_examples": 0,
        "allowed_serve_prior_examples": 0,
    }


def test_training_labels_must_be_available_before_validation_forecasts():
    train_id, validation_id = UUID(int=1), UUID(int=2)
    temporal = {
        train_id: {"label_available_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
                   "forecast_at": datetime(2025, 12, 31, tzinfo=timezone.utc)},
        validation_id: {"label_available_at": datetime(2026, 1, 3, tzinfo=timezone.utc),
                        "forecast_at": datetime(2026, 1, 2, tzinfo=timezone.utc)},
    }
    assert _temporal_precedence([train_id], [validation_id], temporal)["passed"] is True
    temporal[train_id]["label_available_at"] = datetime(2026, 1, 2, 0, 0, 1, tzinfo=timezone.utc)
    result = _temporal_precedence([train_id], [validation_id], temporal)
    assert result["passed"] is False
    assert result["latest_train_label_available_at"] > result["earliest_validation_forecast_at"]


def test_walk_forward_split_keeps_undated_confirmed_labels_on_evaluation_timeline(tmp_path, monkeypatch):
    manifest_path = tmp_path / "manifest.json"
    monkeypatch.setenv("TENNIS_TRAINING_MANIFEST_PATH", str(manifest_path))
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    ids = [UUID(int=value) for value in range(1, 31)]
    event_times = {event_id: start + timedelta(days=index) for index, event_id in enumerate(ids)}
    rows = {
        "point": {(event_id, index % 2) for index, event_id in enumerate(ids[:10])},
        "game": {(event_id, index % 2) for index, event_id in enumerate(ids[:20])},
        "set": {(event_id, index % 2) for index, event_id in enumerate(ids[:25])},
        "match": {(event_id, index % 2) for index, event_id in enumerate(ids)},
    }
    temporal = {target: {event_id: {
        "forecast_at": event_times[event_id],
        "label_available_at": None if event_id == ids[-1] else event_times[event_id] + timedelta(hours=1),
    } for event_id in ids} for target in rows}
    report, metadata = _walk_forward_admission(rows, event_times, temporal)
    assert metadata["matches"] == 30
    assert metadata["holdout_quality_metrics_opened"] is False
    for target in rows:
        assert report[target]["manifest_matches"] == 30
        assert report[target]["folds"][0]["train_candidates"]["candidate_matches"] == report["point"]["folds"][0]["train_candidates"]["candidate_matches"]
    manifest = __import__("json").loads(manifest_path.read_text())
    assert ids[-1] in [UUID(value) for value in manifest["ordered_event_ids"]]
    assert ids[-1] in [UUID(value) for value in manifest["holdout_event_ids"]]
    assert report["match"]["holdout"]["matches"] == 6


def test_causal_training_membership_purges_late_and_unknown_labels():
    train_ready, train_late, train_unknown, validation = (
        UUID(int=value) for value in (1, 2, 3, 4)
    )
    cutoff = datetime(2026, 1, 10, tzinfo=timezone.utc)
    labels = {event_id: {1} for event_id in (train_ready, train_late, train_unknown, validation)}
    temporal = {
        train_ready: {"label_available_at": cutoff - timedelta(seconds=1)},
        train_late: {"label_available_at": cutoff + timedelta(seconds=1)},
        train_unknown: {"label_available_at": None},
        validation: {"forecast_at": cutoff},
    }

    eligible, details = _causal_training_membership(
        [train_ready, train_late, train_unknown], [validation], labels, temporal
    )

    assert eligible == [train_ready]
    assert details == {
        "cutoff": cutoff.isoformat(),
        "candidate_labelled_matches": 3,
        "eligible_matches": 1,
        "purged_future_label_matches": 1,
        "purged_unknown_label_time_matches": 1,
        "purged_no_validation_boundary_matches": 0,
        "missing_validation_forecast_times": 0,
    }


def test_walk_forward_manifest_persists_target_specific_causal_training_ids(tmp_path, monkeypatch):
    manifest_path = tmp_path / "manifest.json"
    monkeypatch.setenv("TENNIS_TRAINING_MANIFEST_PATH", str(manifest_path))
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ids = [UUID(int=value) for value in range(1, 31)]
    event_times = {event_id: start + timedelta(days=index) for index, event_id in enumerate(ids[:-1])}
    rows = {"match": {(event_id, index % 2) for index, event_id in enumerate(ids[:-1])}}
    temporal = {"match": {event_id: {
        "label_available_at": event_times[event_id] + timedelta(hours=1),
        "forecast_at": event_times[event_id],
    } for event_id in ids[:-1]}}
    # A training result that arrived after the first validation forecast must
    # be purged from every affected fold's actual train membership.
    temporal["match"][ids[0]]["label_available_at"] = start + timedelta(days=20)

    report, _ = _walk_forward_admission(rows, event_times, temporal)
    manifest = json.loads(manifest_path.read_text())

    assert manifest["manifest_version"] == "tennis_shared_walk_forward_v3"
    assert manifest["training_cutoff_policy"] == "only_source_timed_labels_available_by_first_validation_forecast_enter_training"
    first_fold = manifest["folds"][0]
    assert str(ids[0]) not in first_fold["target_train_event_ids"]["match"]
    assert report["match"]["folds"][0]["training_filter"]["purged_future_label_matches"] == 1
    assert report["match"]["folds"][0]["temporal_precedence"]["passed"] is True


def test_training_gate_cannot_open_when_temporal_precedence_or_result_time_fails():
    status, reasons = _training_gate_status(
        base_coverage_ok=True, windows_ok=True, temporal_ok=False,
        point_in_time_ok=True, manifest_result_times_certified=True,
    )
    assert status == "blocked"
    assert reasons == ["training_labels_not_available_before_validation_forecasts"]

    status, reasons = _training_gate_status(
        base_coverage_ok=True, windows_ok=True, temporal_ok=True,
        point_in_time_ok=True, manifest_result_times_certified=False,
    )
    assert status == "blocked"
    assert reasons == ["manifest_result_availability_not_certified"]


def test_training_gate_opens_only_when_every_admission_check_passes():
    status, reasons = _training_gate_status(
        base_coverage_ok=True, windows_ok=True, temporal_ok=True,
        point_in_time_ok=True, manifest_result_times_certified=True,
    )
    assert status == "eligible_for_training_review"
    assert reasons == []


def test_snapshot_source_coverage_manifest_includes_unlabelled_snapshot_provenance(tmp_path):
    rows = [{
        "fonbet_event_id": "event-1",
        "snapshot_id": "snapshot-1",
        "snapshot_observed_at": "2026-10-26T12:00:00+00:00",
        "feature_availability": {"certified": False, "missing_sources": ["event_format"]},
    }]

    manifest = _write_snapshot_source_coverage(rows, str(tmp_path))

    assert manifest["examples"] == 1
    assert manifest["content_type"] == "jsonl"
    assert Path(manifest["path"]).read_text().strip() == json.dumps(
        rows[0], ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def test_snapshot_coverage_counts_unverifiable_raw_evidence_as_missing():
    result = _summarize_snapshot_source_coverage([{
        "certified": False,
        "source_available_at": {"event_state": "2026-10-26T12:00:00+00:00"},
        "source_evidence": {"event_state": None},
        "source_resolution": {"event_state": "observed_snapshot"},
        "contract_defaulted_sources": [],
        "missing_evidence": ["event_state"],
    }])

    assert result["source_groups"]["event_state"] == {
        "available": 0, "contract_defaulted": 0, "missing_or_unverifiable": 1,
    }
    assert result["exclusions"] == {"event_state:missing_evidence": 1}


def test_allowed_unknown_player_prior_is_not_reported_as_observed_history():
    from tennis_label_audit import _summarize_feature_availability
    result = _summarize_feature_availability([{
        "certified": True, "player_history_fallback": {"a": "history_or_identity_unavailable"}}])
    assert result["certified_examples"] == 1
    assert result["fully_source_observed_examples"] == 0
    assert result["allowed_player_prior_examples"] == 1
