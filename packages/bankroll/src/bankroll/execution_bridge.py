"""Submit a confirmed ACTIVE prediction to bet-manager. This module does not write the ledger."""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from contracts import CandidateItem, DecisionPipelineConfig
from db.models.betting import Bet, BetProposal
from db.models.events import Event
from db.models.ml_registry import ModelVersion
from db.models.odds import Market, MarketSelection, OddsSnapshot
from db.models.predictions import MLPrediction

from .decision_pipeline import CombinedDecisionPipeline

logger = logging.getLogger("bankroll.execution_bridge")

PLACED_STATUSES = ("PENDING", "WON", "LOST", "VOID")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _selection_id(session: Session, event_id: uuid.UUID, market_name: str, outcome: str) -> Optional[uuid.UUID]:
    base_name, _, line_text = market_name.partition(":")
    stmt = select(Market).where(
        Market.event_id == event_id,
        or_(Market.market_type == market_name, Market.name == market_name, Market.market_type == base_name),
    )
    if line_text:
        try:
            stmt = stmt.where(Market.line_value == float(line_text))
        except ValueError:
            stmt = stmt.where(Market.market_type == market_name)
    market = session.execute(stmt).scalars().first()
    if market is None:
        return None
    selection = session.execute(
        select(MarketSelection).where(
            MarketSelection.market_id == market.id,
            MarketSelection.outcome == outcome,
        )
    ).scalars().first()
    return selection.id if selection is not None else None


def _fresh_line(session: Session, event_id: uuid.UUID, market: str, outcome: str, max_age: float):
    """Latest quoted price for this side, or None when the line is older than the policy window."""
    base, _, line = str(market or "").partition(":")
    stmt = select(OddsSnapshot).where(
        OddsSnapshot.event_id == event_id,
        OddsSnapshot.market_type == base,
        OddsSnapshot.outcome == outcome,
    )
    if line:
        try:
            stmt = stmt.where(OddsSnapshot.line_value == float(line))
        except ValueError:
            return None
    row = session.execute(stmt.order_by(OddsSnapshot.observed_at.desc())).scalars().first()
    if row is None:
        return None
    if (_now() - _aware(row.observed_at)).total_seconds() > max_age:
        return None
    return row


def _already_placed(session: Session, event_id: uuid.UUID, market: str, outcome: str) -> bool:
    row = session.execute(
        select(Bet.id).where(
            Bet.event_id == event_id,
            Bet.market == market,
            Bet.outcome == outcome,
            Bet.status.in_(PLACED_STATUSES),
        )
    ).first()
    return row is not None


def _active_model(session: Session, sport_code: str, model_name: str, version_tag: str) -> bool:
    row = session.execute(
        select(ModelVersion.id).where(
            ModelVersion.sport_code == sport_code,
            ModelVersion.model_name == model_name,
            ModelVersion.version_tag == version_tag,
            ModelVersion.status == "ACTIVE",
        )
    ).first()
    return row is not None


