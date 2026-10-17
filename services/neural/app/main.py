import asyncio
import logging
import os
import signal
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .composite_predictor import is_composite_checkpoint, load_predictor
from .inference import MLPredictor

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s UTC [%(levelname)s] [neural] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("neural")

RUNNING = True
_training_lock = threading.Lock()


def handle_shutdown(signum, frame):
    global RUNNING
    logger.info("Shutdown signal received (%s). Exiting gracefully...", signum)
    RUNNING = False


signal.signal(signal.SIGINT, handle_shutdown)
signal.signal(signal.SIGTERM, handle_shutdown)

_logged_skips: set[str] = set()


def _log_skip_once(key: str, message: str) -> None:
    if key in _logged_skips:
        return
    _logged_skips.add(key)
    logger.info(message)


def load_registry_predictors() -> tuple[Dict[str, MLPredictor], Dict[str, List[MLPredictor]]]:
    """Load each sport's own ACTIVE and SHADOW match-winner checkpoints.

    A sport with a missing or unreadable artifact is left out. No other sport's file is used.
    """
    active: Dict[str, MLPredictor] = {}
    shadow: Dict[str, List[MLPredictor]] = {}
    try:
        from db.connection import SessionLocal
        from db.models.ml_registry import ModelVersion
    except ImportError as exc:
        logger.warning("Model registry is unavailable: %s", exc)
        return active, shadow

    try:
        with SessionLocal() as session:
            rows = (
                session.query(ModelVersion)
                .filter(
                    ModelVersion.market == "match_winner",
                    ModelVersion.status.in_(["ACTIVE", "SHADOW"]),
                )
                .order_by(ModelVersion.created_at.desc())
                .all()
            )
    except Exception as exc:
        logger.warning("Could not read model registry: %s", exc)
        return active, shadow

    from .versioning import is_test_tag

    for row in rows:
        sport = (row.sport_code or "").strip().lower()
        if not sport:
            logger.warning("Skip registry row %s: empty sport_code", row.version_tag)
            continue
        if is_test_tag(row.version_tag):
            logger.warning("Skip test registry row %s: a test tag is not loaded", row.version_tag)
            continue
        if row.status == "ACTIVE" and sport in active:
            logger.warning(
                "Extra ACTIVE model for %s ignored: %s",
                sport,
                row.version_tag,
            )
            continue
        predictor = load_predictor(row.artifact_path)
        if not predictor.checkpoint_data:
            logger.warning(
                "Sport %s stays off: artifact for %s is missing or unreadable (%s)",
                sport,
                row.version_tag,
                row.artifact_path,
            )
            continue
        version_tag = predictor.checkpoint_data.get("version_tag")
        if row.status == "ACTIVE":
            active[sport] = predictor
            logger.info("Loaded active %s predictor (%s)", sport, version_tag)
        else:
            shadow.setdefault(sport, []).append(predictor)
            logger.info("Loaded shadow %s predictor (%s)", sport, version_tag)
    return active, shadow


def _snapshot_key(states, odds_rows) -> Optional[str]:
    moments = []
    if states:
        moments.append(states[-1].observed_at)
    if odds_rows:
        moments.append(odds_rows[-1].observed_at)
    if not moments:
        return None
    return max(moments).isoformat()


def _model_line(predictor: MLPredictor) -> str:
    return str((predictor.checkpoint_data or {}).get("model_name") or "")


def _latest_watch(session, event_id, version: str, is_shadow: bool, line: str = "", market: str = ""):
    from db.models.predictions import MLPrediction as DBMLPrediction

    rows = (
        session.query(DBMLPrediction)
        .filter(
            DBMLPrediction.event_id == event_id,
            DBMLPrediction.model_version == version,
            DBMLPrediction.is_shadow.is_(is_shadow),
            DBMLPrediction.has_positive_edge.is_(False),
        )
        .order_by(DBMLPrediction.created_at.desc())
        .limit(20)
        .all()
    )
    for row in rows:
        detail = row.outcomes_detail or {}
        if market and row.market != market:
            continue
        if detail.get("watch") and str(detail.get("model_name") or "") == line:
            return row
    return None


def _mark_observed(session, evt, version: str, is_shadow: bool, line: str, market: str, side: str) -> None:
    """Keep a confirmed tennis side for the match verdict without registering a stake."""
    watch = _latest_watch(session, evt.id, version, is_shadow, line, market)
    if watch is None:
        return
    stored = dict(watch.outcomes_detail or {})
    stored["observed_side"] = side
    stored["decision_reason"] = "SET_OBSERVED"
    watch.outcomes_detail = stored
    watch.has_positive_edge = False
    watch.predicted_outcome = side


def _observed_side(session, event_id, version: str, is_shadow: bool, line: str, market: str) -> Optional[str]:
    watch = _latest_watch(session, event_id, version, is_shadow, line, market)
    if watch is None:
        return None
    side = str((watch.outcomes_detail or {}).get("observed_side") or "")
    return side or None


