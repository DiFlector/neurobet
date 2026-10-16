"""Training jobs published by the backend and executed inside the neural service."""

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger("neural.jobs")

STREAM = "training.jobs"
GROUP = "neural-training"
CONSUMER = "neural-1"


def _client():
    import redis

    url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    return redis.Redis.from_url(url, decode_responses=True, socket_timeout=2)


def _ensure_group(client) -> None:
    try:
        client.xgroup_create(STREAM, GROUP, id="$", mkstream=True)
    except Exception as exc:
        if "BUSYGROUP" not in str(exc):
            raise


def claim_training_job() -> Optional[Dict[str, Any]]:
    """One pending or new training job. None when the stream is empty or Redis is down."""
    try:
        client = _client()
        _ensure_group(client)
        for stream_id in ("0", ">"):
            entries = client.xreadgroup(GROUP, CONSUMER, {STREAM: stream_id}, count=1, block=1)
            job = _first_job(entries)
            if job is None:
                continue
            if client.get(_done_key(job["message_id"])):
                client.xack(STREAM, GROUP, job["redis_id"])
                continue
            fails = int(client.incr(_fail_key(job["redis_id"])))
            if fails > 3:
                logger.error("Dropping training job %s after %d failures", job["message_id"], fails)
                client.xack(STREAM, GROUP, job["redis_id"])
                continue
            return job
    except Exception as exc:
        logger.warning("Training queue is unavailable: %s", exc)
    return None


def finish_training_job(job: Dict[str, Any], ok: bool) -> None:
    try:
        client = _client()
        if ok:
            client.set(_done_key(job["message_id"]), "1", ex=60 * 60 * 24 * 30)
            client.xack(STREAM, GROUP, job["redis_id"])
            return
        logger.info("Training job %s stays pending for a retry", job["message_id"])
    except Exception as exc:
        logger.warning("Could not update training job %s: %s", job.get("message_id"), exc)


def run_training_job(job: Dict[str, Any]) -> None:
    from db.connection import SessionLocal

    from .trainer import ModelTrainer

    payload = job.get("payload") or {}
    sport_code = str(payload.get("sport_code") or "tennis").lower()
    model_type = str(payload.get("model_type") or "gb").lower()
    bump = str(payload.get("bump") or "patch")
    if bump not in ("patch", "minor", "major", "test"):
        bump = "patch"
    hyperparameters = payload.get("hyperparameters") if isinstance(payload.get("hyperparameters"), dict) else {}
    trainer = ModelTrainer(random_seed=42)
    with SessionLocal() as session:
        if model_type == "all":
            trainer.train_all(session=session, sport_code=sport_code, bump=bump)
        else:
            trainer.train_model(
                session=session,
                model_type=model_type,
                sport_code=sport_code,
                market="match_winner",
                bump=bump,
                hyperparameters=hyperparameters,
            )


def _first_job(entries) -> Optional[Dict[str, Any]]:
    import json

    if not entries:
        return None
    for _stream, messages in entries:
        for redis_id, fields in messages:
            if not fields:
                continue
            raw_payload = fields.get("payload") or "{}"
            try:
                payload = json.loads(raw_payload) if raw_payload else {}
            except json.JSONDecodeError:
                payload = {}
            return {
                "redis_id": redis_id,
                "message_id": fields.get("message_id") or redis_id,
                "payload": payload,
            }
    return None


def _done_key(message_id: str) -> str:
    return f"neurobet:train-done:{message_id}"


def _fail_key(redis_id: str) -> str:
    return f"neurobet:train-fails:{redis_id}"
