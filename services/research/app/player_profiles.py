"""Persistent participant identity and incremental tennis metric updates."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import and_, or_, select, text
from db.identity import confirmed_source_player_id

from tennis_adapter.normalizer import normalize_russian_player_name
from tennis_adapter.player_metrics import aggregate_results, append_result, digest_metrics, metrics_as_of

from .tennis_flashscore import PARSER_VERSION, _player_name_matches, _search_tokens


def profile_refresh_timeout_seconds(stop_before_date, environ=None) -> int:
    """Bound recent profile refreshes more tightly than first/full imports."""
    environ = os.environ if environ is None else environ
    key = ("FLASHSCORE_TENNIS_INCREMENTAL_TIMEOUT_SECONDS"
           if stop_before_date is not None else "FLASHSCORE_TENNIS_PROFILE_TIMEOUT_SECONDS")
    default = "120" if stop_before_date is not None else "300"
    return max(30, int(environ.get(key, default)))


def advance_player_refresh_progress(progress: dict[str, Any] | None, stage: str, **fields) -> dict[str, Any]:
    """Update job stage while retaining durable browser-page checkpoints."""
    updated = dict(progress or {})
    updated.update({"stage": stage, **fields})
    return updated


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


def lock_flashscore_source_entities(session, player_ids=(), match_ids=()) -> None:
    """Serialize overlapping profile imports before reading/upserting shared IDs.

    A profile page can contain the same match as another player's page. The
    unique indexes are the final invariant, but a check-then-insert race used
    to abort the whole profile transaction. Acquire deterministic transaction
    advisory locks in one round trip before querying the source projections.
    """
    keys = sorted({
        *(f"flashscore:player:{value}" for value in player_ids if value),
        *(f"flashscore:match:{value}" for value in match_ids if value),
    })
    if not keys:
        return
    session.execute(text(
        "SELECT pg_advisory_xact_lock(hashtextextended(lock_key, 0)) "
        "FROM unnest(CAST(:lock_keys AS text[])) AS lock_keys(lock_key) "
        "ORDER BY lock_key"
    ), {"lock_keys": keys}).all()


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


def _record_tml_history_coverage(session, participant, profile, person, now: datetime) -> bool:
    tml_player_id = (person.source_ids or {}).get("tml")
    if not tml_player_id:
        return False
    from .player_data_coverage import upsert_coverage
    tour_keys = ("season_" + str(now.year) + "_wta",) if person.gender == "w" else (
        "season_" + str(now.year) + "_atp", "season_" + str(now.year) + "_challenger"
    )
    state_rows = session.execute(text("""
        SELECT state_key, state_json
        FROM external_tennis_source_state
        WHERE source='tennis_my_life' AND state_key = ANY(:keys)
    """), {"keys": list(tour_keys)}).all()
    state = {row.state_key: row.state_json for row in state_rows}
    projection_row = session.execute(text("""
        SELECT state_json FROM external_tennis_source_state
        WHERE source='canonical_tennis' AND state_key='projection'
    """)).first()
    projection = (projection_row[0] if projection_row else None) or {}
    projected_at_raw = projection.get("projected_at")
    projected_hashes = projection.get("source_hashes") or {}
    if not projected_at_raw:
        return False
    try:
        projected_at = datetime.fromisoformat(projected_at_raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    if projected_at.tzinfo is None:
        projected_at = projected_at.replace(tzinfo=timezone.utc)
    # A recent HTTP fetch is not enough: the canonical projection must have
    # incorporated exactly those source snapshots, and the profile metrics
    # must have been materialized from that projection.
    if profile.metrics_available_at is None or profile.metrics_available_at < projected_at:
        return False
    checked = []
    for key in tour_keys:
        item = state.get(key) or {}
        if int(item.get("status") or 0) != 200 or not item.get("observed_at"):
            return False
        if not item.get("content_sha256") or projected_hashes.get(key) != item.get("content_sha256"):
            return False
        try:
            observed_at = datetime.fromisoformat(item["observed_at"].replace("Z", "+00:00"))
        except ValueError:
            return False
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
        if not timedelta(0) <= now - observed_at <= timedelta(days=7):
            return False
        checked.append((key, observed_at))
    upsert_coverage(
        session, participant_id=participant.id, source="tennis_my_life",
        capability="match_history", checked_at=min(at for _, at in checked),
        source_watermark=profile.source_watermark, status="available", complete=True,
        evidence={"source_player_id": str(tml_player_id), "season": now.year,
                  "partitions": [key for key, _ in checked],
                  "coverage_scope": "provider_season_files"},
    )
    session.flush()
    return True


def _record_tennis_explorer_partial_coverage(session, participant, profile, person, now: datetime) -> bool:
    """Publish an accessible TE profile snapshot as usable partial history only."""
    slug = (person.source_ids or {}).get("tennis_explorer")
    if not slug or not profile.metrics_version or not profile.history_match_count:
        return False
    row = session.execute(text("""
        SELECT state_json FROM external_tennis_source_state
        WHERE source='tennis_explorer' AND state_key=:key
    """), {"key": f"profile:{slug}"}).first()
    if not row:
        return False
    state = row[0] or {}
    if isinstance(state, str):
        try:
            state = json.loads(state)
        except ValueError:
            return False
    if (not isinstance(state, dict) or int(state.get("status") or 0) != 200
            or state.get("complete_career_history") is not False
            or int(state.get("normalized_matches") or 0) <= 0):
        return False
    checked_raw = state.get("checked_at") or state.get("observed_at")
    try:
        checked_at = datetime.fromisoformat(str(checked_raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    if not timedelta(0) <= now - checked_at <= timedelta(days=7):
        return False
    from .player_data_coverage import upsert_coverage
    upsert_coverage(
        session, participant_id=participant.id, source="tennis_explorer",
        capability="match_history", checked_at=checked_at,
        source_watermark=profile.source_watermark, status="partial", complete=False,
        evidence={"source_player_id": str(slug), "usable_for_metrics": True,
                  "history_scope": state.get("history_scope", "accessible_profile_snapshot"),
                  "complete_career_history": False,
                  "accessible_years": state.get("accessible_years") or [],
                  "normalized_matches": int(state.get("normalized_matches") or 0),
                  "parser_version": state.get("parser_version")},
    )
    session.flush()
    return True


def _record_tennis_explorer_recent_results_coverage(
    session, participant, profile, person, now: datetime,
) -> bool:
    """Publish fresh, canonicalized TE result-feed facts as usable partial history.

    The seven-day results feed is not a career profile. It can still contribute
    the confirmed matches it actually contains once those matches have been
    materialized into this participant's append-only result facts.
    """
    from db.models import TennisPlayerResultFact
    from db.models.external_tennis import ExtTennisCanonicalMatch, ExtTennisMatch
    from .player_data_coverage import upsert_coverage

    source_player_ids = _canonical_player_source_ids(session, person).get("tennis_explorer", set())
    if (not source_player_ids or profile.canonical_player_id != person.id
            or not _profile_has_canonical_source_id(session, profile, person)
            or not profile.metrics_version or not profile.history_match_count
            or not profile.metrics_available_at
            or profile.metrics_available_at < now - timedelta(days=7)):
        return False

    matches = session.execute(select(ExtTennisCanonicalMatch).where(
        or_(ExtTennisCanonicalMatch.winner_player_id == person.id,
            ExtTennisCanonicalMatch.loser_player_id == person.id),
        ExtTennisCanonicalMatch.duplicate_of_id.is_(None),
        ExtTennisCanonicalMatch.available_at <= now,
        ExtTennisCanonicalMatch.available_at >= now - timedelta(days=7),
    )).scalars().all()
    source_ids_by_key = {}
    for match in matches:
        source_ids = {
            ref.split(":", 1)[1]
            for ref in ((match.stats or {}).get("sources") or [])
            if isinstance(ref, str) and ref.startswith("tennis_explorer:")
        }
        if source_ids:
            source_ids_by_key[str(match.canonical_key)] = source_ids
    candidate_source_match_ids = {
        source_id for source_ids in source_ids_by_key.values() for source_id in source_ids
    }
    if not candidate_source_match_ids:
        return False
    source_matches = session.execute(select(ExtTennisMatch).where(
        ExtTennisMatch.source == "tennis_explorer",
        ExtTennisMatch.source_match_id.in_(candidate_source_match_ids),
        or_(ExtTennisMatch.winner_player_id.in_(source_player_ids),
            ExtTennisMatch.loser_player_id.in_(source_player_ids)),
        ExtTennisMatch.available_at <= now,
        ExtTennisMatch.available_at >= now - timedelta(days=7),
    )).scalars().all()
    linked_source_match_ids = {str(row.source_match_id) for row in source_matches}
    te_matches = [match for match in matches if source_ids_by_key.get(str(match.canonical_key), set())
                  & linked_source_match_ids]
    if not te_matches:
        return False

    match_keys = {str(match.canonical_key) for match in te_matches}
    facts = session.execute(select(TennisPlayerResultFact).where(
        TennisPlayerResultFact.participant_id == participant.id,
        TennisPlayerResultFact.source == "canonical_archive",
        TennisPlayerResultFact.source_match_id.in_(match_keys),
        TennisPlayerResultFact.valid.is_(True),
        TennisPlayerResultFact.available_at <= now,
    )).scalars().all()
    materialized = {str(fact.source_match_id): fact for fact in facts}
    covered = [match for match in te_matches if str(match.canonical_key) in materialized]
    if not covered:
        return False
    newest_fact = max(materialized[str(match.canonical_key)].available_at for match in covered)
    if profile.metrics_available_at < newest_fact:
        return False

    upsert_coverage(
        session, participant_id=participant.id, source="tennis_explorer",
        capability="match_history", checked_at=max(match.available_at for match in covered),
        source_watermark=max(match.played_at for match in covered),
        status="partial", complete=False,
        evidence={"source_player_ids": sorted(source_player_ids),
                  "usable_for_metrics": True,
                  "history_scope": "recent_results_feed",
                  "complete_career_history": False,
                  "projected_matches": len(covered),
                  "parser_version": "tennisexplorer_results_v1"},
    )
    session.flush()
    return True


def _record_tennis_abstract_partial_coverage(session, participant, profile, person, now: datetime) -> bool:
    """Publish only freshly projected TA results as incomplete usable history."""
    source_player_id = (person.source_ids or {}).get("tennis_abstract")
    if not source_player_id or not profile.metrics_version or not profile.history_match_count:
        return False
    row = session.execute(text("""
        SELECT state_json FROM external_tennis_source_state
        WHERE source='tennis_abstract' AND state_key=:key
    """), {"key": f"profile:{source_player_id}"}).first()
    if not row:
        return False
    state = row[0] or {}
    if isinstance(state, str):
        try:
            state = json.loads(state)
        except ValueError:
            return False
    if not isinstance(state, dict):
        return False
    projection = state.get("canonical_projection") or {}
    projected = int(projection.get("projected", 0) or 0) + int(projection.get("merged", 0) or 0)
    if (state.get("status") != 200
            or state.get("complete") is not False
            or int(state.get("normalized_matches") or 0) <= 0
            or projected <= 0):
        return False
    checked_raw = state.get("checked_at")
    try:
        checked_at = datetime.fromisoformat(str(checked_raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    if not timedelta(0) <= now - checked_at <= timedelta(days=7):
        return False
    if profile.metrics_available_at is None or profile.metrics_available_at < checked_at:
        return False
    from .player_data_coverage import upsert_coverage
    upsert_coverage(
        session, participant_id=participant.id, source="tennis_abstract",
        capability="match_history", checked_at=checked_at,
        source_watermark=profile.source_watermark, status="partial", complete=False,
        evidence={"source_player_id": str(source_player_id), "usable_for_metrics": True,
                  "history_scope": "recent_fragment_not_full_career",
                  "complete_career_history": False,
                  "normalized_matches": int(state.get("normalized_matches") or 0),
                  "projected_matches": projected,
                  "parser_version": state.get("parser_version"),
                  "serve_statistics_usable": False},
    )
    session.flush()
    return True


def _record_api_tennis_partial_coverage(session, participant, profile, person, now: datetime) -> bool:
    """Publish recent API-Tennis archive facts as incomplete, usable history.

    API-Tennis is a bounded archive feed, not a certified career-profile source.
    Only canonical matches that retain an API source alias and whose raw match
    observation was fetched within seven days can satisfy this partial coverage.
    """
    if not profile.metrics_version or not profile.history_match_count or not profile.metrics_available_at:
        return False
    from db.models.external_tennis import ExtTennisCanonicalMatch, ExtTennisMatch

    matches = session.execute(select(
        ExtTennisCanonicalMatch.canonical_key,
        ExtTennisCanonicalMatch.played_at,
        ExtTennisCanonicalMatch.stats,
        ExtTennisCanonicalMatch.winner_player_id,
        ExtTennisCanonicalMatch.loser_player_id,
        ExtTennisCanonicalMatch.winner_name,
        ExtTennisCanonicalMatch.loser_name,
    ).where(
        or_(ExtTennisCanonicalMatch.winner_player_id == person.id,
            ExtTennisCanonicalMatch.loser_player_id == person.id),
        ExtTennisCanonicalMatch.duplicate_of_id.is_(None),
        ExtTennisCanonicalMatch.available_at <= now,
    )).all()
    source_ids_by_key = {}
    for canonical_key, played_at, match_stats, winner_id, loser_id, winner_name, loser_name in matches:
        source_ids = [
            alias.split(":", 1)[1]
            for alias in (match_stats or {}).get("sources", [])
            if isinstance(alias, str) and alias.startswith("api_tennis:")
        ]
        if source_ids:
            source_ids_by_key[str(canonical_key)] = (
                played_at, source_ids, winner_id, loser_id, winner_name, loser_name
            )
    source_match_ids = {
        source_id for _, ids, *_ in source_ids_by_key.values() for source_id in ids
    }
    if not source_match_ids:
        return False
    source_rows = session.execute(select(ExtTennisMatch).where(
        ExtTennisMatch.source == "api_tennis",
        ExtTennisMatch.source_match_id.in_(source_match_ids),
        ExtTennisMatch.available_at <= now,
        ExtTennisMatch.available_at >= now - timedelta(days=7),
    )).scalars().all()
    rows_by_id = {str(row.source_match_id): row for row in source_rows}
    recent = []
    source_player_ids = set()
    for canonical_key, (played_at, ids, winner_id, loser_id, winner_name, loser_name) in source_ids_by_key.items():
        for source_id in ids:
            row = rows_by_id.get(source_id)
            if row is not None:
                source_winner_is_canonical_winner = _canonical_tml_side_is_winner(
                    winner_name, loser_name, row.winner_name, row.loser_name,
                    participant_is_winner=True,
                )
                if source_winner_is_canonical_winner is None:
                    continue
                participant_is_canonical_winner = winner_id == person.id
                participant_is_source_winner = (
                    participant_is_canonical_winner == source_winner_is_canonical_winner
                )
                provider_player_id = (
                    row.winner_player_id if participant_is_source_winner else row.loser_player_id
                )
                if provider_player_id in (None, ""):
                    continue
                provider_player_id = str(provider_player_id)
                source_player_ids.add(provider_player_id)
                recent.append((canonical_key, played_at, row, provider_player_id))
    if not recent:
        return False
    checked_at = max(row.available_at for _, _, row, _ in recent)
    if profile.metrics_available_at < checked_at:
        return False
    from .player_data_coverage import upsert_coverage
    upsert_coverage(
        session, participant_id=participant.id, source="api_tennis",
        capability="match_history", checked_at=checked_at,
        source_watermark=max(played for _, played, _, _ in recent), status="partial", complete=False,
        evidence={"source_player_id": (
                      next(iter(source_player_ids)) if len(source_player_ids) == 1
                      else (str((person.source_ids or {}).get("api_tennis"))
                            if (person.source_ids or {}).get("api_tennis") else None)
                  ),
                  "source_player_ids": sorted(source_player_ids),
                  "usable_for_metrics": True, "history_scope": "recently_imported_archive_results",
                  "complete_career_history": False, "retrospective": True,
                  "projected_matches": len({key for key, _, _, _ in recent}),
                  "serve_statistics_available": sum(
                      row.serve_won_winner is not None or row.serve_won_loser is not None
                      for _, _, row, _ in recent
                  ),
                  "parser_version": "api_tennis_v1"},
    )
    session.flush()
    return True


def _record_profile_source_coverage(session, participant, profile, person, now: datetime) -> tuple[str, str | None]:
    """Publish every independently available history capability for this profile."""
    complete_history = _record_tml_history_coverage(session, participant, profile, person, now)
    partial_history = False
    for recorder in (
        _record_tennis_explorer_partial_coverage,
        _record_tennis_explorer_recent_results_coverage,
        _record_api_tennis_partial_coverage,
        _record_tennis_abstract_partial_coverage,
    ):
        partial_history = recorder(session, participant, profile, person, now) or partial_history
    if complete_history:
        return "available", None
    if partial_history:
        return "partial", "PARTIAL_HISTORY_COVERAGE"
    return "stale", "SOURCE_FRESHNESS_UNVERIFIED"


def _canonical_player_source_ids(session, person) -> dict[str, set[str]]:
    from .canonical_player_aliases import confirmed_source_ids_for_player

    source_ids = {source: {str(value)} for source, value in (person.source_ids or {}).items() if value}
    for source, aliases in confirmed_source_ids_for_player(session, person.id).items():
        source_ids.setdefault(source, set()).update(aliases)
    return source_ids


def _profile_has_canonical_source_id(session, profile, person) -> bool:
    """Trust an existing canonical link only when an archived source ID agrees."""
    if profile.canonical_player_id != person.id:
        return False
    linked_source_ids: dict[str, set[str]] = {}
    for key, value in (profile.source_identities or {}).items():
        if key.startswith("archive:") and value:
            linked_source_ids.setdefault(key.removeprefix("archive:"), set()).add(str(value))
        elif key.startswith("archive_alias:"):
            _prefix, source, source_id = key.split(":", 2)
            if source_id:
                linked_source_ids.setdefault(source, set()).add(source_id)
    source_ids = _canonical_player_source_ids(session, person)
    return any(source_ids.get(source, set()) & values for source, values in linked_source_ids.items())


def _is_singles_archive_player(person) -> bool:
    """Pair display names must not become candidates for singles profiles."""
    return "/" not in (person.display_name or "")


def _canonical_player_surname_keys(name: str) -> set[str]:
    """Build indexed surname candidates across common Russian/Latin spellings.

    Some Eastern European surnames transcribed through Russian differ only by
    ``c``/``k`` before a front vowel (for example, Cretu/Kretu). These variants
    widen candidate lookup only. This also handles Spanish c/s spelling and
    Dutch ``ie``/long ``oo`` vowels (Serundolo/Cerundolo, Grikspor/Griekspoor).
    ``_player_name_matches`` and the existing single-candidate requirement
    still decide whether a link is safe.
    """
    tokens = [token for token in _search_tokens(name) if len(token) > 1]
    keys = set(tokens)
    for token in tokens:
        # Russian transliteration often drops the silent final e in names such
        # as Brengle -> Brengl. The full name matcher still verifies candidates.
        if token.endswith("le") and len(token) > 3:
            keys.add(token[:-1])
        elif token.endswith("l") and len(token) > 3:
            keys.add(token + "e")
        if token.startswith("k") and len(token) > 1:
            keys.add("c" + token[1:])
        elif token.startswith("c") and len(token) > 1:
            keys.add("k" + token[1:])
        if token.startswith("s") and len(token) > 1:
            keys.add("c" + token[1:])
        elif token.startswith("c") and len(token) > 1:
            keys.add("s" + token[1:])
        for index, char in enumerate(token[:-1]):
            if token[index + 1] in {"e", "i"} and char == "k":
                keys.add(token[:index] + "c" + token[index + 1 :])
            elif token[index + 1] in {"e", "i"} and char == "c":
                keys.add(token[:index] + "k" + token[index + 1 :])
        # Russian phonetic transliteration may omit Dutch vowel spelling:
        # ``ie`` and ``oo`` are commonly rendered as a single vowel. Generate
        # bounded query-only variants, then rely on full-name matching below.
        vowel_insertions = []
        for index, char in enumerate(token[:-1]):
            if token[index + 1].isalpha() and token[index + 1] not in "aeiouy":
                if char == "i":
                    vowel_insertions.append((index + 1, "e"))
                elif char == "o":
                    vowel_insertions.append((index + 1, "o"))
        # Cap the subset expansion so unusual long names cannot create an
        # unbounded SQL IN list. Applying insertions right-to-left preserves
        # the original token offsets and permits combinations such as ie+oo.
        vowel_insertions = vowel_insertions[:6]
        for mask in range(1, 1 << len(vowel_insertions)):
            candidate = token
            for bit, (offset, vowel) in reversed(list(enumerate(vowel_insertions))):
                if mask & (1 << bit):
                    candidate = candidate[:offset] + vowel + candidate[offset:]
            keys.add(candidate)
    for width in (2, 3):
        for start in range(max(0, len(tokens) - width + 1)):
            keys.add(" ".join(tokens[start : start + width]))
    return keys


def _french_archive_identity_match(name: str, person) -> bool:
    """Match a Russian phonetic surname to a French canonical identity narrowly.

    This handles cases such as Fonbet's ``Жакмо Э`` for ``Elsa Jacquemot``.
    The fallback is limited to French players with both TML and TennisExplorer
    IDs, exactly one surname plus one initial, and an agreeing canonical
    initial. It only generates candidates; the caller still requires a unique
    person.
    """
    if str(getattr(person, "ioc", "") or "").upper() != "FRA":
        return False
    source_ids = getattr(person, "source_ids", {}) or {}
    if not source_ids.get("tml") or not source_ids.get("tennis_explorer"):
        return False

    from .tennis_flashscore import _search_tokens

    tokens = _search_tokens(name)
    if len(tokens) != 2:
        return False
    initials = [token for token in tokens if len(token) == 1]
    surnames = [token for token in tokens if len(token) > 1]
    if len(initials) != 1 or len(surnames) != 1:
        return False

    def french_surname_key(token: str) -> str:
        token = token.casefold().replace("zh", "j")
        token = re.sub(r"cque|que", "k", token)
        # Final t is silent in this French orthographic ending (Jacquemot).
        if token.endswith("ot"):
            token = token[:-1]
        return token

    return (
        french_surname_key(surnames[0]) == french_surname_key(str(person.surname_key or ""))
        and initials[0] == str(person.initial or "").casefold()
    )


def _tml_serve_counts(stats: dict | None, *, is_winner: bool) -> tuple[int, int] | None:
    """Return explicit TML serve-point counts for a match side, if internally valid."""
    if not isinstance(stats, dict):
        return None
    prefix = "w_" if is_winner else "l_"

    def count(field: str) -> int | None:
        value = stats.get(prefix + field)
        if value in (None, ""):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not number.is_integer() or number < 0:
            return None
        return int(number)

    points = count("svpt")
    first_won, second_won = count("1stWon"), count("2ndWon")
    if points is None or first_won is None or second_won is None:
        return None
    won = first_won + second_won
    if points <= 0 or won > points:
        return None
    return points, won


def _canonical_tml_side_is_winner(
    canonical_winner: str, canonical_loser: str,
    tml_winner: str, tml_loser: str, *, participant_is_winner: bool,
) -> bool | None:
    """Map a canonical participant to TML's winner/loser side after name checks."""
    def matches(left: str, right: str) -> bool:
        # Source feeds often abbreviate first names (`L. Musetti`) while the
        # canonical archive stores full names. The matcher is intentionally
        # conservative but directional, so accept either representation as
        # the query side and still require both surnames and initials to agree.
        return _player_name_matches(left, right) or _player_name_matches(right, left)

    direct = (
        matches(canonical_winner, tml_winner)
        and matches(canonical_loser, tml_loser)
    )
    swapped = (
        matches(canonical_winner, tml_loser)
        and matches(canonical_loser, tml_winner)
    )
    if direct:
        return participant_is_winner
    if swapped:
        return not participant_is_winner
    return None