def _side_gate(res, side: Optional[str], market: str = "match_winner", sport_code: str = "") -> tuple:
    from .strategy import stake_gate

    if not side:
        return True, ""
    if str(sport_code or "").strip().lower() != "tennis":
        return True, ""
    best = next((item for item in (res.outcomes or []) if item.outcome == side), None)
    if best is None:
        return False, "LOW_PROBABILITY"
    return stake_gate(best.model_probability, best.bookmaker_odds, market, sport_code)


def _apply_watch_score(
    row,
    res,
    side: Optional[str],
    snapshot_at: str,
    line: str,
    eligible: bool = True,
    reason: str = "",
) -> None:
    chosen = side or getattr(res, "best_outcome", None)
    best = next((item for item in (res.outcomes or []) if item.outcome == chosen), None)
    if best is None and res.outcomes:
        best = max(res.outcomes, key=lambda item: item.model_probability)
    row.predicted_outcome = (best.outcome if best else None) or side or "wait"
    row.model_probability = float(best.model_probability) if best else 0.0
    row.bookmaker_odds = float(best.bookmaker_odds) if best else 0.0
    row.fair_odds = float(best.fair_odds) if best else 0.0
    row.edge = float(best.edge) if best else 0.0
    row.expected_value = float(best.expected_value) if best else 0.0
    row.confidence = float(best.confidence) if best else 0.0
    row.has_positive_edge = False
    if not eligible and reason:
        row.created_at = datetime.now(timezone.utc)
    row.outcomes_detail = {
        "watch": True,
        "snapshot_at": snapshot_at,
        "side": side,
        "model_name": line,
        "decision_eligible": bool(eligible),
        "decision_reason": reason,
        "outcomes": [
            {
                "outcome": item.outcome,
                "probability": item.model_probability,
                "fair_odds": item.fair_odds,
            }
            for item in (res.outcomes or [])
        ],
    }


def _remember_watch(
    session,
    evt,
    sport_code: str,
    res,
    side: Optional[str],
    snapshot_at: str,
    is_shadow: bool,
    line: str = "",
    market: str = "",
    eligible: bool = True,
    reason: str = "",
) -> None:
    from db.models.predictions import MLPrediction as DBMLPrediction

    watch = _latest_watch(session, evt.id, res.model_version, is_shadow, line, market)
    if watch is None:
        watch = DBMLPrediction(
            event_id=evt.id,
            sport_code=sport_code,
            model_version=res.model_version,
            market=market or res.market or "match_winner",
            predicted_outcome=side or "wait",
            model_probability=0.0,
            bookmaker_odds=0.0,
            fair_odds=0.0,
            edge=0.0,
            expected_value=0.0,
            confidence=0.0,
            has_positive_edge=False,
            is_shadow=is_shadow,
            outcomes_detail={},
        )
        session.add(watch)
    _apply_watch_score(watch, res, side, snapshot_at, line, eligible, reason)


