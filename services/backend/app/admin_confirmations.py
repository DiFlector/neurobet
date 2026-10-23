"""Admin session and durable manual-confirmation API."""

import os
import re
import uuid
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, text
from db.identity import confirmed_source_player_id

from db.connection import SessionLocal
from db.models import (
    Event, ExtTennisMatch, ManualConfirmationDecision, ManualConfirmationRequest,
    Participant, TennisMatchLookupJob, TennisPlayerIdentityLink, TennisPlayerProfile,
    TennisPlayerMetricSnapshot, TennisPlayerRefreshJob, TennisPlayerResultFact,
    TennisPointMatchLink, TennisPointRefreshJob,
    TennisPointRefreshJobEvent,
)
from db.tennis_confirmations import upsert_manual_confirmation

from .admin_auth import authenticate, clear_session_cookie, create_session, require_admin, set_session_cookie

router = APIRouter()


class AdminLogin(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class ConfirmationDecisionInput(BaseModel):
    decision: Literal["approve", "reject"]
    expected_version: int = Field(ge=1)


@router.post("/api/admin/session")
def create_admin_session(payload: AdminLogin, response: Response) -> dict[str, Any]:
    if not authenticate(payload.username, payload.password):
        raise HTTPException(status_code=401, detail="Invalid administrator credentials")
    set_session_cookie(response, create_session(payload.username.strip()))
    return {"authenticated": True, "expires_in_seconds": 43200}


@router.get("/api/admin/session")
def get_admin_session(request: Request) -> dict[str, Any]:
    return {"authenticated": bool(require_admin(request))}


@router.delete("/api/admin/session")
def delete_admin_session(response: Response) -> dict[str, Any]:
    clear_session_cookie(response)
    return {"authenticated": False}


def _request_payload(row: ManualConfirmationRequest, session) -> dict[str, Any]:
    evidence = dict(row.evidence_json or {})
    subject: dict[str, Any] = {}
    if row.kind == "player_profile":
        try:
            participant = session.get(Participant, uuid.UUID(row.subject_id))
        except ValueError:
            participant = None
        profile = session.get(TennisPlayerProfile, participant.id) if participant else None
        subject = {
            "id": row.subject_id,
            "name": participant.canonical_name if participant else evidence.get("participant_name", ""),
        }
        candidate = evidence.get("candidate") or {}
        evidence["candidate"] = candidate
        evidence.setdefault("source_player_id", row.candidate_id)
        evidence.setdefault("participant_metrics_version", profile.metrics_version if profile else 0)
    elif row.kind == "event_match":
        try:
            event = session.get(Event, uuid.UUID(row.subject_id))
        except ValueError:
            event = None
        match = (
            session.query(ExtTennisMatch)
            .filter_by(source="flashscore", source_match_id=row.candidate_id)
            .order_by(ExtTennisMatch.available_at.desc())
            .first()
        )
        subject = {
            "id": row.subject_id,
            "name": f"{event.participant_a_name} — {event.participant_b_name}" if event else evidence.get("event_name", ""),
            "participant_a_name": event.participant_a_name if event else evidence.get("participant_a_name"),
            "participant_b_name": event.participant_b_name if event else evidence.get("participant_b_name"),
            "scheduled_start_at": event.scheduled_start_at.isoformat() if event and event.scheduled_start_at else None,
            "score": event.current_score if event else evidence.get("fonbet_score"),
        }
        if match:
            evidence.setdefault("source_match", {
                "source_match_id": match.source_match_id,
                "winner_name": match.winner_name,
                "loser_name": match.loser_name,
                "tournament": match.tournament,
                "played_at": match.played_at.isoformat(),
                "score": match.score,
            })
        evidence.setdefault("source_match_id", row.candidate_id)
    latest_decision = session.query(ManualConfirmationDecision).filter_by(
        request_id=row.id
    ).order_by(ManualConfirmationDecision.created_at.desc(), ManualConfirmationDecision.id.desc()).first()
    correctable, correction_reason = _confirmation_correction_state(session, row)
    return {
        "id": str(row.id), "kind": row.kind, "subject_id": row.subject_id,
        "candidate_id": row.candidate_id, "source": row.source,
        "subject": subject, "evidence": evidence, "status": row.status,
        "version": row.version, "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        "decided_by": row.decided_by,
        "latest_decision": latest_decision.decision if latest_decision else None,
        "can_correct": correctable,
        "correction_unavailable_reason": correction_reason,
    }


def _confirmation_correction_state(session, item) -> tuple[bool, str | None]:
    """Only the newest terminal choice for a subject may be corrected."""
    if item.status not in {"approved", "rejected"}:
        return False, "Заявка не имеет решения, которое можно исправить"
    pending = session.query(ManualConfirmationRequest.id).filter(
        ManualConfirmationRequest.kind == item.kind,
        ManualConfirmationRequest.subject_id == item.subject_id,
        ManualConfirmationRequest.source == item.source,
        ManualConfirmationRequest.status == "pending",
    ).first()
    if pending:
        return False, "По этой сущности уже ожидает решения новое предложение"
    latest = session.query(ManualConfirmationRequest).filter(
        ManualConfirmationRequest.kind == item.kind,
        ManualConfirmationRequest.subject_id == item.subject_id,
        ManualConfirmationRequest.source == item.source,
        ManualConfirmationRequest.status.in_(("approved", "rejected")),
    ).order_by(ManualConfirmationRequest.decided_at.desc(), ManualConfirmationRequest.id.desc()).first()
    if latest is None or latest.id != item.id:
        return False, "Есть более новое решение для этой сущности"
    return True, None


def _validate_confirmation_status_for_decision(status: str, decision: str) -> str | None:
    """Return the old terminal state for corrections; pending is a first decision."""
    if status == "pending":
        return None
    if status not in {"approved", "rejected"}:
        raise HTTPException(status_code=409, detail="Only the latest approved or rejected decision can be corrected")
    target_status = {"approve": "approved", "reject": "rejected"}[decision]
    if status == target_status:
        raise HTTPException(status_code=409, detail="This decision is already current; choose the opposite decision to correct it")
    return status


@router.get("/api/admin/confirmations")
def list_admin_confirmations(
    request: Request,
    status: str = Query("pending", pattern="^(pending|approved|rejected|superseded|all)$"),
    kind: Optional[str] = Query(None, pattern="^(player_profile|event_match)$"),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    require_admin(request)
    with SessionLocal() as session:
        query = session.query(ManualConfirmationRequest)
        if status != "all":
            query = query.filter(ManualConfirmationRequest.status == status)
        if kind:
            query = query.filter(ManualConfirmationRequest.kind == kind)
        total = query.count()
        rows = query.order_by(ManualConfirmationRequest.created_at.desc(), ManualConfirmationRequest.id.desc()).offset(offset).limit(limit).all()
        counts = {
            name: int(session.query(func.count(ManualConfirmationRequest.id)).filter(
                ManualConfirmationRequest.status == name
            ).scalar() or 0)
            for name in ("pending", "approved", "rejected", "superseded")
        }
        return {
            "items": [_request_payload(row, session) for row in rows],
            "total": total, "limit": limit, "offset": offset,
            "counts": counts, "generated_at": datetime.now(timezone.utc).isoformat(),
        }


def _latest_confirmed_player_id(session, participant_id) -> str | None:
    links = session.query(TennisPlayerIdentityLink).filter_by(
        participant_id=participant_id, source="flashscore"
    ).order_by(TennisPlayerIdentityLink.available_at.desc(), TennisPlayerIdentityLink.id.desc()).limit(50).all()
    latest = {}
    for link in links:
        latest.setdefault(link.source_player_id, link)
    confirmed = [link.source_player_id for link in latest.values() if link.status == "confirmed"]
    return confirmed[0] if len(confirmed) == 1 else None


def _enqueue_player_refresh(session, participant_id, now: datetime) -> None:
    job = session.get(TennisPlayerRefreshJob, participant_id, with_for_update=True)
    pending = session.query(ManualConfirmationRequest.id).filter_by(
        kind="player_profile", subject_id=str(participant_id), source="flashscore", status="pending"
    ).first()
    if job is None:
        job = TennisPlayerRefreshJob(
            participant_id=participant_id,
            status="waiting_confirmation" if pending else "queued",
            next_run_at=now,
            last_error="IDENTITY_CONFIRMATION_REQUIRED" if pending else None,
            progress_json={"stage": "identity_confirmation_required" if pending else "queued"},
        )
        session.add(job)
    if pending:
        job.status = "waiting_confirmation"
        job.last_error = "IDENTITY_CONFIRMATION_REQUIRED"
        job.progress_json = {"stage": "identity_confirmation_required"}
        job.attempt_token = None
        job.lease_until = None
        job.completed_at = now
        job.updated_at = now
        return
    job.status = "queued"
    job.requested_reason = "manual_identity_resolution"
    job.requested_at = now
    job.next_run_at = now
    job.attempt_token = None
    job.lease_until = None
    job.last_error = None
    job.completed_at = None
    job.updated_at = now


def _get_or_create_player_profile(session, participant_id) -> TennisPlayerProfile:
    """Return the profile row needed to persist a reviewed player identity.

    Confirmation cards are durable snapshots and can outlive a profile row
    created by an earlier refresh attempt. The reviewed candidate itself is
    still available in the request evidence, so the missing denormalized row
    must not make the admin decision impossible.
    """
    profile = session.get(TennisPlayerProfile, participant_id, with_for_update=True)
    if profile is None:
        profile = TennisPlayerProfile(participant_id=participant_id)
        session.add(profile)
    return profile


def _reconcile_player_facts_after_identity_decision(
    session, profile: TennisPlayerProfile, participant_id: uuid.UUID,
    source_player_id: str, *, accept: bool, now: datetime,
) -> int:
    """Append a temporal correction for facts from a manually changed identity.

    Profile facts and metric snapshots are immutable history. Rejecting an
    approved identity appends invalid fact versions and a new current metrics
    snapshot, so neither live forecasts nor future imports keep using the
    wrong player's results. Re-approving a rejected identity restores only
    facts invalidated by this same correction path.
    """
    from tennis_adapter.player_metrics import aggregate_results, digest_metrics

    rows = session.query(TennisPlayerResultFact).filter(
        TennisPlayerResultFact.participant_id == participant_id,
        TennisPlayerResultFact.source == "flashscore",
        TennisPlayerResultFact.source_payload["source_player_id"].astext == source_player_id,
    ).order_by(TennisPlayerResultFact.available_at, TennisPlayerResultFact.id).all()
    latest_by_match = {}
    versions_by_match = {}
    for row in rows:
        versions_by_match.setdefault(row.source_match_id, []).append(row)
        latest_by_match[row.source_match_id] = row

    changed = 0
    for match_id, latest in latest_by_match.items():
        if accept:
            if latest.valid:
                continue
            if (latest.source_payload or {}).get("invalidated_reason") != "admin_rejected_profile_identity":
                continue
            prior_valid = next((row for row in reversed(versions_by_match[match_id][:-1]) if row.valid), None)
            if prior_valid is None:
                continue
            payload = dict(prior_valid.source_payload or {})
            payload.update({"restored_at": now.isoformat(), "identity_restored": True})
            result_hash = hashlib.sha256(json.dumps({
                "prior_hash": prior_valid.result_hash,
                "restored_after": latest.result_hash,
                "available_at": now.isoformat(),
            }, sort_keys=True).encode()).hexdigest()
            session.add(TennisPlayerResultFact(
                participant_id=participant_id, source="flashscore", source_match_id=match_id,
                result_hash=result_hash, played_at=prior_valid.played_at, available_at=now,
                surface=prior_valid.surface, tour_level=prior_valid.tour_level, won=prior_valid.won,
                valid=True, sets_played=prior_valid.sets_played,
                duration_minutes=prior_valid.duration_minutes, serve_points=prior_valid.serve_points,
                serve_points_won=prior_valid.serve_points_won, source_payload=payload,
            ))
            changed += 1
        else:
            if not latest.valid:
                continue
            payload = dict(latest.source_payload or {})
            payload.update({"valid": False, "invalidated_reason": "admin_rejected_profile_identity",
                           "invalidated_at": now.isoformat()})
            result_hash = hashlib.sha256(json.dumps({
                "prior_hash": latest.result_hash,
                "valid": False,
                "reason": "admin_rejected_profile_identity",
                "available_at": now.isoformat(),
            }, sort_keys=True).encode()).hexdigest()
            session.add(TennisPlayerResultFact(
                participant_id=participant_id, source="flashscore", source_match_id=match_id,
                result_hash=result_hash, played_at=latest.played_at, available_at=now,
                surface=latest.surface, tour_level=latest.tour_level, won=latest.won,
                valid=False, sets_played=latest.sets_played,
                duration_minutes=latest.duration_minutes, serve_points=latest.serve_points,
                serve_points_won=latest.serve_points_won, source_payload=payload,
            ))
            changed += 1

    if not changed:
        return 0
    session.flush()

    all_facts = session.query(TennisPlayerResultFact).filter(
        TennisPlayerResultFact.participant_id == participant_id,
        TennisPlayerResultFact.available_at <= now,
    ).order_by(TennisPlayerResultFact.played_at, TennisPlayerResultFact.available_at,
               TennisPlayerResultFact.id).all()
    current_by_match = {}
    for fact in all_facts:
        current_by_match[fact.source_match_id] = fact
    selected = [fact for fact in current_by_match.values() if fact.valid and (
        fact.source != "flashscore" or
        (fact.source_payload or {}).get("source_player_id") == (source_player_id if accept else None)
    )]
    metrics = aggregate_results({
        "played_at": fact.played_at, "won": fact.won, "surface": fact.surface,
        "tour_level": fact.tour_level, "sets_played": fact.sets_played,
        "duration_minutes": fact.duration_minutes, "serve_points": fact.serve_points,
        "serve_points_won": fact.serve_points_won,
    } for fact in selected)
    digest = digest_metrics(metrics)
    profile.metrics_json = metrics
    profile.history_digest = digest
    profile.metrics_version = int(profile.metrics_version or 0) + 1
    profile.history_match_count = int(metrics["overall"]["matches"])
    profile.source_watermark = max((fact.played_at for fact in selected), default=None)
    profile.metrics_available_at = now
    session.add(TennisPlayerMetricSnapshot(
        participant_id=participant_id,
        source_player_id=confirmed_source_player_id(
            session, participant_id, source="flashscore", as_of=now
        ),
        metrics_version=profile.metrics_version,
        available_at=now, source_watermark=profile.source_watermark,
        history_digest=digest, metrics_json=metrics,
    ))
    return changed


def _supersede_siblings(session, item, reviewer: str, now: datetime) -> None:
    siblings = session.query(ManualConfirmationRequest).filter(
        ManualConfirmationRequest.kind == item.kind,
        ManualConfirmationRequest.subject_id == item.subject_id,
        ManualConfirmationRequest.status == "pending",
        ManualConfirmationRequest.id != item.id,
    ).with_for_update().all()
    for sibling in siblings:
        prior_version = sibling.version
        sibling.status = "superseded"
        sibling.decided_at = now
        sibling.decided_by = reviewer
        sibling.updated_at = now
        sibling.version += 1
        session.add(ManualConfirmationDecision(
            request_id=sibling.id, decision="superseded", request_version=prior_version,
            decided_by=reviewer, details_json={"superseded_by_request_id": str(item.id)}, created_at=now,
        ))


def _match_home_is_event_a(session, event, evidence: dict[str, Any]) -> bool:
    a_source = _latest_confirmed_player_id(session, event.participant_a_id)
    b_source = _latest_confirmed_player_id(session, event.participant_b_id)
    candidate = evidence.get("candidate") or evidence.get("source_match") or {}
    url = str(candidate.get("url") or evidence.get("source_url") or "")
    path = urlparse(url).path
    parts = re.search(r"/match/tennis/[^/]+-([A-Za-z0-9]+)/[^/]+-([A-Za-z0-9]+)/", path)
    if parts and a_source and b_source:
        if parts.group(1) == a_source and parts.group(2) == b_source:
            return True
        if parts.group(1) == b_source and parts.group(2) == a_source:
            return False
    raise HTTPException(status_code=409, detail="Cannot verify Flashscore participant order")


@router.post("/api/admin/confirmations/{request_id}/decision")
def decide_admin_confirmation(request_id: uuid.UUID, payload: ConfirmationDecisionInput, request: Request) -> dict[str, Any]:
    reviewer = require_admin(request)
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        item = session.get(ManualConfirmationRequest, request_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Confirmation request not found")
        session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {
            "key": f"confirmation-subject:{item.source}:{item.kind}:{item.subject_id}"
        })
        item = session.query(ManualConfirmationRequest).filter_by(id=request_id).with_for_update().first()
        if item is None:
            raise HTTPException(status_code=404, detail="Confirmation request not found")
        if item.version != payload.expected_version:
            raise HTTPException(status_code=409, detail="Confirmation request is no longer current")
        previous_status = _validate_confirmation_status_for_decision(item.status, payload.decision)
        correction = previous_status is not None
        if correction:
            can_correct, reason = _confirmation_correction_state(session, item)
            if not can_correct:
                raise HTTPException(status_code=409, detail=reason or "Confirmation decision is no longer current")
        evidence = dict(item.evidence_json or {})
        reconciled_facts = 0
        if item.kind == "player_profile":
            try:
                participant_id = uuid.UUID(item.subject_id)
            except ValueError as exc:
                raise HTTPException(status_code=409, detail="Invalid participant reference") from exc
            participant = session.get(Participant, participant_id)
            profile = _get_or_create_player_profile(session, participant_id)
            candidate = evidence.get("candidate") or {}
            source_player_id = str(candidate.get("source_player_id") or item.candidate_id)
            profile_url = candidate.get("profile_url")
            if participant is None:
                raise HTTPException(status_code=409, detail="Fonbet participant is no longer available")
            if not source_player_id or not profile_url:
                raise HTTPException(status_code=409, detail="Stored Flashscore candidate details are incomplete")
            identities = dict(profile.source_identities or {})
            now_latest = session.query(TennisPlayerIdentityLink).filter_by(
                participant_id=participant_id, source="flashscore"
            ).order_by(TennisPlayerIdentityLink.available_at.desc(), TennisPlayerIdentityLink.id.desc()).all()
            latest_by_source = {}
            for link in now_latest:
                latest_by_source.setdefault(link.source_player_id, link)
            if payload.decision == "approve":
                if correction and previous_status == "rejected":
                    reconciled_facts = _reconcile_player_facts_after_identity_decision(
                        session, profile, participant_id, source_player_id, accept=True, now=now,
                    )
                prior_ids = [source_id for source_id, link in latest_by_source.items()
                             if link.status == "confirmed" and source_id != source_player_id]
                for old_id in prior_ids:
                    session.add(TennisPlayerIdentityLink(
                        participant_id=participant_id, source="flashscore", source_player_id=old_id,
                        status="revoked", evidence_json={"method": "admin_reassignment", "replaced_by": source_player_id},
                        available_at=now,
                    ))
                identities["flashscore"] = source_player_id
                identities.pop("flashscore_candidates", None)
                identities.pop("flashscore_candidate_import_job_id", None)
                profile.profile_url = profile_url
                profile.source_identities = identities
                profile.sync_status = "pending"
                profile.sync_error = None
                session.add(TennisPlayerIdentityLink(
                    participant_id=participant_id, source="flashscore", source_player_id=source_player_id,
                    status="confirmed", evidence_json={"method": "admin_confirmation_request",
                        "request_id": str(item.id), "profile_url": profile_url,
                        "participant_name": participant.canonical_name},
                    available_at=now + timedelta(microseconds=1),
                ))
                result_status = "approved"
            else:
                was_current_identity = identities.get("flashscore") == source_player_id
                identities["flashscore_rejected_ids"] = sorted(set(identities.get("flashscore_rejected_ids") or []) | {source_player_id})
                identities["flashscore_candidates"] = [row for row in identities.get("flashscore_candidates", [])
                                                          if str(row.get("source_player_id")) != source_player_id]
                if identities.get("flashscore") == source_player_id:
                    identities.pop("flashscore", None)
                    if profile.profile_url == profile_url:
                        profile.profile_url = None
                profile.source_identities = identities
                profile.sync_status = "pending"
                profile.sync_error = "PROFILE_SEARCH_AFTER_CORRECTION" if correction else "PROFILE_SEARCH_AFTER_REJECTION"
                session.add(TennisPlayerIdentityLink(
                    participant_id=participant_id, source="flashscore", source_player_id=source_player_id,
                    status="rejected", evidence_json={"method": "admin_rejection", "request_id": str(item.id)},
                    available_at=now,
                ))
                if was_current_identity:
                    reconciled_facts = _reconcile_player_facts_after_identity_decision(
                        session, profile, participant_id, source_player_id, accept=False, now=now,
                    )
                result_status = "rejected"
            if payload.decision == "approve":
                rejected_ids = set(identities.get("flashscore_rejected_ids") or [])
                rejected_ids.discard(source_player_id)
                if rejected_ids:
                    identities["flashscore_rejected_ids"] = sorted(rejected_ids)
                else:
                    identities.pop("flashscore_rejected_ids", None)
                profile.source_identities = identities
            if payload.decision == "approve":
                _supersede_siblings(session, item, reviewer, now)
        elif item.kind == "event_match":
            try:
                event_id = uuid.UUID(item.subject_id)
            except ValueError as exc:
                raise HTTPException(status_code=409, detail="Invalid event reference") from exc
            event = session.get(Event, event_id)
            if event is None or event.sport_code != "tennis":
                raise HTTPException(status_code=404, detail="Tennis event not found")
            source_match_id = item.candidate_id
            links = session.query(TennisPointMatchLink).filter_by(source_match_id=source_match_id).order_by(
                TennisPointMatchLink.available_at.desc(), TennisPointMatchLink.id.desc()).all()
            latest_link = links[0] if links else None
            if payload.decision == "approve":
                if latest_link and latest_link.fonbet_event_id not in (None, event_id) and latest_link.status in {"manual", "matched"}:
                    raise HTTPException(status_code=409, detail="Flashscore match is already linked")
                event_order = _match_home_is_event_a(session, event, evidence)
                session.add(TennisPointMatchLink(
                    source_match_id=source_match_id, fonbet_event_id=event_id, status="manual", confidence=1.0,
                    evidence_json={"resolution": "admin_confirmation_request", "request_id": str(item.id),
                                   "external_home_is_fonbet_a": event_order}, available_at=now,
                ))
                lookup = session.get(TennisMatchLookupJob, event_id, with_for_update=True)
                if lookup is None:
                    lookup = TennisMatchLookupJob(event_id=event_id, status="matched", next_run_at=now)
                    session.add(lookup)
                lookup.status = "matched"; lookup.source_match_id = source_match_id; lookup.last_error = None
                lookup.next_run_at = now; lookup.attempt_token = None; lookup.lease_until = None; lookup.updated_at = now
                point_job = session.get(TennisPointRefreshJob, event_id, with_for_update=True)
                if point_job is None:
                    point_job = TennisPointRefreshJob(event_id=event_id, status="queued", next_run_at=now,
                                                       last_requested_at=now, updated_at=now)
                    session.add(point_job)
                else:
                    point_job.status = "queued"; point_job.last_error = None; point_job.next_run_at = now
                    point_job.last_requested_at = now; point_job.attempt_token = None; point_job.lease_until = None
                    point_job.updated_at = now
                session.add(TennisPointRefreshJobEvent(event_id=event_id, status="queued", last_error=None,
                                                       source_match_id=source_match_id, available_at=now))
                result_status = "approved"
                _supersede_siblings(session, item, reviewer, now)
            else:
                session.add(TennisPointMatchLink(
                    source_match_id=source_match_id, fonbet_event_id=event_id, status="rejected", confidence=0.0,
                    evidence_json={"resolution": "admin_rejection", "request_id": str(item.id),
                                   "candidate_event_id": str(event_id)}, available_at=now,
                ))
                lookup = session.get(TennisMatchLookupJob, event_id, with_for_update=True)
                if lookup is None:
                    lookup = TennisMatchLookupJob(event_id=event_id, status="queued", next_run_at=now)
                    session.add(lookup)
                lookup.status = "queued"; lookup.last_error = "MANUAL_CANDIDATE_REJECTED"
                lookup.next_run_at = now; lookup.attempt_token = None; lookup.lease_until = None; lookup.updated_at = now
                point_job = session.get(TennisPointRefreshJob, event_id, with_for_update=True)
                if point_job:
                    point_job.status = "waiting_match"; point_job.last_error = "MATCH_SEARCH_AFTER_REJECTION"
                    point_job.next_run_at = now + timedelta(minutes=10); point_job.attempt_token = None
                    point_job.lease_until = None; point_job.updated_at = now
                    session.add(TennisPointRefreshJobEvent(event_id=event_id, status="waiting_match",
                        last_error="MATCH_SEARCH_AFTER_REJECTION", source_match_id=source_match_id, available_at=now))
                result_status = "rejected"
        else:
            raise HTTPException(status_code=422, detail="Unsupported confirmation kind")

        item.status = result_status
        item.decided_at = now
        item.decided_by = reviewer
        item.updated_at = now
        item.version += 1
        if item.kind == "player_profile":
            # The reviewed request is no longer pending before scheduling the
            # refresh. Other still-pending candidates keep the player frozen.
            _enqueue_player_refresh(session, participant_id, now)
        session.add(ManualConfirmationDecision(
            request_id=item.id, decision=payload.decision, request_version=payload.expected_version,
            decided_by=reviewer, details_json={"kind": item.kind, "subject_id": item.subject_id,
                                                "candidate_id": item.candidate_id,
                                                "reconciled_profile_facts": reconciled_facts,
                                                "corrected_previous_status": previous_status if correction else None}, created_at=now,
        ))
        session.commit()
        return {"id": str(item.id), "status": item.status, "decision": payload.decision,
                "corrected": correction,
                "next_action": "player_search" if item.kind == "player_profile" and payload.decision == "reject" else
                              "player_refresh" if item.kind == "player_profile" else
                              "match_search" if payload.decision == "reject" else "point_history"}
