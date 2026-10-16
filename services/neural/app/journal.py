"""Statistical journal of model sides. Shadows and zero-stake decisions are kept here."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.exc import IntegrityError


def explain_commit(
    *,
    model_name: str,
    version: str,
    is_shadow: bool,
    market: str,
    outcome: str,
    probability: float,
    odds: float,
    edge: float,
) -> str:
    """Short reason stored with the decision. Money is mentioned only to mark that none was staked."""
    role = "Тень" if is_shadow else "Боевая модель"
    if market == "match_winner":
        market_label = "матч"
    elif str(market).startswith("set_winner:"):
        market_label = f"сет {str(market).split(':', 1)[1]}"
    else:
        market_label = str(market)
    money = (
        "Денег нет: тень на баланс не ставит."
        if is_shadow
        else "Ставка на баланс появляется только если bet-manager пропустит решение."
    )
    return (
        f"{role} {model_name} {version} выбрала {outcome} на рынке «{market_label}». "
        f"Вероятность {float(probability):.3f}, коэффициент {float(odds):.2f}, перевес {float(edge):.3f}. "
        f"Та же сторона подтвердилась следующим срезом. {money}"
    )


def record_model_bet(
    session,
    *,
    event_id,
    sport_code: str,
    model_name: str,
    model_version: str,
    is_shadow: bool,
    market: str,
    outcome: str,
    probability: float,
    bookmaker_odds: float,
    fair_odds: float,
    edge: float,
    expected_value: float,
    confidence: float,
    decided_at: Optional[datetime] = None,
    detail: Optional[dict] = None,
) -> bool:
    """Insert one committed side. A repeated decision for the same line and market is ignored."""
    from db.models.predictions import ModelBetJournal

    existing = (
        session.query(ModelBetJournal.id)
        .filter(
            ModelBetJournal.event_id == event_id,
            ModelBetJournal.model_name == model_name,
            ModelBetJournal.model_version == model_version,
            ModelBetJournal.is_shadow.is_(is_shadow),
            ModelBetJournal.market == market,
        )
        .first()
    )
    if existing:
        return False
    row = ModelBetJournal(
        event_id=event_id,
        sport_code=sport_code,
        model_name=model_name,
        model_version=model_version,
        is_shadow=is_shadow,
        market=market,
        outcome=outcome,
        model_probability=probability,
        bookmaker_odds=bookmaker_odds,
        fair_odds=fair_odds,
        edge=edge,
        expected_value=expected_value,
        confidence=confidence,
        stake_amount=0,
        reason=explain_commit(
            model_name=model_name,
            version=model_version,
            is_shadow=is_shadow,
            market=market,
            outcome=outcome,
            probability=probability,
            odds=bookmaker_odds,
            edge=edge,
        ),
        detail=detail or {},
        decided_at=decided_at or datetime.now(timezone.utc),
        result_status="pending",
    )
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError:
        return False
    return True


def _set_score_winner(games_a, games_b) -> Optional[str]:
    try:
        left = int(games_a)
        right = int(games_b)
    except (TypeError, ValueError):
        return None
    if left == right:
        return None
    high, low = max(left, right), min(left, right)
    if high < 6 or high > 7 or (high == 6 and high - low < 2) or (high == 7 and low < 5):
        return None
    return "player_a" if left > right else "player_b"


def winners_from_score_text(text: Any) -> dict:
    """Set winners parsed from a final score like ``0:2 (3-6 4-6)``."""
    import re

    raw = str(text or "")
    start, end = raw.find("("), raw.rfind(")")
    if start < 0 or end <= start:
        return {}
    winners = {}
    for index, pair in enumerate(re.finditer(r"(\d+)\s*[-:]\s*(\d+)", raw[start + 1:end]), start=1):
        winner = _set_score_winner(pair.group(1), pair.group(2))
        if winner is None:
            break
        winners[str(index)] = winner
    return winners


def _score_text(event) -> str:
    score = event.current_score or {}
    if isinstance(score, dict):
        return str(score.get("final_score") or score.get("score") or "")
    return str(score or "")


def _match_is_finished(event) -> bool:
    status = str(getattr(event, "status", "") or "").lower()
    score = event.current_score or {}
    score_status = str(score.get("status") or "").upper() if isinstance(score, dict) else ""
    return status in ("finished", "completed", "ended", "cancelled") or score_status in (
        "FINISHED",
        "COMPLETED",
        "CANCELLED",
        "VOID",
        "RETIRED",
    ) or getattr(event, "finished_at", None) is not None


def _known_set_winners(event) -> dict:
    meta = dict(event.metadata_json or {})
    known = {str(key): value for key, value in (meta.get("set_winners") or {}).items() if value in ("player_a", "player_b")}
    parsed = winners_from_score_text(_score_text(event))
    if parsed and parsed != {key: known.get(key) for key in parsed}:
        known.update(parsed)
        meta["set_winners"] = known
        event.metadata_json = meta
    return known


def _winner_for_market(event, market: str) -> Optional[str]:
    if str(market).startswith("set_winner:"):
        number = str(market).split(":", 1)[1]
        known = _known_set_winners(event)
        winner = str(known.get(number) or "")
        if winner in ("player_a", "player_b"):
            return winner
        if _match_is_finished(event) and number.isdigit() and int(number) > len(known):
            return ""
        return None
    if market != "match_winner":
        return None
    score = event.current_score or {}
    winner = str(score.get("winner") or "")
    if winner in ("player_a", "player_b", "team_a", "team_b"):
        return winner
    status = str(score.get("status") or event.status or "").upper()
    if status in ("CANCELLED", "VOID", "RETIRED") and not winner:
        return ""
    return None


def settle_model_bets(session, event) -> int:
    """Fill won/lost once the set or the match winner is known. Pending rows stay pending."""
    from db.models.predictions import ModelBetJournal

    rows = session.query(ModelBetJournal).filter(ModelBetJournal.event_id == event.id).all()
    changed = 0
    now = datetime.now(timezone.utc)
    for row in rows:
        if row.result_status != "pending":
            continue
        winner = _winner_for_market(event, row.market)
        if winner is None:
            continue
        if winner == "":
            row.result_status = "void"
            row.result_winner = None
        else:
            row.result_winner = winner
            row.result_status = "won" if row.outcome == winner else "lost"
        row.settled_at = now
        changed += 1
    if changed:
        session.flush()
    return changed


def journal_market_counts(session) -> dict:
    """Pending, void, won and lost counts by market. This is the settlement monitor."""
    from db.models.predictions import ModelBetJournal

    counts = {}
    rows = session.query(ModelBetJournal.market, ModelBetJournal.result_status).all()
    for market, status in rows:
        slot = counts.setdefault(str(market), {"pending": 0, "void": 0, "won": 0, "lost": 0})
        key = str(status or "pending")
        if key not in slot:
            slot[key] = 0
        slot[key] += 1
    return counts


def settle_open_journal(session) -> int:
    """Close pending rows once the set or match winner is known, including finished events."""
    from db.models.events import Event
    from db.models.predictions import ModelBetJournal

    event_ids = [
        event_id
        for (event_id,) in (
            session.query(ModelBetJournal.event_id)
            .filter(ModelBetJournal.result_status == "pending")
            .distinct()
            .all()
        )
    ]
    changed = 0
    for event_id in event_ids:
        event = session.get(Event, event_id)
        if event is None:
            continue
        changed += settle_model_bets(session, event)
    return changed


def backfill_from_predictions(session) -> int:
    """Copy already committed predictions into the journal. Watches without a side are skipped."""
    from db.models.predictions import MLPrediction

    rows = (
        session.query(MLPrediction)
        .filter(MLPrediction.has_positive_edge.is_(True))
        .order_by(MLPrediction.created_at.asc())
        .all()
    )
    added = 0
    for row in rows:
        detail: dict[str, Any] = dict(row.outcomes_detail or {})
        model_name = str(detail.get("model_name") or row.model_version)
        if record_model_bet(
            session,
            event_id=row.event_id,
            sport_code=row.sport_code,
            model_name=model_name,
            model_version=row.model_version,
            is_shadow=bool(row.is_shadow),
            market=row.market or "match_winner",
            outcome=row.predicted_outcome,
            probability=float(row.model_probability),
            bookmaker_odds=float(row.bookmaker_odds),
            fair_odds=float(row.fair_odds),
            edge=float(row.edge),
            expected_value=float(row.expected_value or 0),
            confidence=float(row.confidence or 0),
            decided_at=row.created_at,
            detail={"source": "ml_predictions", "outcomes": detail.get("outcomes") or []},
        ):
            added += 1
    return added
