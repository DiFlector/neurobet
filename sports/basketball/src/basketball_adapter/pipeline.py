"""Database and TimescaleDB persistence pipeline for Basketball events and snapshots."""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from contracts import (
    Event as EventContract,
    EventState as EventStateContract,
    OddsSnapshot as OddsSnapshotContract,
)
from db.models import (
    Event as DBEvent,
    EventStateSnapshot as DBEventStateSnapshot,
    Market as DBMarket,
    MarketSelection as DBMarketSelection,
    OddsSnapshot as DBOddsSnapshot,
    OddsChangeEvent as DBOddsChangeEvent,
)
from .adapter import BasketballSportAdapter

logger = logging.getLogger("basketball.pipeline")


class BasketballPipeline:
    """Orchestrates normalization and persistence of live basketball events and snapshots."""

    def __init__(self, adapter: Optional[BasketballSportAdapter] = None):
        self.adapter = adapter or BasketballSportAdapter()

    def process_and_persist(
        self,
        session: Session,
        raw_payload: Dict[str, Any],
        observed_at: Optional[datetime] = None,
    ) -> Tuple[EventContract, EventStateContract, OddsSnapshotContract, bool]:
        """
        Processes raw basketball payload:
        1. Normalizes into Event, EventState, and OddsSnapshot contracts.
        2. Upserts Event in 'events' table with sport_code='basketball'.
        3. Records EventState in 'event_state_snapshots' hypertable.
        4. Checks odds deduplication by content_hash:
           - If odds unchanged: skips redundant snapshot insertion.
           - If odds changed: creates new OddsSnapshot in hypertable and records OddsChangeEvent.
        Returns (EventContract, EventStateContract, OddsSnapshotContract, odds_changed: bool).
        """
        now = observed_at or datetime.now(timezone.utc)

        # 1. Parse contracts via adapter
        event_contract = self.adapter.parse_event(raw_payload)
        state_contract = self.adapter.parse_state(raw_payload)
        odds_contract = self.adapter.parse_odds(raw_payload)

        # 2. Upsert DBEvent by source + source_event_id
        stmt = select(DBEvent).where(
            DBEvent.source == event_contract.source,
            DBEvent.source_event_id == event_contract.source_event_id,
        )
        db_event = session.execute(stmt).scalar_one_or_none()

        if db_event is None:
            db_event = DBEvent(
                id=uuid.uuid4(),
                source=event_contract.source,
                source_event_id=event_contract.source_event_id,
                sport_code=self.adapter.sport_code,
                tournament_name=event_contract.tournament,
                participant_a_name=event_contract.participant_a,
                participant_b_name=event_contract.participant_b,
                scheduled_start_at=event_contract.scheduled_start,
                first_seen_at=now,
                last_seen_at=now,
                status=event_contract.status,
                is_live=event_contract.is_live,
                current_period=state_contract.current_period,
                current_score=state_contract.stats,
                metadata_json=event_contract.metadata,
            )
            session.add(db_event)
            session.flush()
        else:
            db_event.last_seen_at = now
            db_event.status = event_contract.status
            db_event.is_live = event_contract.is_live
            db_event.current_period = state_contract.current_period
            db_event.current_score = state_contract.stats
            session.flush()

        # 3. Insert state snapshot into hypertable
        state_snapshot = DBEventStateSnapshot(
            id=uuid.uuid4(),
            observed_at=now,
            event_id=db_event.id,
            source_event_id=event_contract.source_event_id,
            sport_code=self.adapter.sport_code,
            status=event_contract.status,
            match_clock_seconds=state_contract.match_clock_seconds,
            current_period=state_contract.current_period,
            score=state_contract.stats.get("score_str", ""),
            score_detail=state_contract.stats,
        )
        session.add(state_snapshot)

        # 4. Check odds deduplication
        last_odds_stmt = (
            select(DBOddsSnapshot)
            .where(DBOddsSnapshot.event_id == db_event.id)
            .order_by(desc(DBOddsSnapshot.observed_at))
            .limit(1)
        )
        last_db_odds = session.execute(last_odds_stmt).scalar_one_or_none()

        odds_changed = False
        if last_db_odds is None or last_db_odds.content_hash != odds_contract.content_hash:
            odds_changed = True
            new_odds_snapshot = DBOddsSnapshot(
                id=uuid.uuid4(),
                observed_at=now,
                event_id=db_event.id,
                source_event_id=event_contract.source_event_id,
                sport_code=self.adapter.sport_code,
                content_hash=odds_contract.content_hash,
                is_stale=False,
            )
            session.add(new_odds_snapshot)
            session.flush()

            # Record markets and selections
            for m in odds_contract.markets:
                db_market = DBMarket(
                    id=uuid.uuid4(),
                    odds_snapshot_id=new_odds_snapshot.id,
                    market_type=m.market_type,
                    name=m.name,
                    line_value=m.line_value,
                )
                session.add(db_market)
                session.flush()

                for sel in m.selections:
                    db_sel = DBMarketSelection(
                        id=uuid.uuid4(),
                        market_id=db_market.id,
                        outcome=sel.outcome,
                        odds=sel.odds,
                        is_active=sel.is_active,
                    )
                    session.add(db_sel)

            if last_db_odds is not None:
                change_event = DBOddsChangeEvent(
                    id=uuid.uuid4(),
                    detected_at=now,
                    event_id=db_event.id,
                    sport_code=self.adapter.sport_code,
                    old_snapshot_id=last_db_odds.id,
                    new_snapshot_id=new_odds_snapshot.id,
                    change_type="ODDS_UPDATED",
                )
                session.add(change_event)

        session.flush()
        return event_contract, state_contract, odds_contract, odds_changed
