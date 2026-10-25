from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.canonical_player_aliases import (
    _alias_assignment_is_safe,
    adjudicate_candidate_player_alias,
    reassign_confirmed_player_alias,
    resolve_confirmed_player_aliases,
)


def test_provider_can_have_multiple_ids_for_the_same_canonical_player():
    canonical_id = uuid4()
    assert _alias_assignment_is_safe([], canonical_id)
    assert _alias_assignment_is_safe([
        SimpleNamespace(status="candidate", canonical_player_id=canonical_id),
    ], canonical_id)
    assert not _alias_assignment_is_safe([
        SimpleNamespace(status="confirmed", canonical_player_id=uuid4()),
    ], canonical_id)


def test_conflicting_provider_id_candidates_are_not_auto_promoted():
    canonical_id = uuid4()
    other_id = uuid4()
    rows = [
        SimpleNamespace(status="candidate", canonical_player_id=canonical_id),
        SimpleNamespace(status="candidate", canonical_player_id=other_id),
    ]
    assert not _alias_assignment_is_safe(rows, canonical_id)


def test_existing_confirmed_provider_id_cannot_move_to_another_player():
    canonical_id = uuid4()
    assigned_id = uuid4()
    assert _alias_assignment_is_safe([
        SimpleNamespace(status="confirmed", canonical_player_id=assigned_id),
    ], assigned_id)
    assert not _alias_assignment_is_safe([
        SimpleNamespace(status="confirmed", canonical_player_id=assigned_id),
    ], canonical_id)


def test_manual_alias_adjudication_is_audited_and_requires_exact_candidate_set():
    from db.connection import SessionLocal
    from db.models.external_tennis import (
        ExtTennisCanonicalPlayer,
        ExtTennisCanonicalPlayerAlias,
        ExtTennisCanonicalPlayerAliasDecision,
    )

    chosen_id, rejected_id = uuid4(), uuid4()
    source_id = f"alias-test-{uuid4()}"
    session = SessionLocal()
    try:
        session.add_all([
            ExtTennisCanonicalPlayer(id=chosen_id, display_name="Reviewed Player", gender="m",
                                     surname_key="reviewed player", initial="r", source_ids={}),
            ExtTennisCanonicalPlayer(id=rejected_id, display_name="Other Candidate", gender="m",
                                     surname_key="other candidate", initial="o", source_ids={}),
            ExtTennisCanonicalPlayerAlias(source="test_source", source_player_id=source_id,
                                          canonical_player_id=chosen_id, status="candidate",
                                          evidence_json={"method": "test_seed"}),
            ExtTennisCanonicalPlayerAlias(source="test_source", source_player_id=source_id,
                                          canonical_player_id=rejected_id, status="candidate",
                                          evidence_json={"method": "test_seed"}),
        ])
        session.flush()
        with pytest.raises(ValueError, match="changed since review"):
            adjudicate_candidate_player_alias(
                session, source="test_source", source_player_id=source_id,
                canonical_player_id=chosen_id, expected_candidate_ids=[chosen_id, uuid4()],
                rationale="reviewed", evidence={"independent_matches": 3}, decided_by="test",
            )
        decision = adjudicate_candidate_player_alias(
            session, source="test_source", source_player_id=source_id,
            canonical_player_id=chosen_id, expected_candidate_ids=[chosen_id, rejected_id],
            rationale="Independent source matches confirm the identity.",
            evidence={"independent_matches": 3, "sources": ["test_a", "test_b"]},
            decided_by="test",
        )
        rows = session.query(ExtTennisCanonicalPlayerAlias).filter_by(
            source="test_source", source_player_id=source_id
        ).all()
        assert {str(row.canonical_player_id): row.status for row in rows} == {
            str(chosen_id): "confirmed", str(rejected_id): "rejected",
        }
        assert resolve_confirmed_player_aliases(
            session, source="test_source", source_player_ids=[source_id]
        )[source_id].canonical_player_id == chosen_id
        assert session.get(ExtTennisCanonicalPlayerAliasDecision, decision.id) is decision
        assert decision.rejected_candidate_ids == [str(rejected_id)]
        session.rollback()
    finally:
        session.close()


