"""Persistence helpers for durable human review of external tennis matches."""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from .models.external_tennis import ManualConfirmationRequest


def upsert_manual_confirmation(
    session: Session,
    *,
    kind: str,
    subject_id: str,
    candidate_id: str,
    evidence: dict[str, Any],
    source: str = "flashscore",
) -> ManualConfirmationRequest | None:
    """Create one pending decision unless this exact source pair was decided."""
    if kind not in {"player_profile", "event_match"}:
        raise ValueError(f"Unsupported manual confirmation kind: {kind}")
    subject_id = str(subject_id)
    candidate_id = str(candidate_id)
    subject_lock_key = f"confirmation-subject:{source}:{kind}:{subject_id}"
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": subject_lock_key})
    lock_key = f"confirmation:{source}:{kind}:{subject_id}:{candidate_id}"
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": lock_key})
    prior = (
        session.query(ManualConfirmationRequest)
        .filter_by(kind=kind, subject_id=subject_id, candidate_id=candidate_id, source=source)
        .order_by(ManualConfirmationRequest.created_at.desc(), ManualConfirmationRequest.id.desc())
        .with_for_update()
        .first()
    )
    if prior is not None:
        if prior.status != "pending":
            return None
        # A pending card is a stable proposal. Background polling may discover
        # richer evidence, but it must not invalidate the administrator's
        # decision token or silently replace what the card asked them to review.
        return prior
    request = ManualConfirmationRequest(
        kind=kind,
        subject_id=subject_id,
        candidate_id=candidate_id,
        source=source,
        evidence_json=evidence,
        status="pending",
    )
    session.add(request)
    session.flush()
    return request
