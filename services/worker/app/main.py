import asyncio
import logging
import os
import signal
from datetime import datetime, timezone
import redis.asyncio as aioredis

from streams import (
    AsyncStreamConsumerGroup,
    MessageEnvelope,
    STREAM_TRAINING_JOBS,
    STREAM_BET_VALIDATED,
    STREAM_FONBET_RAW,
    CG_WORKER,
    CG_EVENT_PROCESSOR,
)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s UTC [%(levelname)s] [worker] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("worker")

RUNNING = True


def handle_shutdown(signum, frame):
    global RUNNING
    logger.info("Shutdown signal received (%s). Exiting gracefully...", signum)
    RUNNING = False


signal.signal(signal.SIGINT, handle_shutdown)
signal.signal(signal.SIGTERM, handle_shutdown)


async def process_training_job(envelope: MessageEnvelope):
    logger.info("Processing training job message: %s", envelope.message_id)
    # Background training runner hooks here in Phase 17


async def process_validated_bet(envelope: MessageEnvelope):
    logger.info("Processing validated bet notification: %s", envelope.message_id)


def _post_json(url: str, timeout: float = 60.0) -> dict:
    """Synchronous POST helper (stdlib) for backend internal RPCs."""
    import json
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    req = Request(url, method="POST", data=b"")
    req.add_header("Content-Type", "application/json")
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise RuntimeError(f"HTTP {exc.code}: {body[:200]}") from exc
    except URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc


def process_fonbet_raw(envelope: MessageEnvelope) -> None:
    """Write a collector snapshot into events. Raises so the consumer can retry."""
    from .ingest import persist_fonbet_snapshot

    count = persist_fonbet_snapshot(envelope.payload or {})
    logger.info("Fonbet raw snapshot stored %d events (message %s).", count, envelope.message_id)


async def main():
    redis_url = os.getenv("REDIS_URL", "redis://:Nb_Rd_3vF8nK2pW7sY1tZ4mQ6bC9eH5jL@redis:6379/0")
    logger.info("Worker started. Connecting to Redis Streams...")

    r = aioredis.from_url(redis_url, decode_responses=True)
    await r.ping()
    logger.info("Connected to Redis successfully.")

    consumer_training = AsyncStreamConsumerGroup(
        redis_client=r,
        stream_name=STREAM_TRAINING_JOBS,
        group_name=CG_WORKER,
        consumer_name="worker_inst_1",
    )
    await consumer_training.create_group()

    consumer_bets = AsyncStreamConsumerGroup(
        redis_client=r,
        stream_name=STREAM_BET_VALIDATED,
        group_name=CG_WORKER,
        consumer_name="worker_inst_1",
    )
    await consumer_bets.create_group()

    consumer_raw = AsyncStreamConsumerGroup(
        redis_client=r,
        stream_name=STREAM_FONBET_RAW,
        group_name=CG_EVENT_PROCESSOR,
        consumer_name="worker_inst_1",
    )
    await consumer_raw.create_group()

    # Initial healthcheck heartbeat
    with open("/tmp/healthy", "w") as f:
        f.write(datetime.now(timezone.utc).isoformat())

    logger.info("Worker ready and listening for events on streams.")

    last_settle_sync = 0.0
    backend_url = os.getenv("BACKEND_INTERNAL_URL", "http://backend:8000")

    while RUNNING:
        try:
            # Poll streams
            await consumer_training.process_one(process_training_job, count=5, block_ms=200)
            await consumer_bets.process_one(process_validated_bet, count=5, block_ms=200)
            await consumer_raw.process_one(process_fonbet_raw, count=5, block_ms=200)

            # Periodic settlement + LLM decision sync (every ~30s)
            now_ts = asyncio.get_event_loop().time()
            if now_ts - last_settle_sync > 30:
                last_settle_sync = now_ts
                try:
                    settle_data = await asyncio.to_thread(
                        _post_json, f"{backend_url}/api/settlement/sync", 30.0
                    )
                    if settle_data.get("bets_settled", 0) > 0 or settle_data.get("events_matched", 0) > 0:
                        logger.info("Settlement sync via Fonbet Results completed: %s", settle_data)

                    llm_data = await asyncio.to_thread(
                        _post_json,
                        f"{backend_url}/api/scheduler/sync-ml-predictions?limit=10&dispatch=true",
                        90.0,
                    )
                    if llm_data.get("enqueued_count", 0) > 0 or (
                        llm_data.get("dispatch") or {}
                    ).get("dispatched_count", 0) > 0:
                        logger.info(
                            "LLM sync completed: enqueued=%s dispatched=%s queue=%s",
                            llm_data.get("enqueued_count"),
                            (llm_data.get("dispatch") or {}).get("dispatched_count"),
                            llm_data.get("queue_size"),
                        )
                except Exception as ex:
                    logger.warning("Settlement/LLM sync request failed: %s", ex)

            # Update heartbeat
            with open("/tmp/healthy", "w") as f:
                f.write(datetime.now(timezone.utc).isoformat())
        except Exception as exc:
            logger.error("Error in worker event loop: %s", exc)
            await asyncio.sleep(2)

    await r.aclose()
    logger.info("Worker stopped cleanly.")


if __name__ == "__main__":
    asyncio.run(main())
