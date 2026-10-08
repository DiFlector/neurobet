"""Redis Streams consumer group implementation with idempotency, retries, and dead-letter routing."""

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple
import redis
import redis.asyncio as aioredis

from .constants import IDEMPOTENCY_KEY_PREFIX, DEFAULT_IDEMPOTENCY_TTL_SECONDS, STREAM_DEAD_LETTER
from .envelope import MessageEnvelope

logger = logging.getLogger("streams.consumer")


class StreamConsumerGroup:
    """Synchronous Redis Streams Consumer Group reader with idempotency and retry handling."""

    def __init__(
        self,
        redis_client: redis.Redis,
        stream_name: str,
        group_name: str,
        consumer_name: str,
        idempotency_ttl: int = DEFAULT_IDEMPOTENCY_TTL_SECONDS,
    ):
        self.client = redis_client
        self.stream_name = stream_name
        self.group_name = group_name
        self.consumer_name = consumer_name
        self.idempotency_ttl = idempotency_ttl

    def create_group(self, start_id: str = "$") -> None:
        """Create consumer group if it does not already exist."""
        try:
            self.client.xgroup_create(
                name=self.stream_name,
                groupname=self.group_name,
                id=start_id,
                mkstream=True,
            )
            logger.info("Consumer group '%s' created on stream '%s'", self.group_name, self.stream_name)
        except redis.ResponseError as exc:
            if "BUSYGROUP" in str(exc):
                logger.debug("Consumer group '%s' already exists on '%s'", self.group_name, self.stream_name)
            else:
                raise

    def is_completed(self, idempotency_key: str) -> bool:
        """Returns True if the message with idempotency_key has already been successfully processed."""
        redis_key = f"{IDEMPOTENCY_KEY_PREFIX}{idempotency_key}"
        val = self.client.get(redis_key)
        if isinstance(val, bytes):
            val = val.decode("utf-8")
        return val == "DONE"

    def mark_completed(self, idempotency_key: str) -> None:
        """Mark idempotency key as successfully processed."""
        redis_key = f"{IDEMPOTENCY_KEY_PREFIX}{idempotency_key}"
        self.client.set(redis_key, "DONE", ex=self.idempotency_ttl)

    def read_messages(self, count: int = 10, block_ms: Optional[int] = 2000) -> List[Tuple[str, MessageEnvelope]]:
        """Read pending or new messages for this consumer."""
        raw_entries = self.client.xreadgroup(
            groupname=self.group_name,
            consumername=self.consumer_name,
            streams={self.stream_name: ">"},
            count=count,
            block=block_ms,
        )
        results: List[Tuple[str, MessageEnvelope]] = []
        if not raw_entries:
            return results

        for stream_resp, messages in raw_entries:
            stream_str = stream_resp.decode("utf-8") if isinstance(stream_resp, bytes) else str(stream_resp)
            for msg_id, fields in messages:
                id_str = msg_id.decode("utf-8") if isinstance(msg_id, bytes) else str(msg_id)
                envelope = MessageEnvelope.from_redis_dict(fields, stream_name=stream_str)
                results.append((id_str, envelope))
        return results

    def ack(self, msg_id: str) -> None:
        """Acknowledge message processing."""
        self.client.xack(self.stream_name, self.group_name, msg_id)

    def route_to_dead_letter(self, envelope: MessageEnvelope, error_msg: str, original_msg_id: str) -> str:
        """Send message exceeding max retries to dead letter stream and ack original."""
        failed_env = envelope.increment_retry(error_msg)
        fields = failed_env.to_redis_dict()
        fields["original_stream"] = self.stream_name
        fields["original_msg_id"] = original_msg_id
        dl_id = self.client.xadd(STREAM_DEAD_LETTER, fields)
        self.ack(original_msg_id)
        logger.warning(
            "Message %s routed to dead letter queue (%s) after %d retries: %s",
            envelope.message_id,
            STREAM_DEAD_LETTER,
            failed_env.retry_count,
            error_msg,
        )
        return dl_id.decode("utf-8") if isinstance(dl_id, bytes) else str(dl_id)

    def process_one(
        self,
        handler: Callable[[MessageEnvelope], Any],
        count: int = 1,
        block_ms: int = 1000,
    ) -> int:
        """
        Fetch and process up to `count` messages.
        Handles idempotency deduplication, retries, and dead-letter routing.
        Returns number of successfully processed messages.
        """
        messages = self.read_messages(count=count, block_ms=block_ms)
        processed_count = 0

        for msg_id, envelope in messages:
            # Check if this task already completed in the past
            if self.is_completed(envelope.idempotency_key):
                logger.info(
                    "Duplicate message detected (key=%s, msg=%s). Skipping and acking.",
                    envelope.idempotency_key,
                    envelope.message_id,
                )
                self.ack(msg_id)
                continue

            try:
                handler(envelope)
                self.mark_completed(envelope.idempotency_key)
                self.ack(msg_id)
                processed_count += 1
            except Exception as exc:
                logger.exception("Error processing message %s: %s", envelope.message_id, exc)
                if envelope.retry_count >= envelope.max_retries:
                    self.route_to_dead_letter(envelope, str(exc), msg_id)
                else:
                    # Increment retry and re-publish to stream, then ack old
                    retry_env = envelope.increment_retry(str(exc))
                    self.client.xadd(self.stream_name, retry_env.to_redis_dict())
                    self.ack(msg_id)

        return processed_count


