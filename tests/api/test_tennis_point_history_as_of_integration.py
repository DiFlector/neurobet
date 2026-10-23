"""PostgreSQL integration check for immutable point-history as_of reads."""
from datetime import datetime, timedelta, timezone
import uuid

from app.main import get_event_point_history
from db.connection import SessionLocal
from db.models.events import Event
from db.models.external_tennis import (
    TennisPointDocument,
    TennisPointHistoryVersion,
    TennisPointMatchLink,
)


def test_late_pbp_correction_and_link_cancellation_do_not_change_old_as_of():
    now = datetime.now(timezone.utc)
    source_match_id = f"__asof_test_{uuid.uuid4()}"
    document_id = uuid.uuid4()
    event_id = None

    with SessionLocal() as session:
        event = session.query(Event).filter(Event.sport_code == "tennis").first()
        assert event is not None, "integration DB needs at least one tennis event"
        event_id = event.id
        reassigned_event = session.query(Event).filter(
            Event.sport_code == "tennis", Event.id != event_id
        ).first()
        assert reassigned_event is not None, "integration DB needs two tennis events for reassignment coverage"
        reassigned_event_id = reassigned_event.id
        session.add(TennisPointDocument(
            id=document_id,
            source="flashscore",
            source_match_id=source_match_id,
            source_url=f"https://www.flashscore.com/match/{source_match_id}/",
            content_hash=f"test-{uuid.uuid4().hex}",
            parser_version="asof-integration-test",
            retrieved_at=now - timedelta(minutes=3),
            parsed_at=now - timedelta(minutes=3),
            available_at=now - timedelta(minutes=3),
            availability_status="available",
            retrospective=True,
            parse_status="parsed",
            quality_json={"set_number": 1},
        ))
        session.add(TennisPointHistoryVersion(
            document_id=document_id,
            source_match_id=source_match_id,
            fonbet_event_id=event_id,
            parser_version="asof-integration-test",
            retrieved_at=now - timedelta(minutes=3),
            parsed_at=now - timedelta(minutes=3),
            available_at=now - timedelta(minutes=3),
            availability_status="available",
            events_json=[{"set": 1, "game": 1, "point_score": "15:0", "winner": "a"}],
            quality_json={"set_number": 1},
            retrospective=True,
        ))
        session.add(TennisPointMatchLink(
            source_match_id=source_match_id,
            fonbet_event_id=event_id,
            status="matched",
            confidence=1.0,
            evidence_json={"integration_test": True},
            available_at=now - timedelta(minutes=3),
        ))
        session.commit()

    try:
        fixed_as_of = now - timedelta(minutes=2)
        old_read = get_event_point_history(str(event_id), as_of=fixed_as_of)
        assert old_read["total"] == 1
        assert old_read["sequence"][0]["point_score"] == "15:0"

        # A -> B -> A is an append-only correction history. Each fixed as_of
        # must see the version that had actually become available then.
        corrected_at = now - timedelta(seconds=90)
        restored_at = now - timedelta(seconds=60)
        with SessionLocal() as session:
            for parser_version, point_score, available_at in (
                ("asof-integration-test-B", "30:0", corrected_at),
                ("asof-integration-test-A-restored", "15:0", restored_at),
            ):
                session.add(TennisPointHistoryVersion(
                    document_id=document_id,
                    source_match_id=source_match_id,
                    fonbet_event_id=event_id,
                    parser_version=parser_version,
                    retrieved_at=available_at,
                    parsed_at=available_at,
                    available_at=available_at,
                    availability_status="available",
                    events_json=[{"set": 1, "game": 1, "point_score": point_score, "winner": "a"}],
                    quality_json={"set_number": 1, "set_tab_confirmed": True,
                                  "collection_status": "parsed"},
                    retrospective=True,
                ))
            session.commit()
        b_read = get_event_point_history(str(event_id), as_of=now - timedelta(seconds=75))
        restored_read = get_event_point_history(str(event_id), as_of=now - timedelta(seconds=30))
        assert b_read["sequence"][0]["point_score"] == "30:0"
        assert restored_read["sequence"][0]["point_score"] == "15:0"
        assert get_event_point_history(str(event_id), as_of=fixed_as_of) == old_read

        with SessionLocal() as session:
            session.add(TennisPointHistoryVersion(
                document_id=document_id,
                source_match_id=source_match_id,
                fonbet_event_id=event_id,
                parser_version="asof-integration-test-correction",
                retrieved_at=now,
                parsed_at=now,
                available_at=now,
                availability_status="available",
                events_json=[],
                quality_json={"set_number": 1, "set_tab_confirmed": False,
                              "gaps": ["point_by_point_markup_missing", "HISTORY_EMPTY_OR_UNRECOGNIZED"]},
                retrospective=True,
            ))
            session.commit()

        assert get_event_point_history(str(event_id), as_of=fixed_as_of) == old_read
        non_authoritative_read = get_event_point_history(str(event_id), as_of=now + timedelta(seconds=1))
        assert non_authoritative_read["sequence"][0]["point_score"] == "15:0"

        # Reassigning the same source match must affect only reads at or after
        # the new link's availability time. A fixed earlier as_of remains
        # stable, the old Fonbet event loses the source, and the new event
        # receives its source history at its own point in time.
        reassigned_at = now + timedelta(seconds=2)
        with SessionLocal() as session:
            session.add(TennisPointMatchLink(
                source_match_id=source_match_id,
                fonbet_event_id=reassigned_event_id,
                status="matched",
                confidence=1.0,
                evidence_json={"integration_test": True, "reassigned": True},
                available_at=reassigned_at,
            ))
            session.commit()
        assert get_event_point_history(str(event_id), as_of=fixed_as_of) == old_read
        old_event_after_reassignment = get_event_point_history(
            str(event_id), as_of=reassigned_at + timedelta(seconds=1)
        )
        new_event_after_reassignment = get_event_point_history(
            str(reassigned_event_id), as_of=reassigned_at + timedelta(seconds=1)
        )
        assert old_event_after_reassignment["total"] == 0
        assert old_event_after_reassignment["source_match_id"] is None
        assert new_event_after_reassignment["total"] == 1
        assert new_event_after_reassignment["sequence"][0]["point_score"] == "15:0"

        # A source can deliberately tombstone a set only with an explicit
        # correction marker. An ordinary missing PBP page above must not.
        correction_at = now + timedelta(seconds=4)
        with SessionLocal() as session:
            session.add(TennisPointHistoryVersion(
                document_id=document_id,
                source_match_id=source_match_id,
                fonbet_event_id=event_id,
                parser_version="asof-integration-test-empty-correction",
                retrieved_at=correction_at,
                parsed_at=correction_at,
                available_at=correction_at,
                availability_status="available",
                events_json=[],
                quality_json={"set_number": 1, "explicit_empty_correction": True},
                retrospective=True,
            ))
            session.add(TennisPointMatchLink(
                source_match_id=source_match_id,
                fonbet_event_id=reassigned_event_id,
                status="unmatched",
                confidence=0.0,
                evidence_json={"integration_test": True, "cancelled": True},
                available_at=correction_at,
            ))
            session.commit()
        new_read = get_event_point_history(
            str(reassigned_event_id), as_of=correction_at + timedelta(seconds=1)
        )
        assert new_read["total"] == 0
    finally:
        with SessionLocal() as session:
            session.query(TennisPointMatchLink).filter_by(source_match_id=source_match_id).delete()
            session.query(TennisPointHistoryVersion).filter_by(source_match_id=source_match_id).delete()
            session.query(TennisPointDocument).filter_by(source_match_id=source_match_id).delete()
            session.commit()
