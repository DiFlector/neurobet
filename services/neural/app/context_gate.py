"""Search, then LLM numbers, then the numeric model. An empty search does not stop the cycle."""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any, Dict, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger("neural.context")

_FROZEN_ATTEMPTS: set[str] = set()

NEUTRAL_CONTEXT = {
    "context_side_a": 0.5,
    "injury_risk_score": 0.0,
    "fatigue_risk_score": 0.0,
    "news_risk_score": 0.0,
}


def research_state(session, event_id) -> str:
    """waiting, empty, or ready. A missing run is still waiting for the search queue."""
    from db.models.research import WebEvidence, WebResearchRun

    evidence = session.query(WebEvidence.id).filter(WebEvidence.event_id == event_id).first()
    if evidence is not None:
        return "ready"
    run = (
        session.query(WebResearchRun)
        .filter(WebResearchRun.event_id == event_id)
        .order_by(WebResearchRun.created_at.desc())
        .first()
    )
    if run is None or run.status in ("queued", "running"):
        return "waiting"
    return "empty"


def context_scores(
    session,
    event,
    odds_a: Optional[float],
    odds_b: Optional[float],
    allow_llm_request: bool = True,
) -> tuple[Optional[Dict[str, float]], bool]:
    """
    (None, False) while search is still running or the LLM call is deferred.
    Otherwise the numbers and whether this call performed an LLM request.
    """
    state = research_state(session, event.id)
    if state == "waiting":
        return None, False
    stored = _stored_scores(session, event.id)
    if stored is not None:
        return _overlay_abstract(session, event, stored), False
    if not allow_llm_request:
        return None, False
    fresh = _ask_llm(session, event, odds_a, odds_b, with_sources=state == "ready")
    return _overlay_abstract(session, event, fresh or dict(NEUTRAL_CONTEXT)), True


def context_side_from_event(event) -> Optional[float]:
    """The match-level side saved when the match finished. None if it was never stored."""
    meta = getattr(event, "metadata_json", None)
    if not isinstance(meta, dict) or "context_side_a" not in meta:
        return None
    return _unit(meta.get("context_side_a"), 0.5)


def abstract_side_from_decisions(rows) -> Optional[float]:
    """Latest Tennis Abstract probability already written on this match."""
    for row in rows:
        raw = row.raw_json if isinstance(getattr(row, "raw_json", None), dict) else {}
        if raw.get("context_side_source") != "tennis_abstract":
            continue
        if "context_side_a" not in raw:
            continue
        return _unit(raw.get("context_side_a"), 0.5)
    return None


