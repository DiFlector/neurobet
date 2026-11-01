"""Database and TimescaleDB persistence pipeline for Tennis events and snapshots."""

import logging
import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
from sqlalchemy import select, desc
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from contracts import Event as EventContract, EventState as EventStateContract, OddsSnapshot as OddsSnapshotContract
from contracts.tennis_pit import content_addressed_object_matches_hash
from db.models import (
    Event as DBEvent,
    EventStateSnapshot as DBEventStateSnapshot,
    Market as DBMarket,
    MarketSelection as DBMarketSelection,
    OddsSnapshot as DBOddsSnapshot,
    OddsChangeEvent as DBOddsChangeEvent,
    Participant,
    TennisEventAttributeObservation,
)
from .adapter import TennisSportAdapter, closed_set_winners, final_set_winners, merge_event_metadata
from .normalizer import normalize_russian_player_name

logger = logging.getLogger("tennis.pipeline")


def _is_stale_observation(observed_at: datetime, last_seen_at: Optional[datetime]) -> bool:
    """Whether an archived/replayed observation predates the current event row."""
    if last_seen_at is None:
        return False
    observed = observed_at.replace(tzinfo=timezone.utc) if observed_at.tzinfo is None else observed_at.astimezone(timezone.utc)
    previous = last_seen_at.replace(tzinfo=timezone.utc) if last_seen_at.tzinfo is None else last_seen_at.astimezone(timezone.utc)
    return observed < previous


def _best_of_forced_by_live_score(state: EventStateContract) -> int | None:
    """Return BO5 only when observed play makes BO3 impossible.

    A 1:1 score can occur in both BO3 and BO5, so it remains unknown. Reaching
    a live third set after a player has already won two sets, the fourth set,
    or four completed sets proves the match is BO5. This is a source observation
    at the current snapshot time, not a prediction about how long the match will take.
    """
    try:
        period = int(state.current_period or 0)
    except (TypeError, ValueError):
        period = 0
    stats = state.stats if isinstance(state.stats, dict) else {}
    try:
        sets_a = int(stats.get("sets_a") or 0)
        sets_b = int(stats.get("sets_b") or 0)
        sets_played = sets_a + sets_b
    except (TypeError, ValueError):
        sets_a = sets_b = 0
        sets_played = 0
    forced_bo5 = period >= 4 or sets_played >= 4 or (period >= 3 and max(sets_a, sets_b) >= 2)
    return 5 if forced_bo5 else None


def _sha256_hex(value: Any) -> Optional[str]:
    raw = str(value or "").removeprefix("sha256:").lower()
    return raw if len(raw) == 64 and all(char in "0123456789abcdef" for char in raw) else None


def _raw_object_matches_hash(content_hash: Any, raw_key: Any) -> bool:
    return content_addressed_object_matches_hash(content_hash, raw_key)