def _consider_composite(
    session,
    evt,
    sport_code: str,
    predictor,
    f_vec,
    odds_a,
    odds_b,
    is_shadow: bool,
    states,
    odds_rows,
    llm_budget: List[int],
    market_name: Optional[str] = None,
) -> bool:
    """Confirm each inner line on its own, then commit the composite only at 3/3."""
    from .composite_vote import advance_components
    from .context_gate import apply_context_features, context_scores

    snapshot_at = _snapshot_key(states, odds_rows)
    if snapshot_at is None:
        return False
    scores, requested = context_scores(session, evt, odds_a, odds_b, allow_llm_request=llm_budget[0] > 0)
    if scores is None:
        return False
    if requested:
        llm_budget[0] -= 1
    apply_context_features(f_vec, predictor, scores)
    stored_market = market_name or "match_winner"
    line = _model_line(predictor)
    if _market_confirmed(session, evt.id, predictor.checkpoint_data.get("version_tag"), is_shadow, line, stored_market):
        return False
    votes = predictor.component_votes(f_vec, odds_a, odds_b, stored_market)
    if float((f_vec.features or {}).get("match_wait") or 0.0) >= 0.5:
        votes = [{**item, "side": None, "reason": item.get("reason") or "MATCH_WAIT"} for item in votes]
    watch = _latest_watch(session, evt.id, predictor.checkpoint_data.get("version_tag"), is_shadow, line, stored_market)
    detail = (watch.outcomes_detail or {}) if watch else {}
    if detail.get("snapshot_at") == snapshot_at:
        return False
    state = advance_components((detail.get("composite") or {}).get("components"), votes)
    state["market"] = stored_market
    side = state.get("side") if state.get("selected") else None
    _remember_watch(
        session,
        evt,
        sport_code,
        predictor.predict(f_vec, odds_a=odds_a, odds_b=odds_b, is_shadow=is_shadow),
        side or state.get("side"),
        snapshot_at,
        is_shadow,
        line,
        stored_market,
        state["selected"],
        "" if state["selected"] else state["display_phase"],
    )
    watch = _latest_watch(session, evt.id, predictor.checkpoint_data.get("version_tag"), is_shadow, line, stored_market)
    if watch is not None:
        stored = dict(watch.outcomes_detail or {})
        stored["composite"] = state
        stored["model_name"] = line
        watch.outcomes_detail = stored
        if not state["selected"]:
            watch.predicted_outcome = state.get("side") or "wait"
    if not state["selected"] or not side:
        return False
    members = [item for item in state["components"] if item.get("confirmed_side") == side]
    if len(members) < 3:
        return False
    from .set_match import set_is_observation_only
    from tennis_adapter.policy import model_commits_market

    version = predictor.checkpoint_data.get("version_tag")
    if set_is_observation_only(stored_market, sport_code) or not model_commits_market(line, version, stored_market):
        _mark_observed(session, evt, version, is_shadow, line, stored_market, side)
        return False
    probability = round(sum(float(item["probability"]) for item in members) / len(members), 4)
    odds = round(sum(float(item["odds"]) for item in members) / len(members), 4)
    edge = round(sum(float(item["edge"]) for item in members) / len(members), 4)
    from db.models.predictions import MLPrediction as DBMLPrediction

    session.add(
        DBMLPrediction(
            event_id=evt.id,
            sport_code=sport_code,
            model_version=predictor.checkpoint_data.get("version_tag"),
            market=stored_market,
            predicted_outcome=side,
            model_probability=probability,
            bookmaker_odds=odds,
            fair_odds=max(1.01, round(1.0 / max(0.001, probability), 2)),
            edge=edge,
            expected_value=round(probability * (odds - 1.0) - (1.0 - probability), 4),
            confidence=1.0,
            has_positive_edge=True,
            is_shadow=is_shadow,
            outcomes_detail={
                "confirmed_at": snapshot_at,
                "model_name": line,
                "decision_eligible": True,
                "decision_reason": "",
                "composite": state,
            },
        )
    )
    from .journal import record_model_bet

    record_model_bet(
        session,
        event_id=evt.id,
        sport_code=sport_code,
        model_name=line,
        model_version=predictor.checkpoint_data.get("version_tag"),
        is_shadow=is_shadow,
        market=stored_market,
        outcome=side,
        probability=probability,
        bookmaker_odds=odds,
        fair_odds=max(1.01, round(1.0 / max(0.001, probability), 2)),
        edge=edge,
        expected_value=round(probability * (odds - 1.0) - (1.0 - probability), 4),
        confidence=1.0,
        detail={"snapshot_at": snapshot_at, "composite": state},
    )
    return True