class AsyncStreamConsumerGroup:
    """Asynchronous Redis Streams Consumer Group reader."""

    def __init__(
        self,
        redis_client: aioredis.Redis,
        stream_name: str,
        group_name: str,
        consumer_name: str,
        idempotency_ttl: int = DEFAULT_IDEMPOTENCY_TTL_SECONDS,
    ):
        self.client = redis_client
        self.stream_name = stream_name
        self.group_name = group_name
        self.consumer_name = consumer_name
        self.idempotency_ttl = idempotency_ttl

    async def create_group(self, start_id: str = "$") -> None:
        """Create consumer group if it does not already exist."""
        try:
            await self.client.xgroup_create(
                name=self.stream_name,
                groupname=self.group_name,
                id=start_id,
                mkstream=True,
            )
            logger.info("Consumer group '%s' created on stream '%s'", self.group_name, self.stream_name)
        except aioredis.ResponseError as exc:
            if "BUSYGROUP" in str(exc):
                logger.debug("Consumer group '%s' already exists on '%s'", self.group_name, self.stream_name)
            else:
                raise

    async def is_completed(self, idempotency_key: str) -> bool:
        redis_key = f"{IDEMPOTENCY_KEY_PREFIX}{idempotency_key}"
        val = await self.client.get(redis_key)
        if isinstance(val, bytes):
            val = val.decode("utf-8")
        return val == "DONE"

    async def mark_completed(self, idempotency_key: str) -> None:
        redis_key = f"{IDEMPOTENCY_KEY_PREFIX}{idempotency_key}"
        await self.client.set(redis_key, "DONE", ex=self.idempotency_ttl)

    async def read_messages(self, count: int = 10, block_ms: Optional[int] = 2000) -> List[Tuple[str, MessageEnvelope]]:
        raw_entries = await self.client.xreadgroup(
            groupname=self.group_name,
            consumername=self.consumer_name,
            streams={self.stream_name: ">"},
            count=count,
            block=block_ms,
        )
        results: List[Tuple[str, MessageEnvelope]] = []
        if not raw_entries:
            return results

        for stream_resp, messages in raw_entries:
            stream_str = stream_resp.decode("utf-8") if isinstance(stream_resp, bytes) else str(stream_resp)
            for msg_id, fields in messages:
                id_str = msg_id.decode("utf-8") if isinstance(msg_id, bytes) else str(msg_id)
                envelope = MessageEnvelope.from_redis_dict(fields, stream_name=stream_str)
                results.append((id_str, envelope))
        return results

    async def ack(self, msg_id: str) -> None:
        await self.client.xack(self.stream_name, self.group_name, msg_id)

    async def route_to_dead_letter(self, envelope: MessageEnvelope, error_msg: str, original_msg_id: str) -> str:
        failed_env = envelope.increment_retry(error_msg)
        fields = failed_env.to_redis_dict()
        fields["original_stream"] = self.stream_name
        fields["original_msg_id"] = original_msg_id
        dl_id = await self.client.xadd(STREAM_DEAD_LETTER, fields)
        await self.ack(original_msg_id)
        logger.warning(
            "Async: Message %s routed to dead letter queue (%s) after %d retries: %s",
            envelope.message_id,
            STREAM_DEAD_LETTER,
            failed_env.retry_count,
            error_msg,
        )
        return dl_id.decode("utf-8") if isinstance(dl_id, bytes) else str(dl_id)

    async def process_one(
        self,
        handler: Callable[[MessageEnvelope], Any],
        count: int = 1,
        block_ms: int = 1000,
    ) -> int:
        messages = await self.read_messages(count=count, block_ms=block_ms)
        processed_count = 0

        for msg_id, envelope in messages:
            if await self.is_completed(envelope.idempotency_key):
                logger.info(
                    "Duplicate message detected (key=%s, msg=%s). Skipping and acking.",
                    envelope.idempotency_key,
                    envelope.message_id,
                )
                await self.ack(msg_id)
                continue

            try:
                res = handler(envelope)
                if hasattr(res, "__await__"):
                    await res
                await self.mark_completed(envelope.idempotency_key)
                await self.ack(msg_id)
                processed_count += 1
            except Exception as exc:
                logger.exception("Error processing message %s: %s", envelope.message_id, exc)
                if envelope.retry_count >= envelope.max_retries:
                    await self.route_to_dead_letter(envelope, str(exc), msg_id)
                else:
                    retry_env = envelope.increment_retry(str(exc))
                    await self.client.xadd(self.stream_name, retry_env.to_redis_dict())
                    await self.ack(msg_id)

        return processed_count
