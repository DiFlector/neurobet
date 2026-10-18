"""Prediction matching engine, 24h cleanup lifecycle, and consensus builder."""

from datetime import datetime, timezone
import logging
from typing import List, Optional
import uuid

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from contracts import PredictionConsensusSummary
from db.connection import SessionLocal
from db.models import Event, ExternalPrediction
from .base import normalize_tennis_player

logger = logging.getLogger("research.predictions.manager")


class PredictionManager:
    """Manages prediction persistence, matching to Fonbet events, and consensus."""

    def __init__(self, session_factory=SessionLocal):
        self._session_factory = session_factory

    def save_predictions(self, dtos: list) -> int:
        """Persists scraped prediction DTOs into external_predictions with deduplication."""
        if not dtos:
            return 0

        # In-memory deduplication by source_url
        unique_dtos = {}
        for d in dtos:
            if d.source_url not in unique_dtos:
                unique_dtos[d.source_url] = d

        saved = 0
        with self._session_factory() as session:
            for dto in unique_dtos.values():
                try:
                    # Check if already exists by source_url
                    existing = session.execute(
                        select(ExternalPrediction).where(ExternalPrediction.source_url == dto.source_url)
                    ).scalar_one_or_none()

                    if existing:
                        # Update odds/reasoning if updated
                        if dto.recommended_odds and dto.recommended_odds != existing.recommended_odds:
                            existing.recommended_odds = dto.recommended_odds
                        if dto.reasoning_snippet and len(dto.reasoning_snippet) > len(existing.reasoning_snippet or ""):
                            existing.reasoning_snippet = dto.reasoning_snippet
                    else:
                        pred = ExternalPrediction(
                            source_code=dto.source_code,
                            source_url=dto.source_url,
                            sport_code=dto.sport_code,
                            tournament_name=dto.tournament_name or "",
                            raw_participant_a=dto.raw_participant_a,
                            raw_participant_b=dto.raw_participant_b,
                            canonical_participant_a=dto.canonical_participant_a,
                            canonical_participant_b=dto.canonical_participant_b,
                            estimated_start_at=dto.estimated_start_at,
                            market_type=dto.market_type,
                            predicted_outcome=dto.predicted_outcome,
                            raw_prediction_text=dto.raw_prediction_text,
                            recommended_odds=dto.recommended_odds,
                            author_name=dto.author_name,
                            author_roi=dto.author_roi,
                            author_winrate=dto.author_winrate,
                            reasoning_snippet=dto.reasoning_snippet,
                            status="pending",
                            created_at=dto.created_at or datetime.now(timezone.utc),
                            expires_at=dto.expires_at,
                        )
                        session.add(pred)
                        saved += 1
                except Exception as e:
                    logger.warning(f"Error persisting prediction from {dto.source_url}: {e}")

            session.commit()

        # Run auto-matching for newly saved
        matched_count = self.match_pending_predictions()
        logger.info(f"Saved {saved} new predictions, auto-matched {matched_count} to Fonbet events")
        return saved

    def match_pending_predictions(self) -> int:
        """
        Matches pending predictions to existing events in database.
        Matches if canonical players match within +/- 12h of scheduled start time.
        """
        matched = 0
        with self._session_factory() as session:
            pending_preds = session.execute(
                select(ExternalPrediction).where(
                    ExternalPrediction.status == "pending",
                    ExternalPrediction.matched_event_id.is_(None),
                )
            ).scalars().all()

            if not pending_preds:
                return 0

            # Query candidate events once and pre-normalize names
            candidate_events = session.execute(
                select(Event).where(
                    Event.sport_code == "tennis",
                    Event.status.notin_(["finished", "cancelled", "abandoned"]),
                )
            ).scalars().all()

            norm_events = []
            for ev in candidate_events:
                ev_a = normalize_tennis_player(ev.participant_a_name).lower()
                ev_b = normalize_tennis_player(ev.participant_b_name).lower()
                norm_events.append((ev, ev_a, ev_b))

            for pred in pending_preds:
                pa_norm = normalize_tennis_player(pred.canonical_participant_a).lower()
                pb_norm = normalize_tennis_player(pred.canonical_participant_b).lower()

                # Extract primary surnames for fast token matching
                pa_surname = pa_norm.split()[-1] if pa_norm else ""
                pb_surname = pb_norm.split()[-1] if pb_norm else ""

                for ev, ev_a, ev_b in norm_events:
                    # Match if full name or surname matches
                    direct_match = (
                        (pa_norm in ev_a or ev_a in pa_norm or (pa_surname and pa_surname in ev_a)) and
                        (pb_norm in ev_b or ev_b in pb_norm or (pb_surname and pb_surname in ev_b))
                    )
                    reverse_match = (
                        (pa_norm in ev_b or ev_b in pa_norm or (pa_surname and pa_surname in ev_b)) and
                        (pb_norm in ev_a or ev_a in pb_norm or (pb_surname and pb_surname in ev_a))
                    )

                    if direct_match or reverse_match:
                        # Check time window +/- 24 hours
                        if ev.scheduled_start_at and pred.estimated_start_at:
                            delta_sec = abs((ev.scheduled_start_at - pred.estimated_start_at).total_seconds())
                            if delta_sec > 86400:  # > 24 hours
                                continue

                        pred.matched_event_id = ev.id
                        pred.matched_at = datetime.now(timezone.utc)
                        pred.status = "matched"
                        matched += 1
                        logger.info(
                            f"Matched prediction [{pred.source_code}] to event {ev.id} "
                            f"({ev.participant_a_name} vs {ev.participant_b_name})"
                        )
                        break

            session.commit()
        return matched

    def cleanup_expired_unmatched_predictions(self) -> int:
        """
        Deletes predictions that never appeared in line 24 hours after estimated start time.
        Rule: "Если матч так и не настал - удаляй запись этого прогноза спустя сутки".
        """
        now = datetime.now(timezone.utc)
        with self._session_factory() as session:
            stmt = delete(ExternalPrediction).where(
                ExternalPrediction.matched_event_id.is_(None),
                ExternalPrediction.expires_at < now,
            )
            res = session.execute(stmt)
            deleted_count = res.rowcount
            session.commit()

        if deleted_count > 0:
            logger.info(f"Cleaned up {deleted_count} expired unmatched predictions past 24h TTL")
        return deleted_count

    def get_consensus_for_event(self, event_id: str | uuid.UUID) -> PredictionConsensusSummary:
        """
        Calculates consensus summary across all external predictions linked to this event.
        """
        ev_uuid = uuid.UUID(str(event_id)) if isinstance(event_id, str) else event_id

        with self._session_factory() as session:
            preds = session.execute(
                select(ExternalPrediction).where(ExternalPrediction.matched_event_id == ev_uuid)
            ).scalars().all()

            if not preds:
                return PredictionConsensusSummary(
                    event_id=str(ev_uuid),
                    sport_code="tennis",
                    total_predictions=0,
                )

            total = len(preds)
            p1_count = 0
            p2_count = 0
            other_count = 0
            p1_odds_list = []
            p2_odds_list = []
            sources = set()
            snippets = []

            for p in preds:
                sources.add(p.source_code)
                if p.reasoning_snippet:
                    snippets.append(f"[{p.source_code}] {p.reasoning_snippet}")

                outcome = (p.predicted_outcome or "").lower()
                if outcome == "p1" or "1" in outcome:
                    p1_count += 1
                    if p.recommended_odds:
                        p1_odds_list.append(float(p.recommended_odds))
                elif outcome == "p2" or "2" in outcome:
                    p2_count += 1
                    if p.recommended_odds:
                        p2_odds_list.append(float(p.recommended_odds))
                else:
                    other_count += 1

            p1_share = p1_count / total if total > 0 else 0.0
            p2_share = p2_count / total if total > 0 else 0.0

            avg_p1_odds = sum(p1_odds_list) / len(p1_odds_list) if p1_odds_list else None
            avg_p2_odds = sum(p2_odds_list) / len(p2_odds_list) if p2_odds_list else None

            # Determine consensus pick
            consensus_pick = "neutral"
            if p1_share >= 0.60:
                consensus_pick = "p1"
            elif p2_share >= 0.60:
                consensus_pick = "p2"

            # Confidence based on volume and unanimity
            volume_factor = min(1.0, total / 5.0)
            unanimity = max(p1_share, p2_share)
            confidence = round(volume_factor * unanimity, 4)

            return PredictionConsensusSummary(
                event_id=str(ev_uuid),
                sport_code="tennis",
                total_predictions=total,
                p1_count=p1_count,
                p2_count=p2_count,
                other_count=other_count,
                p1_share=round(p1_share, 4),
                p2_share=round(p2_share, 4),
                avg_p1_odds=round(avg_p1_odds, 4) if avg_p1_odds else None,
                avg_p2_odds=round(avg_p2_odds, 4) if avg_p2_odds else None,
                consensus_pick=consensus_pick,
                consensus_confidence=confidence,
                sources=sorted(list(sources)),
                expert_snippets=snippets[:5],
                updated_at=datetime.now(timezone.utc),
            )
