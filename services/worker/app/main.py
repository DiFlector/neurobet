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
    CG_WORKER,
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

    # Initial healthcheck heartbeat
    with open("/tmp/healthy", "w") as f:
        f.write(datetime.now(timezone.utc).isoformat())

    logger.info("Worker ready and listening for events on streams.")

    last_settle_sync = 0.0
    backend_url = os.getenv("BACKEND_INTERNAL_URL", "http://backend:8000")

    while RUNNING:
        try:
            # Poll streams
            await consumer_training.process_one(process_training_job, count=5, block_ms=1000)
            await consumer_bets.process_one(process_validated_bet, count=5, block_ms=1000)

            # Periodic official settlement verification via https://fon.bet/results (every ~30s)
            now_ts = asyncio.get_event_loop().time()
            if now_ts - last_settle_sync > 30:
                last_settle_sync = now_ts
                try:
                    import httpx
                    async with httpx.AsyncClient(timeout=10) as client:
                        resp = await client.post(f"{backend_url}/api/settlement/sync")
                        if resp.status_code == 200:
                            data = resp.json()
                            if data.get("bets_settled", 0) > 0 or data.get("events_matched", 0) > 0:
                                logger.info("Settlement sync via Fonbet Results completed: %s", data)
                except Exception as ex:
                    logger.debug("Settlement sync request failed: %s", ex)

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