def freeze_finished_context(session, limit: int = 5) -> int:
    """Copy the forecast onto the finished match. One number, no extra document."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy.orm.attributes import flag_modified

    from db.models.events import Event
    from db.models.predictions import LLMDecision

    cutoff = datetime.now(timezone.utc) - timedelta(hours=18)
    pending = (
        session.query(Event)
        .filter(Event.sport_code == "tennis")
        .filter(Event.status.in_(("finished", "completed")))
        .filter(Event.finished_at.isnot(None))
        .filter(Event.finished_at >= cutoff)
        .filter(Event.metadata_json["context_side_a"].astext.is_(None))
        .order_by(Event.finished_at.desc())
        .limit(max(1, limit))
        .all()
    )
    saved = 0
    for event in pending:
        key = str(event.id)
        if key in _FROZEN_ATTEMPTS:
            continue
        _FROZEN_ATTEMPTS.add(key)
        decisions = (
            session.query(LLMDecision)
            .filter(LLMDecision.event_id == event.id)
            .order_by(LLMDecision.created_at.desc())
            .all()
        )
        side = abstract_side_from_decisions(decisions)
        if side is None:
            from .abstract_forecast import match_win_probability

            meta = event.metadata_json if isinstance(event.metadata_json, dict) else {}
            side = match_win_probability(
                str(meta.get("raw_tournament") or ""),
                event.participant_a_name or "",
                event.participant_b_name or "",
            )
        if side is None:
            continue
        merged = dict(event.metadata_json or {})
        merged["context_side_a"] = side
        event.metadata_json = merged
        flag_modified(event, "metadata_json")
        saved += 1
    if saved:
        session.flush()
    return saved


def apply_context_features(feature_vector, predictor, scores: Dict[str, float]) -> None:
    names = (predictor.checkpoint_data or {}).get("feature_names") or []
    for key, default in NEUTRAL_CONTEXT.items():
        if key in names:
            feature_vector.features[key] = float(scores.get(key, default))


def _stored_scores(session, event_id) -> Optional[Dict[str, float]]:
    from db.models.predictions import LLMDecision

    rows = (
        session.query(LLMDecision)
        .filter(LLMDecision.event_id == event_id)
        .order_by(LLMDecision.created_at.desc())
        .all()
    )
    chosen = None
    for row in rows:
        raw = row.raw_json if isinstance(row.raw_json, dict) else {}
        if "context_side_a" not in raw:
            continue
        chosen = raw
        if raw.get("context_role") == "pre_model":
            break
    if chosen is None:
        return None
    return {
        "context_side_a": _unit(chosen.get("context_side_a"), 0.5),
        "injury_risk_score": _unit(chosen.get("injury_risk_score"), 0.0),
        "fatigue_risk_score": _unit(chosen.get("fatigue_risk_score"), 0.0),
        "news_risk_score": _unit(chosen.get("news_risk_score"), 0.0),
    }


def _overlay_abstract(session, event, scores: Dict[str, float]) -> Dict[str, float]:
    """Replace the neutral side with the Tennis Abstract match probability when the pair is listed."""
    from .abstract_forecast import match_win_probability

    meta = event.metadata_json if isinstance(getattr(event, "metadata_json", None), dict) else {}
    probability = match_win_probability(
        str(meta.get("raw_tournament") or ""),
        getattr(event, "participant_a_name", "") or "",
        getattr(event, "participant_b_name", "") or "",
    )
    if probability is None:
        return scores
    updated = dict(scores)
    updated["context_side_a"] = probability
    if str(getattr(event, "status", "") or "") in ("live", "prematch"):
        _store_abstract_side(session, event.id, probability)
    return updated


def _store_abstract_side(session, event_id, probability: float) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from db.models.predictions import LLMDecision

    rows = (
        session.query(LLMDecision)
        .filter(LLMDecision.event_id == event_id)
        .order_by(LLMDecision.created_at.desc())
        .all()
    )
    chosen = None
    for row in rows:
        raw = row.raw_json if isinstance(row.raw_json, dict) else {}
        if "context_side_a" not in raw:
            continue
        chosen = row
        if raw.get("context_role") == "pre_model":
            break
    if chosen is None:
        return
    raw = chosen.raw_json if isinstance(chosen.raw_json, dict) else {}
    current = _unit(raw.get("context_side_a"), 0.5)
    if abs(current - probability) < 0.01 and raw.get("context_side_source") == "tennis_abstract":
        return
    merged = dict(raw)
    merged["context_side_a"] = probability
    merged["context_side_source"] = "tennis_abstract"
    chosen.raw_json = merged
    flag_modified(chosen, "raw_json")
    session.flush()


def _unit(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return min(1.0, max(0.0, number))


def _ask_llm(session, event, odds_a: Optional[float], odds_b: Optional[float], with_sources: bool) -> Optional[Dict[str, float]]:
    from db.models.predictions import LLMDecision
    from db.models.research import WebDocument, WebEvidence

    research = []
    if with_sources:
        rows = (
            session.query(WebEvidence, WebDocument)
            .join(WebDocument, WebEvidence.document_id == WebDocument.id)
            .filter(WebEvidence.event_id == event.id)
            .limit(5)
            .all()
        )
        for evidence, doc in rows:
            research.append(
                {
                    "title": doc.title or "Источник",
                    "domain": doc.source_domain or "unknown",
                    "snippet": (evidence.snippet or "")[:500],
                }
            )
    odds = odds_a if odds_a and odds_a > 1 else (odds_b if odds_b and odds_b > 1 else 1.01)
    payload = {
        "event": {
            "sport": event.sport_code or "tennis",
            "home": event.participant_a_name or "A",
            "away": event.participant_b_name or "B",
            "status": event.status or "live",
            "score": event.current_score or {},
        },
        "market": {
            "type": "match_winner",
            "selection": "unknown",
            "odds": float(odds),
        },
        "ml": {
            "probability": 0.5,
            "market_probability": 0.5,
            "edge": 0.0,
            "confidence": 0.0,
            "model_version": "pending",
        },
        "research": research,
        "constraints": {"simulation_only": True},
    }
    url = os.getenv("LLM_URL", "http://llm:8000/api/analyze")
    req = Request(url, data=json.dumps(payload).encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urlopen(req, timeout=25) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        logger.info("LLM context unavailable for %s: %s", event.id, exc)
        _store_fallback(session, event.id)
        return dict(NEUTRAL_CONTEXT)
    body["context_role"] = "pre_model"
    scores = {
        "context_side_a": _unit(body.get("context_side_a"), 0.5),
        "injury_risk_score": _unit(body.get("injury_risk_score"), 0.0),
        "fatigue_risk_score": _unit(body.get("fatigue_risk_score"), 0.0),
        "news_risk_score": _unit(body.get("news_risk_score"), 0.0),
    }
    session.add(
        LLMDecision(
            id=uuid.uuid4(),
            event_id=event.id,
            llm_version=f"{body.get('model_identifier', 'llm')}:{body.get('prompt_version', 'neurobet_llm_v1.1')}"[:64],
            verdict=str(body.get("verdict") or "INSUFFICIENT_DATA")[:32],
            confidence_adjustment=0.0,
            injury_risk="high" if scores["injury_risk_score"] >= 0.7 else "low",
            fatigue_risk="high" if scores["fatigue_risk_score"] >= 0.7 else "low",
            reasoning=str(body.get("summary") or "")[:1000] or "Контекст без числовой модели.",
            raw_json=body,
        )
    )
    session.flush()
    return scores


def _store_fallback(session, event_id) -> None:
    from db.models.predictions import LLMDecision

    session.add(
        LLMDecision(
            id=uuid.uuid4(),
            event_id=event_id,
            llm_version="fallback:neurobet_llm_v1.1",
            verdict="INSUFFICIENT_DATA",
            confidence_adjustment=0.0,
            injury_risk="unknown",
            fatigue_risk="unknown",
            reasoning="Контекст модели ещё не получен.",
            raw_json={"verdict": "INSUFFICIENT_DATA", "context_role": "pre_model", **NEUTRAL_CONTEXT},
        )
    )
    session.flush()