def _api_tennis_serve_counts(
    stats: dict | None, *, provider_player_id: str | None,
) -> tuple[int, int] | None:
    """Read explicit service-points won/total from API-Tennis match statistics."""
    if not isinstance(stats, dict) or not provider_player_id:
        return None
    values = []
    for row in stats.get("statistics") or []:
        if (not isinstance(row, dict)
                or str(row.get("stat_period") or "").casefold() != "match"
                or str(row.get("stat_name") or "").strip().casefold() != "service points won"
                or str(row.get("player_key") or "") != str(provider_player_id)):
            continue
        try:
            won_value, total_value = float(row.get("stat_won")), float(row.get("stat_total"))
        except (TypeError, ValueError):
            continue
        if (not won_value.is_integer() or not total_value.is_integer()
                or total_value <= 0 or won_value < 0 or won_value > total_value):
            continue
        values.append((int(total_value), int(won_value)))
    return values[0] if values and all(value == values[0] for value in values) else None


def _consistent_serve_counts(candidates: list[tuple[tuple[int, int], Any]]) -> tuple[int, int] | None:
    """Reject cross-source disagreement instead of making source-order decisions."""
    values = {counts for counts, _source_row in candidates}
    return candidates[0][0] if len(values) == 1 and candidates else None


def _canonical_fact_version_needed(previous, serve_counts: tuple[int, int] | None) -> bool:
    """Keep a result without stats; append a version only for newly observed stats."""
    if previous is None:
        return True
    if serve_counts is None:
        return False
    return (previous.serve_points, previous.serve_points_won) != serve_counts


