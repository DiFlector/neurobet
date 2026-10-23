from datetime import datetime, timedelta, timezone
import re
import hashlib
import json
import sys
from types import SimpleNamespace
from uuid import UUID

sys.path.insert(0, "/srv/neurobet/scripts/research")
from tennis_label_audit import (_utc_result_time, _validate_feature_availability,
                                _temporal_precedence, _causal_training_membership,
                                _walk_forward_admission, _window_summary,
                                _official_winner,
                                _source_point_key, _snapshot_matches_source_point,
                                _training_gate_status, _tour, _tour_source,
                                _external_tour_level, _resolve_result_availability,
                                _result_score_snapshot_evidence, _score_token_pattern,
                                _summarize_feature_availability,
                                _snapshot_search_lower_bound, _write_training_examples)


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


def test_feature_availability_fails_closed_for_missing_or_future_sources():
    cutoff = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
    complete = {name: cutoff.isoformat() for name in (
        "player_metrics", "serve_metrics_a", "serve_metrics_b", "event_state", "event_format", "event_surface")}
    assert _validate_feature_availability(cutoff, complete)["certified"] is True
    complete["serve_metrics_a"] = (cutoff + timedelta(seconds=1)).isoformat()
    result = _validate_feature_availability(cutoff, complete)
    assert result["certified"] is False
    assert result["future_sources"] == ["serve_metrics_a"]
    complete.pop("event_surface")
    result = _validate_feature_availability(cutoff, complete)
    assert result["missing_sources"] == ["event_surface"]


def test_explicit_contract_default_is_causal_without_inventing_source_timestamp():
    cutoff = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
    complete = {name: cutoff.isoformat() for name in (
        "player_metrics", "serve_metrics_a", "serve_metrics_b", "event_state", "event_format", "event_surface")}
    complete["serve_metrics_a"] = None
    result = _validate_feature_availability(cutoff, complete, {"serve_metrics_a"})
    assert result["certified"] is True
    assert result["contract_defaulted_sources"] == ["serve_metrics_a"]
    assert result["missing_sources"] == []


def test_event_format_is_mandatory_and_cannot_be_contract_defaulted():
    cutoff = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
    sources = {name: cutoff.isoformat() for name in (
        "player_metrics", "serve_metrics_a", "serve_metrics_b", "event_state", "event_surface")}
    result = _validate_feature_availability(cutoff, sources, {"event_format"})
    assert result["certified"] is False
    assert result["missing_sources"] == ["event_format"]


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


def test_walk_forward_split_is_shared_and_excludes_undated_matches(tmp_path, monkeypatch):
    manifest_path = tmp_path / "manifest.json"
    monkeypatch.setenv("TENNIS_TRAINING_MANIFEST_PATH", str(manifest_path))
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    ids = [UUID(int=value) for value in range(1, 31)]
    event_times = {event_id: start + timedelta(days=index) for index, event_id in enumerate(ids)}
    event_times.pop(ids[-1])
    rows = {
        "point": {(event_id, index % 2) for index, event_id in enumerate(ids[:10])},
        "game": {(event_id, index % 2) for index, event_id in enumerate(ids[:20])},
        "set": {(event_id, index % 2) for index, event_id in enumerate(ids[:25])},
        "match": {(event_id, index % 2) for index, event_id in enumerate(ids[:29])},
    }
    report, metadata = _walk_forward_admission(rows, event_times)
    assert metadata["matches"] == 29
    assert metadata["holdout_quality_metrics_opened"] is False
    for target in rows:
        assert report[target]["manifest_matches"] == 29
        assert report[target]["folds"][0]["train_candidates"]["candidate_matches"] == report["point"]["folds"][0]["train_candidates"]["candidate_matches"]
    assert ids[-1] not in [UUID(value) for value in __import__("json").loads(manifest_path.read_text())["ordered_event_ids"]]


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

    assert manifest["manifest_version"] == "tennis_shared_walk_forward_v2"
    assert manifest["training_cutoff_policy"] == "target_label_available_at_or_before_earliest_validation_forecast"
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
