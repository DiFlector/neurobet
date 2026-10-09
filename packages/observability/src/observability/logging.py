import contextvars
from datetime import datetime, timezone
import json
import logging
import sys
import time
import uuid
from typing import Any, Dict, Optional


# Context variables for distributed request tracing and event context
ctx_request_id = contextvars.ContextVar("neurobet_request_id", default=None)
ctx_event_id = contextvars.ContextVar("neurobet_event_id", default=None)
ctx_sport_code = contextvars.ContextVar("neurobet_sport_code", default=None)


class ObservabilityContext:
    """Context manager to scope request_id, event_id, and sport_code."""

    def __init__(
        self,
        request_id: Optional[str] = None,
        event_id: Optional[str] = None,
        sport_code: Optional[str] = None,
    ):
        self.request_id = request_id or str(uuid.uuid4())
        self.event_id = str(event_id) if event_id is not None else None
        self.sport_code = str(sport_code) if sport_code is not None else None
        self._tokens = []

    def __enter__(self):
        self._tokens.append(ctx_request_id.set(self.request_id))
        if self.event_id is not None:
            self._tokens.append(ctx_event_id.set(self.event_id))
        if self.sport_code is not None:
            self._tokens.append(ctx_sport_code.set(self.sport_code))
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if len(self._tokens) >= 3 and self.sport_code is not None:
            ctx_sport_code.reset(self._tokens.pop())
        if len(self._tokens) >= 2 and self.event_id is not None:
            ctx_event_id.reset(self._tokens.pop())
        if len(self._tokens) >= 1:
            ctx_request_id.reset(self._tokens.pop())


class StructuredJsonFormatter(logging.Formatter):
    """
    JSON Log Formatter adhering to Neurobet Architecture Section 41:
    - timestamp
    - service
    - level
    - request_id
    - event_id
    - sport_code
    - operation
    - latency_ms
    - result
    - error
    - message
    """

    def __init__(self, service_name: str = "neurobet"):
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        # Extract context or extra fields
        req_id = getattr(record, "request_id", None) or ctx_request_id.get() or None
        evt_id = getattr(record, "event_id", None) or ctx_event_id.get() or None
        sport = getattr(record, "sport_code", None) or ctx_sport_code.get() or None

        op = getattr(record, "operation", None) or record.funcName or "execute"
        lat = getattr(record, "latency_ms", None)
        res = getattr(record, "result", None) or ("ERROR" if record.levelno >= logging.ERROR else "SUCCESS")
        err_msg = None

        if record.exc_info:
            err_msg = self.formatException(record.exc_info)
        elif getattr(record, "error", None):
            err_msg = str(getattr(record, "error"))

        payload: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "service": getattr(record, "service", self.service_name),
            "level": record.levelname,
            "request_id": req_id,
            "event_id": str(evt_id) if evt_id is not None else None,
            "sport_code": sport,
            "operation": op,
            "latency_ms": round(lat, 2) if lat is not None else None,
            "result": res,
            "error": err_msg,
            "message": record.getMessage(),
        }

        # Include additional extras if present
        if hasattr(record, "extra_data") and isinstance(record.extra_data, dict):
            payload["extra"] = record.extra_data

        return json.dumps(payload, ensure_ascii=False)


def get_logger(service_name: str = "neurobet", level: int = logging.INFO) -> logging.Logger:
    """Factory creating or configuring a structured logger for a service."""
    logger = logging.getLogger(f"neurobet.{service_name}")
    logger.setLevel(level)

    # Avoid duplicate handlers
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(StructuredJsonFormatter(service_name=service_name))
        logger.addHandler(handler)
        logger.propagate = False

    return logger
