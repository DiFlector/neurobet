"""PostgreSQL integration check for metric provenance across identity rebinds."""
from datetime import datetime, timedelta, timezone
import uuid

from app.player_metrics import load_player_metric
from db.connection import SessionLocal
from db.models import (
    Participant,
    TennisPlayerIdentityLink,
    TennisPlayerMetricSnapshot,
    TennisPlayerProfile,
)


def _metrics(wins: int, losses: int) -> dict:
    return {
        "overall": {"matches": wins + losses, "wins": wins, "losses": losses},
        "recent": {"last_10": {"matches": wins + losses, "wins": wins}},
    }


def test_reassigned_profile_metrics_remain_point_in_time():
    start = datetime.now(timezone.utc) - timedelta(minutes=10)
    participant_id = uuid.uuid4()
    source_a = f"__identity_asof_A_{uuid.uuid4().hex}"
    source_b = f"__identity_asof_B_{uuid.uuid4().hex}"
    first = _metrics(8, 4)
    second = _metrics(2, 5)

    with SessionLocal() as session:
        participant = Participant(
            id=participant_id, sport_code="tennis",
            canonical_name=f"__identity_asof_{uuid.uuid4().hex}", is_team=False,
        )
        session.add(participant)
        session.flush()
        session.add(TennisPlayerProfile(
            participant_id=participant_id,
            source_identities={"flashscore": source_b},
            profile_url=f"https://www.flashscore.com/player/{source_b}/",
            sync_status="available", metrics_version=2, metrics_json=second,
            metrics_available_at=start + timedelta(minutes=9),
        ))
        session.add_all([
            TennisPlayerIdentityLink(
                participant_id=participant_id, source="flashscore", source_player_id=source_a,
                status="confirmed", evidence_json={"test": True}, available_at=start,
            ),
            TennisPlayerMetricSnapshot(
                participant_id=participant_id, source_player_id=source_a, metrics_version=1,
                available_at=start + timedelta(minutes=1), source_watermark=start,
                history_digest=uuid.uuid4().hex, metrics_json=first,
            ),
            TennisPlayerIdentityLink(
                participant_id=participant_id, source="flashscore", source_player_id=source_a,
                status="revoked", evidence_json={"test": True}, available_at=start + timedelta(minutes=5),
            ),
            TennisPlayerIdentityLink(
                participant_id=participant_id, source="flashscore", source_player_id=source_b,
                status="confirmed", evidence_json={"test": True},
                available_at=start + timedelta(minutes=5, microseconds=1),
            ),
            TennisPlayerMetricSnapshot(
                participant_id=participant_id, source_player_id=source_b, metrics_version=2,
                available_at=start + timedelta(minutes=9), source_watermark=start,
                history_digest=uuid.uuid4().hex, metrics_json=second,
            ),
        ])
        session.commit()

    try:
        with SessionLocal() as session:
            before_identity = load_player_metric(session, participant_id, start - timedelta(seconds=1))
            before_reassignment = load_player_metric(session, participant_id, start + timedelta(minutes=2))
            before_new_snapshot = load_player_metric(session, participant_id, start + timedelta(minutes=6))
            after_reassignment = load_player_metric(session, participant_id, start + timedelta(minutes=10))
            after_freshness_window = load_player_metric(session, participant_id, start + timedelta(days=8))
        assert before_identity is None
        assert before_reassignment["metrics"]["overall"]["wins"] == 8
        assert before_reassignment["metrics_version"] == 1
        assert before_new_snapshot is None
        assert after_reassignment["metrics"]["overall"]["wins"] == 2
        assert after_freshness_window is None
        assert after_reassignment["metrics_version"] == 2
    finally:
        with SessionLocal() as session:
            session.query(TennisPlayerMetricSnapshot).filter_by(participant_id=participant_id).delete()
            session.query(TennisPlayerIdentityLink).filter_by(participant_id=participant_id).delete()
            session.query(TennisPlayerProfile).filter_by(participant_id=participant_id).delete()
            session.query(Participant).filter_by(id=participant_id).delete()
            session.commit()
