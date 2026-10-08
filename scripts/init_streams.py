#!/usr/bin/env python3
"""Initialize all canonical Redis Streams and consumer groups for Neurobet."""

import os
import sys
import logging
import redis

from streams.constants import ALL_STREAMS

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("init_streams")

REDIS_URL = os.getenv("REDIS_URL")
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_INTERNAL_PORT", os.getenv("REDIS_PORT", "6379")))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "Nb_Rd_3vF8nK2pW7sY1tZ4mQ6bC9eH5jL")

STREAM_GROUPS = {
    "fonbet.raw": ["cg.collector", "cg.event_processor"],
    "fonbet.events": ["cg.event_processor", "cg.research"],
    "fonbet.state": ["cg.neural", "cg.worker"],
    "fonbet.odds": ["cg.neural", "cg.worker"],
    "features.ready": ["cg.neural"],
    "ml.predictions": ["cg.llm", "cg.bet_manager"],
    "research.requests": ["cg.research"],
    "research.results": ["cg.llm"],
    "llm.requests": ["cg.llm"],
    "llm.results": ["cg.bet_manager"],
    "bet.proposals": ["cg.bet_manager"],
    "bet.validated": ["cg.bet_manager", "cg.worker"],
    "bet.executed": ["cg.bet_manager", "cg.worker"],
    "bet.settled": ["cg.bet_manager", "cg.neural"],
    "training.jobs": ["cg.neural", "cg.worker"],
    "streams.dead_letter": ["cg.monitor"],
}


def main():
    if REDIS_URL:
        logger.info("Connecting to Redis via REDIS_URL...")
        client = redis.from_url(REDIS_URL, decode_responses=True)
    else:
        logger.info("Connecting to Redis at %s:%s...", REDIS_HOST, REDIS_PORT)
        client = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            password=REDIS_PASSWORD if REDIS_PASSWORD else None,
            decode_responses=True,
        )
    client.ping()
    logger.info("Redis ping successful.")

    initialized_count = 0
    for stream_name in ALL_STREAMS:
        groups = STREAM_GROUPS.get(stream_name, ["cg.default"])
        for group in groups:
            try:
                # XGROUP CREATE stream group $ MKSTREAM
                client.xgroup_create(name=stream_name, groupname=group, id="$", mkstream=True)
                logger.info("Created stream '%s' with consumer group '%s'", stream_name, group)
                initialized_count += 1
            except redis.ResponseError as exc:
                if "BUSYGROUP" in str(exc):
                    logger.debug("Consumer group '%s' already exists for stream '%s'", group, stream_name)
                    initialized_count += 1
                else:
                    logger.error("Error creating consumer group for %s: %s", stream_name, exc)
                    raise

    logger.info("All %d streams and consumer groups verified successfully!", len(ALL_STREAMS))


if __name__ == "__main__":
    main()
