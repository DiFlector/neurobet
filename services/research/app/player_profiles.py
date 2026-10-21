"""Persistent participant identity and incremental tennis metric updates."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import and_, case, cast, literal, or_, select
from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert

from tennis_adapter.normalizer import normalize_russian_player_name
from tennis_adapter.player_metrics import aggregate_results, append_result, digest_metrics, metrics_as_of

from .tennis_flashscore import PARSER_VERSION, _player_name_matches, _search_tokens


def _name_key(value: Optional[str]) -> str:
    normalized = normalize_russian_player_name(value or "")
    return re.sub(r"[^a-zа-яё0-9]+", "", normalized.casefold().replace("ё", "е"))


def resolve_participant(session, raw_name: str):
    """Resolve an exact unique Fonbet participant or leave the person unlinked."""
    from db.models import Participant, ParticipantAlias

    key = _name_key(raw_name)
    if not key:
        return None
    rows = session.execute(
        select(Participant.id, Participant.canonical_name)
        .where(Participant.sport_code == "tennis")
    ).all()
    candidates = {row.id for row in rows if _name_key(row.canonical_name) == key}
    aliases = session.execute(
        select(ParticipantAlias.participant_id, ParticipantAlias.alias)
        .join(Participant, Participant.id == ParticipantAlias.participant_id)
        .where(ParticipantAlias.source == "fonbet", Participant.sport_code == "tennis")
    ).all()
    candidates.update(row.participant_id for row in aliases if _name_key(row.alias) == key)
    if len(candidates) != 1:
        return None
    return session.get(Participant, next(iter(candidates)))


def _canonical_player_id(session, participant_name: str):
    from db.models.external_tennis import ExtTennisCanonicalPlayer

    key = _name_key(participant_name)
    rows = session.execute(select(ExtTennisCanonicalPlayer.id, ExtTennisCanonicalPlayer.display_name)).all()
    found = [row.id for row in rows if _name_key(row.display_name) == key]
    return found[0] if len(found) == 1 else None


def _canonical_match_key(item) -> str:
    # The source match id is immutable across result corrections. Keep the
    # participant/tournament fingerprint as a fallback for cross-source merges.
    if getattr(item, "source_match_id", None):
        payload = json.dumps(["source-match", str(item.source_match_id)], separators=(",", ":"))
    else:
        names = sorted((_name_key(item.home_name), _name_key(item.away_name)))
        payload = json.dumps([
            item.played_at.date().isoformat(), names,
            _name_key(getattr(item, "tournament", "")),
            _name_key(getattr(item, "round_name", "")),
            str(getattr(item, "gender", "unknown")),
        ], separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _matching_canonical_match_key(item, candidates) -> Optional[str]:
    """Link an archive result only when date, player pair and venue agree."""
    # Candidate pairs have already been restricted to the same two players.
    candidates = [row for row in candidates if row.played_at.date() == item.played_at.date()]
    if item.gender not in ("unknown", "mixed"):
        candidates = [row for row in candidates if row.gender in (item.gender, "unknown")]
    ignored = {"open", "tennis", "singles", "atp", "wta"}
    venue_tokens = {token for token in _search_tokens(item.tournament)
                    if len(token) >= 4 and token not in ignored}
    matched = [row for row in candidates if venue_tokens.intersection(
        {token for token in _search_tokens(row.tournament) if len(token) >= 4 and token not in ignored}
    )]
    return matched[0].canonical_key if len(matched) == 1 else None


def seed_profile_from_archive(session, participant, profile) -> bool:
    """Materialize an existing canonical archive for a new participant once."""
    from db.models import TennisPlayerMetricSnapshot, TennisPlayerResultFact
    from db.models.external_tennis import ExtTennisCanonicalMatch, ExtTennisCanonicalPlayer

    if profile.metrics_version or not participant or not participant.canonical_name:
        return bool(profile.metrics_version)
    tokens = _search_tokens(participant.canonical_name)
    names = [token for token in tokens if len(token) > 1]
    surname_keys = set(names)
    for width in (2, 3):
        for start in range(max(0, len(names) - width + 1)):
            surname_keys.add(" ".join(names[start : start + width]))
    candidates = session.execute(
        select(ExtTennisCanonicalPlayer).where(ExtTennisCanonicalPlayer.surname_key.in_(surname_keys))
    ).scalars().all() if surname_keys else []
    candidates = [row for row in candidates if _player_name_matches(participant.canonical_name, row.display_name)]
    if len(candidates) != 1:
        return False
    person = candidates[0]
    profile.canonical_player_id = person.id
    identities = dict(profile.source_identities or {})
    identities.update({f"archive:{key}": value for key, value in (person.source_ids or {}).items()})
    profile.source_identities = identities
    matches = session.execute(
        select(ExtTennisCanonicalMatch).where(
            (ExtTennisCanonicalMatch.winner_player_id == person.id)
            | (ExtTennisCanonicalMatch.loser_player_id == person.id)
        ).order_by(ExtTennisCanonicalMatch.played_at, ExtTennisCanonicalMatch.canonical_key)
    ).scalars().all()
    now = datetime.now(timezone.utc)
    inserted = 0
    for match in matches:
        if not match.score or re.search(r"retired|walkover|void|abandon|cancel", match.score, re.I):
            continue
        sets = re.findall(r"\b\d{1,2}-\d{1,2}\b", match.score)
        if len(sets) < 2:
            continue
        source_match_id = str(match.canonical_key)
        if session.execute(
            select(TennisPlayerResultFact.id).where(
                TennisPlayerResultFact.participant_id == participant.id,
                TennisPlayerResultFact.source == "canonical_archive",
                TennisPlayerResultFact.source_match_id == source_match_id,
            ).limit(1)
        ).first():
            continue
        won = match.winner_player_id == person.id
        fact_hash = _result_fact_hash(
            source_match_id, won, len(sets), match.surface or "unknown", match.tour_level or "unknown"
        )
        session.add(TennisPlayerResultFact(
            participant_id=participant.id,
            source="canonical_archive",
            source_match_id=source_match_id,
            result_hash=fact_hash,
            played_at=match.played_at,
            available_at=match.available_at,
            surface=match.surface or "unknown",
            tour_level=match.tour_level or "unknown",
            won=won,
            sets_played=len(sets),
            source_payload={
                "canonical_player_id": str(person.id),
                "canonical_match_id": str(match.id),
                "archive_available_at": match.available_at.isoformat(),
            },
        ))
        inserted += 1
    if not inserted:
        profile.source_watermark = None
        return False
    session.flush()
    facts = session.execute(
        select(TennisPlayerResultFact).where(
            TennisPlayerResultFact.participant_id == participant.id,
            TennisPlayerResultFact.available_at <= now,
        ).order_by(
            TennisPlayerResultFact.source_match_id,
            TennisPlayerResultFact.available_at,
            TennisPlayerResultFact.created_at,
        )
    ).scalars().all()
    latest_facts = {}
    for fact in facts:
        current = latest_facts.get(fact.source_match_id)
        if current is None or (fact.available_at, fact.created_at) > (current.available_at, current.created_at):
            latest_facts[fact.source_match_id] = fact
    facts = [fact for fact in latest_facts.values() if fact.valid]
    metrics = aggregate_results({
        "source_match_id": fact.source_match_id,
        "played_at": fact.played_at,
        "won": fact.won,
        "surface": fact.surface,
        "tour_level": fact.tour_level,
        "sets_played": fact.sets_played,
        "duration_minutes": fact.duration_minutes,
        "serve_points": fact.serve_points,
        "serve_points_won": fact.serve_points_won,
    } for fact in facts)
    digest = digest_metrics(metrics)
    profile.metrics_version = 1
    profile.metrics_json = metrics
    profile.history_digest = digest
    profile.history_match_count = int(metrics["overall"]["matches"])
    profile.source_watermark = max((fact.played_at for fact in facts), default=None)
    profile.metrics_available_at = now
    profile.sync_status = "stale"
    profile.sync_error = "FLASHscore_REFRESH_PENDING"
    session.add(TennisPlayerMetricSnapshot(
        participant_id=participant.id,
        metrics_version=profile.metrics_version,
        available_at=now,
        source_watermark=profile.source_watermark,
        history_digest=digest,
        metrics_json=metrics,
    ))
    return True


def enqueue_player_refresh(session, participant_id, reason: str):
    """Coalesce refresh requests into one durable work item per participant."""
    from db.models import ManualConfirmationRequest, TennisPlayerRefreshJob
    from sqlalchemy import text

    now = datetime.now(timezone.utc)
    subject_id = str(participant_id)
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {
        "key": f"confirmation-subject:flashscore:player_profile:{subject_id}"
    })
    pending_confirmation = session.query(ManualConfirmationRequest.id).filter_by(
        kind="player_profile", subject_id=subject_id, source="flashscore", status="pending"
    ).first()
    if pending_confirmation:
        job = session.get(TennisPlayerRefreshJob, participant_id, with_for_update=True)
        if job is None:
            job = TennisPlayerRefreshJob(
                participant_id=participant_id,
                status="waiting_confirmation",
                requested_reason=reason,
                requested_at=now,
                next_run_at=now,
                progress_json={"stage": "identity_confirmation_required"},
                updated_at=now,
            )
            session.add(job)
        else:
            job.status = "waiting_confirmation"
            job.last_error = "IDENTITY_CONFIRMATION_REQUIRED"
            job.progress_json = {"stage": "identity_confirmation_required"}
            job.attempt_token = None
            job.lease_until = None
            job.completed_at = now
            job.updated_at = now
        return job
    table = TennisPlayerRefreshJob.__table__
    active_lease = and_(table.c.status == "running", table.c.lease_until.is_not(None), table.c.lease_until > now)
    session.execute(
        pg_insert(TennisPlayerRefreshJob).values(
            participant_id=participant_id,
            status="queued",
            requested_reason=reason,
            requested_at=now,
            next_run_at=now,
            progress_json={"stage": "queued"},
            updated_at=now,
        ).on_conflict_do_update(
            index_elements=[table.c.participant_id],
            set_={
                "status": case((active_lease, "running"), else_="queued"),
                "requested_reason": case((active_lease, table.c.requested_reason), else_=reason),
                "requested_at": case((active_lease, table.c.requested_at), else_=now),
                "next_run_at": case((active_lease, table.c.next_run_at), else_=now),
                "last_error": case((active_lease, table.c.last_error), else_=None),
                "progress_json": case(
                    (active_lease, table.c.progress_json),
                    else_=cast(literal('{"stage":"queued"}'), JSONB),
                ),
                "lease_until": case((active_lease, table.c.lease_until), else_=None),
                "completed_at": case((active_lease, table.c.completed_at), else_=None),
                "updated_at": now,
            },
        )
    )
    return session.get(TennisPlayerRefreshJob, participant_id, with_for_update=True)


def _result_hash(item, won: bool) -> str:
    score_sets = [part for part in (getattr(item, "score", "") or "").split() if "-" in part]
    payload = json.dumps({
        "key": _canonical_match_key(item),
        "won": won,
        "sets_played": len(score_sets),
        "surface": getattr(item, "surface", "unknown"),
        "tour_level": getattr(item, "tour_level", "unknown"),
    }, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _result_fact_hash(match_key: str, won: bool, sets_played: int, surface: str, tour_level: str) -> str:
    payload = json.dumps({
        "key": match_key,
        "won": won,
        "sets_played": sets_played,
        "surface": surface or "unknown",
        "tour_level": tour_level or "unknown",
    }, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _is_duplicate_adjacent_fact(previous_fact, result_hash: str) -> bool:
    """Suppress a repeated current result, while preserving A -> B -> A corrections."""
    return previous_fact is not None and previous_fact.result_hash == result_hash


def _confirmed_source_participants(session, available_at: datetime) -> dict[str, Any]:
    """Resolve source IDs only through the latest point-in-time confirmation."""
    from db.models import TennisPlayerIdentityLink

    rows = session.execute(
        select(TennisPlayerIdentityLink)
        .where(
            TennisPlayerIdentityLink.source == "flashscore",
            TennisPlayerIdentityLink.available_at <= available_at,
        )
        .order_by(
            TennisPlayerIdentityLink.participant_id,
            TennisPlayerIdentityLink.available_at,
            TennisPlayerIdentityLink.id,
        )
    ).scalars().all()
    latest_by_participant = {}
    for row in rows:
        latest_by_participant[row.participant_id] = row
    by_source_id: dict[str, set[uuid.UUID]] = {}
    for participant_id, row in latest_by_participant.items():
        if row.status == "confirmed":
            by_source_id.setdefault(row.source_player_id, set()).add(participant_id)
    return {
        source_id: next(iter(participant_ids))
        for source_id, participant_ids in by_source_id.items()
        if len(participant_ids) == 1
    }


def persist_profile_facts(session, result, owner_participant_id=None) -> int:
    """Save versioned player facts and refresh current metric rows only on new source versions."""
    from db.models import Participant, TennisPlayerMetricSnapshot, TennisPlayerProfile, TennisPlayerResultFact
    from db.models.external_tennis import ExtTennisCanonicalPlayer

    profile = result.profile
    if profile is None:
        return 0
    changes: dict[uuid.UUID, list[tuple[Any, bool, bool]]] = {}
    participant_for_source_id: dict[str, Any] = {}
    profile_rows_by_participant: dict[uuid.UUID, Any] = {}
    participants = session.execute(select(Participant).where(Participant.sport_code == "tennis")).scalars().all()
    participant_by_id = {participant.id: participant for participant in participants}
    confirmed_source_ids = _confirmed_source_participants(session, result.retrieved_at)
    canonical_ids: dict[str, list[uuid.UUID]] = {}
    canonical_players = session.execute(select(ExtTennisCanonicalPlayer)).scalars().all()
    canonical_by_token: dict[str, list[Any]] = {}
    for canonical in canonical_players:
        canonical_ids.setdefault(_name_key(canonical.display_name), []).append(canonical.id)
        for token in set(_search_tokens(canonical.display_name)):
            if len(token) > 1:
                canonical_by_token.setdefault(token, []).append(canonical)

    owner_participant = participant_by_id.get(owner_participant_id) if owner_participant_id else None

    canonical_person_cache: dict[str, Optional[uuid.UUID]] = {}
    source_for_fact_key: dict[tuple[uuid.UUID, str], str] = {}

    def canonical_person_id(raw_name: str):
        key = _name_key(raw_name)
        if key in canonical_person_cache:
            return canonical_person_cache[key]
        exact = canonical_ids.get(_name_key(raw_name), [])
        if len(exact) == 1:
            canonical_person_cache[key] = exact[0]
            return exact[0]
        # A full archive scan per opponent made profile imports quadratic in
        # the size of canonical history. Player feeds preserve the surname token
        # even when the given name is abbreviated; use exact token buckets for
        # fuzzy-order matching. Unknown spellings stay unresolved rather than
        # triggering an expensive whole-archive guess.
        tokens = {token for token in _search_tokens(raw_name) if len(token) > 1}
        candidates = {row.id: row for token in tokens for row in canonical_by_token.get(token, [])}
        fuzzy = [row.id for row in candidates.values() if _player_name_matches(raw_name, row.display_name)]
        canonical_person_cache[key] = fuzzy[0] if len(fuzzy) == 1 else None
        return canonical_person_cache[key]

    def canonical_match_id(item):
        winner_id = canonical_person_id(item.winner_name)
        loser_id = canonical_person_id(item.loser_name)
        if not winner_id or not loser_id:
            return None
        from db.models.external_tennis import ExtTennisCanonicalMatch

        start = item.played_at - timedelta(days=1)
        finish = item.played_at + timedelta(days=1)
        matches = session.execute(
            select(ExtTennisCanonicalMatch).where(
                ExtTennisCanonicalMatch.played_at >= start,
                ExtTennisCanonicalMatch.played_at <= finish,
                or_(
                    and_(
                        ExtTennisCanonicalMatch.winner_player_id == winner_id,
                        ExtTennisCanonicalMatch.loser_player_id == loser_id,
                    ),
                    and_(
                        ExtTennisCanonicalMatch.winner_player_id == loser_id,
                        ExtTennisCanonicalMatch.loser_player_id == winner_id,
                    ),
                ),
            )
        ).scalars().all()
        return _matching_canonical_match_key(item, matches)

    for item in profile.matches:
        for raw_name, source_id in ((item.home_name, item.home_player_id), (item.away_name, item.away_player_id)):
            if not source_id:
                continue
            participant_id = (
                owner_participant_id
                if source_id == profile.source_player_id and owner_participant is not None
                else confirmed_source_ids.get(source_id)
            )
            participant = participant_by_id.get(participant_id) if participant_id else None
            if participant is None:
                continue
            participant_for_source_id[source_id] = participant
            current = profile_rows_by_participant.get(participant.id)
            if current is None:
                current = session.get(TennisPlayerProfile, participant.id)
            if current is None:
                current = TennisPlayerProfile(participant_id=participant.id)
                session.add(current)
            profile_rows_by_participant[participant.id] = current
            identities = dict(current.source_identities or {})
            identities["flashscore"] = source_id
            current.source_identities = identities
            canonical_matches = canonical_ids.get(_name_key(participant.canonical_name), [])
            current.canonical_player_id = canonical_matches[0] if len(canonical_matches) == 1 else None
            if source_id == profile.source_player_id:
                current.profile_url = result.url
                current.sync_status = result.status
                current.sync_error = result.reason
                current.last_attempt_at = result.retrieved_at
                current.last_success_at = result.retrieved_at
        # Fact attribution can become possible after the source observation was
        # first imported (for example after a manual identity confirmation).
        # Revisit every parsed row on a profile refresh; the append-only fact
        # hash below makes unchanged rows idempotent while allowing newly
        # confirmed participants to receive their historical metrics.
        match_key = canonical_match_id(item) or _canonical_match_key(item)
        score_sets = [part for part in item.score.split() if "-" in part]
        for raw_name, source_id, is_winner in (
            (item.winner_name, item.winner_player_id, True),
            (item.loser_name, item.loser_player_id, False),
        ):
            participant = participant_for_source_id.get(source_id or "")
            if participant is None and source_id:
                confirmed_participant_id = confirmed_source_ids.get(source_id)
                participant = participant_by_id.get(confirmed_participant_id) if confirmed_participant_id else None
            if participant is None:
                continue
            fact_identity = (participant.id, match_key)
            earlier_source_id = source_for_fact_key.get(fact_identity)
            if earlier_source_id is not None and earlier_source_id != item.source_match_id:
                # Two distinct source match IDs cannot share one fact version
                # timestamp. Retain both and leave the cross-source link open.
                match_key_for_fact = _canonical_match_key(item)
            else:
                match_key_for_fact = match_key
                source_for_fact_key[fact_identity] = item.source_match_id
            old_facts = session.execute(
                select(TennisPlayerResultFact).where(
                    TennisPlayerResultFact.participant_id == participant.id,
                    TennisPlayerResultFact.source_match_id == match_key_for_fact,
                ).order_by(TennisPlayerResultFact.available_at.desc())
            ).scalars().all()
            old_fact = old_facts[0] if old_facts else None
            fact_hash = _result_fact_hash(
                match_key_for_fact, is_winner, len(score_sets), item.surface, item.tour_level
            )
            # Only the current version suppresses an import. A historical match
            # correction that later reverts to an earlier value is still a new
            # source observation and must become the latest version again.
            if _is_duplicate_adjacent_fact(old_fact, fact_hash):
                continue
            changed_prior = old_fact is not None
            fact = TennisPlayerResultFact(
                participant_id=participant.id,
                source="flashscore",
                source_match_id=match_key_for_fact,
                result_hash=fact_hash,
                played_at=item.played_at,
                available_at=result.retrieved_at,
                surface=item.surface,
                tour_level=item.tour_level,
                won=is_winner,
                sets_played=len(score_sets),
                source_payload={
                    "source_match_id": item.source_match_id,
                    "source_player_id": source_id,
                    "source_url": item.source_url or result.url,
                    "source_content_hash": result.content_hash,
                    "parser_version": PARSER_VERSION,
                    "result_hash": fact_hash,
                },
            )
            session.add(fact)
            changes.setdefault(participant.id, []).append((fact, changed_prior, is_winner))

    # A full accessible-profile scan can reveal that a result accepted by an
    # older parser is actually incomplete (for example a men's major ending
    # before three sets). Append a false fact version so historical as-of reads
    # retain the old knowledge while current metrics stop counting it.
    if profile.pagination_complete and profile.history_scope == "full_accessible" and profile.rejected_source_matches:
        rejected_by_id = {
            row["source_match_id"]: row
            for row in profile.rejected_source_matches
            if row.get("source_match_id")
        }
        if rejected_by_id:
            prior_versions = session.execute(
                select(TennisPlayerResultFact).where(
                    TennisPlayerResultFact.source == "flashscore",
                    TennisPlayerResultFact.source_payload["source_player_id"].astext == profile.source_player_id,
                    TennisPlayerResultFact.source_payload["source_match_id"].astext.in_(list(rejected_by_id)),
                ).order_by(TennisPlayerResultFact.available_at)
            ).scalars().all()
            latest_by_fact = {}
            for prior in prior_versions:
                key = (prior.participant_id, prior.source_match_id)
                current = latest_by_fact.get(key)
                if current is None or prior.available_at > current.available_at:
                    latest_by_fact[key] = prior
            for (participant_id, fact_key), prior in latest_by_fact.items():
                if not prior.valid:
                    continue
                rejected = rejected_by_id[prior.source_payload.get("source_match_id")]
                corrected_hash = hashlib.sha256(json.dumps({
                    "prior_hash": prior.result_hash,
                    "valid": False,
                    "reason": rejected["reason"],
                    "parser_version": PARSER_VERSION,
                }, sort_keys=True).encode()).hexdigest()
                correction = TennisPlayerResultFact(
                    participant_id=participant_id,
                    source="flashscore",
                    source_match_id=fact_key,
                    result_hash=corrected_hash,
                    played_at=prior.played_at,
                    available_at=result.retrieved_at,
                    surface=prior.surface,
                    tour_level=prior.tour_level,
                    won=prior.won,
                    valid=False,
                    sets_played=prior.sets_played,
                    duration_minutes=prior.duration_minutes,
                    serve_points=prior.serve_points,
                    serve_points_won=prior.serve_points_won,
                    source_payload={
                        **(prior.source_payload or {}),
                        "valid": False,
                        "invalidated_reason": rejected["reason"],
                        "invalidated_at": result.retrieved_at.isoformat(),
                        "parser_version": PARSER_VERSION,
                    },
                )
                session.add(correction)
                changes.setdefault(participant_id, []).append((correction, True, prior.won))

    session.flush()
    now = result.retrieved_at
    for participant_id, facts in changes.items():
        profile_row = profile_rows_by_participant.get(participant_id) or session.get(
            TennisPlayerProfile, participant_id
        )
        existing_rows = session.execute(
            select(TennisPlayerResultFact).where(
                TennisPlayerResultFact.participant_id == participant_id,
                TennisPlayerResultFact.available_at <= now,
            ).order_by(TennisPlayerResultFact.played_at, TennisPlayerResultFact.available_at)
        ).scalars().all()
        latest_by_match = {}
        for fact in existing_rows:
            previous = latest_by_match.get(fact.source_match_id)
            if previous is None or fact.available_at > previous.available_at:
                latest_by_match[fact.source_match_id] = fact
        # A false latest version is an append-only correction that removes the
        # earlier result from current statistics without erasing its history.
        selected = [fact for fact in latest_by_match.values() if fact.valid]
        watermark = profile_row.source_watermark if profile_row else None
        can_append = bool(profile_row and profile_row.metrics_json and all(
            not correction and (watermark is None or fact.played_at >= watermark)
            for fact, correction, _ in facts
        ))
        if can_append:
            metrics = dict(profile_row.metrics_json)
            for fact, _, _ in sorted(facts, key=lambda row: (row[0].played_at, row[0].source_match_id)):
                metrics = append_result(metrics, {
                    "played_at": fact.played_at, "won": fact.won, "surface": fact.surface,
                    "tour_level": fact.tour_level, "sets_played": fact.sets_played,
                    "duration_minutes": fact.duration_minutes,
                    "serve_points": fact.serve_points, "serve_points_won": fact.serve_points_won,
                })
        else:
            metrics = aggregate_results({
                "source_match_id": fact.source_match_id, "played_at": fact.played_at, "won": fact.won,
                "surface": fact.surface, "tour_level": fact.tour_level, "sets_played": fact.sets_played,
                "duration_minutes": fact.duration_minutes,
                "serve_points": fact.serve_points, "serve_points_won": fact.serve_points_won,
            } for fact in selected)
        digest = digest_metrics(metrics)
        profile_row.metrics_json = metrics
        profile_row.history_digest = digest
        profile_row.metrics_version = int(profile_row.metrics_version or 0) + 1
        profile_row.history_match_count = int(metrics["overall"]["matches"])
        profile_row.source_watermark = max((fact.played_at for fact in selected), default=None)
        profile_row.metrics_available_at = now
        snapshot = TennisPlayerMetricSnapshot(
            participant_id=participant_id,
            metrics_version=profile_row.metrics_version,
            available_at=now,
            source_watermark=profile_row.source_watermark,
            history_digest=digest,
            metrics_json=metrics,
        )
        session.add(snapshot)
    return sum(len(facts) for facts in changes.values())


