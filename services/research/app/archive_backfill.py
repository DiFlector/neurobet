"""Checkpoint migrations shared by offline external-tennis archive imports."""

from __future__ import annotations

from typing import Any


AVAILABLE_TIME_CURSOR_VERSION = 2


def migrate_to_available_time_cursor(state: dict[str, Any] | None) -> dict[str, Any]:
    """Restart a source-id-first checkpoint without losing its audit trail.

    Observations for an existing source match can arrive later. A cursor ordered
    by source ID would skip those rows after it moved past that ID, so v1 state
    is retained for audit and replay starts at the beginning in available-time
    order. Importers are idempotent and therefore safe to replay.
    """
    current = dict(state or {})
    if current.get("cursor_version") == AVAILABLE_TIME_CURSOR_VERSION:
        return current
    if current:
        current = {
            "status": "running",
            "cursor_version": AVAILABLE_TIME_CURSOR_VERSION,
            "legacy_cursor_v1": current,
        }
    else:
        current = {
            "status": "running",
            "cursor_version": AVAILABLE_TIME_CURSOR_VERSION,
        }
    return current