def post_bet_manager(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Call the only endpoint that can register a virtual bet."""
    import httpx

    base = os.getenv("BET_MANAGER_URL", "http://bet-manager:8000").rstrip("/")
    with httpx.Client(timeout=20.0) as client:
        response = client.post(f"{base}/api/proposals/evaluate", json=payload)
        response.raise_for_status()
        return response.json()


def execute_confirmed(
    session: Session,
    limit: int = 10,
    submit: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Turn fresh ACTIVE tennis predictions into bet-manager proposals."""
    from tennis_adapter.policy import load_tennis_policy, market_limits, model_commits_market, windows_allow
    from tennis_adapter.stake import expected_value, probability_edge, quote_stake

    policy = load_tennis_policy()
    sender = submit or post_bet_manager
    llm_required = os.getenv("LLM_REQUIRED", "true").lower() in ("1", "true", "yes")
    pipeline = CombinedDecisionPipeline(
        DecisionPipelineConfig(llm_enabled=True, llm_required=llm_required, min_edge=0.0, min_confidence=0.0)
    )
    rows = (
        session.query(Event, MLPrediction)
        .join(MLPrediction, MLPrediction.event_id == Event.id)
        .filter(Event.status == "live")
        .filter(Event.sport_code == "tennis")
        .filter(MLPrediction.is_shadow.is_(False))
        .filter(MLPrediction.has_positive_edge.is_(True))
        .order_by(MLPrediction.created_at.desc())
        .limit(limit * 5)
        .all()
    )
    seen = set()
    submitted: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    for event, prediction in rows:
        key = (str(event.id), prediction.market, prediction.predicted_outcome)
        if key in seen:
            continue
        seen.add(key)
        if len(submitted) >= limit:
            break
        detail = prediction.outcomes_detail or {}
        model_name = str(detail.get("model_name") or "")
        reason_key = {"event_id": str(event.id), "market": prediction.market}
        if not model_name or not model_commits_market(model_name, prediction.model_version, prediction.market):
            skipped.append({**reason_key, "reason": "MODEL_MARKET_MISMATCH"})
            continue
        if not _active_model(session, "tennis", model_name, prediction.model_version):
            skipped.append({**reason_key, "reason": "MODEL_NOT_ACTIVE"})
            continue
        if _already_placed(session, event.id, prediction.market, prediction.predicted_outcome):
            skipped.append({**reason_key, "reason": "ALREADY_PLACED"})
            continue
        selection_id = _selection_id(session, event.id, prediction.market, prediction.predicted_outcome)
        if selection_id is None:
            skipped.append({**reason_key, "reason": "SELECTION_NOT_FOUND"})
            continue
        fresh = _fresh_line(
            session,
            event.id,
            prediction.market,
            prediction.predicted_outcome,
            float(policy.max_context_age_seconds),
        )
        if fresh is None:
            skipped.append({**reason_key, "reason": "STALE_LINE"})
            continue
        odds = float(fresh.odds or 0.0)
        probability = float(prediction.model_probability or 0.0)
        allowed, window_reason = windows_allow(probability, odds, prediction.market, policy)
        if not allowed:
            skipped.append({**reason_key, "reason": window_reason or "ODDS_OUT_OF_WINDOW"})
            continue
        if expected_value(probability, odds) <= 0.0:
            skipped.append({**reason_key, "reason": "NEGATIVE_EV"})
            continue
        candidate = CandidateItem(
            event_id=str(event.id),
            sport_code="tennis",
            participant_a=event.participant_a_name or "Participant A",
            participant_b=event.participant_b_name or "Participant B",
            market=prediction.market,
            selection=prediction.predicted_outcome,
            selection_id=str(selection_id),
            odds=odds,
            model_probability=probability,
            market_probability=round(1.0 / odds, 4) if odds > 1 else 1.0,
            edge=round(probability_edge(probability, odds), 4),
            expected_value=float(prediction.expected_value if prediction.expected_value is not None else 0.0),
            confidence=float(prediction.confidence or 0.0),
            model_version=prediction.model_version,
            model_name=model_name,
            ml_prediction_id=str(prediction.id),
            data_quality_score=float(event.quality_score if event.quality_score is not None else 100.0),
            config_version="tennis.cfg",
            is_shortlisted=True,
            is_shadow=False,
        )
        decision = pipeline.process_candidate(session, candidate)
        if decision.status != "PROPOSED" or not decision.proposal_id:
            skipped.append({**reason_key, "reason": decision.status, "detail": decision.reason})
            continue
        proposal = session.get(BetProposal, uuid.UUID(decision.proposal_id))
        if proposal is None:
            skipped.append({**reason_key, "reason": "PROPOSAL_MISSING"})
            continue
        verdict = pipeline._last_verdict
        from bankroll.service import BankrollService

        account = BankrollService.get_or_create_account(session)
        exposure_cap = float(os.getenv("MAX_EXPOSURE_FRACTION", "0.20"))
        room = float(account.initial_balance) * exposure_cap - float(account.locked_exposure)
        quote = quote_stake(
            balance=float(account.balance or 0.0),
            odds=odds,
            probability=probability,
            data_quality=candidate.data_quality_score,
            research_quality=None if verdict is None else float(verdict.research_quality),
            llm_confidence=None if verdict is None else float(verdict.confidence),
            llm_verdict=None if verdict is None else verdict.verdict,
            injury_risk=0.0 if verdict is None else float(verdict.injury_risk_score),
            fatigue_risk=0.0 if verdict is None else float(verdict.fatigue_risk_score),
            news_risk=0.0 if verdict is None else float(verdict.news_risk_score),
            contradictions=0 if verdict is None else len(verdict.contradictions),
            exposure_room=room,
            policy=policy,
        )
        if quote["stake"] <= 0:
            skipped.append({**reason_key, "reason": quote["reason"] or "STAKE_TOO_LOW"})
            continue
        proposal.suggested_stake = quote["stake"]
        proposal.edge = quote["expected_value"]
        session.commit()
        payload = {
            "proposal_id": str(proposal.id),
            "event_id": str(event.id),
            "sport_code": "tennis",
            "market": prediction.market,
            "outcome": prediction.predicted_outcome,
            "selection_id": str(selection_id),
            "bookmaker_odds": odds,
            "line_observed_at": _aware(fresh.observed_at).isoformat(),
            "fair_odds": float(proposal.fair_odds),
            "model_probability": probability,
            "edge": quote["expected_value"],
            "suggested_stake": quote["stake"],
            "ml_prediction_id": str(prediction.id),
            "llm_decision_id": str(proposal.llm_decision_id) if proposal.llm_decision_id else None,
            "model_name": model_name,
            "model_version": prediction.model_version,
            "enforce_sport_policy": True,
            "policy_min_edge": float(market_limits(prediction.market, policy)["min_edge"]),
            "data_quality_score": candidate.data_quality_score,
            "research_quality": None if verdict is None else float(verdict.research_quality),
            "llm_verdict": None if verdict is None else verdict.verdict,
            "contradictions": 0 if verdict is None else len(verdict.contradictions),
            "expected_value": quote["expected_value"],
            "config_version": "tennis.cfg",
        }
        try:
            response = sender(payload)
        except Exception as exc:
            logger.warning("bet-manager rejected transport for %s: %s", event.id, exc)
            skipped.append({**reason_key, "reason": "BET_MANAGER_UNAVAILABLE"})
            continue
        submitted.append(
            {
                "event_id": str(event.id),
                "proposal_id": str(proposal.id),
                "status": "SUBMITTED_TO_BET_MANAGER",
                "stake": quote["stake"],
                "potential_win": quote["potential_win"],
                "max_loss": quote["max_loss"],
                "expected_value": quote["expected_value"],
                "bet_id": response.get("bet_id"),
                "is_accepted": response.get("is_accepted"),
                "rejection_code": response.get("rejection_code"),
            }
        )
    return {"submitted": submitted, "skipped": skipped}
