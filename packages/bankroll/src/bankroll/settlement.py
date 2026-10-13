"""Idempotent Bet Settlement Engine driven strictly by official bookmaker results."""

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models.events import Event
from db.models.betting import Bet, BetSettlement, VirtualAccount
from contracts import CanonicalMatchResult
from .service import BankrollService, utc_now
from .exceptions import InvalidSettlementError, BetNotFoundError

logger = logging.getLogger("bankroll.settlement")


@dataclass
class IndexedEvent:
    """Снимок события для сопоставления результата без повторного чтения всей таблицы."""

    id: uuid.UUID
    source_event_id: str
    sport_code: str
    participant_a_name: str
    participant_b_name: str
    is_live: bool
    status: str
    has_official: bool
    last_seen_epoch: float


@dataclass
class FonbetEventIndex:
    by_source_id: Dict[str, IndexedEvent]
    by_sport: Dict[str, List[IndexedEvent]]


class EventResultMatcher:
    """
    Matches canonical results from https://fon.bet/results with platform events,
    and updates event lifecycle state to finished without guessing.
    """

    _INCOMPLETE_SCORE_PREFIXES = ("0:0", "0-0", "-:-", ":")

    @staticmethod
    def normalize_name(name: str) -> str:
        """Strip punctuation for participant matching."""
        return " ".join(re.findall(r"\w+", name.lower())) if name else ""

    @classmethod
    def names_match(cls, left: str, right: str) -> bool:
        """True when one normalized name contains the other (order-safe substring)."""
        a = cls.normalize_name(left)
        b = cls.normalize_name(right)
        if not a or not b:
            return False
        return a in b or b in a

    @classmethod
    def participants_match(
        cls,
        event_a: str,
        event_b: str,
        result_a: str,
        result_b: str,
    ) -> bool:
        """Match both participants, allowing swapped sides."""
        direct = cls.names_match(event_a, result_a) and cls.names_match(event_b, result_b)
        swapped = cls.names_match(event_a, result_b) and cls.names_match(event_b, result_a)
        return direct or swapped

    @classmethod
    def _canonical_from_metadata(cls, payload: Any) -> Optional[CanonicalMatchResult]:
        """Best-effort rebuild of CanonicalMatchResult from stored metadata JSON."""
        if not isinstance(payload, dict):
            return None
        try:
            return CanonicalMatchResult.model_validate(payload)
        except Exception:
            return None

    @classmethod
    def find_event_for_result(
        cls,
        session: Session,
        result: CanonicalMatchResult,
        *,
        allow_live: bool = False,
    ) -> Optional[Event]:
        """
        Resolve a DB event for an official result.

        Fonbet results.json uses internal ids that do not match live source_event_id,
        so name matching is the primary production path after the id lookup.
        """
        stmt = select(Event).where(
            Event.source == "fonbet",
            Event.source_event_id == str(result.source_event_id),
        )
        event = session.execute(stmt).scalars().first()
        if event:
            return event

        candidates_stmt = select(Event).where(
            Event.sport_code == result.sport_code,
            Event.source == "fonbet",
        )
        if not allow_live:
            candidates_stmt = candidates_stmt.where(
                Event.is_live.is_(False),
                Event.status != "live",
            )
        candidates = session.execute(candidates_stmt).scalars().all()
        matched = [
            e
            for e in candidates
            if cls.participants_match(
                e.participant_a_name or "",
                e.participant_b_name or "",
                result.participant_a,
                result.participant_b,
            )
        ]
        if not matched:
            return None
        if len(matched) == 1:
            return matched[0]

        # Prefer rows still missing an official result, then most recently seen.
        def _rank(e: Event) -> Tuple[int, float]:
            has_official = 1 if (e.metadata_json or {}).get("fonbet_official_result") else 0
            ts = e.last_seen_at or e.updated_at or e.created_at
            epoch = ts.timestamp() if ts is not None else 0.0
            return (has_official, -epoch)

        matched.sort(key=_rank)
        return matched[0]

    @classmethod
    def build_index(cls, session: Session) -> FonbetEventIndex:
        """Один проход по событиям Fonbet. Дальше сверка результатов идёт в памяти."""
        rows = session.execute(select(Event).where(Event.source == "fonbet")).scalars().all()
        by_source_id: Dict[str, IndexedEvent] = {}
        by_sport: Dict[str, List[IndexedEvent]] = {}
        for event in rows:
            ts = event.last_seen_at or event.updated_at or event.created_at
            indexed = IndexedEvent(
                id=event.id,
                source_event_id=str(event.source_event_id or ""),
                sport_code=event.sport_code or "",
                participant_a_name=event.participant_a_name or "",
                participant_b_name=event.participant_b_name or "",
                is_live=bool(event.is_live),
                status=event.status or "",
                has_official=1 if (event.metadata_json or {}).get("fonbet_official_result") else 0,
                last_seen_epoch=ts.timestamp() if ts is not None else 0.0,
            )
            if indexed.source_event_id:
                by_source_id[indexed.source_event_id] = indexed
            by_sport.setdefault(indexed.sport_code, []).append(indexed)
        return FonbetEventIndex(by_source_id=by_source_id, by_sport=by_sport)

    @classmethod
    def find_indexed_event(
        cls,
        index: FonbetEventIndex,
        result: CanonicalMatchResult,
        *,
        allow_live: bool = False,
    ) -> Optional[IndexedEvent]:
        """То же правило, что у find_event_for_result, но по уже загруженному снимку."""
        by_id = index.by_source_id.get(str(result.source_event_id))
        if by_id is not None:
            return by_id
        candidates = []
        for event in index.by_sport.get(result.sport_code or "", []):
            if not allow_live and (event.is_live or event.status == "live"):
                continue
            if cls.participants_match(
                event.participant_a_name,
                event.participant_b_name,
                result.participant_a,
                result.participant_b,
            ):
                candidates.append(event)
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        candidates.sort(key=lambda event: (event.has_official, -event.last_seen_epoch))
        return candidates[0]

    @classmethod
    def _tennis_set_total(cls, score: Optional[str]) -> int:
        match = re.search(r"^\s*(\d+)\s*[:\-]\s*(\d+)", str(score or "").strip())
        if not match:
            return 0
        return int(match.group(1)) + int(match.group(2))

    @classmethod
    def _tennis_set_leader(cls, score: Optional[str]) -> int:
        match = re.search(r"^\s*(\d+)\s*[:\-]\s*(\d+)", str(score or "").strip())
        if not match:
            return 0
        return max(int(match.group(1)), int(match.group(2)))

    @classmethod
    def result_is_definitive(cls, result: CanonicalMatchResult) -> bool:
        """
        True only when the official result is safe to remove a match from the live board.
        Placeholder rows (0:0 / review / no winner) must not wipe in-play events.
        Tennis one-set leads (1:0 / 0:1) are mid-match and never definitive.
        """
        if result.status in ("CANCELLED", "VOID"):
            return True
        if result.status == "SETTLEMENT_REVIEW_REQUIRED":
            return False
        if result.status not in ("FINISHED", "RETIRED"):
            return False
        if not result.winner and not result.is_retired and not result.is_walkover:
            return False
        score_head = (result.final_score or "").strip().split()[0] if result.final_score else ""
        if not score_head or score_head in cls._INCOMPLETE_SCORE_PREFIXES:
            return False
        if score_head.startswith("0:0") or score_head.startswith("0-0"):
            return False

        if result.sport_code == "tennis" and result.status == "FINISHED" and not result.is_retired:
            try:
                from sports_core.results_parser import FonbetResultsParser

                sets = FonbetResultsParser.tennis_sets_from_score(result.final_score or "")
                if sets is None or not FonbetResultsParser.tennis_match_is_complete(*sets):
                    return False
            except Exception:
                # Fail closed: without a complete set score tennis must not finish.
                if cls._tennis_set_leader(result.final_score) < 2:
                    return False
        return True

    @classmethod
    def _result_is_weaker_than_existing(
        cls,
        incoming: CanonicalMatchResult,
        existing: CanonicalMatchResult,
    ) -> bool:
        """True when incoming would degrade an already stored definitive result."""
        if not cls.result_is_definitive(existing):
            return False
        if not cls.result_is_definitive(incoming):
            return True
        if incoming.sport_code == "tennis" and existing.sport_code == "tennis":
            incoming_sets = cls._tennis_set_total(incoming.final_score)
            existing_sets = cls._tennis_set_total(existing.final_score)
            if incoming_sets < existing_sets:
                return True
            if (
                incoming_sets == existing_sets
                and cls._tennis_set_leader(incoming.final_score)
                < cls._tennis_set_leader(existing.final_score)
            ):
                return True
        return False

    @classmethod
    def match_and_complete_event(
        cls,
        session: Session,
        result: CanonicalMatchResult,
        index: Optional[FonbetEventIndex] = None,
    ) -> Optional[Event]:
        """
        Find event in DB by source_event_id or participant names and mark finished.
        Incomplete / review-only results never force a still-live event off the board
        and never overwrite an already stored definitive score.
        """
        # Official results feed ids != live source_event_id; name match is required.
        # Allow live only for definitive results so a just-finished match can close.
        definitive_preview = cls.result_is_definitive(result)
        if index is not None:
            indexed = cls.find_indexed_event(
                index,
                result,
                allow_live=definitive_preview,
            )
            event = session.get(Event, indexed.id) if indexed is not None else None
        else:
            event = cls.find_event_for_result(
                session,
                result,
                allow_live=definitive_preview,
            )

        if not event:
            logger.debug(
                "No matching event found for result %s (%s vs %s)",
                result.source_event_id,
                result.participant_a,
                result.participant_b,
            )
            return None

        definitive = cls.result_is_definitive(result)
        still_live = bool(event.is_live) or (event.status == "live")
        meta = dict(event.metadata_json or {})
        existing_official = cls._canonical_from_metadata(meta.get("fonbet_official_result"))

        # Incomplete official rows must not finish the event or wipe a good score.
        if not definitive:
            meta["pending_official_result"] = result.model_dump(mode="json")
            if result.status == "SETTLEMENT_REVIEW_REQUIRED":
                meta["settlement_review_required"] = True
                meta["settlement_review_reason"] = (
                    f"Incomplete official score: {result.final_score}"
                )
            event.metadata_json = meta
            session.flush()
            logger.info(
                "Skip finishing event %s (%s vs %s): non-definitive result score=%s status=%s live=%s",
                event.id,
                event.participant_a_name,
                event.participant_b_name,
                result.final_score,
                result.status,
                still_live,
            )
            return event

        if existing_official and cls._result_is_weaker_than_existing(result, existing_official):
            logger.info(
                "Keep stronger official result for event %s: existing=%s incoming=%s",
                event.id,
                existing_official.final_score,
                result.final_score,
            )
            return event

        # Update event state to finished
        event.status = "finished" if result.status != "CANCELLED" else "cancelled"
        event.is_live = False
        event.finished_at = utc_now()
        event.current_score = {
            "final_score": result.final_score,
            "winner": result.winner,
            "status": result.status,
            "is_retired": result.is_retired,
            "is_walkover": result.is_walkover,
        }
        meta["fonbet_official_result"] = result.model_dump(mode="json")
        meta.pop("pending_official_result", None)
        meta.pop("settlement_review_required", None)
        meta.pop("settlement_review_reason", None)
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
