"""Confirmed provider/canonical identity linking for saved API-Tennis PBP."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json

from sqlalchemy import and_, or_, text
from db.models import Event, TennisPlayerIdentityLink, TennisPlayerProfile, TennisPointDocument, TennisPointHistoryVersion
from db.models.external_tennis import (ExtTennisCanonicalMatch, ExtTennisMatch,
                                       ExtTennisMatchObservation)
from .canonical_player_aliases import confirmed_player_alias_as_of
from tennis_adapter.history_linking import normalize_api_pbp, official_result, finished_set

PARSER = "api_tennis_pbp_link_v2"
LINK_METHOD = "confirmed_canonical_and_provider_ids_v2"


def confirmed_pair(session, event, as_of):
    participants = (event.participant_a_id, event.participant_b_id)
    if not all(participants) or participants[0] == participants[1]:
        return None, "participants_missing_or_identical"
    versions = session.query(TennisPlayerIdentityLink).filter(
        TennisPlayerIdentityLink.participant_id.in_(participants), TennisPlayerIdentityLink.available_at <= as_of,
    ).order_by(TennisPlayerIdentityLink.available_at, TennisPlayerIdentityLink.id).all()
    latest = {(row.participant_id, row.source): row for row in versions}
    identities = [row for row in latest.values() if row.status == "confirmed"]
    source_ids = [(row.source, row.source_player_id) for row in identities]
    aliases_by_key = {}
    alias_available_by_key = {}
    for source, source_player_id in source_ids:
        resolved = confirmed_player_alias_as_of(
            session, source=source, source_player_id=source_player_id, as_of=as_of)
        if resolved is not None:
            aliases_by_key[(source, source_player_id)], alias_available_by_key[(source, source_player_id)] = resolved
    profiles = {row.participant_id: row for row in session.query(TennisPlayerProfile).filter(
        TennisPlayerProfile.participant_id.in_(participants)).all()}
    result = []
    for participant in participants:
        proofs = [(row, aliases_by_key[(row.source, row.source_player_id)]) for row in identities
                  if row.participant_id == participant and (row.source, row.source_player_id) in aliases_by_key]
        if len({alias.canonical_player_id for _, alias in proofs}) != 1:
            return None, "canonical_identity_missing_or_conflicting"
        identity, alias = max(proofs, key=lambda pair: (pair[0].available_at, str(pair[0].id)))
        profile_id = getattr(profiles.get(participant), "canonical_player_id", None)
        if profile_id is not None and profile_id != alias.canonical_player_id:
            return None, "profile_canonical_conflict"
        result.append({"participant_id": str(participant), "canonical_id": str(alias.canonical_player_id),
                       "identity_link_id": str(identity.id), "canonical_alias_id": str(alias.id),
                       "identity_available_at": identity.available_at.isoformat(),
                       "alias_available_at": alias_available_by_key[(identity.source, identity.source_player_id)].isoformat()})
    if result[0]["canonical_id"] == result[1]["canonical_id"]:
        return None, "identical_canonical_players"
    return result, "confirmed"


def plan_api_link(session, event, *, as_of, diagnostics=None):
    diagnostics = diagnostics if diagnostics is not None else {}
    winner, official_scores, reason = official_result(event.status, event.metadata_json)
    if (winner is None or event.source != "fonbet" or not str(event.source_event_id).isdigit()
            or event.sport_code != "tennis" or event.is_live or event.scheduled_start_at is None):
        return None, reason if winner is None else "event_outside_scope"
    official = (event.metadata_json or {}).get("fonbet_official_result") or {}
    discipline = ' '.join(str(value) for value in (event.participant_a_name, event.participant_b_name,
        official.get('tournament'), (event.metadata_json or {}).get('raw_tournament'),
        official.get('discipline'), (event.metadata_json or {}).get('discipline'))).casefold()
    if '/' in discipline or any(token in discipline for token in ('doubles', 'double', 'пары', 'пара ')):
        return None, "doubles"
    identities, reason = confirmed_pair(session, event, as_of)
    if identities is None:
        return None, reason
    diagnostics['canonical_identity_proofs'] = identities
    a, b = (row["canonical_id"] for row in identities)
    matches = session.query(ExtTennisCanonicalMatch).filter(
        or_(and_(ExtTennisCanonicalMatch.winner_player_id == a, ExtTennisCanonicalMatch.loser_player_id == b),
            and_(ExtTennisCanonicalMatch.winner_player_id == b, ExtTennisCanonicalMatch.loser_player_id == a)),
        ExtTennisCanonicalMatch.duplicate_of_id.is_(None), ExtTennisCanonicalMatch.available_at <= as_of,
        ExtTennisCanonicalMatch.played_at >= event.scheduled_start_at - timedelta(days=1),
        ExtTennisCanonicalMatch.played_at < event.scheduled_start_at + timedelta(days=2),
    ).all()
    candidates = [(row, [value.split(':', 1)[1] for value in (row.stats or {}).get('sources', [])
                        if isinstance(value, str) and value.startswith('api_tennis:')]) for row in matches]
    candidates = [(row, source_ids) for row, source_ids in candidates if source_ids]
    if len(candidates) != 1:
        return None, "api_match_missing" if not candidates else "api_match_ambiguous"
    canonical, source_ids = candidates[0]
    diagnostics.update(canonical_match_id=str(canonical.id), canonical_match_key=canonical.canonical_key,
                       provider_match_candidates=source_ids)
    if str(canonical.winner_player_id) != identities[winner]["canonical_id"]:
        return None, "official_provider_winner_conflict"
    providers = session.query(ExtTennisMatch).filter(
        ExtTennisMatch.source == 'api_tennis', ExtTennisMatch.source_match_id.in_(source_ids),
        ExtTennisMatch.available_at <= as_of).all()
    if len(providers) != 1:
        return None, "provider_match_missing_or_ambiguous"
    provider = providers[0]
    observation = session.query(ExtTennisMatchObservation).filter(
        ExtTennisMatchObservation.source == 'api_tennis',
        ExtTennisMatchObservation.source_match_id == provider.source_match_id,
        ExtTennisMatchObservation.available_at <= as_of).order_by(
            ExtTennisMatchObservation.available_at.desc(), ExtTennisMatchObservation.id.desc()).first()
    raw = (observation.payload or {}).get('event') if observation else None
    if not isinstance(raw, dict) or not isinstance(raw.get('pointbypoint'), list) or not raw['pointbypoint']:
        return None, "source_has_no_game_history"
    first, second = str(raw.get('first_player_key') or ''), str(raw.get('second_player_key') or '')
    mapping, provider_alias_available = {}, {}
    for provider_player_id in (first, second):
        resolved = confirmed_player_alias_as_of(
            session, source='api_tennis', source_player_id=provider_player_id, as_of=as_of)
        if resolved is not None:
            mapping[provider_player_id], provider_alias_available[provider_player_id] = resolved
    diagnostics['provider_identity_check'] = {'provider_match_id': provider.source_match_id,
        'provider_player_ids': [first, second],
        'provider_winner_loser_ids': [provider.winner_player_id, provider.loser_player_id],
        'confirmed_aliases': [{'alias_id': str(mapping[player_id].id), 'provider_player_id': player_id,
                               'canonical_id': str(mapping[player_id].canonical_player_id)}
                              for player_id in (first, second) if player_id in mapping],
        'expected_canonical_pair': [a, b], 'source_observation_id': str(observation.id)}
    if (first == second or first not in mapping or second not in mapping
            or {str(mapping[first].canonical_player_id), str(mapping[second].canonical_player_id)} != {a, b}
            or {first, second} != {str(provider.winner_player_id), str(provider.loser_player_id)}):
        return None, "provider_player_ids_unconfirmed_or_conflicting"
    provider_winner = str(provider.winner_player_id)
    if str(mapping[provider_winner].canonical_player_id) != str(canonical.winner_player_id):
        return None, "provider_canonical_winner_conflict"
    first_is_a = str(mapping[first].canonical_player_id) == a
    raw_winner = {"first player": first, "second player": second}.get(str(raw.get('event_winner')).casefold())
    if raw_winner != provider_winner or str(raw.get('event_status')).casefold() not in {'finished', 'ended', 'completed'}:
        return None, "source_result_invalid"
    source_scores = raw.get('scores')
    try:
        source_scores = sorted(source_scores, key=lambda row: int(row['score_set']))
        if [int(row['score_set']) for row in source_scores] != list(range(1, len(source_scores) + 1)):
            return None, "source_set_numbers_invalid"
        oriented_scores = [tuple(int(row[key]) for key in ('score_first','score_second')) for row in source_scores]
        if not first_is_a:
            oriented_scores = [score[::-1] for score in oriented_scores]
    except (KeyError, TypeError, ValueError):
        return None, "source_final_sets_unknown"
    if oriented_scores != official_scores:
        return None, "official_provider_set_score_conflict"
    events, quality = normalize_api_pbp(raw)
    if not quality['confirmed_games']:
        return None, "no_confirmed_game_coordinates"
    from .point_history import _orient_alternative_point_events
    events = _orient_alternative_point_events(events, source_first_is_fonbet_a=first_is_a)
    valid_games = set()
    for row in events:
        if row.get('event_kind') != 'game_result' or not 1 <= row['set'] <= len(official_scores):
            continue
        after = tuple(row['game_score']); final = official_scores[row['set']-1]
        if all(value <= bound for value, bound in zip(after, final)) and (not finished_set(*after) or after == final):
            valid_games.add((row['set'], row['game']))
    events = [row for row in events if (row['set'], row['game']) in valid_games]
    quality['confirmed_games'] = sum(row.get('event_kind') == 'game_result' for row in events)
    quality['confirmed_points'] = sum(row['confirmed'] and row.get('event_kind') != 'game_result' for row in events)
    quality['event_count'] = len(events)
    if not quality['confirmed_games']:
        return None, 'game_history_conflicts_with_final_sets'
    point_hash = hashlib.sha256(json.dumps(raw['pointbypoint'], sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
    document = session.query(TennisPointDocument).filter(
        TennisPointDocument.source == 'api_tennis', TennisPointDocument.source_match_id == provider.source_match_id,
        TennisPointDocument.content_hash == point_hash, TennisPointDocument.available_at <= as_of,
    ).order_by(TennisPointDocument.available_at, TennisPointDocument.id).first()
    raw_reference = observation.source_document_ref or (observation.payload or {}).get('raw_object_key')
    evidence = {"method": LINK_METHOD, "fonbet_event_id": str(event.id), "source": 'api_tennis',
        "source_match_id": provider.source_match_id, "canonical_match_key": canonical.canonical_key,
        "canonical_match_id": str(canonical.id), "source_first_is_fonbet_a": first_is_a,
        "normalized_to_fonbet": True, "canonical_identity_proofs": identities,
        "provider_player_ids": [first, second], "provider_alias_ids": [str(mapping[value].id) for value in (first, second)],
        "provider_alias_available_at": [provider_alias_available[value].isoformat() for value in (first, second)],
        "source_observation_id": str(observation.id), "source_observation_available_at": observation.available_at.isoformat(),
        "source_content_hash": point_hash, "source_raw_reference": raw_reference or f"db:ext_tennis_match_observations:{observation.id}"}
    semantic = {"event_id": str(event.id), "canonical_match_key": canonical.canonical_key,
                "raw_hash": point_hash, "identities": identities,
                "provider_alias_ids": evidence['provider_alias_ids'], "events": events}
    digest = hashlib.sha256(json.dumps(semantic, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {"event_id": event.id, "source_match_id": provider.source_match_id,
        "document_id": document.id if document else None, "raw_hash": point_hash, "raw_reference": raw_reference,
        "retrieved_at": observation.available_at, "events": events, "quality": quality,
        "evidence": {**evidence, "semantic_digest": digest}, "semantic_digest": digest}, 'ready'


def append_api_link(session, plan):
    source_id, event_id = plan['source_match_id'], plan['event_id']
    session.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'), {'key': 'api-pbp-link:'+source_id})
    other = session.query(TennisPointHistoryVersion).filter(
        TennisPointHistoryVersion.source_match_id == source_id,
        TennisPointHistoryVersion.fonbet_event_id.is_not(None),
        TennisPointHistoryVersion.fonbet_event_id != event_id,
        TennisPointHistoryVersion.parser_version.in_((PARSER, 'api_tennis_pbp_link_v1'))).first()
    if other:
        return None, 'source_already_linked_to_other_event'
    existing = session.query(TennisPointHistoryVersion).filter(
        TennisPointHistoryVersion.fonbet_event_id == event_id, TennisPointHistoryVersion.parser_version == PARSER,
        TennisPointHistoryVersion.source_match_id == source_id,
        TennisPointHistoryVersion.quality_json['link']['semantic_digest'].astext == plan['semantic_digest']).first()
    if existing:
        return existing, 'existing'
    now = datetime.now(timezone.utc)
    document_id = plan['document_id']
    if document_id is None:
        document = session.query(TennisPointDocument).filter_by(
            source='api_tennis', source_match_id=source_id, content_hash=plan['raw_hash'], parser_version='api_tennis_pbp_v2').first()
        if document is None:
            document = TennisPointDocument(source='api_tennis', source_match_id=source_id,
                source_url='https://api.api-tennis.com/tennis/?method=get_fixtures',
                content_hash=plan['raw_hash'], archived_path=plan['raw_reference'], parser_version='api_tennis_pbp_v2',
                retrieved_at=plan['retrieved_at'], parsed_at=now, available_at=now, availability_status='known',
                retrospective=True, parse_status='partial', quality_json=plan['quality'])
            session.add(document);session.flush()
        document_id = document.id
    version = TennisPointHistoryVersion(document_id=document_id, source_match_id=source_id, fonbet_event_id=event_id,
        parser_version=PARSER, retrieved_at=plan['retrieved_at'], parsed_at=now, available_at=now,
        availability_status='known', events_json=plan['events'],
        quality_json={**plan['quality'], 'link': {**plan['evidence'], 'linked_at': now.isoformat()}}, retrospective=True)
    session.add(version);session.flush()
    return version, 'appended'


def link_api_history(session, event, *, as_of):
    plan, _reason = plan_api_link(session, event, as_of=as_of)
    if plan is None:
        return None
    version, status = append_api_link(session, plan)
    if version is None:
        return None
    return {"status": "partial", "source": "api_tennis", "source_match_id": plan['source_match_id'],
            "documents_saved": 0, "confirmed_points": plan['quality']['confirmed_points'],
            "confirmed_games": plan['quality']['confirmed_games'], "retrospective": True,
            "cached": status == 'existing', "available_at": version.available_at,
            "quality": version.quality_json, "complete": False, "linked_version_created": status == 'appended'}


def candidate_event_ids(session, as_of):
    rows = session.execute(text("""
        WITH identities AS (
          SELECT DISTINCT ON (participant_id,source) participant_id,source,source_player_id,status
          FROM tennis_player_identity_links WHERE available_at <= :cutoff
          ORDER BY participant_id,source,available_at DESC,id DESC
        ), mapped AS (
          SELECT i.participant_id FROM identities i JOIN ext_tennis_canonical_player_aliases a
            ON a.source=i.source AND a.source_player_id=i.source_player_id
          WHERE i.status='confirmed' AND a.status='confirmed' AND a.created_at <= :cutoff
          GROUP BY i.participant_id HAVING count(DISTINCT a.canonical_player_id)=1
        )
        SELECT e.id FROM events e JOIN mapped a ON a.participant_id=e.participant_a_id
          JOIN mapped b ON b.participant_id=e.participant_b_id
        WHERE e.source='fonbet' AND e.sport_code='tennis' AND e.status='finished'
          AND e.source_event_id ~ '^[0-9]+$' AND e.created_at <= :cutoff ORDER BY e.id
    """), {'cutoff': as_of}).all()
    return [row.id for row in rows]
