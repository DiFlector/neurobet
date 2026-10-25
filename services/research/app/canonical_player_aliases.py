"""Durable source-player ID aliases for canonical identities.

The legacy ``source_ids`` JSON column stores at most one ID per provider. This
table is authoritative for additional confirmed IDs and keeps conflicting
provider IDs as candidates instead of choosing an owner implicitly.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select


def _alias_assignment_is_safe(rows: list[Any], canonical_player_id) -> bool:
    """Do not promote an alias if another candidate owner is recorded."""
    confirmed = [row for row in rows if row.status == "confirmed"]
    if confirmed:
        return len(confirmed) == 1 and confirmed[0].canonical_player_id == canonical_player_id
    return all(row.canonical_player_id == canonical_player_id for row in rows)


def resolve_confirmed_player_aliases(session, *, source: str, source_player_ids: list[str]) -> dict[str, Any]:
    """Return only IDs with exactly one confirmed canonical owner."""
    from db.models.external_tennis import ExtTennisCanonicalPlayerAlias

    ids = sorted({str(value).strip() for value in source_player_ids if str(value or "").strip()})
    if not ids:
        return {}
    rows = session.execute(select(ExtTennisCanonicalPlayerAlias).where(
        ExtTennisCanonicalPlayerAlias.source == source,
        ExtTennisCanonicalPlayerAlias.source_player_id.in_(ids),
        ExtTennisCanonicalPlayerAlias.status == "confirmed",
    )).scalars().all()
    owners: dict[str, dict[Any, Any]] = defaultdict(dict)
    for row in rows:
        owners[str(row.source_player_id)][row.canonical_player_id] = row
    return {
        source_id: next(iter(by_player.values()))
        for source_id, by_player in owners.items()
        if len(by_player) == 1
    }


def confirmed_source_ids_for_player(session, canonical_player_id) -> dict[str, set[str]]:
    """Return all confirmed provider IDs grouped by provider."""
    from db.models.external_tennis import ExtTennisCanonicalPlayerAlias

    rows = session.execute(select(
        ExtTennisCanonicalPlayerAlias.source,
        ExtTennisCanonicalPlayerAlias.source_player_id,
    ).where(
        ExtTennisCanonicalPlayerAlias.canonical_player_id == canonical_player_id,
        ExtTennisCanonicalPlayerAlias.status == "confirmed",
    )).all()
    aliases: dict[str, set[str]] = defaultdict(set)
    for source, source_player_id in rows:
        aliases[str(source)].add(str(source_player_id))
    return dict(aliases)


def register_confirmed_player_alias(
    session, *, source: str, source_player_id: str, canonical_player_id,
    evidence: dict[str, Any],
) -> bool:
    """Confirm a unique alias while preserving every previous candidate.

    A provider ID with a confirmed different owner or a candidate conflict is
    intentionally left unresolved. A player may have multiple IDs for the
    same provider, which the legacy JSON field cannot represent.
    """
    from db.models.external_tennis import ExtTennisCanonicalPlayerAlias

    source_player_id = str(source_player_id or "").strip()
    if not source_player_id:
        return False
    rows = session.execute(select(ExtTennisCanonicalPlayerAlias).where(
        ExtTennisCanonicalPlayerAlias.source == source,
        ExtTennisCanonicalPlayerAlias.source_player_id == source_player_id,
    )).scalars().all()
    if not _alias_assignment_is_safe(rows, canonical_player_id):
        return False
    existing = next((row for row in rows if row.canonical_player_id == canonical_player_id), None)
    if existing is None:
        session.add(ExtTennisCanonicalPlayerAlias(
            source=source,
            source_player_id=source_player_id,
            canonical_player_id=canonical_player_id,
            status="confirmed",
            evidence_json=dict(evidence or {}),
        ))
    else:
        existing.status = "confirmed"
        existing.evidence_json = {**dict(existing.evidence_json or {}), **dict(evidence or {})}
    return True


def adjudicate_candidate_player_alias(
    session, *, source: str, source_player_id: str, canonical_player_id,
    expected_candidate_ids: list[Any], rationale: str, evidence: dict[str, Any],
    decided_by: str,
):
    """Resolve one alias collision with an append-only reviewed decision.

    The reviewed owner may be one of the legacy candidates or another existing
    canonical identity that has stronger independent source evidence. The
    caller must provide the complete set of candidate owners observed during
    review; a concurrent or newly-added candidate makes the operation fail
    closed. This function is never used for automatic matching.
    """
    from db.models.external_tennis import (
        ExtTennisCanonicalPlayer,
        ExtTennisCanonicalPlayerAlias,
        ExtTennisCanonicalPlayerAliasDecision,
    )

    source_player_id = str(source_player_id or "").strip()
    rationale = str(rationale or "").strip()
    decided_by = str(decided_by or "").strip()
    expected = {str(value) for value in expected_candidate_ids}
    evidence = dict(evidence or {})
    if not source or not source_player_id or not rationale or not decided_by:
        raise ValueError("source, source_player_id, rationale and decided_by are required")
    if not evidence:
        raise ValueError("evidence is required for alias adjudication")
    if len(expected) < 2:
        raise ValueError("adjudication must review at least two explicit candidate owners")
    selected_player = session.get(ExtTennisCanonicalPlayer, canonical_player_id)
    if selected_player is None:
        raise ValueError("selected canonical player does not exist")

    rows = session.execute(select(ExtTennisCanonicalPlayerAlias).where(
        ExtTennisCanonicalPlayerAlias.source == source,
        ExtTennisCanonicalPlayerAlias.source_player_id == source_player_id,
    ).with_for_update()).scalars().all()
    actual = {str(row.canonical_player_id) for row in rows}
    if actual != expected:
        raise ValueError("candidate owners changed since review; re-audit before adjudication")
    if any(row.status == "confirmed" for row in rows):
        raise ValueError("source player ID already has a confirmed owner")
    if any(row.status != "candidate" for row in rows):
        raise ValueError("only unresolved candidate aliases can be adjudicated")
    selected = next((row for row in rows if row.canonical_player_id == canonical_player_id), None)
    if selected is None:
        selected = ExtTennisCanonicalPlayerAlias(
            source=source, source_player_id=source_player_id,
            canonical_player_id=canonical_player_id, status="candidate",
            evidence_json={"method": "reviewed_external_canonical_target"},
        )
        session.add(selected)
        session.flush()
        rows.append(selected)

    rejected_ids = sorted(str(row.canonical_player_id) for row in rows if row is not selected)
    decision = ExtTennisCanonicalPlayerAliasDecision(
        source=source,
        source_player_id=source_player_id,
        canonical_player_id=canonical_player_id,
        rejected_candidate_ids=rejected_ids,
        rationale=rationale,
        evidence_json=evidence,
        decided_by=decided_by,
    )
    session.add(decision)
    session.flush()
    for row in rows:
        row.status = "confirmed" if row is selected else "rejected"
        row.evidence_json = {
            **dict(row.evidence_json or {}),
            "adjudication_id": str(decision.id),
            "adjudication_rationale": rationale,
            "adjudicated_by": decided_by,
            "adjudicated_at": decision.created_at.isoformat() if decision.created_at else None,
        }
    session.flush()
    return decision


def reassign_confirmed_player_alias(
    session, *, source: str, source_player_id: str, expected_current_owner_id,
    canonical_player_id, rationale: str, evidence: dict[str, Any], decided_by: str,
):
    """Move a provider ID off an API-only placeholder after reviewed evidence."""
    from db.models.external_tennis import (
        ExtTennisCanonicalPlayer,
        ExtTennisCanonicalPlayerAlias,
        ExtTennisCanonicalPlayerAliasDecision,
    )

    source_player_id = str(source_player_id or "").strip()
    rationale = str(rationale or "").strip()
    decided_by = str(decided_by or "").strip()
    evidence = dict(evidence or {})
    if not source or not source_player_id or not rationale or not decided_by or not evidence:
        raise ValueError("source, source_player_id, rationale, decided_by and evidence are required")
    if expected_current_owner_id == canonical_player_id:
        raise ValueError("new canonical owner must differ from the expected current owner")
    rows = session.execute(select(ExtTennisCanonicalPlayerAlias).where(
        ExtTennisCanonicalPlayerAlias.source == source,
        ExtTennisCanonicalPlayerAlias.source_player_id == source_player_id,
    ).with_for_update()).scalars().all()
    confirmed = [row for row in rows if row.status == "confirmed"]
    if len(confirmed) != 1 or confirmed[0].canonical_player_id != expected_current_owner_id:
        raise ValueError("confirmed alias owner changed since review; re-audit before reassignment")
    target = next((row for row in rows if row.canonical_player_id == canonical_player_id), None)
    if target is not None and target.status != "candidate":
        raise ValueError("new owner alias must be absent or an unresolved candidate")
    if session.get(ExtTennisCanonicalPlayer, canonical_player_id) is None:
        raise ValueError("selected canonical player does not exist")

    prior_owner = confirmed[0]
    if target is None:
        target = ExtTennisCanonicalPlayerAlias(
            source=source, source_player_id=source_player_id,
            canonical_player_id=canonical_player_id, status="candidate",
            evidence_json={"method": "reviewed_reassignment_candidate"},
        )
        session.add(target)
        session.flush()
        rows.append(target)
    decision = ExtTennisCanonicalPlayerAliasDecision(
        source=source, source_player_id=source_player_id,
        canonical_player_id=canonical_player_id,
        rejected_candidate_ids=[str(prior_owner.canonical_player_id)],
        rationale=rationale, evidence_json=evidence, decided_by=decided_by,
    )
    session.add(decision)
    session.flush()
    for row in rows:
        if row is target:
            continue
        row.status = "rejected"
        row.evidence_json = {
            **dict(row.evidence_json or {}),
            "adjudication_id": str(decision.id),
            "adjudication_rationale": rationale,
            "adjudicated_by": decided_by,
            "adjudicated_at": decision.created_at.isoformat() if decision.created_at else None,
        }
    session.flush()
    target.status = "confirmed"
    target.evidence_json = {
        **dict(target.evidence_json or {}),
        "adjudication_id": str(decision.id),
        "adjudication_rationale": rationale,
        "adjudicated_by": decided_by,
        "adjudicated_at": decision.created_at.isoformat() if decision.created_at else None,
    }
    session.flush()
    return decision