def _consider_snapshot(
    session,
    evt,
    sport_code: str,
    predictor: MLPredictor,
    f_vec,
    odds_a,
    odds_b,
    is_shadow: bool,
    states,
    odds_rows,
    llm_budget: List[int],
    market_name: Optional[str] = None,
    peer_probabilities: Optional[List[float]] = None,
) -> bool:
    """Commit only when this snapshot confirms the previous one. A watch is not a recommendation."""
    from .context_gate import apply_context_features, context_scores

    snapshot_at = _snapshot_key(states, odds_rows)
    if snapshot_at is None:
        return False
    if is_composite_checkpoint(getattr(predictor, "checkpoint_data", None)):
        return _consider_composite(
            session,
            evt,
            sport_code,
            predictor,
            f_vec,
            odds_a,
            odds_b,
            is_shadow,
            states,
            odds_rows,
            llm_budget,
            market_name,
        )
    scores, requested = context_scores(session, evt, odds_a, odds_b, allow_llm_request=llm_budget[0] > 0)
    if scores is None:
        return False
    if requested:
        llm_budget[0] -= 1
    apply_context_features(f_vec, predictor, scores)
    res = predictor.predict(
        f_vec,
        odds_a=odds_a,
        odds_b=odds_b,
        is_shadow=is_shadow,
        peer_probabilities=peer_probabilities,
    )
    stored_market = market_name or res.market or "match_winner"
    side = res.best_outcome if res.has_positive_edge else None
    if float((f_vec.features or {}).get("match_wait") or 0.0) >= 0.5:
        side = None
    eligible, reason = _side_gate(res, side, stored_market, sport_code)
    line = _model_line(predictor)
    watch = _latest_watch(session, evt.id, res.model_version, is_shadow, line, stored_market)
    detail = (watch.outcomes_detail or {}) if watch else {}
    from .confirmation import next_watch_action

    action, _updated = next_watch_action(detail, snapshot_at, side, eligible, reason, required=2)
    if action == "unchanged":
        return False
    if action != "commit":
        _remember_watch(
            session,
            evt,
            sport_code,
            res,
            side,
            snapshot_at,
            is_shadow,
            line,
            stored_market,
            eligible,
            reason,
        )
        return False
    previous = detail.get("side")
    if side and previous and side == previous and eligible:
        best = next((item for item in res.outcomes if item.outcome == side), None)
        if best is None:
            return False
        from .set_match import set_is_observation_only
        from tennis_adapter.policy import model_commits_market

        if set_is_observation_only(stored_market, sport_code) or not model_commits_market(line, res.model_version, stored_market):
            _remember_watch(
                session, evt, sport_code, res, side, snapshot_at, is_shadow, line, stored_market, True, "SET_OBSERVED"
            )
            _mark_observed(session, evt, res.model_version, is_shadow, line, stored_market, side)
            return False
        from db.models.predictions import MLPrediction as DBMLPrediction

        session.add(
            DBMLPrediction(
                event_id=evt.id,
                sport_code=sport_code,
                model_version=res.model_version,
                market=stored_market,
                predicted_outcome=best.outcome,
                model_probability=best.model_probability,
                bookmaker_odds=best.bookmaker_odds,
                fair_odds=best.fair_odds,
                edge=best.edge,
                expected_value=best.expected_value,
                confidence=best.confidence,
                has_positive_edge=True,
                is_shadow=is_shadow,
                outcomes_detail={
                    "confirmed_at": snapshot_at,
                    "model_name": line,
                    "decision_eligible": True,
                    "decision_reason": "",
                    "disagreement": None if peer_probabilities is None else round(
                        max(peer_probabilities + [best.model_probability]) - min(peer_probabilities + [best.model_probability]),
                        4,
                    ) if peer_probabilities else 0.0,
                    "outcomes": [
                        {
                            "outcome": item.outcome,
                            "probability": item.model_probability,
                            "fair_odds": item.fair_odds,
                        }
                        for item in res.outcomes
                    ],
                },
            )
        )
        from .journal import record_model_bet

        record_model_bet(
            session,
            event_id=evt.id,
            sport_code=sport_code,
            model_name=line,
            model_version=res.model_version,
            is_shadow=is_shadow,
            market=stored_market,
            outcome=best.outcome,
            probability=best.model_probability,
            bookmaker_odds=best.bookmaker_odds,
            fair_odds=best.fair_odds,
            edge=best.edge,
            expected_value=best.expected_value,
            confidence=best.confidence,
            detail={
                "snapshot_at": snapshot_at,
                "outcomes": [
                    {
                        "outcome": item.outcome,
                        "probability": item.model_probability,
                        "fair_odds": item.fair_odds,
                    }
                    for item in res.outcomes
                ],
            },
        )
        return True
    _remember_watch(
        session,
        evt,
        sport_code,
        res,
        side,
        snapshot_at,
        is_shadow,
        line,
        stored_market,
        eligible,
        reason,
    )
    return False


def _predictor_is_v2(predictor) -> bool:
    from .set_match import is_v2

    if predictor is None or not predictor.checkpoint_data:
        return False
    return is_v2(predictor.checkpoint_data.get("version_tag"))


def _scores_sets(predictor) -> bool:
    """Tennis v2 lines and the composite line both score the open set and the match."""
    if predictor is None or not predictor.checkpoint_data:
        return False
    return _predictor_is_v2(predictor) or is_composite_checkpoint(predictor.checkpoint_data)


def _event_has_v2(predictor, shadows) -> bool:
    if _scores_sets(predictor):
        return True
    return any(_scores_sets(item) for item in shadows)


def _open_set_prices(odds_rows) -> Dict[int, tuple]:
    latest: Dict[int, Dict[str, float]] = {}
    for row in odds_rows:
        if getattr(row, "market_type", None) != "set_winner" or row.line_value is None:
            continue
        number = int(float(row.line_value))
        slot = latest.setdefault(number, {})
        outcome = str(row.outcome or "")
        try:
            price = float(row.odds)
        except (TypeError, ValueError):
            continue
        if outcome in ("player_a", "player_b") and price > 1.0:
            slot[outcome] = price
    return {
        number: (slot["player_a"], slot["player_b"])
        for number, slot in latest.items()
        if "player_a" in slot and "player_b" in slot
    }


def _market_confirmed(session, event_id, version: str, is_shadow: bool, line: str, market: str) -> bool:
    from db.models.predictions import MLPrediction as DBMLPrediction

    rows = (
        session.query(DBMLPrediction)
        .filter(
            DBMLPrediction.event_id == event_id,
            DBMLPrediction.model_version == version,
            DBMLPrediction.is_shadow.is_(is_shadow),
            DBMLPrediction.has_positive_edge.is_(True),
            DBMLPrediction.market == market,
        )
        .all()
    )
    return any(str((row.outcomes_detail or {}).get("model_name") or "") == line for row in rows)