def _record_event_attribute_observations(session, event, observed_at: datetime, *, stale_metadata=None) -> None:
    """Append only hash-backed explicit Fonbet format/surface observations."""
    if isinstance(stale_metadata, dict):
        # A late replay must not rewrite the current event projection, but its
        # source-time facts still belong in the immutable observation history.
        metadata = merge_event_metadata({}, stale_metadata, observed_at)
    else:
        metadata = event.metadata_json if isinstance(event.metadata_json, dict) else {}
    definitions = (
        ("best_of", metadata.get("best_of"), metadata.get("best_of_source"),
         metadata.get("best_of_available_at"), metadata.get("best_of_source_content_sha256"),
         metadata.get("best_of_source_raw_object_key")),
        ("surface", metadata.get("surface"), metadata.get("surface_source"),
         metadata.get("surface_available_at"), metadata.get("surface_source_content_sha256"),
         metadata.get("surface_source_raw_object_key")),
    )
    for attribute, value, source, raw_available, content_hash, raw_key in definitions:
        if attribute == "best_of" and value not in (3, 5):
            continue
        if attribute == "surface" and value not in {"hard", "clay", "grass", "carpet"}:
            continue
        if source not in {"fonbet_explicit_metadata", "fonbet_score_invariant", "live_tennis_api", "api_tennis", "official_result"}:
            continue
        if not _raw_object_matches_hash(content_hash, raw_key):
            # A digest without the retained source object cannot be audited.
            continue
        try:
            available_at = datetime.fromisoformat(str(raw_available).replace("Z", "+00:00")) if raw_available else None
        except ValueError:
            available_at = None
        if available_at is None:
            continue
        if available_at.tzinfo is None:
            available_at = available_at.replace(tzinfo=timezone.utc)
        else:
            available_at = available_at.astimezone(timezone.utc)
        evidence = {
            "source": source, "source_event_id": str(event.source_event_id),
            "attribute": attribute, "value": value,
            "available_at": available_at.isoformat(),
            "source_content_sha256": content_hash, "raw_object_key": raw_key,
        }
        evidence_hash = hashlib.sha256(json.dumps(evidence, sort_keys=True,
            separators=(",", ":")).encode()).hexdigest()
        statement = pg_insert(TennisEventAttributeObservation).values(
            id=uuid.uuid4(), event_id=event.id, attribute_name=attribute,
            value_json={"value": value}, source=source,
            source_record_id=str(event.source_event_id), available_at=available_at,
            raw_object_key=raw_key, source_content_hash=content_hash,
            parser_version="fonbet_event_metadata_v1" if source == "fonbet_explicit_metadata" else "fonbet_score_invariant_v1",
            evidence_hash=evidence_hash, evidence_json=evidence,
            reconstructed_at=observed_at,
        ).on_conflict_do_nothing(constraint="uq_tennis_event_attribute_observation_evidence")
        session.execute(statement)


def ensure_participant(session: Session, canonical_name: str) -> Optional[Participant]:
    """Find or create the tennis player row for a canonical name."""
    name = (canonical_name or "").strip()
    if not name:
        return None
    row = session.execute(
        select(Participant).where(
            Participant.sport_code == "tennis",
            Participant.canonical_name == name,
        )
    ).scalar_one_or_none()
    if row is None:
        row = Participant(
            sport_code="tennis",
            canonical_name=name,
            is_team=False,
            metadata_json={},
        )
        session.add(row)
        session.flush()
    return row


def link_match_participants(session: Session, db_event: DBEvent) -> None:
    """Store both players and point the match at those rows."""
    left = ensure_participant(session, normalize_russian_player_name(db_event.participant_a_name or ""))
    right = ensure_participant(session, normalize_russian_player_name(db_event.participant_b_name or ""))
    if left is not None:
        db_event.participant_a_id = left.id
    if right is not None:
        db_event.participant_b_id = right.id


def backfill_tennis_participants(session: Session) -> int:
    """Link every stored tennis match that does not yet point at its players."""
    events = session.execute(select(DBEvent).where(DBEvent.sport_code == "tennis")).scalars()
    linked = 0
    for event in events:
        before = (event.participant_a_id, event.participant_b_id)
        link_match_participants(session, event)
        if (event.participant_a_id, event.participant_b_id) != before:
            linked += 1
    if linked:
        session.commit()
    return linked


