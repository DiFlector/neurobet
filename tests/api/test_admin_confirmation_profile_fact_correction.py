import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from app.admin_confirmations import _reconcile_player_facts_after_identity_decision
from db.connection import SessionLocal
from db.models import (
    Participant, Sport, TennisPlayerMetricSnapshot, TennisPlayerProfile, TennisPlayerResultFact,
)


def test_rejecting_a_current_identity_invalidates_its_facts_and_preserves_as_of_history():
    participant_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        if session.get(Sport, "tennis") is None:
            session.add(Sport(code="tennis", name="Tennis", is_primary=True))
            session.flush()
        participant = Participant(id=participant_id, sport_code="tennis", canonical_name="Correction Test Player")
        session.add(participant)
        session.flush()
        profile = TennisPlayerProfile(
            participant_id=participant_id, metrics_version=1, metrics_json={"overall": {"matches": 2}},
            history_match_count=2, source_identities={"flashscore": "wrong-profile"},
        )
        session.add(profile)
        old_time = now - timedelta(days=1)
        wrong = TennisPlayerResultFact(
            participant_id=participant_id, source="flashscore", source_match_id="wrong-match",
            result_hash="a" * 64, played_at=old_time, available_at=old_time, won=True,
            source_payload={"source_player_id": "wrong-profile", "source_match_id": "wrong-match"},
        )
        archive = TennisPlayerResultFact(
            participant_id=participant_id, source="canonical_archive", source_match_id="archive-match",
            result_hash="b" * 64, played_at=old_time, available_at=old_time, won=False,
            source_payload={"source_match_id": "archive-match"},
        )
        session.add_all([wrong, archive])
        session.add(TennisPlayerMetricSnapshot(
            participant_id=participant_id, metrics_version=1, available_at=old_time,
            history_digest=hashlib.sha256(b"old").hexdigest(), metrics_json={"overall": {"matches": 2}},
        ))
        session.flush()

        changed = _reconcile_player_facts_after_identity_decision(
            session, profile, participant_id, "wrong-profile", accept=False, now=now,
        )
        session.flush()

        assert changed == 1
        assert profile.metrics_version == 2
        assert profile.history_match_count == 1
        assert profile.metrics_json["overall"]["matches"] == 1
        assert profile.metrics_json["overall"]["wins"] == 0
        assert session.query(TennisPlayerMetricSnapshot).filter_by(
            participant_id=participant_id, metrics_version=1,
        ).one().available_at == old_time
        current_wrong = session.query(TennisPlayerResultFact).filter_by(
            participant_id=participant_id, source_match_id="wrong-match",
        ).order_by(TennisPlayerResultFact.available_at.desc()).first()
        assert current_wrong.valid is False
        assert current_wrong.source_payload["invalidated_reason"] == "admin_rejected_profile_identity"
        session.rollback()