def _confirmed_side(session, event_id, version: str, is_shadow: bool, line: str, market: str) -> Optional[str]:
    from db.models.predictions import MLPrediction as DBMLPrediction

    rows = (
        session.query(DBMLPrediction)
        .filter(
            DBMLPrediction.event_id == event_id,
            DBMLPrediction.model_version == version,
            DBMLPrediction.is_shadow.is_(is_shadow),
            DBMLPrediction.market == market,
        )
        .order_by(DBMLPrediction.created_at.desc())
        .limit(5)
        .all()
    )
    for row in rows:
        if str((row.outcomes_detail or {}).get("model_name") or "") != line:
            continue
        if row.has_positive_edge:
            return row.predicted_outcome
    return None


def _peer_probabilities(predictors, current, f_vec, odds_a, odds_b) -> List[float]:
    """P(player A) from the other loaded tennis lines. Used only as disagreement."""
    values: List[float] = []
    for model, is_shadow in predictors:
        if model is current or is_composite_checkpoint(getattr(model, "checkpoint_data", None)):
            continue
        try:
            probe = model.predict(f_vec, odds_a=odds_a, odds_b=odds_b, is_shadow=is_shadow, peer_probabilities=[])
        except Exception:
            continue
        for item in probe.outcomes:
            if str(item.outcome).endswith("_a"):
                values.append(float(item.model_probability))
                break
    return values


def _score_tennis_v2(session, evt, f_vec, predictor, shadows, odds_rows, llm_budget) -> int:
    """Score every quoted set, then the match only when those sets agree."""
    from features import registry as feature_registry
    from .set_match import match_verdict, set_is_committable

    predictors = []
    if predictor and _scores_sets(predictor):
        predictors.append((predictor, False))
    for shadow in shadows:
        if _scores_sets(shadow):
            predictors.append((shadow, True))
    finished = {}
    for key, winner in ((evt.metadata_json or {}).get("set_winners") or {}).items():
        if winner in ("player_a", "player_b"):
            finished[int(key)] = winner
    prices = _open_set_prices(odds_rows)
    current_set = int(float((f_vec.features or {}).get("current_set") or 1))
    best_of = 5 if max([current_set, *finished.keys(), *prices.keys()], default=1) >= 4 else 3
    saved = 0
    original = dict(f_vec.features)
    odds_a = original.get("current_odds_player_a")
    odds_b = original.get("current_odds_player_b")
    for model, is_shadow in predictors:
        version = str((model.checkpoint_data or {}).get("version_tag") or "")
        line = _model_line(model)
        open_sides: Dict[int, Optional[str]] = {}
        for number, (set_a, set_b) in prices.items():
            if number in finished or not set_is_committable(number, current_set, finished, best_of):
                continue
            market = f"set_winner:{number}"
            from .set_match import set_is_observation_only

            if set_is_observation_only(number, "tennis"):
                observed = _observed_side(session, evt.id, version, is_shadow, line, market)
                if observed:
                    open_sides[number] = observed
                    continue
            if _market_confirmed(session, evt.id, version, is_shadow, line, market):
                open_sides[number] = _confirmed_side(session, evt.id, version, is_shadow, line, market)
                continue
            f_vec.features.clear()
            f_vec.features.update(original)
            builder = feature_registry.get("tennis")
            if builder is not None and hasattr(builder, "apply_market_line"):
                builder.apply_market_line(
                    f_vec.features,
                    odds_rows,
                    f_vec.feature_cutoff_timestamp,
                    market_type="set_winner",
                    line_value=number,
                )
            f_vec.features["market_is_set"] = 1.0
            f_vec.features["set_index"] = float(number)
            f_vec.features["set_has_started"] = 1.0 if current_set >= number else 0.0
            f_vec.features["market_odds_a"] = set_a
            f_vec.features["market_odds_b"] = set_b
            f_vec.features["match_wait"] = 0.0
            if not f_vec.features["set_has_started"]:
                f_vec.features["games_diff"] = 0.0
                f_vec.features["points_diff"] = 0.0
                f_vec.features["total_games_current_set"] = 0.0
            peers = _peer_probabilities(predictors, model, f_vec, set_a, set_b)
            if _consider_snapshot(
                session, evt, "tennis", model, f_vec, set_a, set_b, is_shadow, [], odds_rows, llm_budget, market, peers
            ):
                saved += 1
            open_sides[number] = _observed_side(session, evt.id, version, is_shadow, line, market) or _confirmed_side(
                session, evt.id, version, is_shadow, line, market
            )
        match_market = "match_winner"
        if not _market_confirmed(session, evt.id, version, is_shadow, line, match_market):
            f_vec.features.clear()
            f_vec.features.update(original)
            f_vec.features["market_is_set"] = 0.0
            f_vec.features["set_index"] = 0.0
            f_vec.features["set_has_started"] = 1.0
            f_vec.features["market_odds_a"] = float(odds_a or 0.0)
            f_vec.features["market_odds_b"] = float(odds_b or 0.0)
            probe = model.predict(f_vec, odds_a=odds_a, odds_b=odds_b, is_shadow=is_shadow)
            edge_side = probe.best_outcome
            edge_value = 0.0
            if edge_side:
                chosen = next((item for item in probe.outcomes if item.outcome == edge_side), None)
                edge_value = float(chosen.edge) if chosen else 0.0
            allowed = match_verdict(
                finished,
                open_sides,
                bool(original.get("tape_points_even")),
                edge_side,
                edge_value,
                best_of,
            )
            f_vec.features["match_wait"] = 0.0 if allowed else 1.0
            peers = _peer_probabilities(predictors, model, f_vec, odds_a, odds_b)
            if _consider_snapshot(
                session, evt, "tennis", model, f_vec, odds_a, odds_b, is_shadow, [], odds_rows, llm_budget, match_market, peers
            ):
                saved += 1
        f_vec.features.clear()
        f_vec.features.update(original)
    return saved


