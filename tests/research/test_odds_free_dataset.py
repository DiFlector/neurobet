from datetime import datetime, timezone
from hashlib import sha256
import importlib.util
from pathlib import Path
import sys
import types
import pytest


_APP_DIR = Path(__file__).resolve().parents[2] / "services" / "neural" / "app"
_PACKAGE_NAME = "neurobet_neural_app"
_package = types.ModuleType(_PACKAGE_NAME)
_package.__path__ = [str(_APP_DIR)]
sys.modules.setdefault(_PACKAGE_NAME, _package)
_forecast_spec = importlib.util.spec_from_file_location(
    f"{_PACKAGE_NAME}.tennis_forecast", _APP_DIR / "tennis_forecast.py"
)
_forecast = importlib.util.module_from_spec(_forecast_spec)
sys.modules[_forecast_spec.name] = _forecast
_forecast_spec.loader.exec_module(_forecast)
_spec = importlib.util.spec_from_file_location(
    f"{_PACKAGE_NAME}.odds_free_dataset", _APP_DIR / "odds_free_dataset.py"
)
_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _module
_spec.loader.exec_module(_module)

build_odds_free_dataset = _module.build_odds_free_dataset
build_odds_free_dataset_example = _module.build_odds_free_dataset_example
ODDS_FREE_FEATURE_NAMES = _forecast.ODDS_FREE_FEATURE_NAMES
ODDS_FREE_FEATURE_SOURCE_GROUPS = _forecast.ODDS_FREE_FEATURE_SOURCE_GROUPS
ODDS_FREE_FEATURE_NAMES_V1 = _forecast.ODDS_FREE_FEATURE_NAMES_V1

AS_OF = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)


def _valid_example():
    timestamp = "2026-10-26T11:00:00+00:00"
    source_evidence = {}
    for name in ODDS_FREE_FEATURE_SOURCE_GROUPS:
        digest = sha256(name.encode()).hexdigest()
        source_evidence[name] = {
            "source": f"fixture_{name}", "snapshot_id": f"{name}-1",
            "available_at": timestamp, "raw_hash": digest,
            "raw_object_key": f"fonbet/tennis/2026-10-26/{digest[:4]}/{digest}.json",
        }
    feature_values = {name: 0 for name in ODDS_FREE_FEATURE_NAMES}
    feature_values.update(serve_probability_a=0.62, serve_probability_b=0.62,
                          serve_sample_weight_a=0.5, serve_sample_weight_b=0.5)
    return {
        "target": "match",
        "fonbet_event_id": "event-1",
        "snapshot_id": "snapshot-1",
        "snapshot_observed_at": timestamp,
        "label": 1,
        "label_available_at": "2026-10-26T11:30:00+00:00",
        "feature_values": feature_values,
        "feature_availability": {
            "source_available_at": {name: timestamp for name in ODDS_FREE_FEATURE_SOURCE_GROUPS},
            "source_evidence": source_evidence,
            "source_resolution": {name: "observed" for name in ODDS_FREE_FEATURE_SOURCE_GROUPS},
        },
        "source_match_id": "source-1",
        "point_version_id": "version-1",
    }


def _as_v1(example):
    example["feature_availability"]["contract_version"] = "tennis_odds_free_v1"
    example["feature_values"] = {name: 0 for name in ODDS_FREE_FEATURE_NAMES_V1}
    evidence = example["feature_availability"]["source_evidence"]["event_format"]
    example["feature_availability"]["source_available_at"]["event_surface"] = evidence["available_at"]
    example["feature_availability"]["source_evidence"]["event_surface"] = dict(evidence)
    example["feature_availability"]["source_resolution"]["event_surface"] = "observed"
    return example


def test_odds_free_dataset_accepts_only_full_allowlisted_pit_example():
    row = build_odds_free_dataset_example(_valid_example(), dataset_as_of=AS_OF)

    assert row["status"] == "accepted", row["rejection_reasons"]
    assert row["feature_names"] == list(ODDS_FREE_FEATURE_NAMES)
    assert row["feature_values"] == [float(_valid_example()["feature_values"][name])
                                     for name in ODDS_FREE_FEATURE_NAMES]
    assert row["provenance"]["point_version_id"] == "version-1"
    assert row["rejection_reasons"] == []


