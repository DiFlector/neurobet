"""Merge raw tennis results that describe the same match.

Sackmann and TML rows that share player ids collapse first. Other files join
by surname, initial, gender and surface when the dates sit inside 14 days and
the scores do not disagree. An empty score joins only when one such match exists.
"""

from datetime import datetime
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from tennis_adapter.external_names import surname_and_initial

SACKMANN_SOURCES = {"tml", "sackmann_sqlite"}
ODDS_SOURCES = ("atp_odds", "ten")
SERVE_SOURCES = ("sackmann_sqlite", "tml", "ten")


@dataclass
class RawMatch:
    source: str
    source_match_id: str
    played_at: datetime
    available_at: datetime
    tour_level: str
    tournament: str
    surface: str
    best_of: int
    round_name: str
    gender: str
    winner_name: str
    loser_name: str
    winner_player_id: Optional[str] = None
    loser_player_id: Optional[str] = None
    winner_rank: Optional[int] = None
    loser_rank: Optional[int] = None
    score: str = ""
    odds_winner: Optional[float] = None
    odds_loser: Optional[float] = None
    serve_won_winner: Optional[float] = None
    serve_won_loser: Optional[float] = None
    return_won_winner: Optional[float] = None
    return_won_loser: Optional[float] = None


def normalize_surface(value: str) -> str:
    text = (value or "").strip().lower()
    for name in ("hard", "clay", "grass", "carpet"):
        if name in text:
            return name
    return "unknown"


def normalize_score(value: str) -> str:
    text = (value or "").lower()
    text = text.replace("retired", "").replace("ret.", "").replace("walkover", "")
    return re.sub(r"[^0-9\-]", "", text)


def coarse_level(value: str) -> str:
    text = (value or "").strip().lower()
    if "challenger" in text:
        return "challenger"
    if "itf" in text or "future" in text or text in {"f", "15", "25"}:
        return "itf"
    if any(token in text for token in ("atp", "wta", "grand", "masters", "slam", "premier", "international")):
        return "atp_wta"
    return "other"


def side_key(name: str) -> str:
    surname, initial = surname_and_initial(name or "")
    if not surname:
        return ""
    return f"{surname}|{initial}"


