"""Evidence-safe primitives for tennis doubles events."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


DISCIPLINES = {"mens_doubles", "womens_doubles", "mixed_doubles"}


def parse_doubles_side(value: str | None) -> tuple[str, str] | None:
    """Split a displayed team into two search candidates, never identities."""
    if not isinstance(value, str) or value.count("/") != 1:
        return None
    left, right = (part.strip() for part in value.split("/", 1))
    if not left or not right or left.casefold() == right.casefold():
        return None
    return left, right


def classify_discipline(*source_facts: Any) -> str:
    """Classify only from explicit provider facts; names are deliberately ignored."""
    text = " ".join(str(value or "") for value in source_facts).casefold()
    if not any(token in text for token in ("doubles", "double", "пары", "парный")):
        return "unknown"
    if any(token in text for token in ("mixed", "мixed", "смешан")):
        return "mixed_doubles"
    if any(token in text for token in ("women", "female", "wta", "жен")):
        return "womens_doubles"
    if any(token in text for token in ("men", "male", "atp", "муж")):
        return "mens_doubles"
    return "unknown_doubles"


def classify_doubles_discipline(*source_facts: Any) -> str:
    """Map unclassifiable doubles source facts to the doubles-specific unknown."""
    value = classify_discipline(*source_facts)
    return value if value in DISCIPLINES else "unknown_doubles"


def event_discipline_source_facts(event: Any, competition_name: str | None = None) -> tuple[str, ...]:
    """Return stored source facts without assuming Event has a tournament field."""
    metadata = event.metadata_json if isinstance(getattr(event, "metadata_json", None), dict) else {}
    official = metadata.get("fonbet_official_result")
    if not isinstance(official, dict):
        official = {}
    keys = ("discipline", "event_type", "draw_type", "gender", "category")
    return (str(competition_name or ""), *(str(official.get(key) or metadata.get(key) or "") for key in keys))


def canonical_pair(player_ids: list[str] | tuple[str, str]) -> tuple[str, str]:
    """Return order-independent pair identity after two confirmed IDs exist."""
    if len(player_ids) != 2 or not all(isinstance(item, str) and item.strip() for item in player_ids):
        raise ValueError("A doubles pair requires two confirmed canonical player IDs")
    if player_ids[0] == player_ids[1]:
        raise ValueError("A doubles pair cannot contain the same player twice")
    return tuple(sorted(player_ids))  # type: ignore[return-value]


def validate_scoring_rules(rules: dict[str, Any]) -> dict[str, Any]:
    """Validate an explicitly sourced scoring format without inferring defaults."""
    required = {"version", "sets_to_win", "advantage", "set_tiebreak", "match_tiebreak"}
    if not isinstance(rules, dict) or not required.issubset(rules):
        raise ValueError("Scoring format is incomplete")
    if not str(rules["version"]).strip():
        raise ValueError("Scoring format needs a source/version")
    if rules["sets_to_win"] not in (1, 2, 3):
        raise ValueError("Unsupported sets_to_win")
    if rules["advantage"] not in (True, False):
        raise ValueError("advantage must be explicitly true or false")
    for field in ("set_tiebreak", "match_tiebreak"):
        value = rules[field]
        if value is not None and (not isinstance(value, dict) or not isinstance(value.get("target"), int)):
            raise ValueError(f"{field} must be null or include an integer target")
    return dict(rules)


def symmetric_pair_features(values: list[float | None]) -> dict[str, float | int | None]:
    """Order-invariant mean/range used by the doubles feature contract."""
    if len(values) != 2:
        raise ValueError("Exactly two partner values are required")
    observed = [float(value) for value in values if value is not None]
    if not observed:
        return {"mean": None, "spread": None, "observed_count": 0}
    return {"mean": sum(observed) / len(observed),
            "spread": max(observed) - min(observed) if len(observed) == 2 else None,
            "observed_count": len(observed)}


def confirm_roster_player(roster: dict[str, Any], participant_id: str,
                          canonical_player_id: str) -> tuple[dict[str, Any], int, bool]:
    """Return a copied roster with one confirmed identity and explicit count."""
    updated = deepcopy(roster)
    matched = 0
    for side in ("A", "B"):
        for player in updated.get(side, []):
            if str(player.get("participant_id") or "") == str(participant_id):
                player["canonical_player_id"] = str(canonical_player_id)
                player["identity_confirmed"] = True
                matched += 1
    players = [player for side in ("A", "B") for player in updated.get(side, [])]
    count = sum(bool(player.get("canonical_player_id") and player.get("identity_confirmed"))
                for player in players)
    return updated, count, matched == 1


def confirmed_provider_roster(roster: dict[str, Any], provider_id_for_participant) -> dict[str, tuple[str, str]] | None:
    """Resolve all four roster identities without inferring team membership."""
    if not isinstance(roster, dict):
        return None
    resolved: dict[str, tuple[str, str]] = {}
    seen: set[str] = set()
    for side in ("A", "B"):
        players = roster.get(side)
        if not isinstance(players, list) or len(players) != 2:
            return None
        ids: list[str] = []
        for player in players:
            if not isinstance(player, dict) or not player.get("identity_confirmed"):
                return None
            participant_id = player.get("participant_id")
            canonical_id = player.get("canonical_player_id")
            if not participant_id or not canonical_id:
                return None
            provider_id = provider_id_for_participant(str(participant_id))
            if not provider_id or str(provider_id) in seen:
                return None
            seen.add(str(provider_id))
            ids.append(str(provider_id))
        resolved[side] = (ids[0], ids[1])
    return resolved if len(seen) == 4 else None