def test_odds_free_v2_accepts_unknown_format_and_drops_surface_contract():
    example = _valid_example()
    names = set(ODDS_FREE_FEATURE_NAMES)
    example["feature_values"] = {name: 0 for name in names}
    example["feature_values"].update({"sets_to_win": 0, "match_format_known": 0})
    example["feature_availability"]["contract_version"] = "tennis_odds_free_v2"
    example["feature_availability"]["source_available_at"].pop("event_format")
    example["feature_availability"]["source_evidence"].pop("event_format")
    example["feature_availability"]["source_resolution"].pop("event_format")
    example["feature_values"]["surface_hard"] = 1

    row = build_odds_free_dataset_example(example, dataset_as_of=AS_OF)

    assert row["status"] == "rejected"
    assert "feature_not_in_allowlist:surface_hard" in row["rejection_reasons"]

    example["feature_values"].pop("surface_hard")
    row = build_odds_free_dataset_example(example, dataset_as_of=AS_OF)
    assert row["status"] == "accepted"
    assert row["feature_pit_certified"] is True
    assert len(row["feature_values"]) == 26


def test_odds_free_v2_requires_format_evidence_only_when_known():
    example = _valid_example()
    example["feature_availability"]["contract_version"] = "tennis_odds_free_v2"
    example["feature_values"] = {name: 0 for name in ODDS_FREE_FEATURE_NAMES}
    example["feature_values"].update({"sets_to_win": 2, "match_format_known": 1})
    example["feature_availability"]["source_available_at"].pop("event_surface", None)
    example["feature_availability"]["source_evidence"].pop("event_surface", None)
    example["feature_availability"]["source_resolution"].pop("event_surface", None)

    row = build_odds_free_dataset_example(example, dataset_as_of=AS_OF)
    assert row["status"] == "accepted"

    example["feature_availability"]["source_evidence"].pop("event_format")
    row = build_odds_free_dataset_example(example, dataset_as_of=AS_OF)
    assert row["status"] == "rejected"
    assert "pit_evidence_missing:event_format" in row["rejection_reasons"]


def test_confirmed_label_without_receipt_time_passes_feature_pit_but_is_not_chronology_eligible():
    example = _valid_example()
    example["feature_availability"]["contract_version"] = "tennis_odds_free_v2"
    example["feature_values"] = {name: 0 for name in ODDS_FREE_FEATURE_NAMES}
    example["label_available_at"] = None
    example["feature_availability"]["source_available_at"].pop("event_surface", None)
    example["feature_availability"]["source_evidence"].pop("event_surface", None)
    example["feature_availability"]["source_resolution"].pop("event_surface", None)

    row = build_odds_free_dataset_example(example, dataset_as_of=AS_OF)

    assert row["status"] == "accepted"
    assert row["feature_pit_certified"] is True
    assert row["label_timing_status"] == "unknown"
    assert row["label_availability_time_known"] is False
    assert row["training_chronology"] == "requires_fold_boundary_evaluation"


def test_odds_free_v1_feature_vector_contract_remains_loadable():
    example = _valid_example()
    example["feature_availability"]["contract_version"] = "tennis_odds_free_v1"
    example["feature_values"] = {name: 0 for name in ODDS_FREE_FEATURE_NAMES_V1}
    example["feature_values"]["sets_to_win"] = 2
    example["feature_availability"]["source_available_at"]["event_surface"] = "2026-10-26T11:00:00+00:00"
    example["feature_availability"]["source_evidence"]["event_surface"] = example["feature_availability"]["source_evidence"]["event_format"]
    example["feature_availability"]["source_resolution"]["event_surface"] = "observed"

    row = build_odds_free_dataset_example(example, dataset_as_of=AS_OF)
    assert row["status"] == "accepted"
    assert row["feature_contract"] == "tennis_odds_free_v1"
    assert len(row["feature_values"]) == 30


def test_odds_free_dataset_rejects_missing_and_future_feature_times_and_never_fills_priors():
    example = _as_v1(_valid_example())
    example["feature_availability"]["source_available_at"].pop("event_format")
    example["feature_availability"]["source_available_at"]["event_surface"] = "2026-10-26T12:01:00+00:00"
    example["feature_availability"]["contract_defaulted_sources"] = ["serve_metrics_a"]
    example["feature_values"].pop("serve_probability_a")

    row = build_odds_free_dataset_example(example, dataset_as_of=AS_OF)

    assert row["status"] == "rejected"
    assert row["feature_values"] is None
    assert "pit_source_missing:event_format" in row["rejection_reasons"]
    assert "pit_source_after_cutoff:event_surface" in row["rejection_reasons"]
    assert "contract_prior_not_allowed:serve_metrics_a" in row["rejection_reasons"]
    assert "feature_value_missing:serve_probability_a" in row["rejection_reasons"]