def _aware(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        from datetime import timezone

        return moment.replace(tzinfo=timezone.utc)
    return moment


def _span_ok(cluster: Sequence[RawMatch], match: RawMatch) -> bool:
    dates = [_aware(item.played_at) for item in cluster]
    dates.append(_aware(match.played_at))
    return (max(dates) - min(dates)).days <= 14


def set_counts(value: str):
    """Winner/loser set tally. A single small pair is already a set score."""
    pairs = re.findall(r"(\d+)\s*-\s*(\d+)", value or "")
    if not pairs:
        return None
    numbers = [(int(left), int(right)) for left, right in pairs]
    if len(numbers) == 1 and numbers[0][0] <= 5 and numbers[0][1] <= 5:
        return numbers[0]
    won = sum(1 for left, right in numbers if left > right)
    lost = sum(1 for left, right in numbers if right > left)
    if won + lost == 0:
        return None
    return won, lost


def _scores_ok(cluster: Sequence[RawMatch], match: RawMatch) -> bool:
    incoming = set_counts(match.score)
    present = [set_counts(item.score) for item in cluster]
    present = [item for item in present if item]
    if incoming is None and not present:
        days = {_aware(item.played_at).date() for item in cluster}
        days.add(_aware(match.played_at).date())
        return len(days) == 1
    if incoming is None or not present:
        return True
    for item in present:
        if incoming != item and incoming != (item[1], item[0]):
            return False
    return True


def _surface_ok(cluster: Sequence[RawMatch], match: RawMatch) -> bool:
    known = {item.surface for item in cluster if item.surface != "unknown"}
    if match.surface != "unknown":
        known.add(match.surface)
    return len(known) <= 1


def _gender_ok(cluster: Sequence[RawMatch], match: RawMatch) -> bool:
    known = {item.gender for item in cluster if item.gender in ("m", "w")}
    if match.gender in ("m", "w"):
        known.add(match.gender)
    return len(known) <= 1


def can_join(cluster: Sequence[RawMatch], match: RawMatch) -> bool:
    return _span_ok(cluster, match) and _scores_ok(cluster, match) and _surface_ok(cluster, match) and _gender_ok(cluster, match)


def pair_keys(match: RawMatch) -> List[tuple]:
    left, right = side_key(match.winner_name), side_key(match.loser_name)
    if not left or not right:
        return []
    ordered = tuple(sorted((left, right)))
    keys = [ordered + ("",)]
    if match.gender in ("m", "w"):
        keys.append(ordered + (match.gender,))
    return keys


def id_key(match: RawMatch):
    if match.source not in SACKMANN_SOURCES:
        return None
    if not match.winner_player_id or not match.loser_player_id:
        return None
    return tuple(sorted((str(match.winner_player_id), str(match.loser_player_id))))


def _place(clusters: List[List[RawMatch]], match: RawMatch, require_unique: bool) -> None:
    options = [cluster for cluster in clusters if can_join(cluster, match)]
    if len(options) == 1 or (options and not require_unique):
        options[0].append(match)
        return
    clusters.append([match])


def _cluster_bucket(matches: Sequence[RawMatch]) -> List[List[RawMatch]]:
    scored = [item for item in matches if normalize_score(item.score)]
    empty = [item for item in matches if not normalize_score(item.score)]
    clusters: List[List[RawMatch]] = []
    for match in sorted(scored, key=lambda item: _aware(item.played_at)):
        _place(clusters, match, require_unique=True)
    for match in sorted(empty, key=lambda item: _aware(item.played_at)):
        _place(clusters, match, require_unique=True)
    return clusters


def _merge_by_name(clusters: List[List[RawMatch]]) -> List[List[RawMatch]]:
    """Join Sackmann copies whose player ids differ but the names, week and score agree.

    The date span of a merged group stays inside 14 days, so a later match of the
    same pair does not stick to the previous one.
    """
    named: Dict[tuple, List[List[RawMatch]]] = {}
    alone: List[List[RawMatch]] = []
    for cluster in clusters:
        key = None
        for member in cluster:
            keys = pair_keys(member)
            if keys:
                key = keys[0]
                break
        if key is None:
            alone.append(cluster)
            continue
        named.setdefault(key, []).append(cluster)
    merged = list(alone)
    for group in named.values():
        group.sort(key=lambda cluster: min(_aware(member.played_at) for member in cluster))
        current = group[0]
        for other in group[1:]:
            if all(can_join(current, member) for member in other):
                current.extend(other)
                continue
            merged.append(current)
            current = other
        merged.append(current)
    return merged


def cluster_matches(matches: Sequence[RawMatch]) -> List[List[RawMatch]]:
    """Return groups of raw rows that are one match."""
    buckets: Dict[tuple, List[RawMatch]] = {}
    loose: List[RawMatch] = []
    for match in matches:
        match.surface = normalize_surface(match.surface)
        key = id_key(match)
        if key is None:
            loose.append(match)
            continue
        buckets.setdefault(("id",) + key, []).append(match)
    clusters: List[List[RawMatch]] = []
    for grouped in buckets.values():
        clusters.extend(_cluster_bucket(grouped))
    index: Dict[tuple, List[List[RawMatch]]] = {}
    for cluster in clusters:
        seen = set()
        for member in cluster:
            for key in pair_keys(member):
                if key in seen:
                    continue
                seen.add(key)
                index.setdefault(key, []).append(cluster)
    for match in sorted(loose, key=lambda item: _aware(item.played_at)):
        found = []
        seen_ids = set()
        for key in pair_keys(match):
            for cluster in index.get(key, []):
                marker = id(cluster)
                if marker in seen_ids:
                    continue
                seen_ids.add(marker)
                if can_join(cluster, match):
                    found.append(cluster)
        if len(found) == 1:
            found[0].append(match)
            continue
        fresh = [match]
        clusters.append(fresh)
        for key in pair_keys(match):
            index.setdefault(key, []).append(fresh)
    return _merge_by_name(clusters)


def pick_odds(members: Sequence[RawMatch]):
    for source in ODDS_SOURCES:
        for member in members:
            if member.source == source and member.odds_winner and member.odds_loser:
                if member.odds_winner > 1 and member.odds_loser > 1:
                    return member.odds_winner, member.odds_loser, source
    return None, None, None


def pick_serve(members: Sequence[RawMatch]):
    for source in SERVE_SOURCES:
        for member in members:
            if member.source != source:
                continue
            if member.serve_won_winner is None and member.serve_won_loser is None:
                continue
            return member, source
    return None, None