def _with_fonbet_clock(
    score: Dict[str, Any],
    clock: Optional[Dict[str, Any]],
    previous_score: Optional[Dict[str, Any]],
    now: datetime,
) -> Dict[str, Any]:
    """Attach the Fonbet match clock. A stopped timer or a pause mark freezes the time."""
    if not clock:
        return score
    now_ms = int(now.timestamp() * 1000)
    prev = {}
    if isinstance(previous_score, dict):
        prev = ((previous_score.get("stats") or {}).get("match_clock")) or {}
    pause_label = clock.get("pause_label")
    has_timer = bool(clock.get("has_timer")) and clock.get("timer_seconds") is not None

    if has_timer:
        base = int(clock.get("timer_seconds") or 0)
        updated = int(clock.get("timer_updated_at_ms") or now_ms)
        running = clock.get("timer_direction") == 1 and not pause_label
        if pause_label and running is False and clock.get("timer_direction") == 1:
            base = base + max(0, int((now_ms - updated) / 1000))
            updated = now_ms
        paused = bool(pause_label) or clock.get("timer_direction") == 0
        match_clock = {
            "running": running and not paused,
            "paused": paused,
            "base_seconds": base,
            "updated_at_ms": updated,
            "label": pause_label,
        }
    elif clock.get("start_time"):
        start = int(clock["start_time"])
        if pause_label:
            if prev.get("paused") and prev.get("base_seconds") is not None:
                base = int(prev["base_seconds"])
                updated = int(prev.get("updated_at_ms") or now_ms)
            elif prev.get("base_seconds") is not None:
                extra = max(0, int((now_ms - int(prev.get("updated_at_ms") or now_ms)) / 1000))
                base = int(prev["base_seconds"]) + (extra if prev.get("running") else 0)
                updated = now_ms
            else:
                base = max(0, int(now.timestamp()) - start)
                updated = now_ms
            match_clock = {
                "running": False,
                "paused": True,
                "base_seconds": base,
                "updated_at_ms": updated,
                "label": pause_label,
            }
        else:
            match_clock = {
                "running": True,
                "paused": False,
                "base_seconds": max(0, int(now.timestamp()) - start),
                "updated_at_ms": now_ms,
                "label": None,
            }
    else:
        return score

    stats = dict(score.get("stats") or {})
    stats["match_clock"] = match_clock
    score["stats"] = stats
    score["match_clock_seconds"] = match_clock["base_seconds"]
    return score