def evaluate_pending_events(
    predictors: Dict[str, MLPredictor],
    shadow_predictors: Dict[str, List[MLPredictor]] | None = None,
) -> int:
    """
    Следит за live и prematch, пока боевая модель или тень ещё не подтвердили сторону.
    Один срез с перевесом ещё не ставка: нужен следующий срез с той же стороной.
    """
    shadows = shadow_predictors or {}
    try:
        from sqlalchemy import and_, exists, or_

        from db.connection import SessionLocal
        from db.models.events import Event, EventStateSnapshot
        from db.models.odds import OddsSnapshot
        from db.models.predictions import MLPrediction as DBMLPrediction
        from features import registry as feature_registry
    except ImportError as e:
        logger.debug("Database models not available for inference: %s", e)
        return 0

    evaluated_count = 0
    try:
        with SessionLocal() as session:
            from .journal import settle_open_journal

            from .journal import journal_market_counts

            settled = settle_open_journal(session)
            if settled:
                session.commit()
                logger.info("Settled %d model journal rows with a known winner.", settled)
            try:
                from .context_gate import freeze_finished_context

                frozen = freeze_finished_context(session)
                if frozen:
                    session.commit()
                    logger.info("Saved context_side_a on %d finished matches.", frozen)
            except Exception as exc:
                logger.info("Could not save match context_side_a: %s", exc)
            counts = journal_market_counts(session)
            if counts:
                logger.info("Model journal by market: %s", counts)
            synthetic = or_(
                Event.source_event_id.like("sim_evt_%"),
                Event.source_event_id.like("ext_%"),
                Event.source_event_id.like("evt_%"),
                Event.source_event_id.like("dr_%"),
            )
            combat_done = exists().where(
                and_(
                    DBMLPrediction.event_id == Event.id,
                    DBMLPrediction.is_shadow.is_(False),
                    DBMLPrediction.has_positive_edge.is_(True),
                )
            )
            shadow_sports = list(shadows.keys())
            # A sport with shadow lines stays in the queue until each line confirms
            # its own row. An older shadow prediction must not hide the match.
            needs_work = ~combat_done
            if shadow_sports:
                needs_work = or_(needs_work, Event.sport_code.in_(shadow_sports))
            active_events = (
                session.query(Event)
                .filter(Event.status.in_(["live", "prematch"]))
                .filter(~synthetic)
                .filter(needs_work)
                .order_by(Event.is_live.desc(), Event.created_at.desc())
                .limit(30)
                .all()
            )
            if not active_events:
                session.commit()
                return 0

            cutoff = datetime.now(timezone.utc)
            llm_budget = [1]
            player_history = None
            for evt in active_events:
                sport_code = (evt.sport_code or "").strip().lower()
                if not sport_code:
                    _log_skip_once("empty-sport", "Skip events with an empty sport code")
                    continue
                predictor = predictors.get(sport_code)
                shadows_for_sport = shadows.get(sport_code, [])
                if (not predictor or not predictor.checkpoint_data) and not shadows_for_sport:
                    _log_skip_once(
                        f"off:{sport_code}",
                        f"Skip {sport_code} events: no model for this sport",
                    )
                    continue
                if feature_registry.get(sport_code) is None:
                    _log_skip_once(
                        f"features:{sport_code}",
                        f"Skip {sport_code} events: no feature builder for this sport",
                    )
                    continue

                odds_a, odds_b = _latest_outcome_odds(session, evt.id)
                if odds_a is None or odds_b is None:
                    logger.debug("Skip event %s: no match-winner odds snapshot", evt.id)
                    continue

                states = (
                    session.query(EventStateSnapshot)
                    .filter(
                        EventStateSnapshot.event_id == evt.id,
                        EventStateSnapshot.observed_at <= cutoff,
                    )
                    .order_by(EventStateSnapshot.observed_at.asc())
                    .all()
                )
                odds_rows = (
                    session.query(OddsSnapshot)
                    .filter(
                        OddsSnapshot.event_id == evt.id,
                        OddsSnapshot.observed_at <= cutoff,
                    )
                    .order_by(OddsSnapshot.observed_at.asc())
                    .all()
                )
                try:
                    f_vec = feature_registry.build(
                        event_id=evt.id,
                        sport_code=sport_code,
                        cutoff_time=cutoff,
                        event_snapshots=states,
                        odds_snapshots=odds_rows,
                    )
                except Exception as ex:
                    logger.debug("Skip event %s: feature build failed: %s", evt.id, ex)
                    continue
                if sport_code == "tennis":
                    if player_history is None:
                        from .players import PlayerHistory

                        player_history = PlayerHistory.load(session)
                    f_vec.features.update(
                        player_history.features(
                            evt.participant_a_name,
                            evt.participant_b_name,
                            cutoff,
                            exclude_event_id=str(evt.id),
                        )
                    )

                try:
                    if sport_code == "tennis" and _event_has_v2(predictor, shadows_for_sport):
                        evaluated_count += _score_tennis_v2(
                            session, evt, f_vec, predictor, shadows_for_sport, odds_rows, llm_budget
                        )
                    combat_done = (
                        session.query(DBMLPrediction.id)
                        .filter(
                            DBMLPrediction.event_id == evt.id,
                            DBMLPrediction.is_shadow.is_(False),
                            DBMLPrediction.has_positive_edge.is_(True),
                        )
                        .first()
                    )
                    if (
                        predictor
                        and predictor.checkpoint_data
                        and not _predictor_is_v2(predictor)
                        and combat_done is None
                        and _consider_snapshot(
                            session,
                            evt,
                            sport_code,
                            predictor,
                            f_vec,
                            odds_a,
                            odds_b,
                            False,
                            states,
                            odds_rows,
                            llm_budget,
                        )
                    ):
                        evaluated_count += 1
                    for shadow_predictor in shadows_for_sport:
                        if _scores_sets(shadow_predictor):
                            continue
                        version = (shadow_predictor.checkpoint_data or {}).get("version_tag")
                        line = _model_line(shadow_predictor)
                        prior = (
                            session.query(DBMLPrediction)
                            .filter(
                                DBMLPrediction.event_id == evt.id,
                                DBMLPrediction.is_shadow.is_(True),
                                DBMLPrediction.has_positive_edge.is_(True),
                                DBMLPrediction.model_version == version,
                            )
                            .all()
                        )
                        if any(str((row.outcomes_detail or {}).get("model_name") or "") == line for row in prior):
                            continue
                        if _consider_snapshot(
                            session,
                            evt,
                            sport_code,
                            shadow_predictor,
                            f_vec,
                            odds_a,
                            odds_b,
                            True,
                            states,
                            odds_rows,
                            llm_budget,
                        ):
                            evaluated_count += 1
                    from .journal import settle_model_bets

                    settle_model_bets(session, evt)
                except Exception as ex:
                    logger.debug("Prediction error for event %s: %s", evt.id, ex)

            session.commit()
            if evaluated_count > 0:
                logger.info(
                    "Evaluated and saved ML predictions for %d active events.",
                    evaluated_count,
                )
                _trigger_llm_sync(limit=min(evaluated_count, 15))
    except Exception as exc:
        logger.error("Error during evaluate_pending_events: %s", exc)

    return evaluated_count