def test_odds_free_dataset_keeps_per_example_reasons_and_does_not_certify_partial_input():
    accepted = _valid_example()
    rejected = _valid_example()
    rejected["feature_values"] = {}

    rows, summary = build_odds_free_dataset([accepted, rejected], dataset_as_of=AS_OF)

    assert [row["status"] for row in rows] == ["accepted", "rejected"]
    assert summary["input_examples"] == 2
    assert summary["accepted_examples"] == 1
    assert summary["rejected_examples"] == 1
    assert summary["pit_certified"] is False
    assert summary["training_admission"] == "not_evaluated"
    assert rows[1]["rejection_reasons"]


def test_odds_free_dataset_rejects_label_after_build_cutoff_and_unknown_features():
    example = _valid_example()
    example["label_available_at"] = "2026-10-26T13:00:00+00:00"
    example["feature_values"]["odds_a"] = 1.9

    row = build_odds_free_dataset_example(example, dataset_as_of=AS_OF)

    assert row["status"] == "rejected"
    assert "label_not_yet_available" in row["rejection_reasons"]
    assert "feature_not_in_allowlist:odds_a" in row["rejection_reasons"]


def test_odds_free_dataset_requires_explicit_snapshot_time_and_integer_label():
    example = _valid_example()
    example["snapshot_observed_at"] = None
    example["label"] = 1.0

    row = build_odds_free_dataset_example(example, dataset_as_of=AS_OF)

    assert row["status"] == "rejected"
    assert row["snapshot_observed_at"] is None
    assert "snapshot_timestamp_missing" in row["rejection_reasons"]
    assert "label_missing_or_invalid" in row["rejection_reasons"]


def test_odds_free_dataset_rejects_missing_or_late_source_evidence():
    example = _as_v1(_valid_example())
    example["feature_availability"]["source_evidence"].pop("event_state")
    example["feature_availability"]["source_evidence"]["event_format"]["available_at"] = "2026-10-26T12:01:00+00:00"
    example["feature_availability"]["source_evidence"]["event_surface"].pop("raw_hash")

    row = build_odds_free_dataset_example(example, dataset_as_of=AS_OF)

    assert row["status"] == "rejected"
    assert "pit_evidence_missing:event_state" in row["rejection_reasons"]
    assert "pit_evidence_after_cutoff:event_format" in row["rejection_reasons"]
    assert "pit_evidence_hash_missing:event_surface" in row["rejection_reasons"]


def test_projection_hash_does_not_substitute_for_source_payload_hash():
    example = _as_v1(_valid_example())
    evidence = example["feature_availability"]["source_evidence"]["event_format"]
    evidence.pop("raw_hash")
    evidence["evidence_hash"] = "hash-of-our-projection-envelope"

    row = build_odds_free_dataset_example(example, dataset_as_of=AS_OF)

    assert row["status"] == "rejected"
    assert "pit_evidence_hash_missing:event_format" in row["rejection_reasons"]


def test_event_attribute_requires_retained_source_object_key():
    example = _as_v1(_valid_example())
    evidence = example["feature_availability"]["source_evidence"]["event_format"]
    evidence.pop("raw_object_key")

    row = build_odds_free_dataset_example(example, dataset_as_of=AS_OF)

    assert row["status"] == "rejected"
    assert "pit_evidence_object_missing:event_format" in row["rejection_reasons"]


def test_non_fonbet_event_state_requires_retained_source_object_key():
    example = _valid_example()
    example["feature_availability"]["source_evidence"]["event_state"].pop("raw_object_key")

    row = build_odds_free_dataset_example(example, dataset_as_of=AS_OF)

    assert row["status"] == "rejected"
    assert "pit_evidence_object_missing:event_state" in row["rejection_reasons"]


@pytest.mark.parametrize("source", ["fonbet_event_state_snapshot", "fonbet_archived_live_snapshot"])
def test_fonbet_db_state_is_sufficient_without_raw_archive(source):
    example = _valid_example()
    evidence = example["feature_availability"]["source_evidence"]["event_state"]
    evidence["source"] = source
    evidence.pop("raw_hash")
    evidence.pop("raw_object_key")

    row = build_odds_free_dataset_example(example, dataset_as_of=AS_OF)

    assert row["status"] == "accepted"
    assert row["pit_evidence_policy"] == "tennis_pit_db_state_v2"


