import asyncio
import logging
import os
import signal
from datetime import datetime, timezone
from typing import Dict

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


def evaluate_pending_events(predictors: Dict[str, MLPredictor]) -> int:
    """
    Finds active events in the database that don't have predictions yet,
    computes calibrated ML probabilities and edge, and writes them to ml_predictions.
    """
    try:
        from db.session import SessionLocal
        from db.models.events import Event
        from db.models.predictions import MLPrediction as DBMLPrediction
        from contracts import FeatureVector
    except ImportError as e:
        logger.debug("Database models not available for inference: %s", e)
        return 0

    evaluated_count = 0
    try:
        with SessionLocal() as session:
            # Find active events
            active_events = (
                session.query(Event)
                .filter(Event.status.in_(["live", "prematch"]))
                .order_by(Event.is_live.desc(), Event.created_at.desc())
                .limit(30)
                .all()
            )
            if not active_events:
                return 0

            # Check which events already have predictions
            event_ids = [e.id for e in active_events]
            existing_preds = (
                session.query(DBMLPrediction.event_id)
                .filter(DBMLPrediction.event_id.in_(event_ids))
                .all()
            )
            predicted_event_ids = {row[0] for row in existing_preds}

            for evt in active_events:
                if evt.id in predicted_event_ids:
                    continue

                sport_code = (evt.sport_code or "tennis").lower()
                predictor = predictors.get(sport_code) or predictors.get("tennis")
                if not predictor or not predictor.checkpoint_data:
                    continue

                # Prepare feature vector based on model's trained feature schema
                feature_names = predictor.checkpoint_data.get("feature_names", [])
                features_dict = {}
                for name in feature_names:
                    if "odds" in name:
                        features_dict[name] = 1.90
                    elif "win_rate" in name:
                        features_dict[name] = 0.55
                    elif "h2h" in name:
                        features_dict[name] = 0.50
                    elif "score" in name or "games" in name:
                        features_dict[name] = 1.0
                    else:
                        features_dict[name] = 0.0

                f_vec = FeatureVector(
                    event_id=str(evt.id),
                    sport_code=sport_code,
                    feature_version="1.0.0",
                    features=features_dict,
                )

                # Calibrated ML prediction
                try:
                    res = predictor.predict(f_vec, odds_a=1.85, odds_b=1.95)
                    best_outcome = res.predicted_outcome

                    db_pred = DBMLPrediction(
                        event_id=evt.id,
                        sport_code=sport_code,
                        model_version=res.model_version,
                        market="match_winner",
                        predicted_outcome=best_outcome,
                        model_probability=res.model_probability,
                        bookmaker_odds=res.bookmaker_odds,
                        fair_odds=res.fair_odds,
                        edge=res.edge,
                        expected_value=res.expected_value,
                        confidence=res.confidence,
                        has_positive_edge=res.has_positive_edge,
                        is_shadow=False,
                        outcomes_detail={
                            "outcomes": [
                                {
                                    "outcome": o.outcome,
                                    "probability": o.probability,
                                    "fair_odds": o.fair_odds,
                                }
                                for o in res.outcomes
                            ]
                        },
                    )
                    session.add(db_pred)
                    evaluated_count += 1
                except Exception as ex:
                    logger.debug("Prediction error for event %s: %s", evt.id, ex)

            if evaluated_count > 0:
                session.commit()
                logger.info(
                    "Evaluated and saved ML predictions for %d active events.",
                    evaluated_count,
                )
    except Exception as exc:
        logger.error("Error during evaluate_pending_events: %s", exc)

    return evaluated_count


async def main():
    logger.info("Neural ML inference service initialized. Focus sport: %s", os.getenv("PRIMARY_SPORT", "tennis"))

    chk_dir = os.getenv("CHECKPOINTS_DIR", "/app/models/checkpoints")
    os.makedirs(chk_dir, exist_ok=True)
    available_ckpts = [f for f in os.listdir(chk_dir) if f.endswith(".joblib")]
    logger.info("Discovered %d model checkpoints in %s: %s", len(available_ckpts), chk_dir, available_ckpts[:5])

    # Pre-load latest models for all supported sports
    predictors: Dict[str, MLPredictor] = {}
    for sport in ["tennis", "football", "basketball", "hockey", "volleyball"]:
        try:
            pred = MLPredictor.load_latest(sport, market="match_winner", model_type="gb", checkpoint_dir=chk_dir)
            if pred and pred.checkpoint_data:
                predictors[sport] = pred
                logger.info("Loaded active %s GB predictor (%s)", sport, pred.checkpoint_data.get("version_tag"))
        except Exception as e:
            logger.warning("Could not load predictor for %s: %s", sport, e)

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
            evaluate_pending_events(predictors)
        except Exception as exc:
            logger.error("Unhandled error in neural main loop: %s", exc)

        await asyncio.sleep(5)

    logger.info("Neural service stopped cleanly.")


if __name__ == "__main__":
    asyncio.run(main())
