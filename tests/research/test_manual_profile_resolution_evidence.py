from app.main import _manual_profile_resolution_evidence


def test_manual_resolution_preserves_review_sources():
    candidate = {
        "name": "Yidi Yang",
        "profile_url": "https://www.flashscore.com/player/yang-yidi/ziXD7L4M/",
        "manual_evidence": {
            "decision": "confirm",
            "sources": ["https://www.wtatennis.com/players/324323/yidi-yang/stats"],
        },
    }

    evidence = _manual_profile_resolution_evidence("Ян Иди", candidate)

    assert evidence["method"] == "manual_confirmation"
    assert evidence["participant_name"] == "Ян Иди"
    assert evidence["candidate_name"] == "Yidi Yang"
    assert evidence["manual_evidence"] == candidate["manual_evidence"]


def test_manual_resolution_without_extra_sources_keeps_legacy_shape():
    evidence = _manual_profile_resolution_evidence(
        "Иванов Иван", {"name": "Ivan Ivanov", "profile_url": "https://example.test/profile"}
    )

    assert evidence == {
        "method": "manual_confirmation",
        "participant_name": "Иванов Иван",
        "candidate_name": "Ivan Ivanov",
        "profile_url": "https://example.test/profile",
    }
