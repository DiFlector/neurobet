"""Database and TimescaleDB persistence pipeline for Tennis events and snapshots."""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from contracts import Event as EventContract, EventState as EventStateContract, OddsSnapshot as OddsSnapshotContract
from db.models import (
    Event as DBEvent,
    EventStateSnapshot as DBEventStateSnapshot,
    Market as DBMarket,
    MarketSelection as DBMarketSelection,
    OddsSnapshot as DBOddsSnapshot,
    OddsChangeEvent as DBOddsChangeEvent,
)
from .adapter import TennisSportAdapter

logger = logging.getLogger("tennis.pipeline")


class TennisPipeline:
    """Orchestrates normalization and persistence of live tennis events and snapshots."""

    def __init__(self, adapter: Optional[TennisSportAdapter] = None):
        self.adapter = adapter or TennisSportAdapter()

    def process_and_persist(
        self,
        session: Session,
        raw_payload: Dict[str, Any],
        observed_at: Optional[datetime] = None,
    ) -> Tuple[EventContract, EventStateContract, OddsSnapshotContract, bool]:
        """
        Processes raw tennis payload:
        1. Normalizes into Event, EventState, and OddsSnapshot contracts.
        2. Upserts Event in 'events' table.
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
                source=event_contract.source,
                source_event_id=event_contract.source_event_id,
                sport_code=event_contract.sport_code,
                participant_a_name=event_contract.participant_a,
                participant_b_name=event_contract.participant_b,
                scheduled_start_at=event_contract.scheduled_start,
                first_seen_at=now,
                last_seen_at=now,
                status=event_contract.status,
                is_live=event_contract.is_live,
                current_period=state_contract.current_period,
                current_score=state_contract.model_dump(mode="json"),
                metadata_json=event_contract.metadata,
            )
            session.add(db_event)
            session.flush()  # Generates db_event.id UUID
            logger.debug("Created new DBEvent: %s (source_id=%s)", db_event.id, db_event.source_event_id)
        else:
            db_event.is_live = event_contract.is_live
            db_event.status = event_contract.status
            db_event.last_seen_at = now
            db_event.current_period = state_contract.current_period
            db_event.current_score = state_contract.model_dump(mode="json")
            session.flush()
            logger.debug("Updated existing DBEvent: %s", db_event.id)

        # 3. Insert state snapshot into TimescaleDB hypertable
        raw_score = raw_payload.get("score_state", {}).get("raw_score_string") or raw_payload.get("score")
        state_snapshot = DBEventStateSnapshot(
            observed_at=now,
            event_id=db_event.id,
            source_event_id=db_event.source_event_id,
            sport_code=self.adapter.sport_code,
            status="live",
            current_period=state_contract.current_period,
            score=str(raw_score) if raw_score else None,
            score_detail=state_contract.model_dump(mode="json"),
            sport_state={
                "server": state_contract.server,
                "is_break_point": state_contract.is_break_point,
                "stats": state_contract.stats,
            },
        )
        session.add(state_snapshot)

        # 4. Upsert Market and MarketSelections
        market_contract = odds_contract.markets[0]
        stmt_mkt = select(DBMarket).where(
            DBMarket.event_id == db_event.id,
            DBMarket.market_type == market_contract.market_type,
        )
        db_market = session.execute(stmt_mkt).scalar_one_or_none()
        if db_market is None:
            db_market = DBMarket(
                event_id=db_event.id,
                source_market_id=market_contract.market_id,
                market_type=market_contract.market_type,
                name=market_contract.name,
                status="active",
            )
            session.add(db_market)
            session.flush()

        # Map selections
        selection_models: Dict[str, DBMarketSelection] = {}
        for sel in market_contract.selections:
            stmt_sel = select(DBMarketSelection).where(
                DBMarketSelection.market_id == db_market.id,
                DBMarketSelection.outcome == sel.outcome,
            )
            db_sel = session.execute(stmt_sel).scalar_one_or_none()
            if db_sel is None:
                db_sel = DBMarketSelection(
                    market_id=db_market.id,
                    source_selection_id=sel.selection_id,
                    outcome=sel.outcome,
                    name=sel.name,
                    status="active",
                )
                session.add(db_sel)
                session.flush()
            selection_models[sel.outcome] = db_sel

        # 5. Check odds deduplication by content_hash
        stmt_latest_odds = (
            select(DBOddsSnapshot)
            .where(DBOddsSnapshot.event_id == db_event.id)
            .order_by(desc(DBOddsSnapshot.observed_at))
            .limit(1)
        )
        latest_odds = session.execute(stmt_latest_odds).scalar_one_or_none()

        odds_changed = False
        clean_hash = odds_contract.content_hash.replace("sha256:", "")[:64]
        if latest_odds is None or latest_odds.content_hash != clean_hash:
            odds_changed = True
            # Create a snapshot row for each selection
            for sel in market_contract.selections:
                db_sel = selection_models[sel.outcome]
                snapshot_row = DBOddsSnapshot(
                    observed_at=now,
                    event_id=db_event.id,
                    market_id=db_market.id,
                    selection_id=db_sel.id,
                    market_type=db_market.market_type,
                    outcome=sel.outcome,
                    odds=sel.odds,
                    probability_implied=sel.probability_implied,
                    status="active",
                    is_live=True,
                    content_hash=clean_hash,
                )
                session.add(snapshot_row)

            # Record change event if prior odds existed
            if latest_odds is not None:
                db_sel_a = selection_models.get("player_a")
                if db_sel_a:
                    new_odds_val = float(market_contract.selections[0].odds)
                    old_odds_val = float(latest_odds.odds)
                    change_event = DBOddsChangeEvent(
                        event_id=db_event.id,
                        selection_id=db_sel_a.id,
                        old_odds=old_odds_val,
                        new_odds=new_odds_val,
                        delta=round(new_odds_val - old_odds_val, 4),
                        changed_at=now,
                    )
                    session.add(change_event)

            logger.info("Persisted new OddsSnapshot (%s) for event %s", odds_contract.content_hash[:16], db_event.id)
        else:
            logger.debug("Odds unchanged (hash=%s). Deduplication applied.", odds_contract.content_hash[:16])

        session.commit()
        return event_contract, state_contract, odds_contract, odds_changed
