"""Idempotent Bet Settlement Engine driven strictly by official bookmaker results."""

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
import uuid

from sqlalchemy import select, or_, and_
from sqlalchemy.orm import Session

from db.models.events import Event
from db.models.betting import Bet, BetSettlement, VirtualAccount
from contracts import CanonicalMatchResult
from .service import BankrollService, utc_now
from .exceptions import InvalidSettlementError, BetNotFoundError

logger = logging.getLogger("bankroll.settlement")


class EventResultMatcher:
    """
    Matches canonical results from https://fon.bet/results with platform events,
    and updates event lifecycle state to finished without guessing.
    """

    @staticmethod
    def normalize_name(name: str) -> str:
        """Strip initials and common prefixes for fuzzy participant matching."""
        return " ".join(re.findall(r"\w+", name.lower())) if name else ""

    @classmethod
    def match_and_complete_event(
        cls,
        session: Session,
        result: CanonicalMatchResult,
    ) -> Optional[Event]:
        """
        Find event in DB by source_event_id or participant names and mark finished.
        """
        # 1. Primary lookup by source_event_id
        stmt = select(Event).where(
            Event.source == "fonbet",
            Event.source_event_id == str(result.source_event_id),
        )
        event = session.execute(stmt).scalars().first()

        # 2. Secondary fallback lookup by participant names if IDs differ between live & results feed
        if not event:
            stmt_fuzzy = select(Event).where(
                Event.sport_code == result.sport_code,
                Event.source == "fonbet",
                or_(
                    and_(
                        Event.participant_a_name.ilike(f"%{result.participant_a[:4]}%"),
                        Event.participant_b_name.ilike(f"%{result.participant_b[:4]}%"),
                    ),
                    and_(
                        Event.participant_a_name.ilike(f"%{result.participant_b[:4]}%"),
                        Event.participant_b_name.ilike(f"%{result.participant_a[:4]}%"),
                    ),
                ),
            )
            event = session.execute(stmt_fuzzy).scalars().first()

        if not event:
            logger.debug(
                "No matching event found for result %s (%s vs %s)",
                result.source_event_id,
                result.participant_a,
                result.participant_b,
            )
            return None

        # Update event state to finished
        event.status = "finished" if result.status != "CANCELLED" else "cancelled"
        event.finished_at = utc_now()
        event.current_score = {
            "final_score": result.final_score,
            "winner": result.winner,
            "status": result.status,
            "is_retired": result.is_retired,
            "is_walkover": result.is_walkover,
        }
        meta = dict(event.metadata_json or {})
        meta["fonbet_official_result"] = result.model_dump(mode="json")
        event.metadata_json = meta
        session.flush()

        logger.info(
            "Event %s (%s vs %s) completed via official Fonbet result: score=%s, winner=%s",
            event.id,
            event.participant_a_name,
            event.participant_b_name,
            result.final_score,
            result.winner,
        )
        return event


class BetSettlementEngine:
    """
    Resolves open bets against canonical match results with strict idempotency.
    Enforces that no bet is settled twice and ambiguous matches are flagged for review.
    """

    @classmethod
    def settle_event_bets(
        cls,
        session: Session,
        event: Event,
        result: CanonicalMatchResult,
    ) -> List[BetSettlement]:
        """
        Settle all pending bets for the given finished event based on official result.
        Returns list of newly or existing BetSettlement objects.
        """
        stmt = select(Bet).where(Bet.event_id == event.id)
        bets = session.execute(stmt).scalars().all()

        settlements: List[BetSettlement] = []

        # Check if result requires manual intervention
        if result.status == "SETTLEMENT_REVIEW_REQUIRED" or (
            result.status == "FINISHED" and result.winner is None
        ):
            logger.warning(
                "Event %s marked SETTLEMENT_REVIEW_REQUIRED (Score: %s). Skipping automated bet settlement.",
                event.id,
                result.final_score,
            )
            meta = dict(event.metadata_json or {})
            meta["settlement_review_required"] = True
            meta["settlement_review_reason"] = "Ambiguous or incomplete score from Fonbet results"
            event.metadata_json = meta
            session.flush()
            return settlements

        for bet in bets:
            # 1. Idempotency Check: if bet is already settled, return existing settlement
            if bet.status != "PENDING":
                existing = session.query(BetSettlement).filter(BetSettlement.bet_id == bet.id).first()
                if existing:
                    settlements.append(existing)
                    logger.debug("Bet %s is already settled (%s). Skipping.", bet.id, bet.status)
                continue

            # 2. Resolve Market Rules (currently match_winner)
            settlement_status = "VOID"
            settlement_reason = f"MATCH_{result.status}"

            if result.status in ("CANCELLED", "VOID") or result.is_walkover:
                settlement_status = "VOID"
                settlement_reason = f"MATCH_CANCELLED - {result.comments or 'Official bookmaker void'}"
            elif result.winner:
                if bet.outcome == result.winner:
                    settlement_status = "WON"
                    settlement_reason = (
                        f"MATCH_COMPLETED - Winner: {result.winner}, Score: {result.final_score}"
                    )
                else:
                    settlement_status = "LOST"
                    settlement_reason = (
                        f"MATCH_COMPLETED - Winner: {result.winner}, Score: {result.final_score}"
                    )
            else:
                # Cannot determine cleanly -> flag review
                logger.warning("Bet %s cannot be settled cleanly. Setting review flag.", bet.id)
                meta = dict(event.metadata_json or {})
                meta["settlement_review_required"] = True
                event.metadata_json = meta
                session.flush()
                continue

            # 3. Atomically settle bet and append immutable ledger entry via BankrollService
            try:
                updated_bet, settlement = BankrollService.settle_bet(
                    session=session,
                    bet_id=bet.id,
                    status=settlement_status,
                    reason=settlement_reason,
                )
                settlements.append(settlement)
                logger.info(
                    "Settled bet %s: status=%s, payout=%.2f, net_profit=%.2f (reason: %s)",
                    bet.id,
                    settlement.status,
                    settlement.payout,
                    settlement.net_profit,
                    settlement_reason,
                )
            except InvalidSettlementError as ex:
                if "already settled" in str(ex).lower():
                    # Idempotent recovery
                    existing = session.query(BetSettlement).filter(BetSettlement.bet_id == bet.id).first()
                    if existing:
                        settlements.append(existing)
                else:
                    raise

        session.flush()
        return settlements

    @classmethod
    def get_review_queue(cls, session: Session) -> List[Dict[str, Any]]:
        """List events requiring manual settlement review."""
        stmt = select(Event).where(
            Event.metadata_json["settlement_review_required"].as_boolean() == True
        )
        events = session.execute(stmt).scalars().all()
        return [
            {
                "event_id": str(e.id),
                "source_event_id": e.source_event_id,
                "sport_code": e.sport_code,
                "participant_a": e.participant_a_name,
                "participant_b": e.participant_b_name,
                "score": e.current_score,
                "review_reason": (e.metadata_json or {}).get("settlement_review_reason", "Ambiguous outcome"),
            }
            for e in events
        ]