def test_manual_alias_adjudication_can_select_an_existing_external_canonical_identity():
    from db.connection import SessionLocal
    from db.models.external_tennis import (
        ExtTennisCanonicalPlayer,
        ExtTennisCanonicalPlayerAlias,
        ExtTennisCanonicalPlayerAliasDecision,
    )

    candidate_a, candidate_b, established = uuid4(), uuid4(), uuid4()
    source_id = f"alias-target-test-{uuid4()}"
    session = SessionLocal()
    try:
        session.add_all([
            ExtTennisCanonicalPlayer(id=candidate_a, display_name="Legacy A", gender="unknown",
                                     surname_key="legacy a", initial="l", source_ids={}),
            ExtTennisCanonicalPlayer(id=candidate_b, display_name="Legacy B", gender="unknown",
                                     surname_key="legacy b", initial="l", source_ids={}),
            ExtTennisCanonicalPlayer(id=established, display_name="Established Full Name", gender="m",
                                     surname_key="established full name", initial="e",
                                     source_ids={"tml": "confirmed-id"}),
            ExtTennisCanonicalPlayerAlias(source="test_source", source_player_id=source_id,
                                          canonical_player_id=candidate_a, status="candidate",
                                          evidence_json={"method": "test_seed"}),
            ExtTennisCanonicalPlayerAlias(source="test_source", source_player_id=source_id,
                                          canonical_player_id=candidate_b, status="candidate",
                                          evidence_json={"method": "test_seed"}),
        ])
        session.flush()
        decision = adjudicate_candidate_player_alias(
            session, source="test_source", source_player_id=source_id,
            canonical_player_id=established,
            expected_candidate_ids=[candidate_a, candidate_b],
            rationale="Two independent source match records establish the existing identity.",
            evidence={"exact_source_matches": ["api:1", "te:2"],
                      "independent_sources": ["api_tennis", "tennis_explorer", "tml"]},
            decided_by="test",
        )
        rows = session.query(ExtTennisCanonicalPlayerAlias).filter_by(
            source="test_source", source_player_id=source_id
        ).all()
        assert {str(row.canonical_player_id): row.status for row in rows} == {
            str(candidate_a): "rejected", str(candidate_b): "rejected",
            str(established): "confirmed",
        }
        assert resolve_confirmed_player_aliases(
            session, source="test_source", source_player_ids=[source_id]
        )[source_id].canonical_player_id == established
        assert decision.rejected_candidate_ids == sorted([str(candidate_a), str(candidate_b)])
        assert session.get(ExtTennisCanonicalPlayerAliasDecision, decision.id) is decision
        session.rollback()
    finally:
        session.close()


def test_reviewed_alias_reassignment_is_audited_and_fences_expected_owner():
    from db.connection import SessionLocal
    from db.models.external_tennis import (
        ExtTennisCanonicalPlayer,
        ExtTennisCanonicalPlayerAlias,
        ExtTennisCanonicalPlayerAliasDecision,
    )

    old_id, new_id = uuid4(), uuid4()
    source_id = f"reassignment-test-{uuid4()}"
    session = SessionLocal()
    try:
        session.add_all([
            ExtTennisCanonicalPlayer(id=old_id, display_name="API Placeholder", gender="unknown",
                                     surname_key="placeholder", initial="p", source_ids={}),
            ExtTennisCanonicalPlayer(id=new_id, display_name="Verified Full Name", gender="m",
                                     surname_key="name", initial="v", source_ids={"tml": "test"}),
            ExtTennisCanonicalPlayerAlias(source="test_source", source_player_id=source_id,
                                          canonical_player_id=old_id, status="confirmed",
                                          evidence_json={"method": "legacy_alias"}),
        ])
        session.flush()
        with pytest.raises(ValueError, match="owner changed"):
            reassign_confirmed_player_alias(
                session, source="test_source", source_player_id=source_id,
                expected_current_owner_id=uuid4(), canonical_player_id=new_id,
                rationale="reviewed", evidence={"matches": ["a", "b"]}, decided_by="test",
            )
        decision = reassign_confirmed_player_alias(
            session, source="test_source", source_player_id=source_id,
            expected_current_owner_id=old_id, canonical_player_id=new_id,
            rationale="Two independent cross-source matches verify the full identity.",
            evidence={"matches": ["a", "b"], "independent_sources": ["api", "te"]},
            decided_by="test",
        )
        aliases = resolve_confirmed_player_aliases(
            session, source="test_source", source_player_ids=[source_id]
        )
        assert aliases[source_id].canonical_player_id == new_id
        rows = session.query(ExtTennisCanonicalPlayerAlias).filter_by(
            source="test_source", source_player_id=source_id
        ).all()
        assert {str(row.canonical_player_id): row.status for row in rows} == {
            str(old_id): "rejected", str(new_id): "confirmed",
        }
        assert session.get(ExtTennisCanonicalPlayerAliasDecision, decision.id) is decision
        session.rollback()
    finally:
        session.close()