@pytest.mark.parametrize("timestamp,accepted", [
    ("2026-10-26T10:59:59+00:00", True),
    ("2026-10-26T11:00:00+00:00", True),
    ("2026-10-26T11:00:01+00:00", False),
    (None, False),
])
def test_db_state_archive_exception_preserves_evidence_timing(timestamp, accepted):
    example = _valid_example()
    example["feature_availability"]["source_evidence"]["event_state"] = {
        "source": "fonbet_event_state_snapshot", "snapshot_id": "state-1",
        "available_at": timestamp,
    }
    row = build_odds_free_dataset_example(example, dataset_as_of=AS_OF)
    assert (row["status"] == "accepted") is accepted


def test_db_state_archive_exception_requires_snapshot_reference():
    example = _valid_example()
    example["feature_availability"]["source_evidence"]["event_state"] = {
        "source": "fonbet_event_state_snapshot", "available_at": "2026-10-26T11:00:00+00:00",
    }
    row = build_odds_free_dataset_example(example, dataset_as_of=AS_OF)
    assert row["status"] == "rejected"
    assert "pit_evidence_object_missing:event_state" in row["rejection_reasons"]


def test_shared_pit_validator_requires_raw_for_non_fonbet_state():
    from contracts.tennis_pit import validate_feature_source_evidence

    example = _valid_example()
    availability = example["feature_availability"]
    availability["source_evidence"]["event_state"].pop("raw_object_key")

    result = validate_feature_source_evidence(
        AS_OF,
        availability["source_available_at"],
        availability["source_evidence"],
        ("event_state",),
    )

    assert result["certified"] is False
    assert result["evidence_object_missing"] == ["event_state"]


def test_source_object_key_must_match_its_content_hash():
    from contracts.tennis_pit import validate_feature_source_evidence

    example = _as_v1(_valid_example())
    availability = example["feature_availability"]
    availability["source_evidence"]["event_format"]["raw_object_key"] = (
        "fonbet/tennis/2026-10-26/deadbeef/deadbeef.json"
    )

    result = validate_feature_source_evidence(
        AS_OF,
        availability["source_available_at"],
        availability["source_evidence"],
        ("event_format",),
    )

    assert result["certified"] is False
    assert result["evidence_object_hash_mismatch"] == ["event_format"]


def test_archived_gzip_provider_source_has_the_same_valid_content_address():
    example = _as_v1(_valid_example())
    example["feature_availability"]["source_evidence"]["event_surface"]["raw_object_key"] += ".gz"
    row = build_odds_free_dataset_example(example, dataset_as_of=AS_OF)
    assert row["status"] == "accepted"


def test_late_identity_cannot_certify_earlier_source_bytes():
    example = _as_v1(_valid_example())
    example["feature_availability"]["source_evidence"]["event_surface"]["identity_available_at"] = "2026-10-26T11:01:00Z"
    row = build_odds_free_dataset_example(example, dataset_as_of=AS_OF)
    assert row["status"] == "rejected"
    assert "pit_evidence_after_cutoff:event_surface" in row["rejection_reasons"]


@pytest.mark.parametrize("unavailable", ["missing", "future", "identity_future", "malformed"])
def test_v2_unknown_player_history_uses_explicit_priors(unavailable):
    example = _valid_example()
    evidence = example["feature_availability"]["source_evidence"]
    valid = dict(evidence["player_metrics"])
    invalid = {} if unavailable == "missing" else dict(valid)
    if unavailable == "future":
        invalid["available_at"] = "2026-10-26T13:00:00+00:00"
    if unavailable == "identity_future":
        invalid["identity_available_at"] = "2026-10-26T13:00:00+00:00"
    if unavailable == "malformed":
        invalid = "not an evidence object"
    evidence["player_metrics"] = {"a": invalid, "b": valid}
    example["feature_values"].update(elo_a=2200, elo_b=1600, elo_diff=600,
        match_count_a=100, win_rate_90d_a=0.9, win_rate_90d_b=0.7)
    row = build_odds_free_dataset_example(example, dataset_as_of=AS_OF)
    assert row["status"] == "accepted", row["rejection_reasons"]
    values = dict(zip(row["feature_names"], row["feature_values"]))
    assert values["elo_a"] == 1500 and values["elo_b"] == 1600
    assert values["elo_diff"] == -100 and values["match_count_a"] == 0
    assert values["player_metrics_missing_a"] == 1
    assert row["player_history_fallback"].keys() == {"a"}
    # Missing player history never fabricates observed serving points.
    example["feature_availability"]["source_evidence"].pop("serve_metrics_a")
    missing_serve = build_odds_free_dataset_example(example, dataset_as_of=AS_OF)
    assert missing_serve["status"] == "accepted"
    assert "a" in missing_serve["serve_history_fallback"]
    assert missing_serve["source_evidence"]["serve_metrics_a"][0]["observation_kind"] == "contract_prior"


