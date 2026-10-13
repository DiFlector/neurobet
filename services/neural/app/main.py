import asyncio
import logging
import os
import signal
from datetime import datetime, timezone
from typing import Dict, List

from .inference import MLPredictor

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s UTC [%(levelname)s] [neural] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("neural")

RUNNING = True


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

    for row in rows:
        sport = (row.sport_code or "").strip().lower()
        if not sport:
            logger.warning("Skip registry row %s: empty sport_code", row.version_tag)
            continue
        if row.status == "ACTIVE" and sport in active:
            logger.warning(
                "Extra ACTIVE model for %s ignored: %s",
                sport,
                row.version_tag,
            )
            continue
        predictor = MLPredictor(checkpoint_path=row.artifact_path)
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


def _save_prediction(session, evt, sport_code: str, predictor: MLPredictor, f_vec, odds_a, odds_b, is_shadow: bool) -> bool:
    from db.models.predictions import MLPrediction as DBMLPrediction

    res = predictor.predict(f_vec, odds_a=odds_a, odds_b=odds_b, is_shadow=is_shadow)
    best = next(
        (item for item in res.outcomes if item.outcome == res.best_outcome),
        None,
    )
    if best is None and res.outcomes:
        best = max(res.outcomes, key=lambda item: item.edge)
    if best is None:
        return False

    session.add(
        DBMLPrediction(
            event_id=evt.id,
            sport_code=sport_code,
            model_version=res.model_version,
            market=res.market or "match_winner",
            predicted_outcome=best.outcome,
            model_probability=best.model_probability,
            bookmaker_odds=best.bookmaker_odds,
            fair_odds=best.fair_odds,
            edge=best.edge,
            expected_value=best.expected_value,
            confidence=best.confidence,
            has_positive_edge=res.has_positive_edge,
            is_shadow=res.is_shadow,
            outcomes_detail={
                "outcomes": [
                    {
                        "outcome": item.outcome,
                        "probability": item.model_probability,
                        "fair_odds": item.fair_odds,
                    }
                    for item in res.outcomes
                ]
            },
        )
    )
    return True


def evaluate_pending_events(
    predictors: Dict[str, MLPredictor],
    shadow_predictors: Dict[str, List[MLPredictor]] | None = None,
) -> int:
    """
    Finds active events in the database that don't have predictions yet,
    computes calibrated ML probabilities and edge, and writes them to ml_predictions.
    """
    shadows = shadow_predictors or {}
    try:
        from sqlalchemy import and_, or_
        from sqlalchemy.orm import aliased

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
            synthetic = or_(
                Event.source_event_id.like("sim_evt_%"),
                Event.source_event_id.like("ext_%"),
                Event.source_event_id.like("evt_%"),
                Event.source_event_id.like("dr_%"),
            )
            combat = aliased(DBMLPrediction)
            active_events = (
                session.query(Event)
                .outerjoin(
                    combat,
                    and_(combat.event_id == Event.id, combat.is_shadow.is_(False)),
                )
                .filter(Event.status.in_(["live", "prematch"]))
                .filter(~synthetic)
                .filter(combat.id.is_(None))
                .order_by(Event.is_live.desc(), Event.created_at.desc())
                .limit(30)
                .all()
            )
            if not active_events:
                return 0

            cutoff = datetime.now(timezone.utc)
            for evt in active_events:
                sport_code = (evt.sport_code or "").strip().lower()
                if not sport_code:
                    _log_skip_once("empty-sport", "Skip events with an empty sport code")
                    continue
                predictor = predictors.get(sport_code)
                if not predictor or not predictor.checkpoint_data:
                    _log_skip_once(
                        f"off:{sport_code}",
                        f"Skip {sport_code} events: no active model for this sport",
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

                try:
                    if _save_prediction(session, evt, sport_code, predictor, f_vec, odds_a, odds_b, False):
                        evaluated_count += 1
                    for shadow_predictor in shadows.get(sport_code, []):
                        if _save_prediction(
                            session, evt, sport_code, shadow_predictor, f_vec, odds_a, odds_b, True
                        ):
                            evaluated_count += 1
                except Exception as ex:
                    logger.debug("Prediction error for event %s: %s", evt.id, ex)

            if evaluated_count > 0:
                session.commit()
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

            # Evaluate any events currently awaiting ML prediction
            evaluate_pending_events(predictors, shadow_predictors)
        except Exception as exc:
            logger.error("Unhandled error in neural main loop: %s", exc)

        await asyncio.sleep(5)

    logger.info("Neural service stopped cleanly.")


if __name__ == "__main__":
    asyncio.run(main())
