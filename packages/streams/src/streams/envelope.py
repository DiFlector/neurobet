"""Message envelope definition for Redis Streams."""

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class MessageEnvelope(BaseModel):
    """Canonical message envelope for all Redis Streams messages."""

    message_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    stream_name: str
    idempotency_key: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    producer: str = "neurobet"
    retry_count: int = 0
    max_retries: int = 3
    payload: Dict[str, Any] = Field(default_factory=dict)
    error_context: Optional[Dict[str, Any]] = None

    def to_redis_dict(self) -> Dict[str, str]:
        """Convert envelope to flat string dictionary for Redis XADD."""
        return {
            "message_id": self.message_id,
            "stream_name": self.stream_name,
            "idempotency_key": self.idempotency_key,
            "timestamp": self.timestamp.isoformat(),
            "producer": self.producer,
            "retry_count": str(self.retry_count),
            "max_retries": str(self.max_retries),
            "payload": json.dumps(self.payload, default=str),
            "error_context": json.dumps(self.error_context, default=str) if self.error_context else "",
        }

    @classmethod
    def from_redis_dict(cls, data: Dict[Any, Any], stream_name: Optional[str] = None) -> "MessageEnvelope":
        """Reconstruct envelope from Redis stream entry dictionary."""
        # Normalize keys and values from bytes if decode_responses=False
        normalized: Dict[str, str] = {}
        for k, v in data.items():
            key_str = k.decode("utf-8") if isinstance(k, bytes) else str(k)
            val_str = v.decode("utf-8") if isinstance(v, bytes) else str(v)
            normalized[key_str] = val_str

        # Parse timestamp
        raw_ts = normalized.get("timestamp")
        if raw_ts:
            try:
                parsed_ts = datetime.fromisoformat(raw_ts)
                if parsed_ts.tzinfo is None:
                    parsed_ts = parsed_ts.replace(tzinfo=timezone.utc)
            except Exception:
                parsed_ts = datetime.now(timezone.utc)
        else:
            parsed_ts = datetime.now(timezone.utc)

        # Parse payload
        raw_payload = normalized.get("payload", "{}")
        try:
            parsed_payload = json.loads(raw_payload) if raw_payload else {}
        except Exception:
            parsed_payload = {"raw": raw_payload}

        # Parse error context
        raw_err = normalized.get("error_context", "")
        parsed_err = None
        if raw_err:
            try:
                parsed_err = json.loads(raw_err)
            except Exception:
                parsed_err = {"raw_error": raw_err}

        return cls(
            message_id=normalized.get("message_id", uuid.uuid4().hex),
            stream_name=normalized.get("stream_name", stream_name or "unknown"),
            idempotency_key=normalized.get("idempotency_key", uuid.uuid4().hex),
            timestamp=parsed_ts,
            producer=normalized.get("producer", "neurobet"),
            retry_count=int(normalized.get("retry_count", 0)),
            max_retries=int(normalized.get("max_retries", 3)),
            payload=parsed_payload,
            error_context=parsed_err,
        )

    def increment_retry(self, error_message: str) -> "MessageEnvelope":
        """Return a new envelope with incremented retry count and error context."""
        new_err = {
            "failed_at": datetime.now(timezone.utc).isoformat(),
            "error": error_message,
            "previous_retries": self.retry_count,
        }
        return self.model_copy(
            update={
                "retry_count": self.retry_count + 1,
                "error_context": new_err,
            }
        )
