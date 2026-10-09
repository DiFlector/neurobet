"""Collector for official FON.BET match results from https://fon.bet/results."""

import asyncio
import hashlib
import json
import logging
import subprocess
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import redis

from streams import STREAM_FONBET_RESULTS, MessageEnvelope, StreamPublisher
from .config import settings
from .s3_writer import MinIORawWriter

logger = logging.getLogger("collector.results")

DEFAULT_RESULTS_URLS = [
    "https://clientsapi-lb51.bk6bba-resources.com/results/results.json",
    "https://clientsapi-lb52.bk6bba-resources.ru/results/results.json",
    "https://clientsapi-vk-w.bk6bba-resources.ru/results/results.json",
]


class FonbetResultsCollector:
    """
    Fetches official match results from https://fon.bet/results (results.json),
    persists raw snapshots to MinIO S3, and publishes to Redis Streams.
    Guarantees match results are not guessed or assumed.
    """

    def __init__(
        self,
        s3_writer: Optional[MinIORawWriter] = None,
        redis_client: Optional[redis.Redis] = None,
        endpoints: Optional[List[str]] = None,
    ):
        self.s3_writer = s3_writer or MinIORawWriter()
        self.redis_client = redis_client or redis.from_url(settings.redis_url, decode_responses=True)
        self.publisher = StreamPublisher(self.redis_client, producer="results-collector")
        self.endpoints = endpoints or DEFAULT_RESULTS_URLS
        self.last_content_hash: Optional[str] = None
        self.last_poll_time: Optional[datetime] = None

    def fetch_results_feed(self) -> Optional[Dict[str, Any]]:
        """
        Query Fonbet results feed using curl with gzip compression.
        Returns parsed JSON dict with 'events' and 'sections'.
        """
        for url in self.endpoints:
            try:
                logger.debug("Fetching Fonbet results from %s...", url)
                cmd = [
                    "curl",
                    "-s",
                    "--compressed",
                    "--connect-timeout",
                    "5",
                    "--max-time",
                    "15",
                    "-H",
                    "User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
                    "-H",
                    "Accept: application/json, text/plain, */*",
                    url,
                ]
                proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
                if proc.returncode == 0 and proc.stdout:
                    text = proc.stdout.decode("utf-8", errors="replace")
                    data = json.loads(text)
                    if "events" in data and "sections" in data:
                        logger.info(
                            "Successfully fetched results from %s (Events: %d, Sections: %d)",
                            url,
                            len(data.get("events", [])),
                            len(data.get("sections", [])),
                        )
                        return data
            except Exception as ex:
                logger.warning("Failed to fetch results from %s: %s", url, ex)

        logger.error("All Fonbet results endpoints failed.")
        return None

    def process_and_publish(self, results_data: Dict[str, Any]) -> Optional[str]:
        """
        Save results payload to MinIO S3 and publish event to Redis Stream.
        Deduplicates if results have not changed.
        """
        collected_at = datetime.now(timezone.utc)
        self.last_poll_time = collected_at

        # Canonical hash
        serialized = json.dumps(results_data, sort_keys=True, ensure_ascii=False)
        content_hash = f"sha256:{hashlib.sha256(serialized.encode('utf-8')).hexdigest()}"

        if content_hash == self.last_content_hash:
            logger.debug("Results snapshot unchanged (hash: %s). Skipping publish.", content_hash[:16])
            return content_hash

        # Wrap in snapshot envelope for storage
        snapshot_dict = {
            "source": "fonbet",
            "page_type": "results",
            "sport_code": "all",
            "url": "https://fon.bet/results",
            "collected_at": collected_at.isoformat(),
            "content_hash": content_hash,
            "collector_version": settings.collector_version,
            "events_count": len(results_data.get("events", [])),
            "sections_count": len(results_data.get("sections", [])),
            "payload": results_data,
        }

        # 1. Store in S3
        s3_path = self.s3_writer.save_raw_snapshot(
            content_hash=content_hash,
            snapshot_dict=snapshot_dict,
            collected_at=collected_at,
        )

        # 2. Publish to Redis Streams
        envelope = MessageEnvelope(
            producer="collector-results",
            event_type="fonbet.results.raw",
            payload={
                "storage_path": s3_path,
                "content_hash": content_hash,
                "collected_at": collected_at.isoformat(),
                "events_count": len(results_data.get("events", [])),
                "sections_count": len(results_data.get("sections", [])),
            },
        )
        self.publisher.publish(STREAM_FONBET_RESULTS, envelope)

        self.last_content_hash = content_hash
        logger.info(
            "Published results snapshot to %s: %s (events=%d)",
            STREAM_FONBET_RESULTS,
            content_hash[:16],
            len(results_data.get("events", [])),
        )
        return content_hash

    def poll_once(self) -> Optional[str]:
        """Perform a single poll cycle of official Fonbet results."""
        data = self.fetch_results_feed()
        if not data:
            return None
        return self.process_and_publish(data)
