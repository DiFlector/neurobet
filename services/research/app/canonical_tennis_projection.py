"""Incrementally project recent TennisExplorer singles into canonical history.

This deliberately projects only the bounded set just ingested by the TE worker.
The bulk canonical rebuild is a separate maintenance operation and must not run
for each daily result refresh.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import or_, select, tuple_

from tennis_adapter.external_names import surname_and_initial


_MATCH_NS = uuid.UUID("a31c0e74-5b29-4f6d-9e80-1c7d2a4b6e90")
_SOURCE = "tennis_explorer"


def _player_key(name: str, gender: str) -> tuple[str, str, str]:
    surname, initial = surname_and_initial(name or "")
    return surname, initial, gender if gender in {"m", "w"} else "unknown"


def _compound_surname_candidates(surname: str, initial: str, gender: str,
                                 candidates: list[Any], *, source: str = _SOURCE) -> list[Any]:
    """Resolve provider compound surnames only against one full-name identity.

    Some result feeds include both parts of a compound surname while canonical
    profiles key only the final part. Require every surname token in the full
    canonical display name, the same initial and gender, and no conflicting ID
    already attached for this provider.
    """
    tokens = set(re.findall(r"[^\W\d_]+", surname.casefold(), flags=re.UNICODE))
    if len(tokens) < 2:
        return []

    def matches(person) -> bool:
        name_tokens = set(re.findall(
            r"[^\W\d_]+", str(person.display_name or "").casefold(), flags=re.UNICODE
        ))
        prior_source_id = str((person.source_ids or {}).get(source) or "")
        return (
            person.initial.casefold() == initial.casefold()
            and person.surname_key.casefold() in tokens
            and tokens.issubset(name_tokens)
            and not prior_source_id
        )

    name_matches = [person for person in candidates if matches(person)]
    exact_gender = [person for person in name_matches
                    if gender == "unknown" or person.gender == gender]
    if exact_gender or gender == "unknown":
        return exact_gender
    return [person for person in name_matches if person.gender == "unknown"]


def _confirmed_alias_name_matches(surname: str, person: Any) -> bool:
    """Validate a confirmed provider alias against a full compound surname.

    Some feeds include both parts of a compound surname while canonical player
    rows key only its final part. A confirmed source ID may bridge that format
    difference when every source surname token appears in the canonical name.
    """
    tokens = set(re.findall(r"[^\W\d_]+", surname.casefold(), flags=re.UNICODE))
    if not tokens:
        return False
    name_tokens = set(re.findall(
        r"[^\W\d_]+", str(person.display_name or "").casefold(), flags=re.UNICODE
    ))
    return tokens.issubset(name_tokens)


def _confirmed_alias_person_matches(alias: Any, person: Any, *, surname: str,
                                   initial: str, gender: str) -> bool:
    """Validate ordinary aliases strictly and reviewed aliases by full surname.

    A manually adjudicated provider ID can bridge a provider's different
    abbreviation convention (for example, a middle-name initial vs the
    canonical first-name initial). The adjudication still must agree on the
    complete surname tokens and gender. Automatically confirmed aliases keep
    the strict initial check.
    """
    if person is None or (gender != "unknown" and person.gender not in {gender, "unknown"}):
        return False
    if (getattr(alias, "evidence_json", None) or {}).get("adjudication_id"):
        return _confirmed_alias_name_matches(surname, person)
    return (person.initial.casefold() == initial.casefold()
            and (person.surname_key.casefold() == surname.casefold()
                 or _confirmed_alias_name_matches(surname, person)))


def _score_key(score: str) -> tuple[tuple[int, int], ...]:
    # Providers place tie-break points in parentheses next to the game score
    # (`7-6(5)` or `6(7)-7`). Strip those annotations before extracting set
    # scores so equivalent results can be safely joined across sources.
    normalized = re.sub(r"[\[(]\s*\d+\s*[\])]", "", score or "")
    return tuple((int(a), int(b)) for a, b in re.findall(r"(\d{1,2})\s*-\s*(\d{1,2})", normalized))


def _api_result_scores_agree(left: str, right: str) -> bool:
    """Compare detailed scores and provider set-summary scores safely."""
    a, b = _score_key(left), _score_key(right)
    if not a or not b:
        return False
    if a == b:
        return True
    detailed, summary = (a, b) if len(a) >= len(b) else (b, a)
    if len(summary) != 1 or len(detailed) < 2:
        return False
    winner_sets = sum(x > y for x, y in detailed)
    loser_sets = sum(y > x for x, y in detailed)
    return summary[0] in {(winner_sets, loser_sets), (loser_sets, winner_sets)}


def _correct_api_linked_match_identities(session, eligible) -> None:
    """Apply confirmed source-player aliases to canonical rows carrying that exact match ID.

    Earlier projections sometimes attached an API result to a Flashscore row
    while retaining its old API-only player IDs. The namespaced raw-match
    reference plus validated participant aliases lets us correct only those
    exact rows, with the decision captured in the row's correction history.
    """
    from db.models.external_tennis import ExtTennisCanonicalMatch

    by_source_ref = {
        f"api_tennis:{item['source_match_id']}": (item, winner, loser)
        for item, winner, loser in eligible
    }
    if not by_source_ref:
        return
    rows = session.execute(select(ExtTennisCanonicalMatch).where(or_(
        *(ExtTennisCanonicalMatch.stats["sources"].contains([ref])
          for ref in by_source_ref)
    ))).scalars().all()
    for row in rows:
        row_stats = dict(row.stats or {})
        refs = set(row_stats.get("sources") or [])
        match = next((by_source_ref[ref] for ref in refs if ref in by_source_ref), None)
        if match is None:
            continue
        item, winner, loser = match
        gender = str(item.get("gender") or "unknown").casefold()
        if abs((row.played_at.date() - item["played_at"].date()).days) > 7:
            continue
        compatible_genders = {gender, "unknown"}
        if gender == "m":
            compatible_genders.add("male")
        elif gender == "w":
            compatible_genders.add("female")
        if gender in {"m", "w"} and row.gender not in compatible_genders:
            continue
        if not _api_result_scores_agree(row.score, str(item.get("score") or "")):
            continue
        row_tournament = _tournament_tokens(row.tournament)
        item_tournament = _tournament_tokens(str(item.get("tournament") or ""))
        if row_tournament and item_tournament and not row_tournament.intersection(item_tournament):
            continue
        if (_player_key(row.winner_name, gender)[:2]
                != _player_key(str(item.get("winner_name") or ""), gender)[:2]
                or _player_key(row.loser_name, gender)[:2]
                != _player_key(str(item.get("loser_name") or ""), gender)[:2]):
            continue
        if row.winner_player_id == winner.id and row.loser_player_id == loser.id:
            continue
        previous_ids = [str(row.winner_player_id), str(row.loser_player_id)]
        correction = {
            "method": "confirmed_api_source_alias_reprojection",
            "source_match_id": str(item["source_match_id"]),
            "source_player_ids": [str(item.get("winner_player_id") or ""),
                                  str(item.get("loser_player_id") or "")],
            "previous_canonical_player_ids": previous_ids,
            "canonical_player_ids": [str(winner.id), str(loser.id)],
            "corrected_at": datetime.now(timezone.utc).isoformat(),
        }
        corrections = list(row_stats.get("identity_corrections") or [])
        if correction not in corrections:
            corrections.append(correction)
        row_stats["identity_corrections"] = corrections
        row.winner_player_id = winner.id
        row.loser_player_id = loser.id
        row.winner_name = winner.display_name
        row.loser_name = loser.display_name
        row.stats = row_stats


def _tournament_tokens(value: str) -> set[str]:
    generic = {"open", "challenger", "tournament", "qualification", "qualifying",
               "qualifier", "men", "women"}
    return set(re.findall(r"[a-z0-9]{4,}", (value or "").casefold())) - generic


def _same_cross_source_match(item: dict[str, Any], target, candidate) -> bool:
    """Require strong match evidence; an exact shared source ID permits date drift.

    Some weekly result feeds use a tournament-week date rather than the actual
    match day. A shared, namespaced source match ID is stronger evidence than
    that date difference, but is accepted only when the canonical sides, score,
    gender, and tournament still agree.
    """
    candidate_id = getattr(candidate, "id", None)
    target_id = getattr(target, "id", None)
    if candidate is target or (candidate_id is not None and candidate_id == target_id) \
            or getattr(candidate, "duplicate_of_id", None) is not None:
        return False
    date_delta = abs((candidate.played_at.date() - target.played_at.date()).days)
    target_sources = {
        ref for ref in ((target.stats or {}).get("sources") or [])
        if isinstance(ref, str) and ":" in ref
    }
    candidate_sources = {
        ref for ref in ((candidate.stats or {}).get("sources") or [])
        if isinstance(ref, str) and ":" in ref
    }
    shared_source_ids = target_sources.intersection(candidate_sources)
    same_canonical_sides = (
        getattr(target, "winner_player_id", None) == getattr(candidate, "winner_player_id", None)
        and getattr(target, "loser_player_id", None) == getattr(candidate, "loser_player_id", None)
    )
    if shared_source_ids:
        if not same_canonical_sides or date_delta > 7:
            return False
    elif date_delta > 1:
        return False
    gender = str(item.get("gender") or "unknown").casefold()
    accepted_genders = {gender, "unknown"}
    if gender == "m":
        accepted_genders.add("male")
    elif gender == "w":
        accepted_genders.add("female")
    if gender in {"m", "w"} and candidate.gender not in accepted_genders:
        return False
    if _score_key(candidate.score) != _score_key(item.get("score") or target.score):
        return False
    venue = _tournament_tokens(str(item.get("tournament") or target.tournament))
    other_venue = _tournament_tokens(candidate.tournament)
    if venue and other_venue and not venue.intersection(other_venue):
        return False
    target_names = (
        _player_key(str(item.get("winner_name") or target.winner_name), gender)[:2],
        _player_key(str(item.get("loser_name") or target.loser_name), gender)[:2],
    )
    candidate_names = (
        _player_key(candidate.winner_name, gender)[:2],
        _player_key(candidate.loser_name, gender)[:2],
    )
    # Canonical IDs already establish both participants. Provider display
    # names can differ in compound-surname formatting, so use name keys only
    # as a guard when the IDs themselves do not agree.
    if target_names != candidate_names and not same_canonical_sides:
        return False
    target_namespaces = {ref.split(":", 1)[0] for ref in target_sources}
    candidate_namespaces = {ref.split(":", 1)[0] for ref in candidate_sources}
    return bool(shared_source_ids or target_namespaces.isdisjoint(candidate_namespaces))


def _reconcile_cross_source_duplicates(session, source: str, targets: dict[str, tuple[Any, dict[str, Any]]]) -> int:
    """Link a unique cross-provider duplicate while retaining both canonical rows."""
    from db.models.external_tennis import ExtTennisCanonicalMatch

    if not targets:
        return 0
    target_rows = [row for row, _item in targets.values()
                   if getattr(row, "duplicate_of_id", None) is None]
    if not target_rows:
        return 0
    days = {row.played_at.date() for row in target_rows}
    # Current TE ingestion spans seven days; archive cursors normally contain
    # one day. Avoid turning a wide backfill batch into a full-table date scan.
    if len(days) > 14:
        return 0
    gender_filter = {
        str(item.get("gender") or row.gender or "unknown").casefold()
        for row, item in targets.values()
    }
    gender_filter = (gender_filter - {"unknown"}) | {"unknown"}
    if "m" in gender_filter:
        gender_filter.add("male")
    if "w" in gender_filter:
        gender_filter.add("female")
    if not gender_filter:
        gender_filter = {"unknown"}
    low_day, high_day = min(days), max(days)
    candidate_rows = session.execute(select(ExtTennisCanonicalMatch).where(
        ExtTennisCanonicalMatch.played_at >= datetime.combine(
            low_day - timedelta(days=7), datetime.min.time(), tzinfo=timezone.utc
        ),
        ExtTennisCanonicalMatch.played_at < datetime.combine(
            high_day + timedelta(days=8), datetime.min.time(), tzinfo=timezone.utc
        ),
        ExtTennisCanonicalMatch.gender.in_(sorted(gender_filter)),
        ExtTennisCanonicalMatch.duplicate_of_id.is_(None),
    )).scalars().all()
    candidates_by_day = {
        # Keep this in sync with the exact shared-ID rule in
        # _same_cross_source_match: weekly feeds can date a match at the
        # tournament start, several days before the actual match date.
        day: [row for row in candidate_rows if abs((row.played_at.date() - day).days) <= 7]
        for day in days
    }

    linked = 0
    for source_match_id, (target, item) in targets.items():
        if getattr(target, "duplicate_of_id", None) is not None:
            continue
        candidates = [row for row in candidates_by_day.get(target.played_at.date(), [])
                      if _same_cross_source_match(item, target, row)]
        if len(candidates) != 1:
            continue
        duplicate = candidates[0]
        if getattr(duplicate, "duplicate_of_id", None) not in {None, target.id}:
            continue
        target_source_refs = {
            ref for ref in ((target.stats or {}).get("sources") or [])
            if isinstance(ref, str) and ":" in ref
        }
        duplicate_source_refs = {
            ref for ref in ((duplicate.stats or {}).get("sources") or [])
            if isinstance(ref, str) and ":" in ref
        }
        shared_source_ids = target_source_refs & duplicate_source_refs
        target_sources = {
            ref.split(":", 1)[0] for ref in target_source_refs
        }
        duplicate_sources = {
            ref.split(":", 1)[0] for ref in duplicate_source_refs
        }
        established_sources = {"tennis_explorer", "tennis_my_life", "sackmann_sqlite"}
        if not (target_sources & established_sources) and duplicate_sources & established_sources:
            survivor, redundant = duplicate, target
        else:
            survivor, redundant = target, duplicate
        survivor_stats = dict(survivor.stats or {})
        redundant_stats = dict(redundant.stats or {})
        survivor_stats["sources"] = sorted(set(
            list(survivor_stats.get("sources") or []) + list(redundant_stats.get("sources") or [])
        ))
        duplicate_refs = list(survivor_stats.get("duplicate_rows") or [])
        duplicate_ref = {
            "canonical_match_id": str(redundant.id),
            "canonical_key": redundant.canonical_key,
            "match_signature": "date_gender_names_score_tournament",
        }
        if duplicate_ref not in duplicate_refs:
            duplicate_refs.append(duplicate_ref)
        survivor_stats["duplicate_rows"] = duplicate_refs
        if shared_source_ids and survivor.played_at.date() != redundant.played_at.date():
            date_conflicts = list(survivor_stats.get("source_date_conflicts") or [])
            date_conflict = {
                "shared_source_ids": sorted(shared_source_ids),
                "survivor_date": survivor.played_at.date().isoformat(),
                "duplicate_date": redundant.played_at.date().isoformat(),
                "delta_days": abs((survivor.played_at.date() - redundant.played_at.date()).days),
            }
            if date_conflict not in date_conflicts:
                date_conflicts.append(date_conflict)
            survivor_stats["source_date_conflicts"] = date_conflicts
        field_provenance = dict(survivor_stats.get("field_provenance") or {})
        field_conflicts = list(survivor_stats.get("field_conflicts") or [])
        for field in (
            "winner_rank", "loser_rank", "odds_winner", "odds_loser",
            "serve_won_winner", "serve_won_loser", "return_won_winner", "return_won_loser",
        ):
            kept, incoming = getattr(survivor, field, None), getattr(redundant, field, None)
            if kept is None and incoming is not None:
                setattr(survivor, field, incoming)
                field_provenance[field] = getattr(redundant, "stats_source", None) or source
            elif kept is not None and incoming is not None and kept != incoming:
                conflict = {
                    "field": field,
                    "kept": kept,
                    "kept_source": getattr(survivor, "stats_source", None),
                    "other": incoming,
                    "other_source": getattr(redundant, "stats_source", None) or source,
                }
                if conflict not in field_conflicts:
                    field_conflicts.append(conflict)
        if field_provenance:
            survivor_stats["field_provenance"] = field_provenance
        if field_conflicts:
            survivor_stats["field_conflicts"] = field_conflicts
        descendants = session.execute(select(ExtTennisCanonicalMatch).where(
            ExtTennisCanonicalMatch.duplicate_of_id == redundant.id
        )).scalars().all()
        for descendant in descendants:
            descendant.duplicate_of_id = survivor.id
            descendant_stats = dict(descendant.stats or {})
            descendant_stats["duplicate_of"] = str(survivor.id)
            descendant.stats = descendant_stats
            child_ref = {
                "canonical_match_id": str(descendant.id),
                "canonical_key": descendant.canonical_key,
                "match_signature": "date_gender_names_score_tournament",
            }
            if child_ref not in duplicate_refs:
                duplicate_refs.append(child_ref)
        if getattr(survivor, "surface", "unknown") in {None, "", "unknown"}:
            incoming_surface = getattr(redundant, "surface", None)
            if incoming_surface not in {None, "", "unknown"}:
                survivor.surface = incoming_surface
                field_provenance["surface"] = getattr(redundant, "stats_source", None) or source
                survivor_stats["field_provenance"] = field_provenance
        if not getattr(survivor, "best_of", 0) and getattr(redundant, "best_of", 0):
            survivor.best_of = redundant.best_of
            field_provenance["best_of"] = getattr(redundant, "stats_source", None) or source
            survivor_stats["field_provenance"] = field_provenance
        if not getattr(survivor, "round_name", "") and getattr(redundant, "round_name", ""):
            survivor.round_name = redundant.round_name
            field_provenance["round_name"] = getattr(redundant, "stats_source", None) or source
            survivor_stats["field_provenance"] = field_provenance
        if not getattr(survivor, "stats_source", None):
            survivor.stats_source = getattr(redundant, "stats_source", None)
        if not getattr(survivor, "odds_source", None):
            survivor.odds_source = getattr(redundant, "odds_source", None)
        survivor.stats = survivor_stats
        redundant.duplicate_of_id = survivor.id
        redundant_stats["duplicate_of"] = str(survivor.id)
        redundant.stats = redundant_stats
        linked += 1
    return linked


def choose_existing_match(candidates, item: dict[str, Any]):
    """Return one safely matching canonical row, or None for both absent/ambiguous.

    A same-pair nearby row that conflicts on score/tournament is returned as an
    ambiguity sentinel (False), preventing a second canonical match from being
    created for a likely correction or provider date-boundary difference.
    """
    played = item["played_at"]
    nearby = [row for row in candidates if abs((row.played_at.date() - played.date()).days) <= 1]
    if item.get("gender") in {"m", "w"}:
        nearby = [row for row in nearby if row.gender in {item["gender"], "unknown"}]
    if not nearby:
        return None
    incoming_score = _score_key(item.get("score", ""))
    incoming_tournament = _tournament_tokens(item.get("tournament", ""))
    matches = []
    for row in nearby:
        existing_score = _score_key(row.score)
        existing_tournament = _tournament_tokens(row.tournament)
        if not incoming_score or not existing_score or incoming_score != existing_score:
            continue
        if incoming_tournament and existing_tournament and not incoming_tournament.intersection(existing_tournament):
            continue
        matches.append(row)
    return matches[0] if len(matches) == 1 else False


def _choose_api_tennis_match(candidates, item, winner, loser):
    """Match an API result to an older canonical row without trusting set totals.

    Some imported canonical rows retain only a match-set summary (for example
    ``0 - 2``) rather than individual game scores. Such a row can be enriched
    only when date, tournament, gender and winner/loser identities all agree,
    and the summary is consistent with the validated incoming set score.
    """
    played = item["played_at"]
    incoming = _score_key(item.get("score", ""))
    venue = _tournament_tokens(item.get("tournament", ""))
    accepted_genders = {item.get("gender"), "unknown"}
    if item.get("gender") == "m":
        accepted_genders.add("male")
    elif item.get("gender") == "w":
        accepted_genders.add("female")
    eligible = []
    related = []
    for row in candidates:
        if abs((row.played_at.date() - played.date()).days) > 1:
            continue
        if row.gender not in accepted_genders:
            continue
        old_venue = _tournament_tokens(row.tournament)
        if venue and old_venue and not venue.intersection(old_venue):
            continue
        related.append(row)
        if row.winner_player_id != winner.id or row.loser_player_id != loser.id:
            continue
        old_score = _score_key(row.score)
        if old_score == incoming:
            eligible.append(row)
            continue
        # Some providers publish only the set summary (2-0) while another
        # publishes each set's games. Match either orientation, but only when
        # the detailed score proves those exact set counts.
        summary_matches = False
        if len(old_score) == 1 and len(incoming) >= 2:
            winner_sets = sum(a > b for a, b in incoming)
            loser_sets = sum(b > a for a, b in incoming)
            summary_matches = old_score[0] in {
                (winner_sets, loser_sets), (loser_sets, winner_sets)
            }
        elif len(incoming) == 1 and len(old_score) >= 2:
            winner_sets = sum(a > b for a, b in old_score)
            loser_sets = sum(b > a for a, b in old_score)
            summary_matches = incoming[0] in {
                (winner_sets, loser_sets), (loser_sets, winner_sets)
            }
        if summary_matches:
            eligible.append(row)
    if len(eligible) == 1:
        return eligible[0]
    if len(eligible) > 1:
        # Prefer the provider's exact match day when a weekly aggregate uses
        # the tournament-start date. If that still leaves several candidates,
        # prefer one uniquely backed by an established external archive over
        # a Flashscore/API-only projection.
        exact_day = [row for row in eligible if row.played_at.date() == played.date()]
        candidates = exact_day if exact_day else eligible
        if len(candidates) > 1:
            established = {"tennis_explorer", "tennis_my_life", "sackmann_sqlite"}
            established_rows = [row for row in candidates if any(
                isinstance(ref, str) and ref.split(":", 1)[0] in established
                for ref in ((row.stats or {}).get("sources") or [])
            )]
            if len(established_rows) == 1:
                return established_rows[0]
        if len(candidates) == 1:
            return candidates[0]
    return False if related else None


def project_recent_tennis_explorer(session, items: list[dict[str, Any]]) -> dict[str, int]:
    """Project only incoming TE rows, retaining unresolved identities in raw storage."""
    from db.models.external_tennis import ExtTennisCanonicalMatch, ExtTennisCanonicalPlayer
    from .canonical_player_aliases import (
        register_confirmed_player_alias, resolve_confirmed_player_aliases,
    )

    stats = {"projected": 0, "merged": 0, "ambiguous": 0, "unlinked": 0, "unchanged": 0}
    if not items:
        return stats

    player_names: dict[str, tuple[str, str, str]] = {}
    for item in items:
        for side in ("winner", "loser"):
            source_id = str(item.get(f"{side}_player_id") or "")
            name = str(item.get(f"{side}_name") or "")
            if source_id and name:
                player_names[source_id] = _player_key(name, str(item.get("gender") or "unknown"))

    surnames = sorted({token for key in player_names.values() for token in key[0].split() if token})
    canonical_players = session.execute(
        select(ExtTennisCanonicalPlayer).where(ExtTennisCanonicalPlayer.surname_key.in_(surnames))
    ).scalars().all() if surnames else []
    aliases = resolve_confirmed_player_aliases(
        session, source=_SOURCE, source_player_ids=list(player_names)
    )
    alias_player_ids = {row.canonical_player_id for row in aliases.values()}
    alias_people = session.execute(select(ExtTennisCanonicalPlayer).where(
        ExtTennisCanonicalPlayer.id.in_(list(alias_player_ids))
    )).scalars().all() if alias_player_ids else []
    alias_people_by_id = {row.id: row for row in alias_people}
    by_key: dict[tuple[str, str, str], list] = {}
    for person in canonical_players:
        by_key.setdefault((person.surname_key, person.initial, person.gender), []).append(person)
    resolved: dict[str, Any] = {}
    for source_id, key in player_names.items():
        surname, initial, gender = key
        alias = aliases.get(source_id)
        if alias is not None:
            person = alias_people_by_id.get(alias.canonical_player_id)
            if _confirmed_alias_person_matches(
                alias, person, surname=surname, initial=initial, gender=gender
            ):
                resolved[source_id] = person
            else:
                stats["ambiguous"] += 1
            continue
        candidates = list(by_key.get((surname, initial, gender), []))
        if not candidates and gender != "unknown":
            candidates += by_key.get((surname, initial, "unknown"), [])
        if not candidates:
            candidates = _compound_surname_candidates(
                surname, initial, gender, canonical_players
            )
        if len({row.id for row in candidates}) == 1:
            resolved[source_id] = candidates[0]

    # The legacy JSON field permits only one ID per provider. Persist an
    # additional ID only after the source name/initial/gender resolved to one
    # canonical person and the alias table contains no conflicting owner.
    for source_id, person in list(resolved.items()):
        if source_id in aliases:
            continue
        if str((person.source_ids or {}).get(_SOURCE) or "") == source_id:
            continue
        evidence_item = next((item for item in items if source_id in {
            str(item.get("winner_player_id") or ""), str(item.get("loser_player_id") or "")
        }), {})
        side = next((side for side in ("winner", "loser")
                     if str(evidence_item.get(f"{side}_player_id") or "") == source_id), "winner")
        if not register_confirmed_player_alias(
            session, source=_SOURCE, source_player_id=source_id,
            canonical_player_id=person.id,
            evidence={
                "method": "unique_surname_initial_gender",
                "provider_name": str(evidence_item.get(f"{side}_name") or ""),
                "provider_gender": str(evidence_item.get("gender") or "unknown"),
                "source_match_id": str(evidence_item.get("source_match_id") or ""),
            },
        ):
            resolved.pop(source_id, None)

    eligible = []
    for item in items:
        winner = resolved.get(str(item.get("winner_player_id") or ""))
        loser = resolved.get(str(item.get("loser_player_id") or ""))
        if winner is None or loser is None or winner.id == loser.id:
            stats["unlinked"] += 1
            continue
        eligible.append((item, winner, loser))

    if not eligible:
        session.flush()
        return stats

    # Fetch all candidate rows in one indexed query. A per-result query makes
    # an accessible career page (hundreds or thousands of rows) take minutes
    # and stalls the external-source lane. The pair index limits this lookup
    # to exact winner/loser identities; choose_existing_match still validates
    # date, score, gender, and tournament for each result.
    pairs = list({pair for _, winner, loser in eligible
                  for pair in ((winner.id, loser.id), (loser.id, winner.id))})
    source_aliases = {
        str(item["source_match_id"]): f"{_SOURCE}:{item['source_match_id']}"
        for item, _, _ in eligible
    }
    source_digests = {
        source_id: hashlib.sha1(alias.encode()).hexdigest()
        for source_id, alias in source_aliases.items()
    }
    existing_by_source_key = {
        row.canonical_key: row
        for row in session.execute(select(ExtTennisCanonicalMatch).where(
            ExtTennisCanonicalMatch.canonical_key.in_(list(source_digests.values()))
        )).scalars().all()
    }
    range_start = min(item["played_at"] for item, _, _ in eligible) - timedelta(days=1)
    range_end = max(item["played_at"] for item, _, _ in eligible) + timedelta(days=2)
    existing_matches = session.execute(select(ExtTennisCanonicalMatch).where(
        tuple_(ExtTennisCanonicalMatch.winner_player_id,
               ExtTennisCanonicalMatch.loser_player_id).in_(pairs),
        ExtTennisCanonicalMatch.played_at >= range_start,
        ExtTennisCanonicalMatch.played_at < range_end,
        ExtTennisCanonicalMatch.duplicate_of_id.is_(None),
    )).scalars().all()
    candidates_by_pair: dict[tuple[Any, Any], list] = {}
    for row in existing_matches:
        candidates_by_pair.setdefault((row.winner_player_id, row.loser_player_id), []).append(row)

    duplicate_targets: dict[str, tuple[Any, dict[str, Any]]] = {}
    for item, winner, loser in eligible:
        pair = (winner.id, loser.id)
        source_id = str(item["source_match_id"])
        source_alias = source_aliases[source_id]
        prior_source_row = existing_by_source_key.get(source_digests[source_id])
        if prior_source_row is not None:
            same_identity = (prior_source_row.winner_player_id == winner.id
                             and prior_source_row.loser_player_id == loser.id)
            valid_same_match = choose_existing_match([prior_source_row], item) is prior_source_row
            if (not same_identity and valid_same_match
                    and str(item.get("winner_player_id") or "") in aliases
                    and str(item.get("loser_player_id") or "") in aliases):
                # An audited alias decision can correct participant links on
                # this exact provider match. Keep an explicit correction trail;
                # source result versions and availability timestamps are intact.
                old_ids = [str(prior_source_row.winner_player_id), str(prior_source_row.loser_player_id)]
                prior_source_row.winner_player_id = winner.id
                prior_source_row.loser_player_id = loser.id
                same_identity = True
                prior_stats = dict(prior_source_row.stats or {})
                corrections = list(prior_stats.get("identity_corrections") or [])
                correction = {
                    "previous_canonical_player_ids": old_ids,
                    "canonical_player_ids": [str(winner.id), str(loser.id)],
                    "source_player_ids": [str(item.get("winner_player_id") or ""),
                                          str(item.get("loser_player_id") or "")],
                    "method": "confirmed_source_alias_reprojection",
                }
                if correction not in corrections:
                    corrections.append(correction)
                prior_stats["identity_corrections"] = corrections
                prior_source_row.stats = prior_stats
            if not same_identity or not valid_same_match:
                stats["ambiguous"] += 1
                continue
            duplicate_targets[source_id] = (prior_source_row, item)
            match_stats = dict(prior_source_row.stats or {})
            aliases = list(match_stats.get("sources") or [])
            if source_alias not in aliases:
                aliases.append(source_alias)
                match_stats["sources"] = sorted(set(aliases))
                prior_source_row.stats = match_stats
                stats["merged"] += 1
            else:
                stats["unchanged"] += 1
            continue
        candidates = candidates_by_pair.setdefault(pair, [])
        matched = choose_existing_match(candidates, item)
        if matched is False:
            stats["ambiguous"] += 1
            continue
        if matched is not None:
            duplicate_targets[source_id] = (matched, item)
            match_stats = dict(matched.stats or {})
            aliases = list(match_stats.get("sources") or [])
            if source_alias in aliases:
                stats["unchanged"] += 1
                continue
            aliases.append(source_alias)
            match_stats["sources"] = sorted(set(aliases))
            matched.stats = match_stats
            stats["merged"] += 1
            for person, source_id in ((winner, item.get("winner_player_id")),
                                      (loser, item.get("loser_player_id"))):
                if source_id and not (person.source_ids or {}).get(_SOURCE):
                    person.source_ids = {**dict(person.source_ids or {}), _SOURCE: str(source_id)}
            continue

        digest = source_digests[source_id]
        new_match = ExtTennisCanonicalMatch(
            id=uuid.uuid5(_MATCH_NS, digest), canonical_key=digest,
            played_at=item["played_at"], available_at=item["available_at"],
            tour_level=item.get("tour_level") or "other", tournament=item.get("tournament") or "",
            surface=item.get("surface") or "unknown", best_of=int(item.get("best_of") or 3),
            round_name=item.get("round_name") or "", gender=item.get("gender") or "unknown",
            winner_name=winner.display_name, loser_name=loser.display_name,
            winner_player_id=winner.id, loser_player_id=loser.id,
            score=item.get("score") or "", stats={"sources": [source_alias], "raw_levels": [item.get("tour_level") or "other"]},
        )
        session.add(new_match)
        duplicate_targets[source_id] = (new_match, item)
        candidates.append(new_match)
        stats["projected"] += 1
        for person, source_id in ((winner, item.get("winner_player_id")),
                                  (loser, item.get("loser_player_id"))):
            if source_id and not (person.source_ids or {}).get(_SOURCE):
                person.source_ids = {**dict(person.source_ids or {}), _SOURCE: str(source_id)}
    session.flush()
    stats["merged"] += _reconcile_cross_source_duplicates(session, _SOURCE, duplicate_targets)
    return stats


def project_recent_tennis_abstract(session, items: list[dict[str, Any]], *, target_player_id,
                                   source_player_id: str) -> dict[str, int]:
    """Project verified recent TA rows only when both canonical players resolve uniquely.

    The target is already identity-checked against the TA profile title. The
    opponent must resolve by surname, initial, and gender. Ambiguous opponents
    and any likely duplicate with conflicting score/tournament remain in the
    append-only observation archive and are not promoted to player metrics.
    """
    from datetime import datetime
    from db.models.external_tennis import ExtTennisCanonicalMatch, ExtTennisCanonicalPlayer
    from sqlalchemy import select, tuple_

    stats = {"projected": 0, "merged": 0, "ambiguous": 0, "unlinked": 0, "unchanged": 0}
    if not items:
        return stats
    target = session.get(ExtTennisCanonicalPlayer, target_player_id)
    if target is None or (target.source_ids or {}).get("tennis_abstract") != source_player_id:
        stats["unlinked"] = len(items)
        return stats

    opponent_keys = {_player_key(
        item.get("opponent_name") or (item.get("loser_name") if item.get("player_won") else item.get("winner_name")) or "",
        target.gender,
    ) for item in items}
    surnames = sorted({key[0] for key in opponent_keys if key[0]})
    people = session.execute(select(ExtTennisCanonicalPlayer).where(
        ExtTennisCanonicalPlayer.surname_key.in_(surnames)
    )).scalars().all() if surnames else []
    by_key: dict[tuple[str, str, str], list] = {}
    for person in people:
        by_key.setdefault((person.surname_key, person.initial, person.gender), []).append(person)
    resolved = {}
    for key in opponent_keys:
        surname, initial, gender = key
        candidates = list(by_key.get((surname, initial, gender), []))
        if not candidates and gender != "unknown":
            candidates = list(by_key.get((surname, initial, "unknown"), []))
        unique = {person.id: person for person in candidates if person.id != target.id}
        if len(unique) == 1:
            resolved[key] = next(iter(unique.values()))

    eligible = []
    for item in items:
        opponent_name = item.get("opponent_name") or (
            item.get("loser_name") if item.get("player_won") else item.get("winner_name")
        ) or ""
        opponent_key = _player_key(opponent_name, target.gender)
        opponent = resolved.get(opponent_key)
        if opponent is None:
            stats["unlinked"] += 1
            continue
        try:
            played_at = datetime.fromisoformat(str(item["played_at"]).replace("Z", "+00:00"))
            available_at = datetime.fromisoformat(str(item["available_at"]).replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError):
            stats["ambiguous"] += 1
            continue
        player_won = bool(item.get("player_won"))
        winner, loser = (target, opponent) if player_won else (opponent, target)
        incoming = dict(item)
        incoming.update({"played_at": played_at, "available_at": available_at,
                         "gender": target.gender, "winner_id": winner.id, "loser_id": loser.id,
                         "winner_name_canonical": winner.display_name,
                         "loser_name_canonical": loser.display_name,
                         "score": _validated_player_score(str(item.get("score") or ""), player_won)})
        if not incoming["score"]:
            stats["ambiguous"] += 1
            continue
        eligible.append((incoming, winner, loser))

    if not eligible:
        session.flush()
        return stats

    pairs = list({(winner.id, loser.id) for _, winner, loser in eligible})
    # The broad candidate window detects provider date drift. We merge only
    # within one day; a same-pair/same-tournament result farther away is held
    # for review instead of creating a duplicate canonical match.
    start = min(item["played_at"] for item, _, _ in eligible) - timedelta(days=30)
    end = max(item["played_at"] for item, _, _ in eligible) + timedelta(days=31)
    existing = session.execute(select(ExtTennisCanonicalMatch).where(
        tuple_(ExtTennisCanonicalMatch.winner_player_id, ExtTennisCanonicalMatch.loser_player_id).in_(pairs),
        ExtTennisCanonicalMatch.played_at >= start,
        ExtTennisCanonicalMatch.played_at < end,
        ExtTennisCanonicalMatch.duplicate_of_id.is_(None),
    )).scalars().all()
    by_pair: dict[tuple[Any, Any], list] = {}
    for match in existing:
        by_pair.setdefault((match.winner_player_id, match.loser_player_id), []).append(match)

    for item, winner, loser in eligible:
        candidates = by_pair.setdefault((winner.id, loser.id), [])
        match = _choose_tennis_abstract_existing_match(candidates, item)
        source_alias = f"tennis_abstract:{item['source_match_id']}"
        reverse_candidates = []
        incoming_score = _score_key(item["score"])
        reverse_score = tuple((b, a) for a, b in incoming_score)
        incoming_tournament = _tournament_tokens(item.get("tournament") or "")
        for row in by_pair.get((loser.id, winner.id), []):
            days = abs((row.played_at.date() - item["played_at"].date()).days)
            if days > 30 or row.gender not in {item["gender"], "unknown"}:
                continue
            same_score = _score_key(row.score) in {incoming_score, reverse_score}
            same_tournament = bool(
                _tournament_tokens(row.tournament) & incoming_tournament
            )
            if days <= 1 or (same_score and same_tournament):
                reverse_candidates.append(row)
        if match is None and reverse_candidates:
            # Same participants/date in the opposite winner orientation is a
            # result conflict. Keep the raw observation; never create a second
            # canonical match that would count the event twice.
            stats["ambiguous"] += 1
            continue
        if match is False:
            stats["ambiguous"] += 1
            continue
        if match is not None:
            sources = set((match.stats or {}).get("sources") or [])
            if source_alias in sources:
                stats["unchanged"] += 1
                continue
            match.stats = {**dict(match.stats or {}), "sources": sorted(sources | {source_alias})}
            stats["merged"] += 1
            continue

        fingerprint = hashlib.sha256(json.dumps([
            item["played_at"].date().isoformat(), sorted([str(winner.id), str(loser.id)]),
            sorted(_tournament_tokens(item.get("tournament") or "")),
            str(item.get("round_name") or "").casefold(), target.gender,
        ], separators=(",", ":"), sort_keys=True).encode()).hexdigest()
        new_match = ExtTennisCanonicalMatch(
            id=uuid.uuid5(_MATCH_NS, f"tennis_abstract:{fingerprint}"), canonical_key=fingerprint,
            played_at=item["played_at"], available_at=item["available_at"],
            tour_level="unknown", tournament=str(item.get("tournament") or "")[:255],
            surface=str(item.get("surface") or "unknown"),
            best_of=5 if target.gender == "m" and len(_score_key(item["score"])) >= 4 else 3,
            round_name=str(item.get("round_name") or "")[:32], gender=target.gender,
            winner_name=winner.display_name, loser_name=loser.display_name,
            winner_player_id=winner.id, loser_player_id=loser.id,
            score=item["score"][:64],
            stats={"sources": [source_alias], "raw_levels": ["unknown"],
                   "history_scope": "recent_fragment_not_full_career",
                   "score_orientation_player_id": str(winner.id)},
        )
        session.add(new_match)
        candidates.append(new_match)
        stats["projected"] += 1
    session.flush()
    return stats


def _validated_player_score(score: str, target_won: bool) -> str:
    """Validate target-perspective score and normalize sets to canonical winner-first."""
    tokens = re.findall(r"(\d{1,2})-(\d{1,2})(\(\d{1,2}\))?", score)
    if len(tokens) < 2 or len(tokens) > 5:
        return ""
    target_sets = other_sets = 0
    for first, second, tb in tokens:
        a, b = int(first), int(second)
        if a == b or max(a, b) > 7:
            return ""
        target_sets += int(a > b)
        other_sets += int(b > a)
    if target_sets == other_sets or (target_won != (target_sets > other_sets)):
        return ""
    if target_won:
        return " ".join(f"{a}-{b}{tb}" for a, b, tb in tokens)
    return " ".join(f"{b}-{a}{tb}" for a, b, tb in tokens)


def _choose_tennis_abstract_existing_match(candidates, item):
    """Match provider-side scores in either orientation; flag wider date drift."""
    played = item["played_at"]
    incoming = _score_key(item.get("score", ""))
    venue = _tournament_tokens(item.get("tournament", ""))
    if not incoming:
        return False
    nearby = []
    date_drift = []
    for row in candidates:
        days = abs((row.played_at.date() - played.date()).days)
        if row.gender not in {item["gender"], "unknown"}:
            continue
        old_score = _score_key(row.score)
        same_score = bool(old_score and (old_score == incoming or old_score == tuple((b, a) for a, b in incoming)))
        old_venue = _tournament_tokens(row.tournament)
        same_venue = bool(venue and old_venue and venue.intersection(old_venue))
        if same_score and same_venue and days <= 30:
            (nearby if days <= 1 else date_drift).append(row)
        elif days <= 1:
            nearby.append(row)
    exact = [row for row in nearby if abs((row.played_at.date() - played.date()).days) <= 1
             and _score_key(row.score) and (
                 _score_key(row.score) == incoming
                 or _score_key(row.score) == tuple((b, a) for a, b in incoming)
             )
             and (not venue or not _tournament_tokens(row.tournament)
                  or venue.intersection(_tournament_tokens(row.tournament)))]
    if len(exact) == 1 and not date_drift:
        return exact[0]
    if exact or nearby or date_drift:
        return False
    return None


def project_api_tennis_results(session, items: list[dict[str, Any]]) -> dict[str, int]:
    """Project safely identified API-Tennis singles into the shared archive.

    Provider player IDs are preferred and checked against names and gender.
    Name fallback requires a unique surname/initial/gender identity with no
    conflicting API-Tennis ID. Match merges require a unique pair, nearby date,
    winner-first set score, and compatible tournament. Unresolved and
    conflicting rows remain in the source archive without canonical duplicates.
    """
    from db.models.external_tennis import ExtTennisCanonicalMatch, ExtTennisCanonicalPlayer
    from .canonical_player_aliases import resolve_confirmed_player_aliases

    stats = {"projected": 0, "merged": 0, "ambiguous": 0, "unlinked": 0, "unchanged": 0}
    if not items:
        return stats
    unique_items = {
        str(item["source_match_id"]): item for item in items
        if str(item.get("source_match_id") or "").strip()
    }
    items = list(unique_items.values())
    player_keys: dict[str, tuple[str, str, str]] = {}
    player_key_conflicts: set[str] = set()
    for item in items:
        gender = str(item.get("gender") or "unknown").casefold()
        for side in ("winner", "loser"):
            source_id = str(item.get(f"{side}_player_id") or "").strip()
            name = str(item.get(f"{side}_name") or "").strip()
            if source_id and name:
                surname, initial = surname_and_initial(name)
                key = (surname.casefold(), initial.casefold(), gender)
                if source_id in player_keys and player_keys[source_id] != key:
                    player_key_conflicts.add(source_id)
                player_keys[source_id] = key
    if not player_keys:
        stats["unlinked"] = len(items)
        return stats

    exact_people = session.execute(
        select(ExtTennisCanonicalPlayer).where(
            ExtTennisCanonicalPlayer.source_ids["api_tennis"].astext.in_(list(player_keys))
        )
    ).scalars().all()
    exact_by_source: dict[str, list] = {}
    for person in exact_people:
        source_id = str((person.source_ids or {}).get("api_tennis") or "")
        exact_by_source.setdefault(source_id, []).append(person)

    surnames = sorted({token for key in player_keys.values() for token in key[0].split() if token})
    named_people = session.execute(
        select(ExtTennisCanonicalPlayer).where(ExtTennisCanonicalPlayer.surname_key.in_(surnames))
    ).scalars().all() if surnames else []
    by_key: dict[tuple[str, str, str], list] = {}
    for person in named_people:
        by_key.setdefault((person.surname_key.casefold(), person.initial.casefold(), person.gender), []).append(person)

    reviewed_aliases = resolve_confirmed_player_aliases(
        session, source="api_tennis", source_player_ids=list(player_keys)
    )
    reviewed_aliases = {
        source_id: alias for source_id, alias in reviewed_aliases.items()
        if (alias.evidence_json or {}).get("adjudication_id")
    }
    reviewed_people = session.execute(select(ExtTennisCanonicalPlayer).where(
        ExtTennisCanonicalPlayer.id.in_(
            [alias.canonical_player_id for alias in reviewed_aliases.values()] or [uuid.UUID(int=0)]
        )
    )).scalars().all() if reviewed_aliases else []
    reviewed_people_by_id = {row.id: row for row in reviewed_people}

    resolved: dict[str, Any] = {}
    pending_alias_updates: list[tuple[Any, dict[str, Any]]] = []
    for source_id, key in player_keys.items():
        if source_id in player_key_conflicts:
            continue
        reviewed_alias = reviewed_aliases.get(source_id)
        if reviewed_alias is not None:
            person = reviewed_people_by_id.get(reviewed_alias.canonical_player_id)
            if (person is not None and person.initial.casefold() == key[1].casefold()
                    and (key[2] == "unknown" or person.gender in {key[2], "unknown"})
                    and _confirmed_alias_name_matches(key[0], person)):
                resolved[source_id] = person
            continue
        candidates = exact_by_source.get(source_id, [])
        if len(candidates) == 1:
            person = candidates[0]
            if ((person.surname_key.casefold(), person.initial.casefold()) == key[:2]
                    and (key[2] == "unknown" or person.gender in {key[2], "unknown"})):
                # Older projections may have created an API-only canonical
                # person before a richer TML/Sackmann identity was imported.
                # Prefer that single established identity for new API match
                # links. Keep the provider's player ID on the raw match row;
                # do not overwrite the stable canonical ID or guess when two
                # established people share the same surname and initial.
                peer_candidates = list(by_key.get(key, []))
                if not peer_candidates and key[2] != "unknown":
                    peer_candidates = list(by_key.get((key[0], key[1], "unknown"), []))
                established = [row for row in peer_candidates
                               if row.id != person.id
                               and isinstance(row.source_ids, dict)
                               and any(row.source_ids.get(name) for name in ("tml", "sackmann_sqlite"))]
                if not established:
                    established = _compound_surname_candidates(
                        key[0], key[1], key[2], named_people, source="api_tennis"
                    )
                    established = [row for row in established if row.id != person.id]
                tml = [row for row in established if row.source_ids.get("tml")]
                peers = tml if len(tml) == 1 else established
                resolved[source_id] = peers[0] if len(peers) == 1 else person
            continue
        if candidates:
            continue
        surname, initial, gender = key
        candidates = list(by_key.get((surname, initial, gender), []))
        if not candidates and gender != "unknown":
            candidates = list(by_key.get((surname, initial, "unknown"), []))
        candidates = [person for person in candidates if not (person.source_ids or {}).get("api_tennis")]
        if len({person.id for person in candidates}) == 1:
            person = candidates[0]
            resolved[source_id] = person
            pending_alias_updates.append((person, {**dict(person.source_ids or {}), "api_tennis": source_id}))

    eligible = []
    for item in items:
        winner = resolved.get(str(item.get("winner_player_id") or ""))
        loser = resolved.get(str(item.get("loser_player_id") or ""))
        if winner is None or loser is None or winner.id == loser.id:
            stats["unlinked"] += 1
            continue
        if "/" in str(item.get("winner_name") or "") or "/" in str(item.get("loser_name") or ""):
            stats["unlinked"] += 1
            continue
        if not _score_key(str(item.get("score") or "")):
            stats["unlinked"] += 1
            continue
        eligible.append((item, winner, loser))

    for person, source_ids in pending_alias_updates:
        person.source_ids = source_ids
    if not eligible:
        session.flush()
        return stats

    _correct_api_linked_match_identities(session, eligible)

    pairs = list({pair for _, winner, loser in eligible
                  for pair in ((winner.id, loser.id), (loser.id, winner.id))})
    aliases_by_source_id = {
        str(item["source_match_id"]): f"api_tennis:{item['source_match_id']}"
        for item, _, _ in eligible
    }
    digests_by_source_id = {
        source_id: hashlib.sha1(alias.encode()).hexdigest()
        for source_id, alias in aliases_by_source_id.items()
    }
    existing_by_key = {}
    if digests_by_source_id:
        keyed_matches = session.execute(select(ExtTennisCanonicalMatch).where(
            ExtTennisCanonicalMatch.canonical_key.in_(list(digests_by_source_id.values()))
        )).scalars().all()
        existing_by_key = {row.canonical_key: row for row in keyed_matches}
    range_start = min(item["played_at"] for item, _, _ in eligible) - timedelta(days=1)
    range_end = max(item["played_at"] for item, _, _ in eligible) + timedelta(days=2)
    existing_matches = session.execute(select(ExtTennisCanonicalMatch).where(
        tuple_(ExtTennisCanonicalMatch.winner_player_id,
               ExtTennisCanonicalMatch.loser_player_id).in_(pairs),
        ExtTennisCanonicalMatch.played_at >= range_start,
        ExtTennisCanonicalMatch.played_at < range_end,
        ExtTennisCanonicalMatch.duplicate_of_id.is_(None),
    )).scalars().all()
    candidates_by_pair: dict[tuple[Any, Any], list] = {}
    for row in existing_matches:
        candidates_by_pair.setdefault((row.winner_player_id, row.loser_player_id), []).append(row)

    duplicate_targets: dict[str, tuple[Any, dict[str, Any]]] = {}
    for item, winner, loser in eligible:
        pair = (winner.id, loser.id)
        candidates = candidates_by_pair.setdefault(pair, []) + candidates_by_pair.setdefault((loser.id, winner.id), [])
        matched = _choose_api_tennis_match(candidates, item, winner, loser)
        source_match_id = str(item["source_match_id"])
        alias = aliases_by_source_id[source_match_id]
        deterministic_key = digests_by_source_id[source_match_id]
        existing_alias_row = existing_by_key.get(deterministic_key)
        if existing_alias_row is not None:
            same_identity = (existing_alias_row.winner_player_id == winner.id
                             and existing_alias_row.loser_player_id == loser.id)
            valid_same_match = _choose_api_tennis_match(
                [existing_alias_row], item, winner, loser
            ) is existing_alias_row
            if not same_identity or not valid_same_match:
                # The same source match may already have been projected under
                # API-only player IDs. If a uniquely validated established
                # pair already has this exact match, attach provenance there
                # rather than retaining a second canonical result.
                if matched is not None and matched is not False:
                    match_stats = dict(matched.stats or {})
                    match_stats["sources"] = sorted(set(
                        list(match_stats.get("sources") or []) + [alias]
                    ))
                    duplicate_ref = {
                        "canonical_match_id": str(existing_alias_row.id),
                        "canonical_key": existing_alias_row.canonical_key,
                        "match_signature": "date_gender_names_score_tournament",
                    }
                    duplicate_refs = list(match_stats.get("duplicate_rows") or [])
                    if duplicate_ref not in duplicate_refs:
                        duplicate_refs.append(duplicate_ref)
                    match_stats["duplicate_rows"] = duplicate_refs
                    matched.stats = match_stats
                    existing_alias_row.duplicate_of_id = matched.id
                    old_stats = dict(existing_alias_row.stats or {})
                    old_stats["duplicate_of"] = str(matched.id)
                    existing_alias_row.stats = old_stats
                    descendants = session.execute(select(ExtTennisCanonicalMatch).where(
                        ExtTennisCanonicalMatch.duplicate_of_id == existing_alias_row.id
                    )).scalars().all()
                    for descendant in descendants:
                        descendant.duplicate_of_id = matched.id
                        descendant_stats = dict(descendant.stats or {})
                        descendant_stats["duplicate_of"] = str(matched.id)
                        descendant.stats = descendant_stats
                    duplicate_targets[source_match_id] = (matched, item)
                    stats["merged"] += 1
                    continue
                stats["ambiguous"] += 1
                continue
            match_stats = dict(existing_alias_row.stats or {})
            aliases = list(match_stats.get("sources") or [])
            already_linked = alias in aliases
            if not already_linked:
                match_stats["sources"] = sorted(set(aliases + [alias]))
            enriched = (len(_score_key(existing_alias_row.score)) == 1
                        and len(_score_key(item["score"])) >= 2)
            if enriched:
                existing_alias_row.score = item["score"]
                score_sources = list(match_stats.get("result_score_sources") or [])
                match_stats["result_score_sources"] = sorted(set(score_sources + ["api_tennis"]))
            if not already_linked or enriched:
                existing_alias_row.stats = match_stats
                stats["merged"] += 1
            else:
                stats["unchanged"] += 1
            reconciliation_target = existing_alias_row
            if (existing_alias_row.duplicate_of_id is not None
                    and matched is not None and matched is not False):
                # A stable API key may already be retained as a duplicate of
                # the canonical survivor. Continue duplicate reconciliation
                # from that survivor so other legacy projections of the same
                # exact source match can be attached as well.
                reconciliation_target = matched
            duplicate_targets[source_match_id] = (reconciliation_target, item)
            continue
        if matched is False:
            stats["ambiguous"] += 1
            continue
        if matched is not None:
            match_stats = dict(matched.stats or {})
            aliases = list(match_stats.get("sources") or [])
            already_linked = alias in aliases
            if not already_linked:
                aliases.append(alias)
                match_stats["sources"] = sorted(set(aliases))
            enriched = len(_score_key(matched.score)) == 1 and len(_score_key(item["score"])) >= 2
            if enriched:
                matched.score = item["score"]
                score_sources = list(match_stats.get("result_score_sources") or [])
                match_stats["result_score_sources"] = sorted(set(score_sources + ["api_tennis"]))
            if not already_linked or enriched:
                matched.stats = match_stats
                stats["merged"] += 1
            else:
                stats["unchanged"] += 1
            duplicate_targets[source_match_id] = (matched, item)
            continue

        digest = deterministic_key
        match = ExtTennisCanonicalMatch(
            id=uuid.uuid5(_MATCH_NS, digest), canonical_key=digest,
            played_at=item["played_at"], available_at=item["available_at"],
            tour_level=item.get("tour_level") or "other", tournament=item.get("tournament") or "",
            surface="unknown", best_of=0, round_name=item.get("round_name") or "",
            gender=item.get("gender") or "unknown",
            winner_name=winner.display_name, loser_name=loser.display_name,
            winner_player_id=winner.id, loser_player_id=loser.id,
            score=item["score"], stats_source="api_tennis",
            stats={"sources": [alias], "raw_levels": [item.get("tour_level") or "other"],
                   "retrospective": True, "best_of_known": False},
        )
        session.add(match)
        candidates.append(match)
        duplicate_targets[source_match_id] = (match, item)
        stats["projected"] += 1
    session.flush()
    stats["merged"] += _reconcile_cross_source_duplicates(session, "api_tennis", duplicate_targets)
    return stats