def _trigger_llm_sync(limit: int = 15) -> None:
    """Ask backend to enqueue ML predictions lacking LLM verdicts and dispatch them."""
    import json
    from urllib.error import URLError, HTTPError
    from urllib.request import Request, urlopen

    backend_url = os.getenv("BACKEND_INTERNAL_URL", "http://backend:8000").rstrip("/")
    url = (
        f"{backend_url}/api/scheduler/sync-ml-predictions"
        f"?limit={int(limit)}&dispatch=true"
    )
    try:
        req = Request(url, method="POST", data=b"")
        req.add_header("Content-Type", "application/json")
        with urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            logger.info(
                "LLM sync after ML: enqueued=%s dispatched=%s",
                data.get("enqueued_count"),
                (data.get("dispatch") or {}).get("dispatched_count"),
            )
    except (URLError, HTTPError, TimeoutError, json.JSONDecodeError) as exc:
        logger.warning("LLM sync request error: %s", exc)


def _latest_outcome_odds(session, event_id):
    """Latest match-winner prices for both sides, or (None, None) when the line is missing."""
    from db.models.odds import OddsSnapshot

    rows = (
        session.query(OddsSnapshot)
        .filter(OddsSnapshot.event_id == event_id)
        .order_by(OddsSnapshot.observed_at.desc())
        .limit(20)
        .all()
    )
    odds_a = None
    odds_b = None
    for row in rows:
        outcome = (row.outcome or "").lower()
        price = float(row.odds) if row.odds is not None else None
        if price is None or price <= 1.0:
            continue
        if outcome in ("player_a", "team_a", "1", "p1") and odds_a is None:
            odds_a = price
        elif outcome in ("player_b", "team_b", "2", "p2") and odds_b is None:
            odds_b = price
        if odds_a is not None and odds_b is not None:
            break
    return odds_a, odds_b


