from datetime import datetime, timedelta, timezone

from app.external_tennis_ingestion import build_observation_envelope


UTC = timezone.utc


def _envelope(*, available_at, result="6-4 6-3", parser_version="flashscore_v1", **overrides):
    kwargs = {
        "source": "flashscore",
        "external_id": "source-match-123",
        "source_url": "https://example.test/player/first/results",
        "parser_version": parser_version,
        "available_at": available_at,
        "source_content_hash": "profile-document-sha256",
        "source_document_ref": "raw/profile-first.json",
        "raw_fields": {"score": result},
        "normalized_fields": {"score": result, "winner_name": "Player A", "loser_name": "Player B"},
        "required_fields": ("score", "winner_name", "loser_name"),
        "hash_source_content": False,
    }
    kwargs.update(overrides)
    return build_observation_envelope(**kwargs)


def test_same_flashscore_match_from_both_profiles_is_idempotent():
    first = _envelope(available_at=datetime(2026, 10, 25, tzinfo=UTC))
    second = _envelope(
        available_at=datetime(2026, 10, 25, 0, 5, tzinfo=UTC),
        source_url="https://example.test/player/second/results",
        source_document_ref="raw/profile-second.json",
        source_content_hash="other-profile-document-sha256",
    )

    assert first["observation_hash"] == second["observation_hash"]
    assert first["field_provenance"]["score"]["source_document_ref"] != second["field_provenance"]["score"]["source_document_ref"]


def test_result_correction_a_b_a_and_parser_revision_have_stable_versions():
    first_time = datetime(2026, 10, 25, tzinfo=UTC)
    a1 = _envelope(available_at=first_time)
    b = _envelope(available_at=first_time + timedelta(minutes=1), result="6-3 6-4")
    a2 = _envelope(available_at=first_time + timedelta(minutes=2))
    reparsed = _envelope(available_at=first_time, parser_version="flashscore_v2")

    assert a1["observation_hash"] != b["observation_hash"]
    assert a1["observation_hash"] == a2["observation_hash"]
    assert a1["observation_hash"] != reparsed["observation_hash"]


def test_missing_required_values_are_partial_and_field_provenance_names_the_source():
    item = build_observation_envelope(
        source="tml", external_id="tml-123", source_url="https://example.test/season.csv",
        parser_version="tml_v1", available_at=datetime(2026, 10, 25, tzinfo=UTC),
        source_content_hash="document-hash", source_document_ref="raw/season.csv",
        raw_fields={"winner_name": "Player A"},
        normalized_fields={"winner_name": "Player A", "score": None},
        required_fields=("winner_name", "score"),
    )

    assert item["ingestion_status"] == "partial"
    assert item["field_provenance"]["winner_name"]["source_field"] == "winner_name"
    assert item["field_provenance"]["score"]["status"] == "missing"
    assert item["field_provenance"]["score"]["required"] is True