class TennisPipeline:
    """Orchestrates normalization and persistence of live tennis events and snapshots."""

    def __init__(self, adapter: Optional[TennisSportAdapter] = None):
        self.adapter = adapter or TennisSportAdapter()

    @staticmethod
    def _official_result_is_definitive(metadata: Optional[Dict[str, Any]]) -> bool:
        """True when metadata carries a definitive Fonbet official result."""
        if not isinstance(metadata, dict):
            return False
        payload = metadata.get("fonbet_official_result")
        if not isinstance(payload, dict):
            return False
        try:
            from contracts import CanonicalMatchResult
            from bankroll.settlement import EventResultMatcher

            return EventResultMatcher.result_is_definitive(
                CanonicalMatchResult.model_validate(payload)
            )
        except Exception:
            return False

    @staticmethod
    def _clear_premature_official_result(metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Drop mid-match / review official markers that must not stick on a live row."""
        meta = dict(metadata or {})
        meta.pop("fonbet_official_result", None)
        meta.pop("pending_official_result", None)
        meta.pop("settlement_review_required", None)
        meta.pop("settlement_review_reason", None)
        return meta

    def process_and_persist(
        self,
        session: Session,
        raw_payload: Dict[str, Any],
        observed_at: Optional[datetime] = None,
        source_payload_hash: Optional[str] = None,
        source_payload_raw_key: Optional[str] = None,
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

        Rows already closed with a definitive official result are never resurrected to live.
        """
        now = observed_at or datetime.now(timezone.utc)
        now = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)

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
        incoming_metadata = dict(event_contract.metadata or {})
        archived_source_hash = _sha256_hex(source_payload_hash)
        source_object_verified = _raw_object_matches_hash(archived_source_hash, source_payload_raw_key)
        for attribute in ("best_of", "surface"):
            if incoming_metadata.get(f"{attribute}_source") == "fonbet_explicit_metadata":
                if source_object_verified:
                    incoming_metadata[f"{attribute}_source_content_sha256"] = archived_source_hash
                    incoming_metadata[f"{attribute}_source_raw_object_key"] = source_payload_raw_key
                else:
                    incoming_metadata.pop(f"{attribute}_source_content_sha256", None)
                    incoming_metadata.pop(f"{attribute}_source_raw_object_key", None)
        score_derived_best_of = _best_of_forced_by_live_score(state_contract)
        if score_derived_best_of is not None and incoming_metadata.get("best_of") not in (3, 5):
            incoming_metadata.update({
                "best_of": score_derived_best_of,
                "best_of_source": "fonbet_score_invariant",
                "best_of_evidence": "observed_fourth_set_or_four_completed_sets",
                "best_of_source_content_sha256": archived_source_hash if source_object_verified else None,
                "best_of_source_raw_object_key": source_payload_raw_key if source_object_verified else None,
            })

        if db_event is None:
            stale_observation = False
            initial_metadata = merge_event_metadata({}, incoming_metadata, now)
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
                current_score=_with_fonbet_clock(
                    state_contract.model_dump(mode="json"),
                    raw_payload.get("fonbet_clock"),
                    None,
                    now,
                ),
                metadata_json=initial_metadata,
            )
            session.add(db_event)
            session.flush()  # Generates db_event.id UUID
            link_match_participants(session, db_event)
            logger.debug("Created new DBEvent: %s (source_id=%s)", db_event.id, db_event.source_event_id)
        else:
            last_observed = db_event.last_seen_at
            stale_observation = _is_stale_observation(now, last_observed)
            has_definitive = self._official_result_is_definitive(db_event.metadata_json)
            terminal = db_event.status in ("finished", "cancelled") or db_event.finished_at is not None

            # A delayed prematch catalog item cannot move an event that has
            # already appeared on the live board or received a terminal result.
            if event_contract.status == "prematch" and (
                db_event.is_live or terminal or db_event.status == "interrupted"
            ):
                session.commit()
                return event_contract, state_contract, odds_contract, False

            if has_definitive and terminal:
                # Official result already sealed the match — never resurrect to live.
                if not stale_observation:
                    db_event.last_seen_at = now
                db_event.is_live = False
                session.flush()
                logger.debug(
                    "Skip live overwrite for sealed event %s (source_id=%s)",
                    db_event.id,
                    db_event.source_event_id,
                )
                session.commit()
                return event_contract, state_contract, odds_contract, False

            if stale_observation:
                # Preserve the historical snapshot below, but never let a late
                # replay move the event row, score, metadata, or watermark back.
                logger.info(
                    "Persisting historical snapshot without changing current event %s (observed_at=%s last_seen_at=%s)",
                    db_event.id, now.isoformat(), last_observed.isoformat() if last_observed else None,
                )
            else:
                # Match still (or again) on the live board: drop premature official markers.
                if event_contract.is_live and db_event.metadata_json:
                    official = (db_event.metadata_json or {}).get("fonbet_official_result")
                    pending = (db_event.metadata_json or {}).get("pending_official_result")
                    if official or pending or (db_event.metadata_json or {}).get("settlement_review_required"):
                        if not has_definitive:
                            db_event.metadata_json = self._clear_premature_official_result(
                                db_event.metadata_json
                            )

                db_event.is_live = event_contract.is_live
                db_event.status = event_contract.status
                db_event.last_seen_at = now
                # Collector re-saw the match: clear heuristic finish/interrupted markers only.
                if event_contract.is_live:
                    db_event.finished_at = None
                db_event.current_period = state_contract.current_period
                db_event.current_score = _with_fonbet_clock(
                    state_contract.model_dump(mode="json"),
                    raw_payload.get("fonbet_clock"),
                    db_event.current_score,
                    now,
                )
                if incoming_metadata:
                    db_event.metadata_json = merge_event_metadata(
                        db_event.metadata_json, incoming_metadata, now
                    )
                link_match_participants(session, db_event)
                session.flush()
                logger.debug("Updated existing DBEvent: %s", db_event.id)

        _record_event_attribute_observations(
            session, db_event, now, stale_metadata=incoming_metadata if stale_observation else None
        )

        # 3. Insert state snapshot into TimescaleDB hypertable
        raw_score = raw_payload.get("score_state", {}).get("raw_score_string") or raw_payload.get("score")
        state_snapshot = DBEventStateSnapshot(
            observed_at=now,
            event_id=db_event.id,
            source_event_id=db_event.source_event_id,
            sport_code=self.adapter.sport_code,
            status=event_contract.status,
            current_period=state_contract.current_period,
            score=str(raw_score) if raw_score else None,
            score_detail=state_contract.model_dump(mode="json"),
            sport_state={
                "server": state_contract.server,
                "is_break_point": state_contract.is_break_point,
                "stats": state_contract.stats,
            },
            raw_hash=archived_source_hash,
            raw_object_key=source_payload_raw_key,
        )
        session.add(state_snapshot)
        if not stale_observation:
            self._remember_closed_sets(db_event, state_contract)

        odds_changed = False
        for market_contract in odds_contract.markets:
            if self._persist_market(session, db_event, market_contract, now):
                odds_changed = True
        if odds_changed:
            logger.info("Persisted new OddsSnapshot (%s) for event %s", odds_contract.content_hash[:16], db_event.id)
        else:
            logger.debug("Odds unchanged (hash=%s). Deduplication applied.", odds_contract.content_hash[:16])

        session.commit()
        return event_contract, state_contract, odds_contract, odds_changed

    def _remember_closed_sets(self, db_event, state_contract) -> None:
        lines = (state_contract.stats or {}).get("set_lines") or []
        stats = state_contract.stats or {}
        try:
            sets_a = int(stats.get("sets_a") or 0)
            sets_b = int(stats.get("sets_b") or 0)
        except (TypeError, ValueError):
            sets_a = sets_b = 0
        period = int(getattr(state_contract, "current_period", 1) or 1)
        best_of = 5 if max(sets_a + sets_b, period) >= 4 else 3
        need = best_of // 2 + 1
        status = str(getattr(db_event, "status", "") or "").lower()
        match_done = status in ("finished", "completed", "ended") or sets_a >= need or sets_b >= need
        winners = final_set_winners(lines) if match_done else closed_set_winners(lines)
        if not winners:
            return
        meta = dict(db_event.metadata_json or {})
        known = dict(meta.get("set_winners") or {})
        known.update(winners)
        if known == (meta.get("set_winners") or {}):
            return
        meta["set_winners"] = known
        db_event.metadata_json = meta
        # Set winners observed in the live feed are informational only.
        # Settlement is delayed until the official Fonbet results feed confirms
        # the completed match, including its final set.

    def _settle_closed_sets(self, db_event) -> None:
        try:
            from bankroll.settlement import BetSettlementEngine
        except ImportError:
            return
        session = Session.object_session(db_event)
        if session is None:
            return
        BetSettlementEngine.settle_known_set_bets(session, db_event)

    def _persist_market(self, session, db_event, market_contract, now: datetime) -> bool:
        line_value = market_contract.line_value
        stmt_mkt = select(DBMarket).where(
            DBMarket.event_id == db_event.id,
            DBMarket.market_type == market_contract.market_type,
        )
        if line_value is None:
            stmt_mkt = stmt_mkt.where(DBMarket.line_value.is_(None))
        else:
            stmt_mkt = stmt_mkt.where(DBMarket.line_value == line_value)
        db_market = session.execute(stmt_mkt).scalar_one_or_none()
        if db_market is None:
            db_market = DBMarket(
                event_id=db_event.id,
                source_market_id=market_contract.market_id,
                market_type=market_contract.market_type,
                name=market_contract.name,
                status="active",
                line_value=line_value,
            )
            session.add(db_market)
            session.flush()

        selection_models = {}
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

        clean_hash = f"{market_contract.market_id}:{line_value}:" + ":".join(
            f"{sel.outcome}={sel.odds}" for sel in market_contract.selections
        )
        latest = session.execute(
            select(DBOddsSnapshot)
            .where(DBOddsSnapshot.market_id == db_market.id)
            .order_by(desc(DBOddsSnapshot.observed_at))
            .limit(1)
        ).scalar_one_or_none()
        if latest is not None and latest.content_hash == clean_hash[:64]:
            return False
        for sel in market_contract.selections:
            session.add(DBOddsSnapshot(
                observed_at=now,
                event_id=db_event.id,
                market_id=db_market.id,
                selection_id=selection_models[sel.outcome].id,
                market_type=db_market.market_type,
                outcome=sel.outcome,
                line_value=line_value,
                odds=sel.odds,
                probability_implied=sel.probability_implied,
                status="active",
                is_live=True,
                content_hash=clean_hash[:64],
            ))
        if latest is not None and "player_a" in selection_models:
            new_odds = float(next(sel.odds for sel in market_contract.selections if sel.outcome == "player_a"))
            session.add(DBOddsChangeEvent(
                event_id=db_event.id,
                selection_id=selection_models["player_a"].id,
                old_odds=float(latest.odds),
                new_odds=new_odds,
                delta=round(new_odds - float(latest.odds), 4),
                changed_at=now,
            ))
        return True