def _canonical_fact_available_at(
    match_available_at: datetime,
    serve_stats_available_at: datetime | None,
    *,
    has_previous_version: bool,
    imported_at: datetime,
) -> datetime:
    """A delayed enrichment becomes visible when we learned it, never earlier."""
    return max(
        match_available_at,
        serve_stats_available_at or match_available_at,
        imported_at if has_previous_version else match_available_at,
    )


def _record_materialized_serve_statistics_coverage(session, participant, facts, now: datetime) -> None:
    """Publish source-specific fresh coverage for explicit serve-point facts.

    History coverage does not imply serve-stat coverage. Only materialized
    facts with explicit total/won counts and a recent source observation are
    admitted, and this remains partial because source rows cover only a subset
    of the player's career.
    """
    from .player_data_coverage import upsert_coverage

    by_source: dict[str, list[tuple[Any, datetime]]] = {}
    for fact in facts:
        if fact.serve_points is None or fact.serve_points_won is None:
            continue
        payload = fact.source_payload or {}
        source = payload.get("serve_stats_source")
        observed_raw = payload.get("serve_stats_available_at")
        if source not in {"tml", "api_tennis"} or not observed_raw:
            continue
        try:
            observed_at = datetime.fromisoformat(str(observed_raw).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
        if not timedelta(0) <= now - observed_at <= timedelta(days=7):
            continue
        by_source.setdefault(source, []).append((fact, observed_at))

    for source, rows in by_source.items():
        unique_matches = {str(fact.source_match_id) for fact, _ in rows}
        if not unique_matches:
            continue
        upsert_coverage(
            session, participant_id=participant.id, source=source,
            capability="serve_statistics",
            checked_at=max(observed_at for _, observed_at in rows),
            source_watermark=max(
                (fact.played_at for fact, _ in rows if fact.played_at is not None),
                default=None,
            ),
            status="partial", complete=False,
            evidence={
                "usable_for_metrics": True,
                "coverage_scope": "recently_observed_explicit_serve_point_facts",
                "metrics": ["serve_points", "serve_points_won"],
                "matches_with_explicit_stats": len(unique_matches),
                "career_history_complete": False,
                "retrospective": True,
            },
        )


def seed_profile_from_archive(session, participant, profile) -> bool:
    """Materialize an existing canonical archive for a new participant once."""
    from db.models import TennisPlayerMetricSnapshot, TennisPlayerResultFact
    from db.models.external_tennis import ExtTennisCanonicalMatch, ExtTennisCanonicalPlayer
    from .canonical_player_aliases import confirmed_source_ids_for_player

    if not participant or not participant.canonical_name:
        return False
    surname_keys = _canonical_player_surname_keys(participant.canonical_name)
    person = None
    identity_match_method = None
    if profile.canonical_player_id:
        linked_person = session.get(ExtTennisCanonicalPlayer, profile.canonical_player_id)
        if (linked_person and _player_name_matches(participant.canonical_name, linked_person.display_name)
                and _profile_has_canonical_source_id(session, profile, linked_person)):
            person = linked_person
    if person is None:
        candidates = session.execute(
            select(ExtTennisCanonicalPlayer).where(ExtTennisCanonicalPlayer.surname_key.in_(surname_keys))
        ).scalars().all() if surname_keys else []
        candidates = [
            row for row in candidates
            if _is_singles_archive_player(row)
            and _player_name_matches(participant.canonical_name, row.display_name)
        ]
        # Prefer a full, directly mapped TennisMyLife identity over an
        # initial-only API/archive row for the same surname. Keep this
        # fail-closed: more than one matching TML identity remains ambiguous.
        tml_candidates = [row for row in candidates if (row.source_ids or {}).get("tml")]
        if len(tml_candidates) == 1:
            candidates = tml_candidates
        if not candidates and re.search(r"[А-Яа-яЁё]", participant.canonical_name):
            # French surname transliteration can be phonetic rather than
            # letter-for-letter in Fonbet (e.g. Жакмо / Jacquemot). Search
            # only the small, independently source-backed French population;
            # a unique surname+initial candidate is still mandatory.
            french_candidates = session.execute(select(ExtTennisCanonicalPlayer).where(
                ExtTennisCanonicalPlayer.ioc == "FRA",
                ExtTennisCanonicalPlayer.source_ids.has_key("tml"),
                ExtTennisCanonicalPlayer.source_ids.has_key("tennis_explorer"),
            )).scalars().all()
            localized = [row for row in french_candidates
                         if _is_singles_archive_player(row)
                         and _french_archive_identity_match(participant.canonical_name, row)]
            if len(localized) == 1:
                candidates = localized
                identity_match_method = "french_orthographic_phonetic_unique"
        if len(candidates) != 1:
            return False
        person = candidates[0]
    profile.canonical_player_id = person.id
    existing_metrics_version = int(profile.metrics_version or 0)
    identities = dict(profile.source_identities or {})
    identities.update({f"archive:{key}": value for key, value in (person.source_ids or {}).items()})
    for source, aliases in confirmed_source_ids_for_player(session, person.id).items():
        for source_id in aliases:
            identities[f"archive_alias:{source}:{source_id}"] = source_id
    if identity_match_method:
        identities["archive_identity_match"] = {
            "method": identity_match_method,
            "canonical_player_id": str(person.id),
            "provider_ids": dict(person.source_ids or {}),
        }
    profile.source_identities = identities
    now = datetime.now(timezone.utc)
    matches = session.execute(
        select(ExtTennisCanonicalMatch).where(
            (ExtTennisCanonicalMatch.winner_player_id == person.id)
            | (ExtTennisCanonicalMatch.loser_player_id == person.id),
            ExtTennisCanonicalMatch.duplicate_of_id.is_(None),
        ).order_by(ExtTennisCanonicalMatch.played_at, ExtTennisCanonicalMatch.canonical_key)
    ).scalars().all()
    inserted = 0
    existing_facts = session.execute(
        select(TennisPlayerResultFact).where(
            TennisPlayerResultFact.participant_id == participant.id,
            TennisPlayerResultFact.source == "canonical_archive",
        ).order_by(TennisPlayerResultFact.source_match_id, TennisPlayerResultFact.available_at)
    ).scalars().all()
    latest_existing_facts = {}
    for existing in existing_facts:
        latest_existing_facts[existing.source_match_id] = existing
    # Canonical rows retain source IDs. Load TML rows in one query so explicit
    # serve counts can enrich the facts without guessing from percentage stats.
    source_ids_by_canonical_key = {}
    all_source_ids = set()
    supported_stats_sources = {"tml", "api_tennis"}
    for match in matches:
        source_refs = (match.stats or {}).get("sources") or []
        refs = [tuple(str(ref).split(":", 1)) for ref in source_refs
                if isinstance(ref, str) and ":" in ref
                and str(ref).split(":", 1)[0] in supported_stats_sources]
        source_ids_by_canonical_key[match.canonical_key] = refs
        all_source_ids.update(refs)
    source_rows_by_id = {}
    if all_source_ids:
        from db.models.external_tennis import ExtTennisMatch
        tml_rows = session.execute(
            select(ExtTennisMatch).where(
                ExtTennisMatch.source.in_(supported_stats_sources),
                ExtTennisMatch.source_match_id.in_({source_id for _, source_id in all_source_ids}),
            )
        ).scalars().all()
        source_rows_by_id = {(row.source, row.source_match_id): row for row in tml_rows}
    for match in matches:
        if not match.score or re.search(r"retired|walkover|void|abandon|cancel", match.score, re.I):
            continue
        sets = re.findall(r"\b\d{1,2}-\d{1,2}\b", match.score)
        if len(sets) < 2:
            continue
        source_match_id = str(match.canonical_key)
        won = match.winner_player_id == person.id
        serve_candidates = []
        for source, source_match_id_ref in source_ids_by_canonical_key.get(match.canonical_key, []):
            source_row = source_rows_by_id.get((source, source_match_id_ref))
            if source_row is None:
                continue
            tml_side_is_winner = _canonical_tml_side_is_winner(
                match.winner_name, match.loser_name,
                source_row.winner_name, source_row.loser_name,
                participant_is_winner=won,
            )
            if tml_side_is_winner is None:
                continue
            if source == "tml":
                candidate = _tml_serve_counts(source_row.stats, is_winner=tml_side_is_winner)
            else:
                provider_player_id = (
                    source_row.winner_player_id if tml_side_is_winner else source_row.loser_player_id
                )
                candidate = _api_tennis_serve_counts(
                    source_row.stats, provider_player_id=provider_player_id
                )
            if candidate is not None:
                serve_candidates.append((candidate, source_row))
        # Conflicting providers are not resolved by source ordering. Keep the
        # value unknown until the disagreement is reviewed or corrected.
        serve_counts = _consistent_serve_counts(serve_candidates)
        if serve_counts is not None:
            serve_source_row = serve_candidates[0][1]
        else:
            serve_source_row = None
        previous = latest_existing_facts.get(source_match_id)
        if not _canonical_fact_version_needed(previous, serve_counts):
            continue
        fact_hash = _result_fact_hash(
            source_match_id, won, len(sets), match.surface or "unknown", match.tour_level or "unknown"
        )
        if serve_counts is not None:
            fact_hash = hashlib.sha256(
                f"{fact_hash}|{serve_source_row.source}:{serve_counts[0]}:{serve_counts[1]}".encode()
            ).hexdigest()
        fact_available_at = _canonical_fact_available_at(
            match.available_at,
            serve_source_row.available_at if serve_source_row is not None else None,
            has_previous_version=previous is not None,
            imported_at=now,
        )
        session.add(TennisPlayerResultFact(
            participant_id=participant.id,
            source="canonical_archive",
            source_match_id=source_match_id,
            result_hash=fact_hash,
            played_at=match.played_at,
            available_at=fact_available_at,
            surface=match.surface or "unknown",
            tour_level=match.tour_level or "unknown",
            won=won,
            sets_played=len(sets),
            serve_points=serve_counts[0] if serve_counts is not None else None,
            serve_points_won=serve_counts[1] if serve_counts is not None else None,
            source_payload={
                "canonical_player_id": str(person.id),
                "canonical_match_id": str(match.id),
                "archive_available_at": match.available_at.isoformat(),
                **({
                    "serve_stats_source": serve_source_row.source,
                    "serve_stats_source_match_id": serve_source_row.source_match_id,
                    "serve_stats_available_at": serve_source_row.available_at.isoformat(),
                } if serve_source_row is not None else {}),
            },
        ))
        latest_existing_facts[source_match_id] = None
        inserted += 1
    if not inserted and existing_metrics_version <= 0:
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
    if not facts:
        return False
    _record_materialized_serve_statistics_coverage(session, participant, facts, now)
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
    digest_changed = digest != profile.history_digest
    if existing_metrics_version <= 0:
        profile.metrics_version = 1
    elif digest_changed:
        profile.metrics_version = existing_metrics_version + 1
    profile.metrics_json = metrics
    profile.history_digest = digest
    profile.history_match_count = int(metrics["overall"]["matches"])
    profile.source_watermark = max((fact.played_at for fact in facts), default=None)
    profile.metrics_available_at = now
    if digest_changed or existing_metrics_version <= 0:
        session.add(TennisPlayerMetricSnapshot(
            participant_id=participant.id,
            source_player_id=confirmed_source_player_id(
                session, participant.id, source="flashscore", as_of=now
            ),
            metrics_version=profile.metrics_version,
            available_at=now,
            source_watermark=profile.source_watermark,
            history_digest=digest,
            metrics_json=metrics,
        ))
    profile.sync_status, profile.sync_error = _record_profile_source_coverage(
        session, participant, profile, person, now
    )
    return True


def has_unmaterialized_external_results(session, participant, profile, *,
                                        sources=("tennis_explorer", "api_tennis", "tennis_abstract")) -> bool:
    """Check whether newly fetched external results are missing from profile facts.

    Complete TML coverage does not imply that later external observations have
    been materialized. Only source aliases on the confirmed canonical player
    and observations fetched within the seven-day freshness window are used.
    """
    from db.models import TennisPlayerResultFact
    from db.models.external_tennis import (
        ExtTennisCanonicalMatch, ExtTennisCanonicalPlayer, ExtTennisMatchObservation,
    )

    canonical_player_id = getattr(profile, "canonical_player_id", None)
    if not participant or not canonical_player_id:
        return False
    person = session.get(ExtTennisCanonicalPlayer, canonical_player_id)
    if person is None or not _is_singles_archive_player(person):
        return False
    as_of = datetime.now(timezone.utc)
    accepted_sources = set(sources)
    if not accepted_sources:
        return False
    matches = session.execute(select(
        ExtTennisCanonicalMatch.canonical_key, ExtTennisCanonicalMatch.stats
    ).where(
        or_(ExtTennisCanonicalMatch.winner_player_id == canonical_player_id,
            ExtTennisCanonicalMatch.loser_player_id == canonical_player_id),
        ExtTennisCanonicalMatch.duplicate_of_id.is_(None),
        ExtTennisCanonicalMatch.played_at >= as_of - timedelta(days=8),
        ExtTennisCanonicalMatch.available_at <= as_of,
    )).all()
    aliases_by_key: dict[str, set[tuple[str, str]]] = {}
    all_source_ids = set()
    for canonical_key, match_stats in matches:
        aliases = set()
        for value in (match_stats or {}).get("sources", []):
            source, separator, source_id = str(value).partition(":")
            if separator and source in accepted_sources and source_id:
                aliases.add((source, source_id))
                all_source_ids.add((source, source_id))
        if aliases:
            aliases_by_key[str(canonical_key)] = aliases
    if not all_source_ids:
        return False
    recent_observations = session.execute(select(
        ExtTennisMatchObservation.source, ExtTennisMatchObservation.source_match_id,
    ).where(
        ExtTennisMatchObservation.source.in_(accepted_sources),
        ExtTennisMatchObservation.source_match_id.in_({source_id for _, source_id in all_source_ids}),
        ExtTennisMatchObservation.available_at <= as_of,
        ExtTennisMatchObservation.available_at >= as_of - timedelta(days=7),
    )).all()
    recent_aliases = {(row.source, str(row.source_match_id)) for row in recent_observations}
    external_keys = [key for key, aliases in aliases_by_key.items() if aliases & recent_aliases]
    if not external_keys:
        return False
    materialized_keys = session.execute(select(TennisPlayerResultFact.source_match_id).where(
        TennisPlayerResultFact.participant_id == participant.id,
        TennisPlayerResultFact.source_match_id.in_(external_keys),
        TennisPlayerResultFact.valid.is_(True),
    )).scalars().all()
    return has_unmaterialized_canonical_results(external_keys, materialized_keys)


def has_unmaterialized_tennis_explorer_results(session, participant, profile) -> bool:
    """Backward-compatible TennisExplorer-only coverage check."""
    return has_unmaterialized_external_results(
        session, participant, profile, sources=("tennis_explorer",)
    )


def has_unmaterialized_canonical_results(canonical_keys, materialized_keys) -> bool:
    """Return whether a projected canonical result still lacks a profile fact."""
    return bool(set(canonical_keys) - set(materialized_keys))


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
    job = session.query(TennisPlayerRefreshJob).filter_by(
        participant_id=participant_id
    ).with_for_update().first()
    if job is None:
        job = TennisPlayerRefreshJob(
            participant_id=participant_id,
            status="queued",
            requested_reason=reason,
            requested_at=now,
            next_run_at=now,
            progress_json={"stage": "queued"},
            updated_at=now,
        )
        session.add(job)
        return job

    if job.status == "running" and job.lease_until is not None and job.lease_until > now:
        job.updated_at = now
        return job

    # A durable negative result must not be reopened by every new match using
    # the same player. Keep the retry time owned by the failed attempt; explicit
    # identity decisions and a later external-source update may retry it.
    if _preserve_player_refresh_backoff(job, reason, now):
        return job

    job.status = "queued"
    job.requested_reason = reason
    job.requested_at = now
    job.next_run_at = now
    job.last_error = None
    job.progress_json = advance_player_refresh_progress(job.progress_json, "queued")
    job.lease_until = None
    job.attempt_token = None
    job.completed_at = None
    job.updated_at = now
    return job


_PLAYER_REFRESH_TERMINAL_ERRORS = {
    "CAPTCHA_CHALLENGE_ABORTED",
    "PLAYER_COUNTRY_CODE_UNRESOLVED",
    "SEARCH_CANDIDATES_REJECTED_BY_NAME",
}
_PLAYER_REFRESH_EXPLICIT_RETRY_REASONS = {
    "manual_identity_resolution",
    "automatic_exact_identity_match",
}


def _preserve_player_refresh_backoff(job, reason: str, now: datetime) -> bool:
    """Return true when routine demand must not defeat a durable retry delay."""
    if reason in _PLAYER_REFRESH_EXPLICIT_RETRY_REASONS:
        return False
    if job.status == "blocked" and job.last_error in _PLAYER_REFRESH_TERMINAL_ERRORS:
        return True
    return bool(
        job.status == "retry"
        and job.last_error == "SEARCH_COMPLETED_NO_MATCH"
        and job.next_run_at is not None
        and job.next_run_at > now
    )


def _post_match_profile_refresh_due(profile, refresh_job, finished_at, now) -> bool:
    """Whether a player cache is missing or at least seven days old.

    ``finished_at`` is retained for call compatibility; a new Fonbet result does
    not make every participant profile immediately stale.
    """
    if profile is None or not (profile.source_identities or {}).get("flashscore"):
        needs_refresh = True
    elif profile.last_success_at is None:
        needs_refresh = True
    else:
        needs_refresh = profile.last_success_at <= now - timedelta(days=7)
    if not needs_refresh:
        return False
    if refresh_job is None:
        return True
    if refresh_job.status == "retry":
        return refresh_job.next_run_at is not None and refresh_job.next_run_at <= now
    if refresh_job.status in {"completed", "failed", "idle"}:
        return refresh_job.updated_at is None or refresh_job.updated_at <= now - timedelta(days=7)
    return False


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
                ExtTennisCanonicalMatch.duplicate_of_id.is_(None),
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
            source_player_id=confirmed_source_player_id(
                session, participant_id, source="flashscore", as_of=now
            ),
            metrics_version=profile_row.metrics_version,
            available_at=now,
            source_watermark=profile_row.source_watermark,
            history_digest=digest,
            metrics_json=metrics,
        )
        session.add(snapshot)
    return sum(len(facts) for facts in changes.values())


