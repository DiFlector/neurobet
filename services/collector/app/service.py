"""Collector service orchestrator for FON.BET live tennis data."""

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import httpx
import redis

from streams import STREAM_FONBET_RAW, MessageEnvelope, StreamPublisher
from .browser import BrowserLifecycleManager
from .circuit_breaker import CircuitBreaker
from .config import settings
from .parser import FonbetLiveTennisParser
from .results_collector import FonbetResultsCollector
from .s3_writer import MinIORawWriter

logger = logging.getLogger("collector.service")


class CollectorService:
    """Orchestrates live data collection, raw S3 persistence, and Redis Stream publishing."""

    def __init__(self):
        self.browser_manager = BrowserLifecycleManager()
        self.s3_writer = MinIORawWriter()
        self.circuit_breaker = CircuitBreaker()
        self.redis_client = redis.from_url(settings.redis_url, decode_responses=True)
        self.publisher = StreamPublisher(self.redis_client, producer="collector-service")
        self.results_collector = FonbetResultsCollector(
            s3_writer=self.s3_writer,
            redis_client=self.redis_client,
        )
        self.semaphore = asyncio.Semaphore(settings.max_concurrency)
        self.last_content_hash: Optional[str] = None
        self.total_polls = 0
        self.total_live_events_seen = 0

    async def initialize(self) -> None:
        """Start dependencies and verify connections."""
        logger.info("Initializing CollectorService...")
        self.s3_writer.ensure_bucket()
        self.redis_client.ping()
        logger.info("CollectorService connected to MinIO and Redis.")

    async def fetch_live_feed(self) -> Dict[str, Any]:
        """
        Fetch Fonbet live tennis data.
        Uses Russian localization headers and parameters.
        """
        headers = {
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
        }

        # Query live endpoints with lang=ru
        endpoints = [
            f"{settings.fonbet_base_url}/line/common/v1/live/events?lang=ru",
            f"https://line01i.bkfon-resources.com/events/list?lang=ru",
            f"{settings.fonbet_live_url}",
        ]

        async with httpx.AsyncClient(timeout=settings.request_timeout_seconds, headers=headers) as client:
            for endpoint in endpoints:
                try:
                    logger.debug("Attempting live feed query: %s", endpoint)
                    resp = await client.get(endpoint)
                    if resp.status_code == 200:
                        content_type = resp.headers.get("content-type", "")
                        if "json" in content_type:
                            data = resp.json()
                            if "events" in data:
                                return data
                except Exception as exc:
                    logger.debug("Endpoint %s failed: %s", endpoint, exc)

        # Fallback to rendered content via Playwright if direct HTTP endpoints return non-JSON
        logger.info("Direct JSON endpoints unavailable or blocked, rendering live page via Playwright...")
        await self.browser_manager.start()
        page = await self.browser_manager.get_page()
        await page.goto(settings.fonbet_live_url, wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(2)  # allow initial live JS hydration

        # Intercept live events from page context or DOM
        events_json = await page.evaluate(
            """() => {
                if (window.__INITIAL_STATE__ && window.__INITIAL_STATE__.events) {
                    return { events: window.__INITIAL_STATE__.events };
                }
                return { events: [] };
            }"""
        )
        return events_json if events_json.get("events") else {"events": []}

    async def poll_once(self) -> Optional[str]:
        """Execute a single polling iteration with resilience and validation."""
        if not self.circuit_breaker.can_execute():
            backoff = self.circuit_breaker.get_backoff_delay()
            logger.warning("Circuit breaker is OPEN. Backing off for %.2fs...", backoff)
            await asyncio.sleep(backoff)
            return None

        async with self.semaphore:
            self.total_polls += 1
            collected_at = datetime.now(timezone.utc)

            try:
                raw_feed = await self.fetch_live_feed()

                # Extract strictly LIVE events
                live_events = FonbetLiveTennisParser.parse_events_feed(raw_feed)
                self.total_live_events_seen += len(live_events)

                # Compute SHA-256 hash
                payload_to_hash = {
                    "source": "fonbet",
                    "sport_code": settings.sport_code,
                    "url": settings.fonbet_live_url,
                    "events": live_events,
                }
                raw_bytes = json.dumps(payload_to_hash, sort_keys=True, ensure_ascii=False).encode("utf-8")
                content_hash = f"sha256:{hashlib.sha256(raw_bytes).hexdigest()}"

                # Only persist and publish if we have active events or state change
                s3_key = None
                if live_events or content_hash != self.last_content_hash:
                    snapshot_dict = {
                        "source": "fonbet",
                        "collector_version": settings.collector_version,
                        "collected_at": collected_at.isoformat(),
                        "sport_code": settings.sport_code,
                        "page_type": "live",
                        "url": settings.fonbet_live_url,
                        "content_hash": content_hash,
                        "events_count": len(live_events),
                        "events": live_events,
                    }

                    # 1. Save raw snapshot to MinIO S3 bucket 'raw-snapshots'
                    s3_key = self.s3_writer.save_raw_snapshot(
                        content_hash=content_hash,
                        snapshot_dict=snapshot_dict,
                        collected_at=collected_at,
                    )

                    # 2. Publish to Redis Stream 'fonbet.raw'
                    envelope_payload = dict(snapshot_dict)
                    envelope_payload["raw_s3_key"] = s3_key

                    envelope = MessageEnvelope(
                        stream_name=STREAM_FONBET_RAW,
                        idempotency_key=f"snap_{content_hash[7:23]}_{int(collected_at.timestamp())}",
                        producer="collector-service",
                        payload=envelope_payload,
                    )
                    self.publisher.publish_envelope(envelope)
                    self.last_content_hash = content_hash
                    logger.info(
                        "Published raw snapshot %s to Redis stream '%s' (%d live tennis events).",
                        content_hash[:16],
                        STREAM_FONBET_RAW,
                        len(live_events),
                    )

                try:
                    from observability import COLLECTOR_POLLS_TOTAL, COLLECTOR_EVENTS_SEEN
                    COLLECTOR_POLLS_TOTAL.inc(labels={"sport": settings.sport_code})
                    COLLECTOR_EVENTS_SEEN.set(len(live_events))
                except Exception:
                    pass

                self.circuit_breaker.record_success()
                return content_hash

            except Exception as e:
                logger.error("Error in poll_once: %s", e)
                try:
                    from observability import COLLECTOR_ERRORS_TOTAL
                    COLLECTOR_ERRORS_TOTAL.inc()
                except Exception:
                    pass
                self.circuit_breaker.record_failure(e)
                return None

    def poll_results_once(self) -> Optional[str]:
        """Trigger a poll of official Fonbet match results."""
        try:
            return self.results_collector.poll_once()
        except Exception as e:
            logger.error("Error polling Fonbet results: %s", e)
            return None

    async def shutdown(self) -> None:
        """Gracefully release browser, redis, and resources."""
        logger.info("Shutting down CollectorService...")
        await self.browser_manager.close()
        self.redis_client.close()
        logger.info("CollectorService shut down cleanly.")