_backtest_lock = threading.Lock()


def _start_backtest_job() -> None:
    """Run one queued historical replay beside inference. It does not touch the production ledger."""
    if not _backtest_lock.acquire(blocking=False):
        return

    def work() -> None:
        from .replay.jobs import claim_backtest_job, finish_backtest_job, run_backtest_job

        try:
            job = claim_backtest_job()
            if job is None:
                return
            try:
                run_backtest_job(job)
                finish_backtest_job(job, ok=True)
                logger.info("Backtest job %s finished", job.get("message_id"))
            except Exception:
                logger.exception("Backtest job %s failed", job.get("message_id"))
                finish_backtest_job(job, ok=False)
        finally:
            _backtest_lock.release()

    threading.Thread(target=work, name="neural-backtest", daemon=True).start()


def _start_training_job() -> None:
    """Run one queued training job beside the inference loop. The heartbeat stays on the main thread."""
    if not _training_lock.acquire(blocking=False):
        return

    def work() -> None:
        from .jobs import claim_training_job, finish_training_job, run_training_job

        try:
            job = claim_training_job()
            if job is None:
                return
            try:
                run_training_job(job)
                finish_training_job(job, ok=True)
                logger.info(
                    "Training job %s finished. Restart neural so the new shadow checkpoint is loaded.",
                    job.get("message_id"),
                )
            except Exception:
                logger.exception("Training job %s failed", job.get("message_id"))
                finish_training_job(job, ok=False)
        finally:
            _training_lock.release()

    threading.Thread(target=work, name="neural-training", daemon=True).start()


async def main():
    logger.info("Neural ML inference service initialized. Focus sport: %s", os.getenv("PRIMARY_SPORT", "tennis"))

    chk_dir = os.getenv("CHECKPOINTS_DIR", "/app/models/checkpoints")
    os.makedirs(chk_dir, exist_ok=True)
    available_ckpts = [f for f in os.listdir(chk_dir) if f.endswith(".joblib")]
    logger.info("Discovered %d model checkpoints in %s: %s", len(available_ckpts), chk_dir, available_ckpts[:5])

    predictors, shadow_predictors = load_registry_predictors()
    if not predictors:
        logger.warning("No active sport models loaded. Live inference stays off until an ACTIVE artifact is readable.")

    # Start embedded metrics HTTP server for Prometheus scraping on port 8000
    try:
        from http.server import HTTPServer, BaseHTTPRequestHandler
        import threading

        class NeuralMetricsHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/health":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"status":"healthy","service":"neural"}')
                elif self.path == "/metrics":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
                    self.end_headers()
                    output = (
                        f"# HELP neural_up Service status\n"
                        f"# TYPE neural_up gauge\n"
                        f"neural_up 1\n"
                        f"# HELP neural_checkpoints_count Count of local checkpoints\n"
                        f"# TYPE neural_checkpoints_count gauge\n"
                        f"neural_checkpoints_count {len(available_ckpts)}\n"
                    )
                    self.wfile.write(output.encode("utf-8"))
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, format, *args):
                pass  # suppress access logs to keep terminal quiet

        httpd = HTTPServer(("0.0.0.0", 8000), NeuralMetricsHandler)
        server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        server_thread.start()
        logger.info("Embedded neural HTTP metrics server started on port 8000 (/metrics, /health)")
    except Exception as e:
        logger.warning("Could not start embedded metrics server: %s", e)

    with open("/tmp/healthy", "w") as f:
        f.write(datetime.now(timezone.utc).isoformat())

    # Active inference loop
    while RUNNING:
        try:
            with open("/tmp/healthy", "w") as f:
                f.write(datetime.now(timezone.utc).isoformat())

            _start_training_job()
            _start_backtest_job()
            # Evaluate any events currently awaiting ML prediction
            evaluate_pending_events(predictors, shadow_predictors)
        except Exception as exc:
            logger.error("Unhandled error in neural main loop: %s", exc)

        await asyncio.sleep(5)

    logger.info("Neural service stopped cleanly.")


if __name__ == "__main__":
    asyncio.run(main())