def test_v1_does_not_adopt_unknown_player_history_policy():
    example = _as_v1(_valid_example())
    example["feature_availability"]["source_evidence"].pop("player_metrics")
    row = build_odds_free_dataset_example(example, dataset_as_of=AS_OF)
    assert "pit_evidence_missing:player_metrics" in row["rejection_reasons"]


@pytest.mark.parametrize("case", ["missing", "future_source", "late_identity", "zero_sample", "missing_hash"])
def test_v2_missing_serve_is_masked_without_using_unverified_values(case):
    example = _valid_example()
    example["feature_values"].update(serve_probability_a=0.9, serve_probability_b=0.65)
    availability = example["feature_availability"]
    if case == "missing":
        availability["source_evidence"].pop("serve_metrics_a")
    elif case == "future_source":
        availability["source_available_at"]["serve_metrics_a"] = "2026-10-27T00:00:00Z"
    elif case == "late_identity":
        availability["source_evidence"]["serve_metrics_a"]["identity_available_at"] = "2026-10-27T00:00:00Z"
    elif case == "zero_sample":
        example["feature_values"]["serve_sample_weight_a"] = 0
    else:
        availability["source_evidence"]["serve_metrics_a"].pop("raw_hash")
    row = build_odds_free_dataset_example(example, dataset_as_of=AS_OF)
    assert row["status"] == "accepted", row["rejection_reasons"]
    values = dict(zip(row["feature_names"], row["feature_values"]))
    assert values["serve_probability_a"] == 0.62
    assert values["serve_sample_weight_a"] == 0
    assert values["serve_probability_b"] == 0.65
    assert values["serve_sample_weight_b"] == 0.5
    assert set(row["serve_history_fallback"]) == {"a"}


def test_v2_both_serve_groups_absent_remain_valid_with_zero_weights():
    example = _valid_example()
    for group in ("serve_metrics_a", "serve_metrics_b"):
        example["feature_availability"]["source_available_at"].pop(group)
        example["feature_availability"]["source_evidence"].pop(group)
    row = build_odds_free_dataset_example(example, dataset_as_of=AS_OF)
    assert row["feature_pit_certified"] and row["status"] == "accepted"
    assert set(row["serve_history_fallback"]) == {"a", "b"}


def test_v1_missing_serve_still_requires_source_evidence():
    example = _as_v1(_valid_example())
    example["feature_availability"]["source_evidence"].pop("serve_metrics_a")
    row = build_odds_free_dataset_example(example, dataset_as_of=AS_OF)
    assert "pit_evidence_missing:serve_metrics_a" in row["rejection_reasons"]


def test_optional_serve_resolution_is_idempotent():
    example = _valid_example()
    from contracts.tennis_pit import resolve_unknown_serve_history
    first = resolve_unknown_serve_history(AS_OF, {}, {}, example["feature_values"])
    repeated = resolve_unknown_serve_history(AS_OF, first[0], first[1], first[2])
    assert repeated == first


def test_streaming_cli_preserves_eager_rows_and_summary(monkeypatch):
    import io
    import json
    app = types.ModuleType('app')
    monkeypatch.setitem(sys.modules, 'app', app)
    monkeypatch.setitem(sys.modules, 'app.odds_free_dataset', _module)
    script = Path(__file__).resolve().parents[2] / 'scripts/research/build_odds_free_dataset.py'
    spec = importlib.util.spec_from_file_location('streaming_dataset_cli', script)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    examples = [_valid_example(), {**_valid_example(), 'label_available_at': None},
                {**_valid_example(), 'feature_values': {}}]
    eager_rows, eager_summary = build_odds_free_dataset(examples, dataset_as_of=AS_OF)
    output = io.StringIO()
    summary = cli._stream_build(iter(examples), output, dataset_as_of=AS_OF, chunk_size=1)
    assert [json.loads(line) for line in output.getvalue().splitlines()] == eager_rows
    assert summary == eager_summary
    assert cli._stream_build([], io.StringIO(), dataset_as_of=AS_OF) == build_odds_free_dataset([], dataset_as_of=AS_OF)[1]
