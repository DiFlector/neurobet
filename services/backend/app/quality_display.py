"""Match-card quality index: Fonbet snapshot score plus search and LLM context."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def display_quality(
    data_score: Optional[float],
    research: str,
    llm_verdict: Optional[str],
) -> Tuple[float, List[str]]:
    """research is waiting, empty, or ready. 100 only when the snapshot, search, and LLM are all fine."""
    base = 100.0 if data_score is None else float(data_score)
    score = base
    reasons: List[str] = []
    if research == "waiting":
        score -= 10
        reasons.append("Поиск в ожидании обработки")
    elif research == "empty":
        score -= 15
        reasons.append("Нет информации о матче в интернете")
    if not llm_verdict or llm_verdict == "INSUFFICIENT_DATA":
        score -= 10
        reasons.append("Контекст модели ещё не получен")
    if base < 100:
        reasons.append("В снимке Fonbet есть пропуски или аномалии")
    if not reasons:
        reasons.append("Снимок полный, источники найдены, контекст модели получен")
    return max(0.0, round(score, 2)), reasons


def quality_by_event(session, events: List[Any]) -> Dict[Any, Dict[str, Any]]:
    from db.models.predictions import LLMDecision
    from db.models.research import WebEvidence, WebResearchRun

    ids = [event.id for event in events]
    if not ids:
        return {}
    runs = (
        session.query(WebResearchRun)
        .filter(WebResearchRun.event_id.in_(ids))
        .order_by(WebResearchRun.created_at.desc())
        .all()
    )
    latest_run = {}
    for run in runs:
        latest_run.setdefault(run.event_id, run)
    with_evidence = {
        row[0]
        for row in session.query(WebEvidence.event_id).filter(WebEvidence.event_id.in_(ids)).distinct()
    }
    decisions = (
        session.query(LLMDecision)
        .filter(LLMDecision.event_id.in_(ids))
        .order_by(LLMDecision.created_at.desc())
        .all()
    )
    latest_llm = {}
    for decision in decisions:
        latest_llm.setdefault(decision.event_id, decision)

    result = {}
    for event in events:
        if event.id in with_evidence:
            research = "ready"
        else:
            run = latest_run.get(event.id)
            if run is None or run.status in ("queued", "running"):
                research = "waiting"
            else:
                research = "empty"
        decision = latest_llm.get(event.id)
        score, reasons = display_quality(
            float(event.quality_score) if event.quality_score is not None else 100.0,
            research,
            decision.verdict if decision else None,
        )
        result[event.id] = {"quality_score": score, "quality_reasons": reasons}
    return result
