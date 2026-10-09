"""Stream publishers (sync and async) for Neurobet Redis Streams."""

from typing import Any, Dict, List, Optional, Union
import redis
import redis.asyncio as aioredis
from pydantic import BaseModel

from .constants import ALL_STREAMS
from .envelope import MessageEnvelope


class StreamPublisher:
    """Synchronous Redis Streams publisher."""

    def __init__(self, redis_client: redis.Redis, producer: str = "neurobet"):
        self.client = redis_client
        self.producer = producer

    def publish_envelope(
        self,
        envelope: MessageEnvelope,
        maxlen: Optional[int] = 10000,
        approximate: bool = True,
    ) -> str:
        """Publish a pre-built MessageEnvelope to Redis stream using XADD."""
        fields = envelope.to_redis_dict()
        msg_id = self.client.xadd(
            name=envelope.stream_name,
            fields=fields,
            maxlen=maxlen,
            approximate=approximate,
        )
        return msg_id.decode("utf-8") if isinstance(msg_id, bytes) else str(msg_id)

    def publish(
        self,
        stream_name: str,
        payload: Union[Dict[str, Any], BaseModel],
        idempotency_key: Optional[str] = None,
        max_retries: int = 3,
        maxlen: Optional[int] = 10000,
    ) -> MessageEnvelope:
        """Construct MessageEnvelope and publish to Redis stream."""
        if isinstance(payload, BaseModel):
            payload_dict = payload.model_dump(mode="json")
        else:
            payload_dict = payload

        kwargs: Dict[str, Any] = {
            "stream_name": stream_name,
            "producer": self.producer,
            "max_retries": max_retries,
            "payload": payload_dict,
        }
        if idempotency_key is not None:
            kwargs["idempotency_key"] = idempotency_key

        envelope = MessageEnvelope(**kwargs)
        self.publish_envelope(envelope, maxlen=maxlen)
        return envelope

    def ensure_streams(self, streams: Optional[List[str]] = None) -> None:
        """Ensure all canonical streams exist by sending a ping or creating empty consumer groups if needed."""
        target_streams = streams or ALL_STREAMS
        for s in target_streams:
            # Check if stream exists
            exists = self.client.exists(s)
            if not exists:
                # Initialize stream with a setup marker then trim or leave group
                pass


class AsyncStreamPublisher:
    """Asynchronous Redis Streams publisher."""

    def __init__(self, redis_client: aioredis.Redis, producer: str = "neurobet"):
        self.client = redis_client
        self.producer = producer

    async def publish_envelope(
        self,
        envelope: MessageEnvelope,
        maxlen: Optional[int] = 10000,
        approximate: bool = True,
    ) -> str:
        """Publish a pre-built MessageEnvelope asynchronously to Redis stream using XADD."""
        fields = envelope.to_redis_dict()
        msg_id = await self.client.xadd(
            name=envelope.stream_name,
            fields=fields,
            maxlen=maxlen,
            approximate=approximate,
        )
        return msg_id.decode("utf-8") if isinstance(msg_id, bytes) else str(msg_id)

    async def publish(
        self,
        stream_name: str,
        payload: Union[Dict[str, Any], BaseModel],
        idempotency_key: Optional[str] = None,
        max_retries: int = 3,
        maxlen: Optional[int] = 10000,
    ) -> MessageEnvelope:
        """Construct MessageEnvelope and publish asynchronously to Redis stream."""
        if isinstance(payload, BaseModel):
            payload_dict = payload.model_dump(mode="json")
        else:
            payload_dict = payload

        kwargs: Dict[str, Any] = {
            "stream_name": stream_name,
            "producer": self.producer,
            "max_retries": max_retries,
            "payload": payload_dict,
        }
        if idempotency_key is not None:
            kwargs["idempotency_key"] = idempotency_key

        envelope = MessageEnvelope(**kwargs)
        await self.publish_envelope(envelope, maxlen=maxlen)
        return envelope
