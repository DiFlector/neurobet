"""Durable collectors for external tennis sources.

External source timestamps describe when Neurobet fetched a document. They are
never backfilled to the match start time and are not live observations.
"""

from __future__ import annotations

import asyncio
import csv
import gzip
import hashlib
import io
import json
import logging
import os
import re
import shutil
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy import create_engine, select, text
from sqlalchemy.pool import NullPool

from db.connection import get_database_url

logger = logging.getLogger("research.external_tennis_sources")
UTC = timezone.utc
API_TENNIS_URL = "https://api.api-tennis.com/tennis/"
LIVE_TENNIS_BASE = "https://api.livetennisapi.com/api/public/v1"
LIVESCORES_LIVE_TENNIS_URL = "https://prod-public-api.livescore.com/v1/api/app/live/tennis/0.00"
MAX_PAYLOAD_BYTES = 32 * 1024 * 1024
ARCHIVE_STOP_AT = datetime(2026, 11, 7, tzinfo=UTC)
TML_PARSER_VERSION = "tml_csv_v1"
TML_HISTORICAL_START_YEAR = {"atp": 1967, "wta": 1990, "challenger": 1978}
TML_HISTORICAL_TOURS = ("atp", "wta", "challenger")
TENNIS_EXPLORER_PROFILE_DAILY_BUDGET_DEFAULT = 20
API_TENNIS_OFFLINE_BATCHES_PER_TICK_DEFAULT = 5


def _api_tennis_offline_batches_per_tick() -> int:
    """Bound local archive replay work while accelerating the expiry-bound import."""
    raw = os.getenv(
        "API_TENNIS_OFFLINE_BATCHES_PER_TICK",
        str(API_TENNIS_OFFLINE_BATCHES_PER_TICK_DEFAULT),
    )
    try:
        return min(5, max(1, int(raw)))
    except (TypeError, ValueError):
        return API_TENNIS_OFFLINE_BATCHES_PER_TICK_DEFAULT


def _tennis_explorer_profile_daily_budget() -> int:
    """Keep ITF profile enrichment modest and explicitly configurable."""
    raw = os.getenv(
        "TENNIS_EXPLORER_PROFILE_DAILY_BUDGET",
        str(TENNIS_EXPLORER_PROFILE_DAILY_BUDGET_DEFAULT),
    )
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def _tml_historical_partitions(current_year: int) -> list[tuple[int, str]]:
    """Return available annual partitions newest-first, excluding the live season."""
    return [
        (year, tour)
        for year in range(current_year - 1, min(TML_HISTORICAL_START_YEAR.values()) - 1, -1)
        for tour in TML_HISTORICAL_TOURS
        if year >= TML_HISTORICAL_START_YEAR[tour]
    ]


def _tml_request_headers(prior: dict[str, Any], parser_version: str = TML_PARSER_VERSION) -> dict[str, str]:
    """Use validators only when the cached raw body was parsed by this version."""
    if (prior.get("status") != 200 or prior.get("parser_version") != parser_version
            or not prior.get("content_sha256") or not prior.get("key")):
        return {}
    if prior.get("etag"):
        return {"If-None-Match": prior["etag"]}
    if prior.get("last_modified"):
        return {"If-Modified-Since": prior["last_modified"]}
    return {}


def _tennis_explorer_profile_needs_fetch(prior: dict[str, Any], now: datetime,
                                         parser_version: str) -> bool:
    """Refetch when a profile is stale or stored output used an older parser."""
    if prior.get("parser_version") != parser_version:
        return True
    checked_at = prior.get("checked_at") or prior.get("observed_at")
    if prior.get("status") != 200 or not checked_at:
        return True
    try:
        checked = datetime.fromisoformat(str(checked_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return True
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=UTC)
    return now - checked >= timedelta(days=7)


def _tennis_explorer_profile_request_headers(prior: dict[str, Any],
                                             parser_version: str) -> dict[str, str]:
    """Use HTTP validators only when a 304 can safely reuse current parsing."""
    if prior.get("parser_version") != parser_version:
        return {}
    if prior.get("etag"):
        return {"If-None-Match": prior["etag"]}
    if prior.get("last_modified"):
        return {"If-Modified-Since": prior["last_modified"]}
    return {}


def _tennis_explorer_archived_profile_body(prior: dict[str, Any], storage) -> bytes | None:
    """Return a hash-verified cached body so parser upgrades do not spend HTTP quota."""
    key = prior.get("key")
    expected_hash = str(prior.get("content_sha256") or "").lower()
    if (not isinstance(key, str) or not key.startswith("snapshots/external-tennis/")
            or not key.endswith(".json.gz")
            or not re.fullmatch(r"[a-f0-9]{64}", expected_hash)):
        return None
    body = storage.read_external_raw_bytes(str(key))
    if body is None or hashlib.sha256(body).hexdigest() != expected_hash:
        return None
    return body


def _prioritize_tennis_explorer_profiles(candidates, active_names: set[str]):
    """Spend the bounded ITF profile budget first on current Fonbet players.

    This only changes fetch order. It does not confirm a source identity or
    alter canonical linking; all profile facts still pass the normal matching
    and quality checks before they can be published.
    """
    from tennis_adapter.external_names import fold, surname_and_initial, transliterate

    def name_key(name: str) -> str:
        return " ".join(transliterate(fold(name)).split())

    active_keys = {
        surname_and_initial(name)
        for name in active_names
        if name and surname_and_initial(name)[0] and surname_and_initial(name)[1]
    }
    active_exact_names = {name_key(name) for name in active_names if name_key(name)}

    def priority(row):
        names = row.get("display_names") or [row.get("display_name")]
        exact_match = any(name_key(str(name)) in active_exact_names for name in names if name)
        surname_initial_match = any(
            surname_and_initial(str(name or "")) in active_keys for name in names if name
        )
        tier = 0 if exact_match else 1 if surname_initial_match else 2
        return (tier, -(row.get("latest_played_at").timestamp()
                                      if row.get("latest_played_at") else 0), str(row.get("player_id") or ""))

    return sorted(candidates, key=priority)


def _as_int(value):
    try:
        return int(float(value)) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _tennis_abstract_imported_observations(profile_states) -> int:
    """Summarize durable per-profile imports, not only the last worker cycle."""
    total = 0
    for value in profile_states:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                continue
        if not isinstance(value, dict) or value.get("status") != 200:
            continue
        try:
            total += max(0, int(value.get("normalized_matches") or 0))
        except (TypeError, ValueError):
            continue
    return total


def _engine():
    return create_engine(get_database_url(), poolclass=NullPool, pool_pre_ping=True)


def _secret(name: str) -> str:
    # Do not log or echo the value. Secrets are read from the server environment.
    return os.getenv(name, "").strip()


def _api_tennis_daily_budget() -> int:
    try:
        return min(6000, max(1, int(os.getenv("API_TENNIS_DAILY_BUDGET", "6000"))))
    except ValueError:
        return 6000


def _normalize_tour(event: dict[str, Any]) -> str:
    value = str(event.get("event_type_type") or "").lower()
    if "itf" in value or "future" in value:
        return "itf"
    if "challenger" in value:
        return "challenger"
    if "wta" in value or "women" in value or "female" in value:
        return "wta"
    if "atp" in value or "men" in value or "male" in value:
        return "atp"
    return "unknown"


def _point_completeness(event: dict[str, Any]) -> dict[str, Any]:
    games = event.get("pointbypoint") or []
    points = sum(len(g.get("points") or []) for g in games if isinstance(g, dict))
    return {
        "point_games": len(games),
        "point_rows": points,
        "has_point_rows": points > 0,
        "has_statistics": bool(event.get("statistics")),
        "completeness": "partial_or_unknown" if points else "none",
        "source_declares_complete": None,
    }


def _livescore_catalog_summary(payload: Any) -> dict[str, Any]:
    """Validate and summarize the unofficial LiveScore discovery catalog."""
    if not isinstance(payload, dict) or not isinstance(payload.get("Stages"), list):
        raise ValueError("invalid_livescore_catalog")
    count = 0
    ids = set()
    tours = set()
    for stage in payload["Stages"]:
        if not isinstance(stage, dict):
            continue
        tournament = str(stage.get("Snm") or "").strip()
        for event in stage.get("Events") or []:
            if not isinstance(event, dict):
                continue
            count += 1
            if event.get("Eid") is not None:
                ids.add(str(event["Eid"]))
            if tournament:
                tours.add(tournament)
    return {"match_count": count, "identified_matches": len(ids),
            "tournaments": sorted(tours)[:100], "catalog_valid": True}


def _live_tennis_player_candidate(payload: Any, full_name: str) -> tuple[dict[str, Any] | None, str]:
    """Accept only one exact, non-team roster name from the free player search."""
    from tennis_adapter.external_names import fold

    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return None, "INVALID_PLAYER_SEARCH_RESPONSE"
    wanted = fold(full_name)
    matches = [row for row in rows if isinstance(row, dict)
               and fold(str(row.get("name") or row.get("full_name") or "")) == wanted
               and row.get("is_doubles_team") is not True and row.get("id") is not None]
    by_id = {str(row["id"]): row for row in matches}
    if not by_id:
        return None, "PLAYER_NOT_FOUND"
    if len(by_id) != 1:
        return None, "PLAYER_SEARCH_AMBIGUOUS"
    return next(iter(by_id.values())), "MATCHED"


def _live_tennis_player_profile(payload: Any, *, expected_id: str,
                                expected_name: str) -> dict[str, Any] | None:
    """Validate profile identity before exposing public bio/ranking fields."""
    from tennis_adapter.external_names import fold

    profile = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else payload
    if not isinstance(profile, dict) or profile.get("id") is None:
        return None
    if str(profile["id"]) != str(expected_id):
        return None
    if fold(str(profile.get("name") or profile.get("full_name") or "")) != fold(expected_name):
        return None
    if profile.get("is_doubles_team") is True:
        return None
    return {
        "player_id": str(profile["id"]),
        "name": str(profile.get("name") or profile.get("full_name") or ""),
        "tour": profile.get("tour"),
        "country": profile.get("country"),
        "ranking": _as_int(profile.get("ranking")),
        "data_completeness": profile.get("data_completeness"),
        "profile_version": _as_int(profile.get("profile_version")),
    }


def _api_tennis_player_stat_ratios(event: dict[str, Any]) -> dict[str, dict[str, float]]:
    """Extract only explicit match-level won/total ratios keyed by provider ID."""
    ratios: dict[str, dict[str, float]] = {}
    for row in event.get("statistics") or []:
        if not isinstance(row, dict) or str(row.get("stat_period") or "").casefold() != "match":
            continue
        name = str(row.get("stat_name") or "").strip().casefold()
        field = {"service points won": "serve_won", "return points won": "return_won"}.get(name)
        if not field or row.get("player_key") is None:
            continue
        try:
            won, total = float(row.get("stat_won")), float(row.get("stat_total"))
        except (TypeError, ValueError):
            continue
        if total <= 0 or won < 0 or won > total:
            continue
        ratios.setdefault(str(row["player_key"]), {})[field] = won / total
    return ratios


def _api_tennis_winner_first_score(event: dict[str, Any]) -> str:
    """Build canonical game scores only from a complete, winner-consistent set list."""
    winner = str(event.get("event_winner") or "").strip().casefold()
    if winner not in {"first player", "second player"}:
        return ""
    raw_sets = event.get("scores")
    if not isinstance(raw_sets, list) or len(raw_sets) < 2:
        return ""
    parsed: list[tuple[int, int, int]] = []
    for position, row in enumerate(raw_sets):
        if not isinstance(row, dict):
            return ""
        first = _as_int(row.get("score_first"))
        second = _as_int(row.get("score_second"))
        set_no = _as_int(row.get("score_set"))
        if first is None or second is None or first < 0 or second < 0 or first > 7 or second > 7 or first == second:
            return ""
        parsed.append((set_no if set_no is not None else position + 1, first, second))
    parsed.sort(key=lambda row: row[0])
    if len({row[0] for row in parsed}) != len(parsed):
        return ""
    first_wins = sum(first > second for _, first, second in parsed)
    second_wins = sum(second > first for _, first, second in parsed)
    first_is_winner = winner == "first player"
    if (first_is_winner and first_wins <= second_wins) or (not first_is_winner and second_wins <= first_wins):
        return ""
    final = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", str(event.get("event_final_result") or ""))
    if final:
        result_first, result_second = int(final.group(1)), int(final.group(2))
        if (result_first, result_second) != (first_wins, second_wins):
            return ""
    if first_is_winner:
        return " ".join(f"{first}-{second}" for _, first, second in parsed)
    return " ".join(f"{second}-{first}" for _, first, second in parsed)


def _api_tennis_projection_item(event: dict[str, Any], available_at: datetime) -> dict[str, Any] | None:
    """Build a strict singles result candidate for canonical history projection."""
    status = str(event.get("event_status") or "").casefold()
    winner = str(event.get("event_winner") or "").casefold()
    event_type = str(event.get("event_type_type") or "")
    if (status not in {"finished", "ended", "completed"}
            or winner not in {"first player", "second player"}
            or "double" in event_type.casefold()):
        return None
    first_name = str(event.get("event_first_player") or "").strip()
    second_name = str(event.get("event_second_player") or "").strip()
    first_id, second_id = event.get("first_player_key"), event.get("second_player_key")
    if not first_name or not second_name or first_id is None or second_id is None:
        return None
    score = _api_tennis_winner_first_score(event)
    if not score:
        return None
    first_winner = winner == "first player"
    try:
        played_at = datetime.fromisoformat(
            f"{event.get('event_date')}T{event.get('event_time') or '00:00'}"
        ).replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None
    tier = _normalize_tour(event)
    gender = (
        "w" if tier == "wta" or any(token in event_type.casefold() for token in ("women", "female"))
        else "m" if tier in {"atp", "challenger"} or any(token in event_type.casefold() for token in ("men", "male"))
        else "unknown"
    )
    return {
        "source_match_id": str(event.get("event_key") or ""),
        "played_at": played_at,
        "available_at": available_at,
        "tour_level": tier,
        "tournament": str(event.get("tournament_name") or "")[:255],
        "round_name": str(event.get("tournament_round") or "")[:32],
        "gender": gender,
        "winner_name": first_name if first_winner else second_name,
        "loser_name": second_name if first_winner else first_name,
        "winner_player_id": str(first_id if first_winner else second_id),
        "loser_player_id": str(second_id if first_winner else first_id),
        "score": score,
    }


def _normalize_api_tennis_pbp(event: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Normalize explicit API-Tennis point states without claiming a complete feed.

    The provider omits some terminal points in observed matches, so a populated
    pointbypoint array is not sufficient evidence that the sequence is complete.
    """
    from tennis_adapter.point_history import infer_point_winner

    output: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    games = event.get("pointbypoint") or []
    set_numbers: set[int] = set()
    for game in games:
        if not isinstance(game, dict):
            gaps.append({"reason": "invalid_game_row"})
            continue
        set_match = re.search(r"\d+", str(game.get("set_number") or ""))
        game_number = _positive_int(game.get("number_game"))
        if not set_match or game_number is None:
            gaps.append({"reason": "missing_set_or_game_number"})
            continue
        set_number = int(set_match.group())
        set_numbers.add(set_number)
        point_rows = game.get("points") or []
        score_pairs = [
            re.fullmatch(r"\s*(\d+)\s*[-:]\s*(\d+)\s*", str((row or {}).get("score") or ""))
            for row in point_rows if isinstance(row, dict)
        ] if isinstance(point_rows, list) else []
        tiebreak = (
            "tie" in str(game.get("set_number") or "").casefold()
            or "tie" in str(game.get("score") or "").casefold()
            or any(pair and any(int(value) not in {0, 15, 30, 40} for value in pair.groups()) for pair in score_pairs)
        )
        server_name = str(game.get("player_served") or "").casefold()
        server = 0 if server_name in {"first player", "1"} else 1 if server_name in {"second player", "2"} else None
        before: tuple[Any, Any] = (0, 0) if tiebreak else ("0", "0")
        if not isinstance(point_rows, list):
            gaps.append({"set": set_number, "game": game_number, "reason": "invalid_point_rows"})
            continue
        for index, point in enumerate(point_rows, start=1):
            match = re.fullmatch(r"\s*(\d+)\s*[-:]\s*(\d+)\s*", str((point or {}).get("score") or "")) if isinstance(point, dict) else None
            if not match:
                gaps.append({"set": set_number, "game": game_number, "point": index, "reason": "invalid_point_score"})
                continue
            if tiebreak:
                after: tuple[Any, Any] = (int(match.group(1)), int(match.group(2)))
                changed = [side for side in (0, 1) if after[side] == before[side] + 1 and after[1-side] == before[1-side]]
                winner = changed[0] if len(changed) == 1 else None
            else:
                after = (match.group(1), match.group(2))
                winner = infer_point_winner(before, after)
            output.append({
                "set": set_number, "game": game_number, "point_index": index,
                "state_before": list(before), "state_after": list(after), "point_score": list(after),
                "server": server, "winner": winner, "confirmed": winner is not None,
                "tie_break": tiebreak, "retrospective": True,
                "quality": "confirmed" if winner is not None else "ambiguous",
            })
            if winner is None:
                gaps.append({"set": set_number, "game": game_number, "point": index, "reason": "ambiguous_point_transition"})
            before = after
        # The feed exposes a game-level score but not a clear terminal-point
        # marker. Keep the uncertainty explicit rather than inventing a point.
        if not tiebreak:
            gaps.append({"set": set_number, "game": game_number, "reason": "terminal_point_not_explicit"})
    quality = {
        "provider": "api_tennis", "event_count": len(output),
        "confirmed_points": sum(bool(row["confirmed"]) for row in output),
        "ambiguous_points": sum(not row["confirmed"] for row in output),
        "set_numbers": sorted(set_numbers), "gaps": gaps,
        "complete": False, "source_declares_complete": None,
        "completeness": "partial_or_unknown" if output else "none",
    }
    return output, quality


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _fixture_events(payload: Any) -> list[dict[str, Any]]:
    # Observed provider empty response: {"success": 1}, with no result key.
    # A failed/malformed envelope is not the same thing as an empty interval.
    if not isinstance(payload, dict) or payload.get("success") != 1:
        raise ValueError("provider_failure_envelope")
    rows = payload.get("result", [])
    if not isinstance(rows, list):
        raise ValueError("provider_result_is_not_a_list")
    return rows


def _store_result(conn, event: dict[str, Any], available_at: datetime) -> None:
    """Add completed source results to the shared external-match read model."""
    status = str(event.get("event_status") or "").lower()
    winner = str(event.get("event_winner") or "").lower()
    if status not in {"finished", "ended", "completed"} or winner not in {"first player", "second player"}:
        return
    first = str(event.get("event_first_player") or "").strip()
    second = str(event.get("event_second_player") or "").strip()
    if not first or not second:
        return
    win_first = winner == "first player"
    first_id = event.get("first_player_key")
    second_id = event.get("second_player_key")
    played_date = str(event.get("event_date") or "")
    played_time = str(event.get("event_time") or "00:00")
    try:
        played_at = datetime.fromisoformat(f"{played_date}T{played_time}").replace(tzinfo=UTC)
    except ValueError:
        return
    tier = _normalize_tour(event)
    event_type = str(event.get("event_type_type") or "").casefold()
    gender = "w" if tier == "wta" or any(token in event_type for token in ("women", "female")) else "m" if tier == "atp" or any(token in event_type for token in ("men", "male")) else "unknown"
    ratios = _api_tennis_player_stat_ratios(event)
    winner_id = first_id if win_first else second_id
    loser_id = second_id if win_first else first_id
    winner_stats = ratios.get(str(winner_id), {}) if winner_id is not None else {}
    loser_stats = ratios.get(str(loser_id), {}) if loser_id is not None else {}
    conn.execute(text("""
      INSERT INTO ext_tennis_matches
        (id,source,source_match_id,played_at,available_at,tour_level,tournament,surface,best_of,round_name,
         gender,winner_name,loser_name,winner_player_id,loser_player_id,score,serve_won_winner,serve_won_loser,
         return_won_winner,return_won_loser,stats)
      VALUES (gen_random_uuid(),'api_tennis',:mid,:played,:available,:tier,:tournament,'unknown',3,:round,
              :gender,:winner,:loser,:winner_id,:loser_id,:score,:serve_won_winner,:serve_won_loser,
              :return_won_winner,:return_won_loser,CAST(:stats AS jsonb))
      ON CONFLICT (source,source_match_id) DO UPDATE SET
        played_at=coalesce(excluded.played_at,ext_tennis_matches.played_at),
        available_at=greatest(ext_tennis_matches.available_at,excluded.available_at),
        tour_level=CASE WHEN excluded.tour_level <> 'unknown' THEN excluded.tour_level ELSE ext_tennis_matches.tour_level END,
        tournament=coalesce(nullif(excluded.tournament,''),ext_tennis_matches.tournament),
        surface=CASE WHEN excluded.surface <> 'unknown' THEN excluded.surface ELSE ext_tennis_matches.surface END,
        best_of=coalesce(excluded.best_of,ext_tennis_matches.best_of),
        round_name=coalesce(nullif(excluded.round_name,''),ext_tennis_matches.round_name),
        gender=CASE WHEN excluded.gender <> 'unknown' THEN excluded.gender ELSE ext_tennis_matches.gender END,
        winner_name=coalesce(nullif(excluded.winner_name,''),ext_tennis_matches.winner_name),
        loser_name=coalesce(nullif(excluded.loser_name,''),ext_tennis_matches.loser_name),
        winner_player_id=coalesce(excluded.winner_player_id,ext_tennis_matches.winner_player_id),
        loser_player_id=coalesce(excluded.loser_player_id,ext_tennis_matches.loser_player_id),
        score=coalesce(nullif(excluded.score,''),ext_tennis_matches.score),
        stats=(ext_tennis_matches.stats || excluded.stats) || jsonb_build_object(
          'statistics',CASE WHEN jsonb_typeof(excluded.stats->'statistics')='array'
                                AND jsonb_array_length(excluded.stats->'statistics')>0
                           THEN excluded.stats->'statistics' ELSE ext_tennis_matches.stats->'statistics' END)
        ,serve_won_winner=coalesce(excluded.serve_won_winner,ext_tennis_matches.serve_won_winner)
        ,serve_won_loser=coalesce(excluded.serve_won_loser,ext_tennis_matches.serve_won_loser)
        ,return_won_winner=coalesce(excluded.return_won_winner,ext_tennis_matches.return_won_winner)
        ,return_won_loser=coalesce(excluded.return_won_loser,ext_tennis_matches.return_won_loser)
    """), {
        "mid": str(event.get("event_key"))[:128], "played": played_at, "available": available_at,
        "tier": tier, "tournament": str(event.get("tournament_name") or "")[:255],
        "round": str(event.get("tournament_round") or "")[:32], "gender": gender,
        "winner": first if win_first else second, "loser": second if win_first else first,
        "winner_id": str(first_id if win_first else second_id) if (first_id if win_first else second_id) is not None else None,
        "loser_id": str(second_id if win_first else first_id) if (second_id if win_first else first_id) is not None else None,
        "score": _api_tennis_winner_first_score(event)[:64],
        "serve_won_winner": winner_stats.get("serve_won"), "serve_won_loser": loser_stats.get("serve_won"),
        "return_won_winner": winner_stats.get("return_won"), "return_won_loser": loser_stats.get("return_won"),
        "stats": json.dumps({"event_type": event.get("event_type_type"),
                             "event_final_result": event.get("event_final_result"),
                             "statistics": event.get("statistics") or [],
                             "point_completeness": _point_completeness(event)}, ensure_ascii=False, default=str),
    })


class SourceArchive:
    """Single-flight archive runner with a DB checkpoint per requested interval."""

    def __init__(self, engine=None):
        self.engine = engine or _engine()

    def seed(self, today: date | None = None) -> int:
        """Seed date-sized partitions, newest year first, without deleting progress."""
        today = today or datetime.now(UTC).date()
        recent_start = today - timedelta(days=365)
        with self.engine.begin() as conn:
            cursor = conn.execute(text("""
                INSERT INTO external_tennis_source_state(source, state_key, state_json, updated_at)
                VALUES ('api_tennis', 'archive_seeded_through', :state, now())
                ON CONFLICT (source, state_key) DO NOTHING
                RETURNING state_json
            """), {"state": json.dumps({"through": today.isoformat()})}).first()
            if cursor is None:
                return 0
            rows = []
            day = today
            while day >= date(1968, 1, 1):
                priority = 0 if day >= recent_start else 1
                rows.append({"d": day.isoformat(), "p": priority})
                day -= timedelta(days=1)
            conn.execute(text("""
                INSERT INTO external_tennis_source_jobs
                  (source, interval_start, interval_end, event_type_key, priority, status, next_run_at, attempts)
                VALUES ('api_tennis', :d, :d, '', :p, 'queued', now(), 0)
                ON CONFLICT (source, interval_start, interval_end, event_type_key) DO NOTHING
            """), rows)
            return len(rows)

    def refresh_recent_window(self, today: date | None = None) -> int:
        """Recheck the most recent seven days once per UTC day for late corrections."""
        today = today or datetime.now(UTC).date()
        with self.engine.begin() as conn:
            state = conn.execute(text("SELECT state_json FROM external_tennis_source_state WHERE source='api_tennis' AND state_key='last_recent_refresh' FOR UPDATE")).scalar()
            if isinstance(state, dict) and state.get("day") == today.isoformat():
                return 0
            days = [(today - timedelta(days=offset)).isoformat() for offset in range(7)]
            conn.execute(text("""
              INSERT INTO external_tennis_source_jobs
                (source,interval_start,interval_end,event_type_key,priority,status,next_run_at,attempts)
              VALUES ('api_tennis',:d,:d,'',0,'queued',now(),0)
              ON CONFLICT(source,interval_start,interval_end,event_type_key) DO UPDATE SET
                priority=0,status='queued',next_run_at=now(),error_code=NULL,lease_until=NULL,updated_at=now()
              WHERE external_tennis_source_jobs.status='complete'
            """), [{"d": day} for day in days])
            conn.execute(text("""
              INSERT INTO external_tennis_source_state(source,state_key,state_json,updated_at)
              VALUES ('api_tennis','last_recent_refresh',CAST(:payload AS jsonb),now())
              ON CONFLICT(source,state_key) DO UPDATE SET state_json=excluded.state_json,updated_at=now()
            """), {"payload": json.dumps({"day": today.isoformat()})})
            return len(days)

    def claim(self) -> dict[str, Any] | None:
        with self.engine.begin() as conn:
            row = conn.execute(text("""
                WITH candidate AS (
                  SELECT id FROM external_tennis_source_jobs
                  WHERE source='api_tennis'
                    AND ((status IN ('queued','retry') AND next_run_at <= now())
                         OR (status='running' AND lease_until < now()))
                  ORDER BY priority, interval_start DESC
                  FOR UPDATE SKIP LOCKED LIMIT 1
                )
                UPDATE external_tennis_source_jobs j
                SET status='running', attempts=j.attempts+1,
                    lease_until=now()+interval '10 minutes', started_at=now(), updated_at=now()
                FROM candidate WHERE j.id=candidate.id RETURNING j.*
            """)).mappings().first()
            return dict(row) if row else None

    def finish(self, job_id, status: str, **fields):
        allowed = {"error_code", "http_status", "response_bytes", "content_sha256", "raw_object_key", "record_count", "point_records", "statistics_records", "metadata"}
        values = {key: value for key, value in fields.items() if key in allowed}
        sets = ["status=:status", "lease_until=NULL", "updated_at=now()", "finished_at=now()"]
        for key in values:
            sets.append(f"{key}=:{key}")
        if status == "retry":
            sets.extend(["next_run_at=now()+interval '5 minutes'", "finished_at=NULL"])
        with self.engine.begin() as conn:
            conn.execute(text(f"UPDATE external_tennis_source_jobs SET {', '.join(sets)} WHERE id=:id"), {"id": job_id, "status": status, **values})

    def persist_events(self, job: dict[str, Any], events: list[dict[str, Any]], raw_key: str, digest: str):
        observed_at = job.get("observed_at") or datetime.now(UTC)
        with self.engine.begin() as conn:
            for index, event in enumerate(events):
                event_id = str(event.get("event_key") or f"{job['interval_start']}:{index}")[:128]
                available_at = observed_at
                payload = {
                    "event": event,
                    "raw_object_key": raw_key,
                    "source_content_hash": digest,
                    "retrospective": True,
                    "point_completeness": _point_completeness(event),
                }
                canonical_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
                conn.execute(text("""
                    INSERT INTO ext_tennis_match_observations
                      (id, source, source_match_id, observation_hash, available_at, source_url,
                       source_content_hash, parser_version, payload, created_at)
                    SELECT gen_random_uuid(), 'api_tennis', :mid, :ohash, :available,
                            'https://api.api-tennis.com/tennis/?method=get_fixtures', :content_hash,
                            'api_tennis_v1', CAST(:payload AS jsonb), now()
                    WHERE NOT EXISTS (SELECT 1 FROM ext_tennis_match_observations
                                      WHERE source='api_tennis' AND source_match_id=:mid AND observation_hash=:ohash)
                """), {"mid": event_id, "ohash": canonical_hash, "available": available_at,
                      "content_hash": digest, "payload": json.dumps(payload, ensure_ascii=False, default=str)})
                normalized_points, point_quality = _normalize_api_tennis_pbp(event)
                if normalized_points:
                    point_hash = hashlib.sha256(json.dumps(event.get("pointbypoint"), sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
                    point_document_payload = {
                        "source": "api_tennis", "source_match_id": event_id,
                        "retrieved_at": available_at.isoformat(), "raw_object_key": raw_key,
                        "source_content_hash": digest, "parser_version": "api_tennis_pbp_v1",
                        "retrospective": True, "quality": point_quality,
                    }
                    conn.execute(text("""
                        WITH inserted_document AS (
                          INSERT INTO tennis_point_documents
                            (id,source,source_match_id,source_url,content_hash,archived_path,parser_version,
                             retrieved_at,parsed_at,available_at,availability_status,retrospective,parse_status,quality_json)
                          SELECT gen_random_uuid(),'api_tennis',:mid,
                            'https://api.api-tennis.com/tennis/?method=get_fixtures',:point_hash,:raw_key,
                            'api_tennis_pbp_v1',:available,:available,:available,'known',true,'partial',CAST(:quality AS jsonb)
                          WHERE NOT EXISTS (SELECT 1 FROM tennis_point_documents
                            WHERE source='api_tennis' AND source_match_id=:mid AND content_hash=:point_hash
                              AND parser_version='api_tennis_pbp_v1')
                          RETURNING id
                        )
                        INSERT INTO tennis_point_history_versions
                          (id,document_id,source_match_id,fonbet_event_id,parser_version,retrieved_at,parsed_at,
                           available_at,availability_status,events_json,quality_json,retrospective)
                        SELECT gen_random_uuid(),id,:mid,NULL,'api_tennis_pbp_v1',:available,:available,
                               :available,'known',CAST(:events AS jsonb),CAST(:quality AS jsonb),true
                        FROM inserted_document
                    """), {
                        "mid": event_id, "point_hash": point_hash, "raw_key": raw_key,
                        "available": available_at, "quality": json.dumps(point_quality, ensure_ascii=False),
                        "events": json.dumps(normalized_points, ensure_ascii=False),
                    })
                _store_result(conn, event, observed_at)
            conn.execute(text("""
                UPDATE external_tennis_source_jobs SET status='complete', lease_until=NULL,
                  http_status=200, response_bytes=:bytes, content_sha256=:digest, raw_object_key=:key,
                  record_count=:records, point_records=:points, statistics_records=:stats,
                  error_code=NULL, metadata=CAST(:meta AS jsonb), updated_at=now(), finished_at=now()
                WHERE id=:id
            """), {"id": job["id"], "bytes": job.get("response_bytes", 0), "digest": digest,
                  "key": raw_key, "records": len(events),
                  "points": sum(bool(_point_completeness(e)["has_point_rows"]) for e in events),
                  "stats": sum(bool(_point_completeness(e)["has_statistics"]) for e in events),
                  "meta": json.dumps({"tours": sorted({_normalize_tour(e) for e in events}), "observed_at": observed_at.isoformat(),
                                      "provider_empty_without_result": bool(job.get("provider_empty_without_result"))})})

        projection_items = [
            item for item in (_api_tennis_projection_item(event, observed_at) for event in events)
            if item is not None
        ]
        if projection_items:
            try:
                from sqlalchemy.orm import Session
                from .canonical_tennis_projection import project_api_tennis_results
                with Session(self.engine) as session:
                    projection = project_api_tennis_results(session, projection_items)
                    session.commit()
                projection_state = {
                    **projection, "status": "projected", "complete": False,
                    "usable_data_scope": "retrospective_partial_match_history",
                    "source_items": len(projection_items), "observed_at": observed_at.isoformat(),
                }
                with self.engine.begin() as conn:
                    conn.execute(text("""
                      INSERT INTO external_tennis_source_state(source,state_key,state_json,updated_at)
                      VALUES ('api_tennis','canonical_projection',CAST(:payload AS jsonb),now())
                      ON CONFLICT(source,state_key) DO UPDATE
                      SET state_json=excluded.state_json,updated_at=now()
                    """), {"payload": json.dumps(projection_state)})
                logger.info("API-Tennis canonical projection %s", projection)
            except Exception:
                # Raw observations and source-keyed normalized rows were already
                # committed. A later recent refresh or raw archive replay retries
                # projection; it must not mark a successfully archived response failed.
                logger.exception("API-Tennis canonical projection failed; raw archive is intact")
                try:
                    with self.engine.begin() as conn:
                        conn.execute(text("""
                          INSERT INTO external_tennis_source_state(source,state_key,state_json,updated_at)
                          VALUES ('api_tennis','canonical_projection',CAST(:payload AS jsonb),now())
                          ON CONFLICT(source,state_key) DO UPDATE
                          SET state_json=excluded.state_json,updated_at=now()
                        """), {"payload": json.dumps({
                            "status": "projection_error", "error": "CANONICAL_PROJECTION_FAILED",
                            "source_items": len(projection_items), "observed_at": observed_at.isoformat(),
                        })})
                except Exception:
                    logger.exception("Could not persist API-Tennis projection status")
        from .source_metrics import try_record_source_metric
        try_record_source_metric(self.engine, "api_tennis", published=1)


class ExternalTennisSourcesWorker:
    def __init__(self, *, storage=None, engine=None):
        self.archive = SourceArchive(engine)
        if storage is None:
            from .storage import ResearchStorage
            storage = ResearchStorage()
        self.storage = storage
        self._http = None
        self._last_api_request = 0.0
        self._live_calls = 0
        self._live_minute_started = 0.0
        self._event_types: list[dict[str, str]] = []
        self._storage_warning_day: str | None = None
        self._last_tennis_explorer_request = 0.0
        self._last_tennis_abstract_request = 0.0

    def _reserve_daily_call(self, source: str, budget: int, *, minute_budget: int | None = None) -> bool:
        """Reserve quota in PostgreSQL so a process restart cannot reset a provider budget."""
        now = datetime.now(UTC)
        day_key = now.date().isoformat()
        minute_key = now.strftime("%Y-%m-%dT%H:%M")
        state_key = "quota"
        with self.archive.engine.begin() as conn:
            row = conn.execute(text("SELECT state_json FROM external_tennis_source_state WHERE source=:source AND state_key=:key FOR UPDATE"),
                               {"source": source, "key": state_key}).scalar()
            state = row if isinstance(row, dict) else {}
            if state.get("day") != day_key:
                state = {"day": day_key, "daily_calls": 0, "minute": minute_key, "minute_calls": 0}
            if state.get("minute") != minute_key:
                state["minute"], state["minute_calls"] = minute_key, 0
            # Keep the configured limit next to its durable usage counter so
            # source status does not have to infer a provider budget.
            state["daily_limit"] = int(budget)
            state["minute_limit"] = int(minute_budget) if minute_budget is not None else None
            if int(state.get("daily_calls", 0)) >= budget:
                conn.execute(text("""
                  INSERT INTO external_tennis_source_state(source,state_key,state_json,updated_at)
                  VALUES (:source,:key,CAST(:payload AS jsonb),now())
                  ON CONFLICT(source,state_key) DO UPDATE SET state_json=excluded.state_json,updated_at=now()
                """), {"source": source, "key": state_key, "payload": json.dumps(state)})
                return False
            if minute_budget is not None and int(state.get("minute_calls", 0)) >= minute_budget:
                conn.execute(text("""
                  INSERT INTO external_tennis_source_state(source,state_key,state_json,updated_at)
                  VALUES (:source,:key,CAST(:payload AS jsonb),now())
                  ON CONFLICT(source,state_key) DO UPDATE SET state_json=excluded.state_json,updated_at=now()
                """), {"source": source, "key": state_key, "payload": json.dumps(state)})
                return False
            state["daily_calls"] = int(state.get("daily_calls", 0)) + 1
            state["minute_calls"] = int(state.get("minute_calls", 0)) + 1
            conn.execute(text("""
              INSERT INTO external_tennis_source_state(source,state_key,state_json,updated_at)
              VALUES (:source,:key,CAST(:payload AS jsonb),now())
              ON CONFLICT(source,state_key) DO UPDATE SET state_json=excluded.state_json,updated_at=now()
            """), {"source": source, "key": state_key, "payload": json.dumps(state)})
            return True

    def _read_source_snapshot(self, source: str, partition: str) -> dict[str, Any]:
        with self.archive.engine.connect() as conn:
            payload = conn.execute(text("""
                SELECT state_json FROM external_tennis_source_state
                WHERE source=:source AND state_key=:partition
            """), {"source": source, "partition": partition}).scalar()
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                return {}
        return payload if isinstance(payload, dict) else {}

    async def _fetch_tml_csv(self, tour: str, partition: str, url: str) -> int:
        source = "tennis_my_life"
        prior = self._read_source_snapshot(source, partition)
        headers = _tml_request_headers(prior)
        result = await self._archive_external_get(
            "tennis-my-life", partition.replace("_", "-"), url, headers=headers
        )
        checked_at = datetime.now(UTC).isoformat()
        if result.get("status") == 304:
            # A 304 is useful only when the previous complete raw response and
            # parser output are still present. Validators are suppressed otherwise.
            if headers and prior.get("status") == 200:
                self._save_source_snapshot(source, partition, {
                    **prior, "status": 200, "validation_status": 304,
                    "checked_at": checked_at, "data_unchanged": True,
                    "parser_version": TML_PARSER_VERSION,
                })
            else:
                self._save_source_snapshot(source, partition, {
                    "status": 0, "error": "HTTP_304_WITHOUT_USABLE_CACHE",
                    "checked_at": checked_at, "parser_version": TML_PARSER_VERSION,
                })
            return 304
        if result.get("status") != 200 or not result.get("body"):
            status = int(result.get("status") or 0)
            if prior.get("status") == 200 and prior.get("content_sha256"):
                self._save_source_snapshot(source, partition, {
                    **prior, "last_attempt_status": status,
                    "last_error": result.get("error") or f"HTTP_{status}",
                    "last_failed_at": checked_at,
                    "retry_after": result.get("retry_after"),
                })
            elif status:
                self._save_source_snapshot(source, partition, {
                    **{key: value for key, value in result.items() if key != "body"},
                    "status": status, "checked_at": checked_at,
                    "parser_version": TML_PARSER_VERSION,
                })
            return status
        digest = result.get("content_sha256") or hashlib.sha256(result["body"]).hexdigest()
        if (prior.get("status") == 200 and prior.get("parser_version") == TML_PARSER_VERSION
                and prior.get("content_sha256") == digest):
            self._save_source_snapshot(source, partition, {
                **result, "parser_version": TML_PARSER_VERSION,
                "checked_at": checked_at, "data_unchanged": True,
            })
            return 200
        try:
            count = self._store_tml_csv(result["body"], tour, url)
        except Exception as exc:
            if prior.get("status") == 200 and prior.get("content_sha256"):
                self._save_source_snapshot(source, partition, {
                    **prior, "last_error": type(exc).__name__,
                    "last_attempt_status": 200, "last_failed_at": checked_at,
                })
            else:
                self._save_source_snapshot(source, partition, {
                    **result, "status": 0, "error": type(exc).__name__,
                    "last_attempt_status": 200, "last_failed_at": checked_at,
                    "parser_version": TML_PARSER_VERSION,
                })
            logger.warning("TennisMyLife CSV rejected partition=%s error=%s", partition, type(exc).__name__, exc_info=True)
            return 0
        self._save_source_snapshot(source, partition, {
            **result, "parser_version": TML_PARSER_VERSION,
            "checked_at": checked_at, "normalized_matches": count,
        })
        logger.info("TennisMyLife CSV imported partition=%s rows=%s", partition, count)
        return 200

    async def _client(self):
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=httpx.Timeout(45, connect=8), follow_redirects=True,
                                           headers={"User-Agent": "Neurobet-Research/1.0 (+external tennis data archive)"})
        return self._http

    async def _archive_once(self) -> bool:
        key = _secret("API_TENNIS_API_KEY")
        if not key or datetime.now(UTC) >= ARCHIVE_STOP_AT:
            return False
        minimum_free = max(1, int(os.getenv("API_TENNIS_MIN_FREE_GB", "20"))) * 1024**3
        free_bytes = shutil.disk_usage("/").free
        if free_bytes < minimum_free:
            day = datetime.now(UTC).date().isoformat()
            if self._storage_warning_day != day:
                self._storage_warning_day = day
                logger.error("API-Tennis archive paused by storage guard free_bytes=%s minimum_bytes=%s", free_bytes, minimum_free)
                with self.archive.engine.begin() as conn:
                    conn.execute(text("""
                      INSERT INTO external_tennis_source_state(source,state_key,state_json,updated_at)
                      VALUES ('api_tennis','storage_guard',CAST(:payload AS jsonb),now())
                      ON CONFLICT(source,state_key) DO UPDATE SET state_json=excluded.state_json,updated_at=now()
                    """), {"payload": json.dumps({"status": "paused_low_disk", "free_bytes": free_bytes,
                                                   "minimum_bytes": minimum_free, "checked_at": datetime.now(UTC).isoformat()})})
            return False
        daily_budget = _api_tennis_daily_budget()
        if not self._reserve_daily_call("api_tennis", daily_budget):
            return False
        job = self.archive.claim()
        if not job:
            return False
        wait = 2.0 - (time.monotonic() - self._last_api_request)
        if wait > 0:
            await asyncio.sleep(wait)
        params = {
            "method": "get_fixtures", "date_start": job["interval_start"].isoformat(),
            "date_stop": job["interval_end"].isoformat(), "APIkey": key, "timezone": "UTC",
        }
        if job.get("event_type_key"):
            params["event_type_key"] = job["event_type_key"]
        client = await self._client()
        self._last_api_request = time.monotonic()
        request_started = time.monotonic()
        try:
            # POST keeps APIkey out of URLs and routine access logs; the provider supports GET and POST.
            response = await client.post(API_TENNIS_URL, data=params)
        except httpx.HTTPError as exc:
            from .source_metrics import try_record_source_metric
            try_record_source_metric(
                self.archive.engine, "api_tennis", request=True, successful=False,
                duration_ms=round((time.monotonic() - request_started) * 1000),
                error_code=type(exc).__name__,
            )
            if isinstance(exc, httpx.ReadTimeout) and self._split_archive_job(job, "TIMEOUT_SPLIT_BY_EVENT_TYPE"):
                return True
            self.archive.finish(job["id"], "retry", error_code=type(exc).__name__)
            logger.warning("API-Tennis transport failed for interval=%s", job["interval_start"])
            return True
        from .source_metrics import try_record_source_metric
        try_record_source_metric(
            self.archive.engine, "api_tennis", request=True,
            successful=200 <= response.status_code < 300,
            duration_ms=round((time.monotonic() - request_started) * 1000),
            status_code=response.status_code,
            error_code=(f"HTTP_{response.status_code}" if not 200 <= response.status_code < 300 else None),
        )
        if response.status_code in (401, 403):
            self.archive.finish(job["id"], "paused", http_status=response.status_code, error_code="AUTH")
            logger.error("API-Tennis archive paused after authorization failure")
            return False
        if response.status_code == 429:
            try:
                retry_after = max(2, min(86400, int(response.headers.get("Retry-After", "60"))))
            except ValueError:
                retry_after = 60
            with self.archive.engine.begin() as conn:
                conn.execute(text("UPDATE external_tennis_source_jobs SET status='retry', lease_until=NULL, next_run_at=now()+make_interval(secs=>:delay), error_code='RATE_LIMIT', updated_at=now() WHERE id=:id"), {"id": job["id"], "delay": retry_after})
            logger.warning("API-Tennis requested backoff seconds=%s", retry_after)
            return True
        if response.status_code != 200:
            self.archive.finish(job["id"], "retry", http_status=response.status_code, error_code=f"HTTP_{response.status_code}")
            return True
        body = response.content
        if len(body) > MAX_PAYLOAD_BYTES:
            if not self._split_archive_job(job, "PAYLOAD_SPLIT_BY_EVENT_TYPE", response_bytes=len(body), http_status=200):
                self.archive.finish(job["id"], "paused", http_status=200, response_bytes=len(body), error_code="PAYLOAD_TOO_LARGE_NO_TYPE_SPLIT")
                logger.error("API-Tennis response exceeded the cap and cannot be partitioned with provider catalog")
            return True
        digest = hashlib.sha256(body).hexdigest()
        raw_key = self.storage.archive_raw_bytes("api-tennis", str(job["interval_start"]), body, digest)
        if not raw_key:
            self.archive.finish(job["id"], "retry", http_status=200, response_bytes=len(body), content_sha256=digest, error_code="RAW_ARCHIVE_FAILED")
            return True
        try:
            data = response.json()
        except ValueError:
            self.archive.finish(job["id"], "retry", http_status=200, response_bytes=len(body), content_sha256=digest, raw_object_key=raw_key, error_code="INVALID_JSON")
            return True
        try:
            events = _fixture_events(data)
        except ValueError:
            self.archive.finish(job["id"], "retry", http_status=200, response_bytes=len(body), content_sha256=digest, raw_object_key=raw_key, error_code="PROVIDER_ENVELOPE_INVALID")
            return True
        job["provider_empty_without_result"] = "result" not in data
        job["response_bytes"] = len(body)
        job["observed_at"] = datetime.now(UTC)
        self.archive.persist_events(job, events, raw_key, digest)
        logger.info("API-Tennis interval saved date=%s records=%s bytes=%s", job["interval_start"], len(events), len(body))
        return True

    async def _project_saved_api_tennis_archives(self) -> None:
        """Advance one low-priority offline batch for each saved archive.

        These projections consume only observations already committed to
        PostgreSQL/MinIO. They never spend provider quota and remain resumable
        from their own durable checkpoints, including after API-Tennis access
        expires. A bounded multi-page tick drains the current backlog faster
        without introducing additional provider requests or parallel DB load.
        """
        import importlib

        projects = (
            ("pbp_backfill", "scripts.import_api_tennis_pbp_archive"),
            ("canonical_projection_backfill", "scripts.import_api_tennis_canonical_archive"),
        )
        max_batches = _api_tennis_offline_batches_per_tick()
        for state_key, module_name in projects:
            try:
                state = self._read_source_snapshot("api_tennis", state_key)
                if state.get("status") == "complete":
                    continue
                run = importlib.import_module(module_name).run
                result = await asyncio.to_thread(run, max_batches=max_batches)
                logger.info(
                    "API-Tennis offline projection key=%s status=%s rows=%s batches=%s",
                    state_key, result.get("status"),
                    result.get("rows_scanned", 0), max_batches,
                )
            except Exception:
                # Preserve the source worker and provider collectors when an
                # offline projection page encounters a transient DB/parser
                # error. The page transaction rolls back and the cursor stays
                # available for the next scheduled attempt.
                logger.exception("API-Tennis offline projection failed key=%s", state_key)

    def _split_archive_job(self, job, reason: str, *, response_bytes=None, http_status=None) -> bool:
        if job.get("event_type_key") or not self._event_types:
            return False
        with self.archive.engine.begin() as conn:
            conn.execute(text("""
              UPDATE external_tennis_source_jobs SET status='split', lease_until=NULL,
                http_status=:status, response_bytes=:bytes, error_code=:reason,
                updated_at=now(), finished_at=now() WHERE id=:id
            """), {"id": job["id"], "status": http_status, "bytes": response_bytes, "reason": reason})
            conn.execute(text("""
              INSERT INTO external_tennis_source_jobs
                (source,interval_start,interval_end,event_type_key,priority,status,next_run_at,attempts)
              VALUES ('api_tennis',:start,:end,:event_type,:priority,'queued',now(),0)
              ON CONFLICT (source,interval_start,interval_end,event_type_key) DO NOTHING
            """), [{"start": job["interval_start"], "end": job["interval_end"],
                    "event_type": item["key"], "priority": job.get("priority", 0)} for item in self._event_types])
        logger.warning("API-Tennis interval partitioned date=%s reason=%s", job["interval_start"], reason)
        return True

    async def _live_tennis_once(self) -> bool:
        key = _secret("LIVE_TENNIS_API_KEY")
        if not key:
            return False
        # Plan budget: at most 80/day and 24/minute, preserving provider headroom.
        if not self._reserve_daily_call("live_tennis_api", 80, minute_budget=24):
            return False
        client = await self._client()
        request_started = time.monotonic()
        response = None
        try:
            response = await client.get(f"{LIVE_TENNIS_BASE}/matches", params={"status": "live", "limit": 100},
                                        headers={"Authorization": f"Bearer {key}"})
            from .source_metrics import try_record_source_metric
            try_record_source_metric(
                self.archive.engine, "live_tennis_api", request=True,
                successful=200 <= response.status_code < 300,
                duration_ms=round((time.monotonic() - request_started) * 1000),
                status_code=response.status_code,
                error_code=(f"HTTP_{response.status_code}" if not 200 <= response.status_code < 300 else None),
            )
            if response.status_code in (401, 403):
                logger.warning("Live Tennis API currently does not permit this endpoint; status=%s", response.status_code)
                return False
            if response.status_code == 429:
                delay = response.headers.get("Retry-After", "60")
                await asyncio.sleep(min(300, max(1, int(delay) if delay.isdigit() else 60)))
                return True
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get("data"), list):
                raise ValueError("invalid_live_tennis_catalog")
            digest = hashlib.sha256(response.content).hexdigest()
            raw_key = self.storage.archive_raw_bytes("live-tennis-api", "live", response.content, digest)
            if raw_key:
                # This provider is a catalog/identity supplement only. Fonbet remains score authority.
                with self.archive.engine.begin() as conn:
                    conn.execute(text("""
                      INSERT INTO external_tennis_source_state(source,state_key,state_json,updated_at)
                      VALUES ('live_tennis_api','latest_live_catalog',CAST(:payload AS jsonb),now())
                      ON CONFLICT(source,state_key) DO UPDATE SET state_json=excluded.state_json,updated_at=now()
                    """), {"payload": json.dumps({"status": 200, "catalog_valid": True,
                                                   "observed_at": datetime.now(UTC).isoformat(), "raw_object_key": raw_key,
                                                   "content_sha256": digest, "match_count": len(data.get("data", [])),
                                                   "role": "catalog_supplement_only"})})
                from .source_metrics import try_record_source_metric
                try_record_source_metric(self.archive.engine, "live_tennis_api", published=1)
            return True
        except (httpx.HTTPError, ValueError) as exc:
            if response is None:
                from .source_metrics import try_record_source_metric
                try_record_source_metric(
                    self.archive.engine, "live_tennis_api", request=True, successful=False,
                    duration_ms=round((time.monotonic() - request_started) * 1000),
                    error_code=type(exc).__name__,
                )
            logger.warning("Live Tennis API catalog fetch failed: %s", type(exc).__name__)
            return False

    async def _fetch_live_tennis_profiles(self, *, limit: int = 2) -> dict[str, int]:
        """Fetch only due current-match player profiles within the shared FREE quota."""
        key = _secret("LIVE_TENNIS_API_KEY")
        if not key:
            return {"attempted": 0, "saved": 0, "skipped": 0}
        limit = max(0, min(int(limit), 2))
        if not limit:
            return {"attempted": 0, "saved": 0, "skipped": 0}
        with self.archive.engine.connect() as conn:
            targets = conn.execute(text("""
                WITH active_players AS (
                  SELECT participant_a_id AS participant_id, is_live, scheduled_start_at
                  FROM events
                  WHERE sport_code='tennis' AND finished_at IS NULL AND participant_a_id IS NOT NULL
                    AND (is_live IS TRUE OR scheduled_start_at BETWEEN now()-interval '6 hours'
                                                               AND now()+interval '6 hours')
                  UNION ALL
                  SELECT participant_b_id AS participant_id, is_live, scheduled_start_at
                  FROM events
                  WHERE sport_code='tennis' AND finished_at IS NULL AND participant_b_id IS NOT NULL
                    AND (is_live IS TRUE OR scheduled_start_at BETWEEN now()-interval '6 hours'
                                                               AND now()+interval '6 hours')
                )
                , candidates AS (
                  SELECT p.id::text AS participant_id,
                         COALESCE(NULLIF(cp.display_name,''), p.canonical_name) AS lookup_name,
                         bool_or(active_players.is_live) AS is_live,
                         min(active_players.scheduled_start_at) AS next_start
                  FROM active_players JOIN participants p ON p.id=active_players.participant_id
                  LEFT JOIN tennis_player_profiles tp ON tp.participant_id=p.id
                  LEFT JOIN ext_tennis_canonical_players cp ON cp.id=tp.canonical_player_id
                  WHERE p.sport_code='tennis' AND p.is_team IS FALSE
                    AND COALESCE(NULLIF(cp.display_name,''), p.canonical_name) ~ '[A-Za-z]'
                  GROUP BY p.id,p.canonical_name,cp.display_name
                ), ranked AS (
                  SELECT candidates.*,
                         row_number() OVER (PARTITION BY lookup_name
                           ORDER BY is_live DESC,next_start ASC,participant_id) AS name_rank
                  FROM candidates
                )
                SELECT ranked.participant_id, ranked.lookup_name AS canonical_name,
                       ranked.is_live, ranked.next_start
                FROM ranked
                WHERE ranked.name_rank=1
                  AND NOT EXISTS (
                    SELECT 1 FROM external_tennis_source_state s
                    WHERE s.source='live_tennis_api'
                      AND s.state_key LIKE 'profile:%'
                      AND s.state_json->>'requested_name'=ranked.lookup_name
                      AND ((s.state_json->>'status'='200' AND s.updated_at >= now()-interval '7 days')
                           OR (s.state_json->>'status'<>'200' AND s.updated_at >= now()-interval '1 day'))
                  )
                ORDER BY ranked.is_live DESC,ranked.next_start ASC
                LIMIT :limit
            """), {"limit": limit}).mappings().all()
        attempted = saved = skipped = 0
        for target in targets:
            participant_id = str(target["participant_id"])
            full_name = str(target["canonical_name"] or "").strip()
            name_tokens = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]+", full_name)
            if (not full_name or "/" in full_name or len(name_tokens) < 2
                    or any(len(token) < 2 for token in name_tokens)):
                skipped += 1
                continue
            state: dict[str, Any] = {
                "participant_id": participant_id, "requested_name": full_name,
                "checked_at": datetime.now(UTC).isoformat(), "role": "profile_supplement",
                "match_data_authority": "fonbet",
            }
            if not self._reserve_daily_call("live_tennis_api", 80, minute_budget=24):
                break
            attempted += 1
            headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
            search_url = f"{LIVE_TENNIS_BASE}/players?{urlencode({'search': full_name, 'limit': 50})}"
            search = await self._archive_external_get(
                "live-tennis-api", f"profile-search-{participant_id}", search_url,
                headers=headers, max_bytes=2 * 1024 * 1024,
            )
            state["search_status"] = search.get("status")
            state["search_raw_object_key"] = search.get("key")
            state["search_sha256"] = search.get("content_sha256")
            state["checked_at"] = search.get("fetched_at") or datetime.now(UTC).isoformat()
            selected = None
            if search.get("status") == 200 and search.get("body"):
                try:
                    selected, match_status = _live_tennis_player_candidate(
                        json.loads(search["body"]), full_name
                    )
                except (ValueError, TypeError, json.JSONDecodeError):
                    match_status = "INVALID_PLAYER_SEARCH_RESPONSE"
                state["match_status"] = match_status
            else:
                state["error"] = search.get("error") or f"HTTP_{search.get('status')}"
                if search.get("status") == 429:
                    state["retry_after"] = search.get("retry_after")
                state["match_status"] = (
                    "SEARCH_REQUEST_FAILED" if not search.get("status")
                    else f"SEARCH_HTTP_{search.get('status')}"
                )

            if selected is not None:
                player_id = str(selected["id"])
                state["player_id"] = player_id
                if self._reserve_daily_call("live_tennis_api", 80, minute_budget=24):
                    profile_url = f"{LIVE_TENNIS_BASE}/players/{player_id}"
                    detail = await self._archive_external_get(
                        "live-tennis-api", f"profile-{participant_id}-{player_id}", profile_url,
                        headers=headers, max_bytes=2 * 1024 * 1024,
                    )
                    state["profile_status"] = detail.get("status")
                    state["profile_raw_object_key"] = detail.get("key")
                    state["profile_sha256"] = detail.get("content_sha256")
                    if detail.get("status") == 200 and detail.get("body"):
                        try:
                            summary = _live_tennis_player_profile(
                                json.loads(detail["body"]), expected_id=player_id,
                                expected_name=full_name,
                            )
                        except (ValueError, TypeError, json.JSONDecodeError):
                            summary = None
                        if summary is not None:
                            state["profile"] = summary
                            state["status"] = 200
                            saved += 1
                        else:
                            state["status"] = "identity_mismatch"
                            state["error"] = "PROFILE_IDENTITY_MISMATCH"
                    else:
                        state["status"] = "profile_fetch_failed"
                        state["error"] = detail.get("error") or f"HTTP_{detail.get('status')}"
                        if detail.get("status") == 429:
                            state["retry_after"] = detail.get("retry_after")
                else:
                    state["status"] = "profile_fetch_deferred_quota"
                    state["error"] = "DAILY_OR_MINUTE_QUOTA"
            elif state.get("status") is None:
                match_status = state.get("match_status")
                state["status"] = (
                    "search_complete_no_exact_profile" if match_status == "PLAYER_NOT_FOUND"
                    else "search_ambiguous" if match_status == "PLAYER_SEARCH_AMBIGUOUS"
                    else "search_response_invalid" if match_status == "INVALID_PLAYER_SEARCH_RESPONSE"
                    else "search_failed"
                )

            state["observed_at"] = datetime.now(UTC).isoformat()
            self._save_source_snapshot("live_tennis_api", f"profile:{participant_id}", state)
        return {"attempted": attempted, "saved": saved, "skipped": skipped}

    async def _livescore_catalog_once(self) -> bool:
        """Fetch a low-rate LiveScore discovery catalog; Fonbet remains score authority."""
        now = datetime.now(UTC)
        with self.archive.engine.connect() as conn:
            prior = conn.execute(text("""
                SELECT state_json FROM external_tennis_source_state
                WHERE source='livescore' AND state_key='live_catalog'
            """)).scalar()
        if isinstance(prior, str):
            try:
                prior = json.loads(prior)
            except ValueError:
                prior = {}
        if isinstance(prior, dict):
            paused_until = prior.get("paused_until")
            fetched_at = prior.get("observed_at")
            try:
                if paused_until and datetime.fromisoformat(paused_until.replace("Z", "+00:00")) > now:
                    return False
                if fetched_at and now - datetime.fromisoformat(fetched_at.replace("Z", "+00:00")) < timedelta(hours=1):
                    return False
            except (TypeError, ValueError):
                pass
        # At most one request/hour and 24/day, with a durable per-minute guard.
        if not self._reserve_daily_call("livescore", 24, minute_budget=1):
            return False
        headers = {
            "Origin": "https://www.livescore.com",
            "Referer": "https://www.livescore.com/",
            "Accept": "application/json",
        }
        result = await self._archive_external_get(
            "livescore", "live-tennis-catalog", LIVESCORES_LIVE_TENNIS_URL,
            headers=headers, max_bytes=8 * 1024 * 1024,
        )
        if result.get("status") == 429:
            try:
                retry_after = min(86400, max(1, int(result.get("retry_after") or 3600)))
            except (TypeError, ValueError):
                retry_after = 3600
            self._save_source_snapshot("livescore", "live_catalog", {
                "status": 429, "retry_after_seconds": retry_after,
                "paused_until": (now + timedelta(seconds=retry_after)).isoformat(),
                "role": "match_discovery_only",
            })
            return False
        if result.get("status") != 200 or not result.get("body"):
            self._save_source_snapshot("livescore", "live_catalog", {
                **{key: value for key, value in result.items() if key != "body"},
                "error": result.get("error") or "HTTP_FAILURE",
                "role": "match_discovery_only",
            })
            return False
        try:
            summary = _livescore_catalog_summary(json.loads(result["body"]))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self._save_source_snapshot("livescore", "live_catalog", {
                **{key: value for key, value in result.items() if key != "body"},
                "status": 0, "error": type(exc).__name__,
                "role": "match_discovery_only",
            })
            return False
        self._save_source_snapshot("livescore", "live_catalog", {
            **{key: value for key, value in result.items() if key != "body"},
            **summary, "status": 200, "role": "match_discovery_only",
            "score_authority": "fonbet", "provider_contract": "unofficial_public_http_endpoint",
        })
        return True

    async def run_forever(self):
        heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        try:
            await self._ensure_event_catalog()
            if _secret("API_TENNIS_API_KEY") and datetime.now(UTC) < ARCHIVE_STOP_AT:
                self.archive.seed()
                self.archive.refresh_recent_window()
            await self._seed_public_sources()
            await self._refresh_public_sources()
            now_mono = time.monotonic()
            last_live = now_mono - 3600
            last_live_profiles = now_mono - 3600
            last_livescore = now_mono - 3600
            last_public = now_mono - 86400
            last_tml_live = now_mono - 3600
            last_api_projection = now_mono
            next_tennis_explorer = now_mono - 1
            next_tennis_abstract = now_mono - 1
            last_archive_window = datetime.now(UTC).date()
            while True:
                if datetime.now(UTC) >= ARCHIVE_STOP_AT and _secret("API_TENNIS_API_KEY"):
                    logger.warning("API-Tennis request cutoff reached; archival fetches are disabled")
                did_work = await self._archive_once()
                if datetime.now(UTC).date() != last_archive_window and _secret("API_TENNIS_API_KEY") and datetime.now(UTC) < ARCHIVE_STOP_AT:
                    self.archive.refresh_recent_window()
                    last_archive_window = datetime.now(UTC).date()
                if time.monotonic() - last_live >= 3600:
                    await self._live_tennis_once(); last_live = time.monotonic()
                if time.monotonic() - last_live_profiles >= 3600:
                    await self._fetch_live_tennis_profiles(limit=2)
                    last_live_profiles = time.monotonic()
                if time.monotonic() - last_livescore >= 3600:
                    await self._livescore_catalog_once(); last_livescore = time.monotonic()
                if time.monotonic() - last_tml_live >= 3600:
                    await self._fetch_tml_ongoing(); last_tml_live = time.monotonic()
                if time.monotonic() >= next_tennis_explorer:
                    retry_delay = await self._fetch_tennis_explorer_recent_window()
                    next_tennis_explorer = time.monotonic() + max(1, retry_delay)
                    await self._fetch_tennis_explorer_profiles()
                if time.monotonic() >= next_tennis_abstract:
                    await self._fetch_tennis_abstract_profiles()
                    next_tennis_abstract = time.monotonic() + 3600
                if time.monotonic() - last_public >= 86400:
                    await self._fetch_tml_season()
                    await self._check_charting_project()
                    await self._refresh_public_sources()
                    last_public = time.monotonic()
                if await self._fetch_tml_historical_season_once():
                    did_work = True
                if time.monotonic() - last_api_projection >= 60:
                    await self._project_saved_api_tennis_archives()
                    last_api_projection = time.monotonic()
                if not did_work:
                    await asyncio.sleep(5)
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            if self._http:
                await self._http.aclose()

    async def _heartbeat_loop(self):
        while True:
            with open("/tmp/healthy", "w", encoding="utf-8") as heartbeat:
                heartbeat.write(str(time.time()))
            await asyncio.sleep(15)

    async def _ensure_event_catalog(self):
        key = _secret("API_TENNIS_API_KEY")
        if not key or datetime.now(UTC) >= ARCHIVE_STOP_AT:
            return
        client = await self._client()
        response = None
        request_started = time.monotonic()
        try:
            await asyncio.sleep(max(0.0, 2.0 - (time.monotonic() - self._last_api_request)))
            self._last_api_request = time.monotonic()
            if not self._reserve_daily_call("api_tennis", _api_tennis_daily_budget()):
                return
            request_started = time.monotonic()
            response = await client.post(API_TENNIS_URL, data={"method": "get_events", "APIkey": key})
            from .source_metrics import try_record_source_metric
            try_record_source_metric(
                self.archive.engine, "api_tennis", request=True,
                successful=200 <= response.status_code < 300,
                duration_ms=round((time.monotonic() - request_started) * 1000),
                status_code=response.status_code,
                error_code=(f"HTTP_{response.status_code}" if not 200 <= response.status_code < 300 else None),
            )
            if response.status_code in (401, 403):
                logger.error("API-Tennis event catalog authorization failed; archive remains paused")
                return
            if response.status_code == 429:
                logger.warning("API-Tennis event catalog rate limited; archive startup will retry later")
                return
            response.raise_for_status()
            data = response.json()
            if not data.get("success") or not isinstance(data.get("result"), list):
                return
            self._event_types = [
                {"key": str(item.get("event_type_key")), "name": str(item.get("event_type_type") or "")}
                for item in data["result"] if item.get("event_type_key") is not None
            ]
            digest = hashlib.sha256(response.content).hexdigest()
            raw_key = self.storage.archive_raw_bytes("api-tennis", "event-types", response.content, digest)
            if raw_key:
                with self.archive.engine.begin() as conn:
                    conn.execute(text("""
                      INSERT INTO external_tennis_source_state(source,state_key,state_json,updated_at)
                      VALUES ('api_tennis','event_types',CAST(:payload AS jsonb),now())
                      ON CONFLICT(source,state_key) DO UPDATE SET state_json=excluded.state_json,updated_at=now()
                    """), {"payload": json.dumps({"observed_at": datetime.now(UTC).isoformat(),
                                                   "raw_object_key": raw_key, "content_sha256": digest,
                                                   "event_types": self._event_types})})
                from .source_metrics import try_record_source_metric
                try_record_source_metric(self.archive.engine, "api_tennis", published=1)
        except (httpx.HTTPError, ValueError) as exc:
            if response is None:
                from .source_metrics import try_record_source_metric
                try_record_source_metric(
                    self.archive.engine, "api_tennis", request=True, successful=False,
                    duration_ms=round((time.monotonic() - request_started) * 1000),
                    error_code=type(exc).__name__,
                )
            logger.warning("API-Tennis event catalog fetch failed error=%s", type(exc).__name__)

    async def _seed_public_sources(self):
        """Keep source schedule state durable; actual public feeds are independent snapshots."""
        with self.archive.engine.begin() as conn:
            conn.execute(text("""
              INSERT INTO external_tennis_source_state(source,state_key,state_json,updated_at)
              VALUES ('permanent_sources','schedule',CAST(:payload AS jsonb),now())
              ON CONFLICT(source,state_key) DO NOTHING
            """), {"payload": json.dumps({"sources": ["tennis_my_life", "tennis_explorer", "tennis_abstract", "match_charting_project", "live_tennis_api", "livescore"],
                                           "cadence": {"tennis_my_life_current": "hourly", "tennis_my_life_season_files": "daily",
                                                       "tennis_explorer": "coverage_gap_only", "tennis_abstract": "coverage_gap_only",
                                                       "match_charting_project": "daily_hash_check", "live_tennis_api": "hourly_catalog_and_two_due_profiles",
                                                       "livescore": "hourly_low_rate_catalog"}})})

    async def _refresh_public_sources(self):
        """Expose adapters whose records need a player identity and coverage-gap query."""
        status = {
            "api_tennis_archive": "credentials_ready" if _secret("API_TENNIS_API_KEY") else "credentials_missing",
            "tennis_my_life": "raw_archived_and_season_results_normalized",
            "tennis_explorer": "scheduled_recent_results_ingestion_player_coverage_unverified",
            "tennis_abstract": "recent_fragment_partial_history_scheduled",
            "match_charting_project": "retrospective_match_metadata_and_pbp_ingestion",
            "live_tennis_api": "hourly_free_catalog_and_due_player_profiles" if _secret("LIVE_TENNIS_API_KEY") else "credentials_missing",
            "livescore": "hourly_unofficial_catalog_candidate",
        }
        with self.archive.engine.begin() as conn:
            ta_state = conn.execute(text("""
                SELECT state_json FROM external_tennis_source_state
                WHERE source='tennis_abstract' AND state_key='profile_import'
            """)).scalar()
            if isinstance(ta_state, str):
                try:
                    ta_state = json.loads(ta_state)
                except ValueError:
                    ta_state = {}
            if isinstance(ta_state, dict) and ta_state.get("status") == "partial_recent_fragments":
                ta_profile_states = conn.execute(text("""
                    SELECT state_json FROM external_tennis_source_state
                    WHERE source='tennis_abstract' AND state_key LIKE 'profile:%'
                """)).scalars().all()
                status["tennis_abstract"] = (
                    f"partial_recent_fragments:{_tennis_abstract_imported_observations(ta_profile_states)}"
                )
            explorer_state = conn.execute(text("""
                SELECT state_json FROM external_tennis_source_state
                WHERE source='tennis_explorer' AND state_key='recent_window'
            """)).scalar()
            if isinstance(explorer_state, str):
                try:
                    explorer_state = json.loads(explorer_state)
                except ValueError:
                    explorer_state = {}
            if isinstance(explorer_state, dict):
                if explorer_state.get("status") == 200:
                    status["tennis_explorer"] = "recent_results_ingested_player_career_unverified"
                elif explorer_state.get("status") == 429:
                    status["tennis_explorer"] = "paused_rate_limit"
                elif explorer_state.get("status") == 0:
                    status["tennis_explorer"] = "recent_results_refresh_error"
            livescore_state = conn.execute(text("""
                SELECT state_json FROM external_tennis_source_state
                WHERE source='livescore' AND state_key='live_catalog'
            """)).scalar()
            if isinstance(livescore_state, str):
                try:
                    livescore_state = json.loads(livescore_state)
                except ValueError:
                    livescore_state = {}
            if isinstance(livescore_state, dict):
                if livescore_state.get("status") == 200 and livescore_state.get("catalog_valid"):
                    status["livescore"] = "live_match_discovery_catalog"
                elif livescore_state.get("status") == 429:
                    status["livescore"] = "paused_rate_limit"
                elif livescore_state.get("status") is not None:
                    status["livescore"] = "catalog_unavailable"
            live_profiles = conn.execute(text("""
                SELECT state_key,state_json,updated_at FROM external_tennis_source_state
                WHERE source='live_tennis_api' AND state_key LIKE 'profile:%'
            """)).all()
            profile_count = 0
            fresh_profile_count = 0
            for _profile_key, profile_state, updated_at in live_profiles:
                if isinstance(profile_state, str):
                    try:
                        profile_state = json.loads(profile_state)
                    except ValueError:
                        profile_state = {}
                if isinstance(profile_state, dict) and profile_state.get("status") == 200:
                    profile_count += 1
                    if updated_at and datetime.now(UTC) - updated_at <= timedelta(days=7):
                        fresh_profile_count += 1
            if profile_count:
                status["live_tennis_api"] = (
                    f"hourly_catalog_with_player_profiles:{profile_count}:{fresh_profile_count}"
                )
            charting_rows = conn.execute(text("""
                SELECT state_key,state_json FROM external_tennis_source_state
                WHERE source='match_charting_project' AND state_key <> 'schedule'
            """)).all()
            charting_state = {}
            for state_key, state_json in charting_rows:
                if isinstance(state_json, str):
                    try:
                        state_json = json.loads(state_json)
                    except ValueError:
                        state_json = {}
                if isinstance(state_json, dict):
                    charting_state[state_key] = state_json
            imported_matches = sum(int(value.get("record_count", 0) or 0) for key, value in charting_state.items()
                                   if key.endswith("_matches") and value.get("status") == 200)
            imported_point_matches = sum(int(value.get("record_count", 0) or 0) for key, value in charting_state.items()
                                         if "_points_" in key and value.get("status") == 200)
            status["match_charting_project"] = (
                f"retrospective_ingested:metadata={imported_matches}:point_match_files={imported_point_matches}"
                if imported_matches or imported_point_matches else "scheduled_not_yet_ingested"
            )
            conn.execute(text("""
              INSERT INTO external_tennis_source_state(source,state_key,state_json,updated_at)
              VALUES ('permanent_sources','status',CAST(:payload AS jsonb),now())
              ON CONFLICT(source,state_key) DO UPDATE SET state_json=excluded.state_json,updated_at=now()
            """), {"payload": json.dumps({"observed_at": datetime.now(UTC).isoformat(), "integrations": status})})

    async def _archive_external_get(self, source: str, partition: str, url: str, *, headers=None, max_bytes=MAX_PAYLOAD_BYTES):
        client = await self._client()
        started = time.monotonic()
        source_key = source.replace("-", "_")
        try:
            response = await client.get(url, headers=headers or {})
            from .source_metrics import try_record_source_metric
            try_record_source_metric(
                self.archive.engine, source_key, request=True,
                successful=response.status_code in (200, 304),
                duration_ms=round((time.monotonic() - started) * 1000),
                status_code=response.status_code,
                error_code=(f"HTTP_{response.status_code}" if response.status_code not in (200, 304) else None),
            )
            if response.status_code == 304:
                return {"status": 304, "etag": response.headers.get("etag"), "key": None}
            if response.status_code != 200:
                logger.info("External source response source=%s status=%s", source, response.status_code)
                return {"status": response.status_code,
                        "retry_after": response.headers.get("retry-after")}
            if len(response.content) > max_bytes:
                logger.warning("External source response over limit source=%s bytes=%s", source, len(response.content))
                return {"status": 200, "error": "payload_too_large"}
            digest = hashlib.sha256(response.content).hexdigest()
            key = self.storage.archive_raw_bytes(source, partition, response.content, digest)
            if not key:
                return {"status": 200, "error": "raw_archive_failed"}
            return {"status": 200, "key": key, "etag": response.headers.get("etag"), "last_modified": response.headers.get("last-modified"),
                    "content_sha256": digest, "raw_bytes": len(response.content), "content_type": response.headers.get("content-type", ""),
                    "fetched_at": datetime.now(UTC).isoformat(), "body": response.content}
        except httpx.HTTPError as exc:
            from .source_metrics import try_record_source_metric
            try_record_source_metric(
                self.archive.engine, source_key, request=True, successful=False,
                duration_ms=round((time.monotonic() - started) * 1000), error_code=type(exc).__name__,
            )
            logger.info("External source request failed source=%s error=%s", source, type(exc).__name__)
            return {"status": 0, "error": type(exc).__name__}

    def _save_source_snapshot(self, source: str, partition: str, result: dict[str, Any]):
        manifest = {key: value for key, value in result.items() if key != "body"}
        licenses = {
            # The owner confirmed use for noncommercial research on 2026-10-23.
            # This does not turn provider data into MIT-licensed software.
            "tennis_my_life": "provider_terms",
            "tennis_explorer": "provider_terms",
            "tennis_abstract": "provider_terms",
            "api_tennis": "provider_terms",
            "match_charting_project": "CC BY-NC-SA 4.0",
            "live_tennis_api": "provider_terms",
            "livescore": "provider_terms_unofficial_public_endpoint",
        }
        if source in licenses:
            manifest["license"] = licenses[source]
            manifest["usage_review"] = {"scope": "noncommercial_research",
                                        "confirmed_by": "project_owner", "confirmed_on": "2026-10-23"}
        with self.archive.engine.begin() as conn:
            conn.execute(text("""
              INSERT INTO external_tennis_source_state(source,state_key,state_json,updated_at)
              VALUES (:source,:key,CAST(:payload AS jsonb),now())
              ON CONFLICT(source,state_key) DO UPDATE SET state_json=excluded.state_json,updated_at=now()
            """), {"source": source, "key": partition, "payload": json.dumps({"observed_at": datetime.now(UTC).isoformat(), **manifest})})
        if (result.get("status") == 200 and result.get("content_sha256")
                and not result.get("data_unchanged")):
            from .source_metrics import try_record_source_metric
            try_record_source_metric(self.archive.engine, source, published=1)

    async def _fetch_tml_ongoing(self):
        urls = {
            "atp": "https://stats.tennismylife.org/data/ongoing_tourneys.csv",
            "wta": "https://stats.tennismylife.org/data/wta_ongoing_tourneys.csv",
            "challenger": "https://stats.tennismylife.org/data/challenger_ongoing_tourneys.csv",
        }
        for tour, url in urls.items():
            await self._fetch_tml_csv(tour, f"ongoing_{tour}", url)
            await asyncio.sleep(2)

    async def _fetch_tml_season(self):
        year = datetime.now(UTC).year
        urls = {
            "atp": f"https://stats.tennismylife.org/data/{year}.csv",
            "wta": f"https://stats.tennismylife.org/data/{year}_wta.csv",
            "challenger": f"https://stats.tennismylife.org/data/{year}_challenger.csv",
        }
        for tour, url in urls.items():
            await self._fetch_tml_csv(tour, f"season_{year}_{tour}", url)
            await asyncio.sleep(2)

    async def _fetch_tml_historical_season_once(self, *, current_year: int | None = None) -> bool:
        """Durably import one previous TML tour-season per pass, newest seasons first."""
        source = "tennis_my_life"
        current_year = current_year or datetime.now(UTC).year
        partitions = _tml_historical_partitions(current_year)
        state = self._read_source_snapshot(source, "historical_archive")
        target_through_year = current_year - 1
        previous_through_year = int(state.get("through_year", 0) or 0)
        if state.get("status") == "complete" and previous_through_year >= target_through_year:
            return False
        if previous_through_year < target_through_year:
            state = {
                "status": "running", "next_partition": 0,
                "available_by_tour": {}, "unavailable_by_tour": {},
                "through_year": target_through_year,
                "start_year_by_tour": TML_HISTORICAL_START_YEAR,
            }
        retry_after_at = state.get("retry_after_at")
        if retry_after_at:
            try:
                if datetime.now(UTC) < datetime.fromisoformat(retry_after_at.replace("Z", "+00:00")):
                    return False
            except (TypeError, ValueError):
                pass

        index = max(0, int(state.get("next_partition", 0) or 0))
        available = dict(state.get("available_by_tour") or {})
        unavailable = dict(state.get("unavailable_by_tour") or {})
        while index < len(partitions):
            year, tour = partitions[index]
            partition = f"historical_{year}_{tour}"
            snapshot = self._read_source_snapshot(source, partition)
            if (snapshot.get("status") == 200
                    and snapshot.get("parser_version") == TML_PARSER_VERSION
                    and snapshot.get("content_sha256") and snapshot.get("key")):
                available[tour] = int(available.get(tour, 0)) + 1
                index += 1
                continue
            if snapshot.get("status") == 404 and snapshot.get("historical_unavailable"):
                unavailable[tour] = int(unavailable.get(tour, 0)) + 1
                index += 1
                continue

            last_request = getattr(self, "_last_tml_history_request", 0.0)
            delay = 2.0 - (time.monotonic() - last_request)
            if delay > 0:
                await asyncio.sleep(delay)
            url_suffix = {"atp": "", "wta": "_wta", "challenger": "_challenger"}[tour]
            url = f"https://stats.tennismylife.org/data/{year}{url_suffix}.csv"
            response_status = await self._fetch_tml_csv(tour, partition, url)
            self._last_tml_history_request = time.monotonic()
            snapshot = self._read_source_snapshot(source, partition)
            now = datetime.now(UTC)
            if (response_status in (200, 304) and snapshot.get("status") == 200
                    and snapshot.get("parser_version") == TML_PARSER_VERSION
                    and snapshot.get("content_sha256") and snapshot.get("key")):
                available[tour] = int(available.get(tour, 0)) + 1
                index += 1
            elif response_status == 404:
                unavailable[tour] = int(unavailable.get(tour, 0)) + 1
                self._save_source_snapshot(source, partition, {
                    **snapshot, "status": 404, "historical_unavailable": True,
                    "checked_at": now.isoformat(), "parser_version": TML_PARSER_VERSION,
                })
                index += 1
            else:
                retry_seconds = 3600
                try:
                    retry_seconds = min(86400, max(1, int(snapshot.get("retry_after") or retry_seconds)))
                except (TypeError, ValueError):
                    pass
                state.update({
                    "status": "running", "next_partition": index,
                    "current_partition": partition, "last_status": response_status,
                    "last_error": snapshot.get("error") or f"HTTP_{response_status}",
                    "retry_after_at": (now + timedelta(seconds=retry_seconds)).isoformat(),
                    "available_by_tour": available, "unavailable_by_tour": unavailable,
                    "updated_at": now.isoformat(),
                })
                self._save_source_snapshot(source, "historical_archive", state)
                return True

            state.update({
                "status": "complete" if index >= len(partitions) else "running",
                "next_partition": index,
                "current_partition": None if index >= len(partitions) else f"{partitions[index][0]}_{partitions[index][1]}",
                "partitions_total": len(partitions),
                "through_year": target_through_year,
                "available_by_tour": available,
                "unavailable_by_tour": unavailable,
                "updated_at": now.isoformat(),
                "parser_version": TML_PARSER_VERSION,
                "start_year_by_tour": TML_HISTORICAL_START_YEAR,
            })
            state.pop("retry_after_at", None)
            state.pop("last_error", None)
            state.pop("last_status", None)
            self._save_source_snapshot(source, "historical_archive", state)
            logger.info("TennisMyLife historical archive progress=%s/%s partition=%s status=%s",
                        index, len(partitions), partition, response_status)
            return True

        state.update({
            "status": "complete", "next_partition": len(partitions),
            "partitions_total": len(partitions), "through_year": target_through_year,
            "available_by_tour": available,
            "unavailable_by_tour": unavailable, "updated_at": datetime.now(UTC).isoformat(),
            "parser_version": TML_PARSER_VERSION,
            "start_year_by_tour": TML_HISTORICAL_START_YEAR,
        })
        self._save_source_snapshot(source, "historical_archive", state)
        return False

    async def _fetch_tennis_explorer_recent_window(self):
        """Refresh seven recent date pages; this does not claim full player histories."""
        from .tennis_explorer import PARSER_VERSION, parse_results_page

        day_key = datetime.now(UTC).date().isoformat()
        with self.archive.engine.begin() as conn:
            state = conn.execute(text("""
                SELECT state_json FROM external_tennis_source_state
                WHERE source='tennis_explorer' AND state_key='recent_window'
            """)).scalar()
        if isinstance(state, str):
            try:
                state = json.loads(state)
            except ValueError:
                state = {}
        if isinstance(state, dict) and state.get("completed_day") == day_key:
            try:
                projection_totals = self._project_tennis_explorer_recent_window(datetime.now(UTC).date())
            except Exception as exc:
                logger.exception("TennisExplorer canonical projection retry failed")
                projection_totals = {"error": type(exc).__name__}
            self._save_source_snapshot("tennis_explorer", "recent_window", {
                **state, "canonical_projection": projection_totals,
                "coverage": "recent_results_only_not_player_career",
            })
            return 86400

        fetched_at = datetime.now(UTC)
        days_done = []
        total_matches = 0
        projection_totals: dict[str, int] = {}
        for offset in range(1, 8):
            event_day = fetched_at.date() - timedelta(days=offset)
            partition = event_day.isoformat()
            with self.archive.engine.connect() as conn:
                prior = conn.execute(text("""
                    SELECT state_json FROM external_tennis_source_state
                    WHERE source='tennis_explorer' AND state_key=:key
                """), {"key": partition}).scalar()
            if isinstance(prior, str):
                try:
                    prior = json.loads(prior)
                except ValueError:
                    prior = {}
            headers = {}
            if isinstance(prior, dict):
                if prior.get("etag"):
                    headers["If-None-Match"] = prior["etag"]
                elif prior.get("last_modified"):
                    headers["If-Modified-Since"] = prior["last_modified"]
            url = ("https://www.tennisexplorer.com/results/?type=all&year="
                   f"{event_day.year}&month={event_day.month:02d}&day={event_day.day:02d}")
            wait = 2.0 - (time.monotonic() - self._last_tennis_explorer_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_tennis_explorer_request = time.monotonic()
            result = await self._archive_external_get("tennis-explorer", partition, url, headers=headers,
                                                       max_bytes=8 * 1024 * 1024)
            if result.get("status") == 304:
                self._save_source_snapshot("tennis_explorer", partition, {
                    "status": 304, "body_unchanged": True,
                    "content_sha256": (prior or {}).get("content_sha256"),
                    "key": (prior or {}).get("key"), "etag": result.get("etag") or (prior or {}).get("etag"),
                    "last_modified": (prior or {}).get("last_modified"),
                    "parser_version": PARSER_VERSION, "checked_at": datetime.now(UTC).isoformat(),
                    "complete_career_coverage": False,
                })
                days_done.append(event_day.isoformat())
                continue
            if result.get("status") == 429:
                try:
                    retry_after = min(86400, max(1, int(result.get("retry_after") or 3600)))
                except (TypeError, ValueError):
                    retry_after = 3600
                self._save_source_snapshot("tennis_explorer", "recent_window", {
                    "status": 429, "error": "RATE_LIMIT", "retry_after_seconds": retry_after,
                    "paused_until": (datetime.now(UTC) + timedelta(seconds=retry_after)).isoformat(),
                    "days_completed": days_done, "fetched_at": datetime.now(UTC).isoformat(),
                })
                return retry_after
            if result.get("status") != 200 or not result.get("body"):
                self._save_source_snapshot("tennis_explorer", "recent_window", {
                    "status": result.get("status", 0), "error": result.get("error") or "HTTP_FAILURE",
                    "days_completed": days_done, "fetched_at": datetime.now(UTC).isoformat(),
                })
                return 3600
            try:
                matches = parse_results_page(result["body"], event_day, fetched_at=fetched_at)
            except (ValueError, TypeError) as exc:
                self._save_source_snapshot("tennis_explorer", event_day.isoformat(), {
                    **result, "status": 0, "error": f"PARSE_{type(exc).__name__}",
                    "parser_version": PARSER_VERSION,
                })
                self._save_source_snapshot("tennis_explorer", "recent_window", {
                    "status": 0, "error": "INVALID_RESULTS_PAGE", "failed_day": event_day.isoformat(),
                    "days_completed": days_done, "fetched_at": datetime.now(UTC).isoformat(),
                })
                return 3600
            try:
                inserted = self._store_tennis_explorer_results(
                    matches, result["content_sha256"], fetched_at=fetched_at)
            except Exception as exc:
                logger.exception("TennisExplorer import failed day=%s", event_day.isoformat())
                self._save_source_snapshot("tennis_explorer", "recent_window", {
                    "status": 0, "error": f"IMPORT_{type(exc).__name__}",
                    "failed_day": event_day.isoformat(), "days_completed": days_done,
                    "fetched_at": datetime.now(UTC).isoformat(),
                })
                return 3600
            total_matches += inserted
            days_done.append(event_day.isoformat())
            self._save_source_snapshot("tennis_explorer", event_day.isoformat(), {
                **result, "status": 200, "parser_version": PARSER_VERSION,
                "singles_results": len(matches), "normalized_matches": inserted,
                "complete_career_coverage": False,
            })
        try:
            projection_totals = self._project_tennis_explorer_recent_window(fetched_at.date())
        except Exception as exc:
            logger.exception("TennisExplorer canonical projection failed")
            projection_totals = {"error": type(exc).__name__}
            self._save_source_snapshot("tennis_explorer", "canonical_projection", {
                "status": "error", "error": type(exc).__name__,
                "coverage": "recent_results_only_not_player_career",
                "failed_at": datetime.now(UTC).isoformat(),
            })
        self._save_source_snapshot("tennis_explorer", "recent_window", {
            "status": 200, "completed_day": day_key, "days_completed": days_done,
            "normalized_matches": total_matches, "parser_version": PARSER_VERSION,
            "coverage": "recent_results_only_not_player_career",
            "canonical_projection": projection_totals,
            "fetched_at": datetime.now(UTC).isoformat(),
        })
        logger.info("TennisExplorer recent results refreshed days=%s normalized=%s", len(days_done), total_matches)
        return 86400

    async def _fetch_tennis_explorer_profiles(self, *, daily_budget: int | None = None) -> dict[str, int]:
        """Refresh bounded ITF profile snapshots and project their singles results.

        Recent ITF players are selected from the seven-day result feed. A
        profile is revisited at most weekly and the process-wide daily quota is
        durable in PostgreSQL. The response is kept as an accessible snapshot;
        this adapter does not certify full-career coverage.
        """
        from sqlalchemy.orm import Session
        from .canonical_tennis_projection import project_recent_tennis_explorer
        from .tennis_explorer import PROFILE_PARSER_VERSION, parse_profile_page

        budget = daily_budget if daily_budget is not None else _tennis_explorer_profile_daily_budget()

        with self.archive.engine.connect() as conn:
            recent_state = conn.execute(text("""
                SELECT state_json FROM external_tennis_source_state
                WHERE source='tennis_explorer' AND state_key='recent_window'
            """)).scalar()
            profile_pause = conn.execute(text("""
                SELECT state_json FROM external_tennis_source_state
                WHERE source='tennis_explorer' AND state_key='profile_pause'
            """)).scalar()
            candidates = conn.execute(text("""
                SELECT player_id, max(played_at) AS latest_played_at,
                       array_agg(display_name ORDER BY played_at DESC) AS display_names
                FROM (
                    SELECT winner_player_id AS player_id, winner_name AS display_name, played_at
                    FROM ext_tennis_matches
                    WHERE source='tennis_explorer' AND tour_level='itf'
                      AND played_at >= now()-interval '8 days'
                    UNION ALL
                    SELECT loser_player_id AS player_id, loser_name AS display_name, played_at
                    FROM ext_tennis_matches
                    WHERE source='tennis_explorer' AND tour_level='itf'
                      AND played_at >= now()-interval '8 days'
                ) AS recent_players
                WHERE player_id IS NOT NULL
                GROUP BY player_id ORDER BY max(played_at) DESC
            """)).all()
            # Parser upgrades are local work over hash-addressed raw snapshots.
            # Keep them independent of the provider request budget and make
            # sure profiles outside the current ITF candidate window are not
            # stranded on an obsolete parser version.
            profile_states = conn.execute(text("""
                SELECT state_key, state_json FROM external_tennis_source_state
                WHERE source='tennis_explorer' AND state_key LIKE 'profile:%'
                ORDER BY state_key
            """)).all()
            active_names = {
                str(row[0]) for row in conn.execute(text("""
                    SELECT DISTINCT p.canonical_name
                    FROM events e
                    JOIN participants p ON p.id IN (e.participant_a_id, e.participant_b_id)
                    WHERE e.sport_code='tennis' AND p.sport_code='tennis' AND p.is_team IS FALSE
                      AND (e.is_live IS TRUE OR e.scheduled_start_at BETWEEN now()-interval '6 hours'
                                                              AND now()+interval '6 hours')
                      AND e.participant_a_name NOT LIKE '%/%' AND e.participant_b_name NOT LIKE '%/%'
                """)).all()
                if row[0]
            }
        if isinstance(recent_state, str):
            try:
                recent_state = json.loads(recent_state)
            except ValueError:
                recent_state = {}
        network_paused = False
        if isinstance(recent_state, dict) and recent_state.get("paused_until"):
            try:
                if datetime.fromisoformat(recent_state["paused_until"].replace("Z", "+00:00")) > datetime.now(UTC):
                    network_paused = True
            except (TypeError, ValueError):
                pass
        if isinstance(profile_pause, str):
            try:
                profile_pause = json.loads(profile_pause)
            except ValueError:
                profile_pause = {}
        if isinstance(profile_pause, dict) and profile_pause.get("paused_until"):
            try:
                if datetime.fromisoformat(profile_pause["paused_until"].replace("Z", "+00:00")) > datetime.now(UTC):
                    network_paused = True
            except (TypeError, ValueError):
                pass

        ordered_candidates = _prioritize_tennis_explorer_profiles(
            [dict(row._mapping) for row in candidates], active_names
        )
        slugs = []
        stale_parser_slugs = []
        profile_state_by_slug = {}
        for state_key, state_json in profile_states:
            slug = state_key.removeprefix("profile:")
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", slug):
                continue
            if isinstance(state_json, str):
                try:
                    state_json = json.loads(state_json)
                except ValueError:
                    state_json = {}
            if not isinstance(state_json, dict):
                state_json = {}
            profile_state_by_slug[slug] = state_json
            if (state_json.get("parser_version") != PROFILE_PARSER_VERSION
                    and state_json.get("key") and state_json.get("content_sha256")):
                stale_parser_slugs.append(slug)
        # Reparse stale snapshots first. This does not call the provider and
        # therefore must run even when HTTP work is paused or out of quota.
        for slug in stale_parser_slugs:
            if slug not in slugs:
                slugs.append(slug)
        for row in ordered_candidates:
            slug = str(row["player_id"])
            if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", slug) and slug not in slugs:
                slugs.append(slug)
        if not slugs:
            return {"attempted": 0, "imported": 0, "projected": 0, "skipped": 0}

        keys = [f"profile:{slug}" for slug in slugs]
        with self.archive.engine.connect() as conn:
            states = conn.execute(text("""
                SELECT state_key, state_json FROM external_tennis_source_state
                WHERE source='tennis_explorer' AND state_key=ANY(:keys)
            """), {"keys": keys}).all()
        prior_by_slug = {}
        for state_key, state_json in states:
            slug = state_key.removeprefix("profile:")
            if isinstance(state_json, str):
                try:
                    state_json = json.loads(state_json)
                except ValueError:
                    state_json = {}
            if isinstance(state_json, dict):
                prior_by_slug[slug] = state_json

        now = datetime.now(UTC)
        attempted = imported = projected = skipped = 0
        for slug in slugs:
            prior = prior_by_slug.get(slug) or profile_state_by_slug.get(slug, {})
            if not _tennis_explorer_profile_needs_fetch(prior, now, PROFILE_PARSER_VERSION):
                skipped += 1
                continue
            if prior.get("parser_version") != PROFILE_PARSER_VERSION and prior.get("key"):
                archived_body = _tennis_explorer_archived_profile_body(prior, self.storage)
                if archived_body is not None:
                    checked_raw = prior.get("checked_at") or prior.get("observed_at")
                    try:
                        original_fetched_at = datetime.fromisoformat(str(checked_raw).replace("Z", "+00:00"))
                        if original_fetched_at.tzinfo is None:
                            original_fetched_at = original_fetched_at.replace(tzinfo=UTC)
                        original_fetched_at = original_fetched_at.astimezone(UTC)
                        snapshot = parse_profile_page(
                            archived_body,
                            f"https://www.tennisexplorer.com/player/{slug}/?annual=all",
                            fetched_at=original_fetched_at,
                        )
                        stored = self._store_tennis_explorer_results(
                            snapshot["matches"], prior["content_sha256"],
                            fetched_at=original_fetched_at, parser_version=PROFILE_PARSER_VERSION,
                        )
                        with Session(self.archive.engine) as session:
                            projection = project_recent_tennis_explorer(session, snapshot["matches"])
                            session.commit()
                        imported += stored
                        projected += int(projection.get("projected", 0)) + int(projection.get("merged", 0))
                        skipped += 1
                        self._save_source_snapshot("tennis_explorer", f"profile:{slug}", {
                            **prior,
                            "status": 200,
                            "parser_version": PROFILE_PARSER_VERSION,
                            "rows_seen": snapshot["rows_seen"],
                            "rows_rejected": snapshot["rows_rejected"],
                            "normalized_matches": stored,
                            "accessible_years": snapshot["years"],
                            "pages_by_year": snapshot["pages_by_year"],
                            "rows_by_year": snapshot["rows_by_year"],
                            "pagination_complete": snapshot["pagination_complete"],
                            "pagination_missing_pages": snapshot["pagination_missing_pages"],
                            "pagination_selector_without_data": snapshot["pagination_selector_without_data"],
                            "pagination_data_without_selector": snapshot["pagination_data_without_selector"],
                            "history_scope": snapshot["history_scope"],
                            "complete_career_history": False,
                            "canonical_projection": projection,
                            "reparsed_from_archived_raw": True,
                            "reparsed_at": datetime.now(UTC).isoformat(),
                            # Keep the source observation time: a local parser
                            # upgrade cannot make historical data appear newer.
                            "checked_at": original_fetched_at.isoformat(),
                        })
                        continue
                    except Exception:
                        logger.exception("TennisExplorer archived profile reparse failed player=%s", slug)
            if network_paused or budget <= 0 or attempted >= budget:
                skipped += 1
                continue
            if not self._reserve_daily_call("tennis_explorer_profiles", budget):
                break
            wait = 2.0 - (time.monotonic() - self._last_tennis_explorer_request)
            if wait > 0:
                await asyncio.sleep(wait)
            # A new parser must see the body again; a conditional 304 contains
            # no content to reparse, so omit validators on parser upgrade.
            headers = _tennis_explorer_profile_request_headers(prior, PROFILE_PARSER_VERSION)
            url = f"https://www.tennisexplorer.com/player/{slug}/?annual=all"
            self._last_tennis_explorer_request = time.monotonic()
            result = await self._archive_external_get(
                "tennis_explorer", f"profile-{slug}-all", url, headers=headers,
                max_bytes=12 * 1024 * 1024,
            )
            attempted += 1
            if result.get("status") == 304:
                self._save_source_snapshot("tennis_explorer", f"profile:{slug}", {
                    **prior, "status": 200, "body_unchanged": True,
                    "checked_at": datetime.now(UTC).isoformat(),
                    "etag": result.get("etag") or prior.get("etag"),
                    "history_scope": "accessible_profile_snapshot",
                    "complete_career_history": False,
                })
                continue
            if result.get("status") == 429:
                try:
                    retry_after = min(86400, max(1, int(result.get("retry_after") or 3600)))
                except (TypeError, ValueError):
                    retry_after = 3600
                self._save_source_snapshot("tennis_explorer", "profile_pause", {
                    "status": 429, "retry_after_seconds": retry_after,
                    "paused_until": (datetime.now(UTC) + timedelta(seconds=retry_after)).isoformat(),
                })
                break
            if result.get("status") != 200 or not result.get("body"):
                self._save_source_snapshot("tennis_explorer", f"profile:{slug}", {
                    **result, "status": result.get("status", 0),
                    "error": result.get("error") or "PROFILE_HTTP_FAILURE",
                    "checked_at": datetime.now(UTC).isoformat(),
                    "history_scope": "accessible_profile_snapshot",
                    "complete_career_history": False,
                })
                continue
            fetched_at = datetime.now(UTC)
            try:
                snapshot = parse_profile_page(result["body"], url, fetched_at=fetched_at)
                stored = self._store_tennis_explorer_results(
                    snapshot["matches"], result["content_sha256"], fetched_at=fetched_at,
                    parser_version=PROFILE_PARSER_VERSION,
                )
                with Session(self.archive.engine) as session:
                    projection = project_recent_tennis_explorer(session, snapshot["matches"])
                    session.commit()
            except Exception as exc:
                logger.exception("TennisExplorer profile import failed player=%s", slug)
                self._save_source_snapshot("tennis_explorer", f"profile:{slug}", {
                    **{k: v for k, v in result.items() if k != "body"},
                    "status": 0, "error": f"IMPORT_{type(exc).__name__}",
                    "checked_at": datetime.now(UTC).isoformat(),
                    "history_scope": "accessible_profile_snapshot",
                    "complete_career_history": False,
                })
                continue
            imported += stored
            projected += int(projection.get("projected", 0)) + int(projection.get("merged", 0))
            self._save_source_snapshot("tennis_explorer", f"profile:{slug}", {
                **{k: v for k, v in result.items() if k != "body"},
                "status": 200, "checked_at": fetched_at.isoformat(),
                "parser_version": PROFILE_PARSER_VERSION,
                "rows_seen": snapshot["rows_seen"],
                "rows_rejected": snapshot["rows_rejected"],
                "normalized_matches": stored,
                "accessible_years": snapshot["years"],
                "pages_by_year": snapshot["pages_by_year"],
                "rows_by_year": snapshot["rows_by_year"],
                "pagination_complete": snapshot["pagination_complete"],
                "pagination_missing_pages": snapshot["pagination_missing_pages"],
                "pagination_selector_without_data": snapshot["pagination_selector_without_data"],
                "pagination_data_without_selector": snapshot["pagination_data_without_selector"],
                "history_scope": snapshot["history_scope"],
                "complete_career_history": False,
                "canonical_projection": projection,
            })
        totals = {"attempted": attempted, "imported": imported, "projected": projected, "skipped": skipped}
        if attempted:
            logger.info("TennisExplorer profile snapshots refreshed totals=%s", totals)
        self._save_source_snapshot("tennis_explorer", "profile_import", {
            **totals, "status": "partial_accessible_profiles",
            "daily_budget": budget,
            "history_scope": "accessible_profile_snapshot",
            "complete_career_history": False,
            "observed_at": datetime.now(UTC).isoformat(),
        })
        return totals

    def _project_tennis_explorer_recent_window(self, through_day: date) -> dict[str, int]:
        """Project the bounded recent raw window, including pages returned as HTTP 304."""
        from sqlalchemy.orm import Session
        from db.models.external_tennis import ExtTennisMatch
        from .canonical_tennis_projection import project_recent_tennis_explorer

        with Session(self.archive.engine) as session:
            start = datetime.combine(through_day - timedelta(days=7), datetime.min.time(), UTC)
            end = datetime.combine(through_day, datetime.min.time(), UTC)
            rows = session.execute(select(ExtTennisMatch).where(
                ExtTennisMatch.source == "tennis_explorer",
                ExtTennisMatch.played_at >= start,
                ExtTennisMatch.played_at < end,
            )).scalars().all()
            matches = [{
                "source_match_id": row.source_match_id, "played_at": row.played_at,
                "available_at": row.available_at, "tour_level": row.tour_level,
                "tournament": row.tournament, "surface": row.surface,
                "best_of": row.best_of, "round_name": row.round_name, "gender": row.gender,
                "winner_name": row.winner_name, "loser_name": row.loser_name,
                "winner_player_id": row.winner_player_id, "loser_player_id": row.loser_player_id,
                "score": row.score,
            } for row in rows]
            result = project_recent_tennis_explorer(session, matches)
            session.commit()
        self._save_source_snapshot("tennis_explorer", "canonical_projection", {
            **result, "status": "partial_recent_results",
            "coverage": "recent_results_only_not_player_career",
            "projected_at": datetime.now(UTC).isoformat(),
        })
        return result

    def _store_tennis_explorer_results(
        self, matches: list[dict], source_hash: str, *, fetched_at: datetime,
        parser_version: str = "tennisexplorer_results_v1",
    ) -> int:
        """Persist TE result facts, source IDs, raw-derived versions, and no career claim."""
        if not matches:
            return 0
        players = {}
        match_rows = []
        observations = []
        for match in matches:
            for side in ("winner", "loser"):
                player_id = match[f"{side}_player_id"]
                players[player_id] = {"player_id": player_id, "name": match[f"{side}_name"],
                                      "gender": match["gender"]}
            payload = {key: value for key, value in match.items() if key != "available_at"}
            payload["retrospective"] = True
            digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                               default=str).encode()).hexdigest()
            payload["fetched_at"] = fetched_at.isoformat()
            match_rows.append({**match, "stats": json.dumps(match["stats"], ensure_ascii=False)})
            observations.append({"mid": match["source_match_id"], "digest": digest,
                                 "available": fetched_at, "url": match["source_url"],
                                 "source_hash": source_hash,
                                 "payload": json.dumps(payload, ensure_ascii=False, default=str),
                                 "parser_version": parser_version})
        with self.archive.engine.begin() as conn:
            conn.execute(text("""
              INSERT INTO ext_tennis_players(id,source,source_player_id,full_name,gender)
              VALUES(gen_random_uuid(),'tennis_explorer',:player_id,:name,:gender)
              ON CONFLICT(source,source_player_id) DO UPDATE SET
                full_name=excluded.full_name,gender=excluded.gender
            """), list(players.values()))
            conn.execute(text("""
              INSERT INTO ext_tennis_matches
                (id,source,source_match_id,played_at,available_at,tour_level,tournament,surface,best_of,
                 round_name,gender,winner_name,loser_name,winner_player_id,loser_player_id,score,stats)
              VALUES(gen_random_uuid(),'tennis_explorer',:source_match_id,:played_at,:available_at,:tour_level,
                 :tournament,:surface,:best_of,:round_name,:gender,:winner_name,:loser_name,
                 :winner_player_id,:loser_player_id,:score,CAST(:stats AS jsonb))
              ON CONFLICT(source,source_match_id) DO UPDATE SET
                available_at=CASE WHEN ext_tennis_matches.played_at IS DISTINCT FROM excluded.played_at OR
                  ext_tennis_matches.tour_level IS DISTINCT FROM excluded.tour_level OR
                  ext_tennis_matches.tournament IS DISTINCT FROM excluded.tournament OR
                  ext_tennis_matches.gender IS DISTINCT FROM excluded.gender OR
                  ext_tennis_matches.winner_name IS DISTINCT FROM excluded.winner_name OR
                  ext_tennis_matches.loser_name IS DISTINCT FROM excluded.loser_name OR
                  ext_tennis_matches.score IS DISTINCT FROM excluded.score OR
                  ext_tennis_matches.stats IS DISTINCT FROM excluded.stats
                  THEN excluded.available_at ELSE ext_tennis_matches.available_at END,
                played_at=excluded.played_at,tour_level=excluded.tour_level,tournament=excluded.tournament,
                surface=excluded.surface,best_of=excluded.best_of,round_name=excluded.round_name,
                gender=excluded.gender,winner_name=excluded.winner_name,loser_name=excluded.loser_name,
                winner_player_id=excluded.winner_player_id,loser_player_id=excluded.loser_player_id,
                score=excluded.score,stats=excluded.stats
            """), match_rows)
            conn.execute(text("""
              INSERT INTO ext_tennis_match_observations
                (id,source,source_match_id,observation_hash,available_at,source_url,source_content_hash,
                 parser_version,payload,created_at)
              SELECT gen_random_uuid(),'tennis_explorer',:mid,:digest,:available,:url,:source_hash,
                     :parser_version,CAST(:payload AS jsonb),now()
              WHERE NOT EXISTS (SELECT 1 FROM ext_tennis_match_observations
                WHERE source='tennis_explorer' AND source_match_id=:mid AND observation_hash=:digest)
            """), observations)
        return len(match_rows)

    def _store_tml_csv(self, body: bytes, tour: str, source_url: str) -> int:
        """Normalize season CSV with fetched-at provenance, not fabricated historical availability."""
        fetched_at = datetime.now(UTC)
        matches, players, observations = [], [], []
        gender = "w" if tour == "wta" else "m"
        level = "challenger" if tour == "challenger" else tour
        reader = csv.DictReader(io.StringIO(body.decode("utf-8-sig", errors="replace")))
        required = {"winner_name", "loser_name", "tourney_date"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise ValueError("tml_csv_missing_required_columns")
        for row in reader:
            winner, loser = str(row.get("winner_name") or "").strip(), str(row.get("loser_name") or "").strip()
            if not winner or not loser or "/" in winner or "/" in loser:
                continue
            try:
                played_at = datetime.strptime(str(row.get("tourney_date") or "")[:8], "%Y%m%d").replace(tzinfo=UTC)
            except ValueError:
                continue
            match_id = f"{tour}-{row.get('tourney_id') or ''}-{row.get('match_num') or ''}-{row.get('winner_id') or ''}"[:128]
            if not match_id.strip("-") or match_id == tour:
                continue
            surface = str(row.get("surface") or "unknown").strip().lower()
            if surface not in {"hard", "clay", "grass", "carpet"}:
                surface = "unknown"
            stats = {key: row.get(key) for key in row if key.startswith(("w_", "l_"))}
            payload = {"row": row, "source_url": source_url, "fetched_at": fetched_at.isoformat(),
                       "retrospective": True, "event_date_precision": "provider_tournament_date",
                       "point_completeness": "none", "statistics_present": any(v not in (None, "") for v in stats.values())}
            row_hash = hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            observations.append({"mid": match_id, "digest": row_hash, "available": fetched_at,
                                 "payload": json.dumps(payload, ensure_ascii=False), "url": source_url})
            try:
                best_of = int(row.get("best_of") or 3)
            except (TypeError, ValueError):
                best_of = 3
            matches.append({"mid": match_id, "played": played_at, "available": fetched_at, "level": level,
                            "tournament": str(row.get("tourney_name") or "")[:255], "surface": surface,
                            "best_of": best_of, "round": str(row.get("round") or "")[:32], "gender": gender,
                            "winner": winner[:255], "loser": loser[:255],
                            "winner_id": str(row.get("winner_id") or "")[:64] or None,
                            "loser_id": str(row.get("loser_id") or "")[:64] or None,
                            "winner_rank": _as_int(row.get("winner_rank")), "loser_rank": _as_int(row.get("loser_rank")),
                            "score": str(row.get("score") or "")[:64], "stats": json.dumps(stats)})
            for side in ("winner", "loser"):
                player_id, name = str(row.get(f"{side}_id") or "").strip(), str(row.get(f"{side}_name") or "").strip()
                if player_id and name:
                    players.append({"player_id": player_id[:64], "name": name[:255], "gender": gender,
                                    "hand": str(row.get(f"{side}_hand") or "")[:8] or None,
                                    "ioc": str(row.get(f"{side}_ioc") or "")[:8] or None})
        # A season CSV repeats each player's source ID once per match. Sending
        # those duplicates in one INSERT .. ON CONFLICT batch makes PostgreSQL
        # reject the whole statement (one command cannot update one conflict row
        # more than once). Collapse each natural key first, retaining the last
        # occurrence in provider order so a corrected duplicate wins.
        players = list({row["player_id"]: row for row in players}.values())
        matches = list({row["mid"]: row for row in matches}.values())
        # One fetch has one available_at timestamp, and observation versions are
        # unique by (source, source_match_id, available_at), so keep one source
        # row per match in this batch as well.
        observations = list({row["mid"]: row for row in observations}.values())
        with self.archive.engine.begin() as conn:
            if players:
                conn.execute(text("""
                  INSERT INTO ext_tennis_players(id,source,source_player_id,full_name,gender,hand,ioc)
                  VALUES(gen_random_uuid(),'tml',:player_id,:name,:gender,:hand,:ioc)
                  ON CONFLICT(source,source_player_id) DO UPDATE SET full_name=excluded.full_name,
                    gender=excluded.gender,hand=coalesce(excluded.hand,ext_tennis_players.hand),ioc=coalesce(excluded.ioc,ext_tennis_players.ioc)
                """), players)
            if matches:
                conn.execute(text("""
                  INSERT INTO ext_tennis_matches
                    (id,source,source_match_id,played_at,available_at,tour_level,tournament,surface,best_of,round_name,
                     gender,winner_name,loser_name,winner_player_id,loser_player_id,winner_rank,loser_rank,score,stats)
                  VALUES(gen_random_uuid(),'tml',:mid,:played,:available,:level,:tournament,:surface,:best_of,:round,
                         :gender,:winner,:loser,:winner_id,:loser_id,:winner_rank,:loser_rank,:score,CAST(:stats AS jsonb))
                  ON CONFLICT(source,source_match_id) DO UPDATE SET
                    available_at=CASE WHEN
                      ext_tennis_matches.played_at IS DISTINCT FROM excluded.played_at OR
                      ext_tennis_matches.tour_level IS DISTINCT FROM excluded.tour_level OR
                      ext_tennis_matches.tournament IS DISTINCT FROM excluded.tournament OR
                      ext_tennis_matches.surface IS DISTINCT FROM excluded.surface OR
                      ext_tennis_matches.best_of IS DISTINCT FROM excluded.best_of OR
                      ext_tennis_matches.round_name IS DISTINCT FROM excluded.round_name OR
                      ext_tennis_matches.gender IS DISTINCT FROM excluded.gender OR
                      ext_tennis_matches.winner_name IS DISTINCT FROM excluded.winner_name OR
                      ext_tennis_matches.loser_name IS DISTINCT FROM excluded.loser_name OR
                      ext_tennis_matches.winner_player_id IS DISTINCT FROM excluded.winner_player_id OR
                      ext_tennis_matches.loser_player_id IS DISTINCT FROM excluded.loser_player_id OR
                      ext_tennis_matches.score IS DISTINCT FROM excluded.score OR
                      ext_tennis_matches.stats IS DISTINCT FROM excluded.stats
                      THEN excluded.available_at ELSE ext_tennis_matches.available_at END,
                    played_at=excluded.played_at,tour_level=excluded.tour_level,
                    tournament=excluded.tournament,surface=excluded.surface,best_of=excluded.best_of,
                    round_name=excluded.round_name,gender=excluded.gender,winner_name=excluded.winner_name,
                    loser_name=excluded.loser_name,winner_player_id=excluded.winner_player_id,
                    loser_player_id=excluded.loser_player_id,winner_rank=excluded.winner_rank,
                    loser_rank=excluded.loser_rank,score=excluded.score,stats=excluded.stats
                """), matches)
            if observations:
                conn.execute(text("""
                  INSERT INTO ext_tennis_match_observations
                    (id,source,source_match_id,observation_hash,available_at,source_url,parser_version,payload,created_at)
                  SELECT gen_random_uuid(),'tml',:mid,:digest,:available,:url,'tml_csv_v1',CAST(:payload AS jsonb),now()
                  WHERE NOT EXISTS (SELECT 1 FROM ext_tennis_match_observations
                                    WHERE source='tml' AND source_match_id=:mid AND observation_hash=:digest)
                """), observations)
        return len(matches)

    async def _fetch_tennis_abstract_profiles(self):
        """Archive partial TA fragments for active or queued full-name players.

        The provider has no stable public catalog. Candidate URLs are derived
        from canonical names, then bound only after the profile title exactly
        matches those name tokens. Initial-only aliases are never queried.
        A Flashscore CAPTCHA must not prevent this independent, low-priority
        source from filling the player's history gap.
        """
        from .tennis_abstract import PARSER_VERSION, discover_fragment, parse_recent_fragment

        now = datetime.now(UTC)
        with self.archive.engine.connect() as conn:
            candidates = conn.execute(text("""
                SELECT p.participant_id::text, cp.id::text, cp.display_name, cp.gender,
                       cp.source_ids->>'tennis_abstract' AS ta_id
                FROM tennis_player_profiles p
                JOIN ext_tennis_canonical_players cp ON cp.id=p.canonical_player_id
                LEFT JOIN tennis_player_refresh_jobs j ON j.participant_id=p.participant_id
                WHERE ((j.status IN ('queued','retry') AND j.next_run_at <= now())
                       OR (j.status='blocked' AND j.last_error='CAPTCHA_CHALLENGE_ABORTED')
                       OR EXISTS (
                           SELECT 1 FROM events e
                           WHERE e.sport_code='tennis'
                             AND (e.participant_a_id=p.participant_id OR e.participant_b_id=p.participant_id)
                             AND (e.is_live IS TRUE OR e.scheduled_start_at BETWEEN now()-interval '6 hours'
                                                                    AND now()+interval '6 hours')
                       ))
                  AND cp.gender IN ('m','w')
                  AND cp.display_name !~ '[./]'
                ORDER BY CASE WHEN EXISTS (
                             SELECT 1 FROM events e
                             WHERE e.sport_code='tennis'
                               AND (e.participant_a_id=p.participant_id OR e.participant_b_id=p.participant_id)
                               AND (e.is_live IS TRUE OR e.scheduled_start_at BETWEEN now()-interval '6 hours'
                                                                      AND now()+interval '6 hours')
                           ) THEN 0 ELSE 1 END,
                         CASE WHEN j.requested_reason='event' THEN 0 ELSE 1 END,
                         j.next_run_at, j.requested_at
                LIMIT 80
            """)).mappings().all()
            states = conn.execute(text("""
                SELECT state_key,state_json FROM external_tennis_source_state
                WHERE source='tennis_abstract' AND state_key LIKE 'profile:%'
            """)).mappings().all()
        state_by_key = {}
        for row in states:
            value = row["state_json"]
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except ValueError:
                    value = {}
            state_by_key[row["state_key"]] = value if isinstance(value, dict) else {}

        attempted = imported = skipped = rejected = 0
        seen_slugs: set[str] = set()
        quota_exhausted = False
        for candidate in candidates:
            full_name = str(candidate["display_name"] or "").strip()
            tokens = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]+", full_name)
            # Skip initials/abbreviations. Guessing their provider slug could
            # bind a same-surname player; exact title verification is a second gate.
            if len(tokens) < 2 or any(len(token) < 2 for token in tokens):
                skipped += 1
                continue
            slug = str(candidate["ta_id"] or "".join(token[:1].upper() + token[1:] for token in tokens))
            if not re.fullmatch(r"[A-Za-z0-9_-]{2,96}", slug):
                skipped += 1
                continue
            if slug in seen_slugs:
                skipped += 1
                continue
            seen_slugs.add(slug)
            profile_key = f"profile:{slug}"
            prior = state_by_key.get(profile_key, {})
            checked_raw = prior.get("checked_at")
            if checked_raw:
                try:
                    checked_at = datetime.fromisoformat(checked_raw.replace("Z", "+00:00"))
                    retry_at = datetime.fromisoformat(prior.get("retry_after_at", "").replace("Z", "+00:00")) \
                        if prior.get("retry_after_at") else checked_at + timedelta(days=7 if prior.get("status") == 200 else 1)
                    if now < retry_at:
                        skipped += 1
                        continue
                except (TypeError, ValueError):
                    pass

            gender_path = "wplayer.cgi" if candidate["gender"] == "w" else "player.cgi"
            profile_url = f"https://www.tennisabstract.com/cgi-bin/{gender_path}?p={slug}"
            headers = {"User-Agent": "Mozilla/5.0 NeurobetResearch/1.0"}
            bodies = []
            urls = [profile_url]
            identity = None
            try:
                for index in range(2):
                    if not self._reserve_daily_call("tennis_abstract", 10):
                        quota_exhausted = True
                        break
                    wait = 2.0 - (time.monotonic() - self._last_tennis_abstract_request)
                    if wait > 0:
                        await asyncio.sleep(wait)
                    url = urls[index]
                    result = await self._archive_external_get(
                        "tennis-abstract", f"{slug}-{('profile' if index == 0 else 'fragment')}",
                        url, headers=headers, max_bytes=16 * 1024 * 1024,
                    )
                    self._last_tennis_abstract_request = time.monotonic()
                    attempted += 1
                    if result.get("status") == 429:
                        try:
                            retry = min(86400, max(1, int(result.get("retry_after") or 3600)))
                        except (TypeError, ValueError):
                            retry = 3600
                        self._save_source_snapshot("tennis_abstract", "pause", {
                            "status": 429, "retry_after_seconds": retry,
                            "paused_until": (datetime.now(UTC) + timedelta(seconds=retry)).isoformat(),
                        })
                        break
                    if result.get("status") != 200 or not result.get("body"):
                        self._save_source_snapshot("tennis_abstract", profile_key, {
                            **{k: v for k, v in result.items() if k != "body"},
                            "status": result.get("status", 0), "checked_at": datetime.now(UTC).isoformat(),
                            "error": result.get("error") or f"HTTP_{result.get('status')}",
                            "history_scope": "not_imported",
                        })
                        break
                    bodies.append(result)
                    if index == 0:
                        try:
                            identity = discover_fragment(result["body"], profile_url, full_name)
                        except ValueError as exc:
                            # A valid profile page can exist without the recent-results
                            # fragment. Keep its raw reference and suppress repeated
                            # probes for a week; this is a capability gap, not a
                            # transport error and must not burn a second request.
                            reason = str(exc)
                            retry_days = 7 if "fragment_" in reason else 1
                            self._save_source_snapshot("tennis_abstract", profile_key, {
                                **{k: v for k, v in result.items() if k != "body"},
                                "status": 200, "checked_at": result.get("fetched_at") or datetime.now(UTC).isoformat(),
                                "profile_url": profile_url,
                                "profile_raw_object_key": result.get("key"),
                                "profile_content_sha256": result.get("content_sha256"),
                                "error": "profile_capability_unavailable",
                                "reason": reason,
                                "retry_after_at": (datetime.now(UTC) + timedelta(days=retry_days)).isoformat(),
                                "history_scope": "not_imported", "usable_for_metrics": False,
                                "complete": False,
                            })
                            bodies = []
                            break
                        urls.append(identity["fragment_url"])
                if len(bodies) != 2:
                    if quota_exhausted:
                        break
                    continue
                fetched_at = datetime.fromisoformat(bodies[1]["fetched_at"])
                snapshot = parse_recent_fragment(
                    bodies[1]["body"], source_player_id=identity["source_player_id"],
                    expected_name=full_name, fetched_at=fetched_at,
                )
                self._store_tennis_abstract_observations(
                    snapshot["matches"], profile_id=candidate["id"], source_url=identity["fragment_url"],
                    source_player_id=identity["source_player_id"], profile_result=bodies[0],
                    fragment_result=bodies[1], fetched_at=fetched_at,
                )
                imported += len(snapshot["matches"])
                from sqlalchemy.orm import Session
                from .canonical_tennis_projection import project_recent_tennis_abstract

                with Session(self.archive.engine) as session:
                    projection = project_recent_tennis_abstract(
                        session, snapshot["matches"], target_player_id=candidate["id"],
                        source_player_id=identity["source_player_id"],
                    )
                    session.commit()
                state = {
                    **{k: v for k, v in bodies[1].items() if k != "body"},
                    "status": 200, "checked_at": fetched_at.isoformat(),
                    "profile_url": profile_url, "fragment_url": identity["fragment_url"],
                    "source_player_id": identity["source_player_id"],
                    "profile_content_sha256": bodies[0]["content_sha256"],
                    "parser_version": PARSER_VERSION, "rows_seen": snapshot["rows_seen"],
                    "rows_rejected": snapshot["rows_rejected"],
                    "normalized_matches": len(snapshot["matches"]),
                    "canonical_projection": projection,
                    "history_scope": "recent_fragment_not_full_career", "complete": False,
                    "serve_statistics_usable": False,
                    "usable_for_metrics": False, "metrics_materialized": False,
                    "metrics_usage": "partial_recent_history_pending_materialization",
                }
                self._save_source_snapshot("tennis_abstract", profile_key, state)
                metrics_materialized = False
                if projection["projected"] + projection["merged"] > 0:
                    from db.models import Participant, TennisPlayerProfile
                    from .player_profiles import seed_profile_from_archive
                    with Session(self.archive.engine) as session:
                        participant = session.get(Participant, candidate["participant_id"])
                        profile = session.get(TennisPlayerProfile, candidate["participant_id"])
                        metrics_materialized = bool(
                            participant is not None and profile is not None
                            and seed_profile_from_archive(session, participant, profile)
                        )
                        session.commit()
                    state.update({
                        "metrics_materialized": metrics_materialized,
                        "usable_for_metrics": metrics_materialized,
                        "metrics_usage": "partial_recent_history_only" if metrics_materialized else "not_materialized",
                    })
                    self._save_source_snapshot("tennis_abstract", profile_key, state)
            except (ValueError, httpx.HTTPError, KeyError) as exc:
                rejected += 1
                # Preserve the response references even when parsing fails so
                # the parser can be repaired and replayed without another call.
                profile_result = bodies[0] if bodies else None
                fragment_result = bodies[1] if len(bodies) > 1 else None
                reason = str(exc)[:180]
                missing_fragment = "fragment_missing" in reason
                self._save_source_snapshot("tennis_abstract", profile_key, {
                    "status": 200 if profile_result else 0,
                    "checked_at": (fragment_result or profile_result or {}).get("fetched_at") or datetime.now(UTC).isoformat(),
                    "error": "profile_capability_unavailable" if missing_fragment else type(exc).__name__,
                    "reason": reason,
                    "profile_url": profile_url,
                    "profile_raw_object_key": (profile_result or {}).get("key"),
                    "profile_content_sha256": (profile_result or {}).get("content_sha256"),
                    "fragment_raw_object_key": (fragment_result or {}).get("key"),
                    "fragment_content_sha256": (fragment_result or {}).get("content_sha256"),
                    "retry_after_at": (datetime.now(UTC) + timedelta(days=7 if missing_fragment or "identity_mismatch" in reason else 1)).isoformat(),
                    "history_scope": "not_imported", "usable_for_metrics": False,
                    "complete": False,
                })
        self._save_source_snapshot("tennis_abstract", "profile_import", {
            "status": "partial_recent_fragments", "attempted_requests": attempted,
            "imported_match_observations": imported, "skipped": skipped, "rejected_profiles": rejected,
            "daily_request_budget": 10, "complete_career_history": False,
            "observed_at": datetime.now(UTC).isoformat(),
        })
        await self._refresh_public_sources()

    def _store_tennis_abstract_observations(self, matches, *, profile_id, source_player_id, source_url,
                                            profile_result, fragment_result, fetched_at):
        """Keep verified TA rows append-only and separate from canonical facts."""
        from sqlalchemy import text as sql_text

        with self.archive.engine.begin() as conn:
            for item in matches:
                payload = {**item, "profile_participant_id": profile_id,
                           "source_file_sha256": fragment_result["content_sha256"],
                           "profile_sha256": profile_result["content_sha256"],
                           "raw_object_key": fragment_result["key"],
                           "profile_raw_object_key": profile_result["key"],
                           "completeness": "partial_recent_results"}
                digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                conn.execute(sql_text("""
                    INSERT INTO ext_tennis_match_observations
                      (id,source,source_match_id,observation_hash,available_at,source_url,
                       source_content_hash,parser_version,payload,created_at)
                    VALUES (gen_random_uuid(),'tennis_abstract',:mid,:digest,:available,:url,
                            :file_hash,:parser,CAST(:payload AS jsonb),now())
                    ON CONFLICT (source,source_match_id,available_at) DO NOTHING
                """), {"mid": item["source_match_id"], "digest": digest, "available": fetched_at,
                       "url": source_url, "file_hash": fragment_result["content_sha256"],
                       "parser": "tennis_abstract_recent_fragment_v1",
                       "payload": json.dumps(payload, ensure_ascii=False)})
            # The profile page title was checked against this exact canonical
            # player; only now may its namespaced provider ID be recorded.
            identity_update = conn.execute(sql_text("""
                UPDATE ext_tennis_canonical_players
                SET source_ids=jsonb_set(COALESCE(source_ids,'{}'::jsonb),'{tennis_abstract}',to_jsonb(CAST(:slug AS text)),true)
                WHERE id=CAST(:profile_id AS uuid)
                  AND (source_ids->>'tennis_abstract' IS NULL OR source_ids->>'tennis_abstract'=:slug)
            """), {"profile_id": profile_id, "slug": source_player_id})
            if identity_update.rowcount != 1:
                raise ValueError("tennis_abstract_source_identity_conflict")

    async def _check_charting_project(self):
        from .match_charting import iter_point_documents, parse_match_metadata

        base = "https://raw.githubusercontent.com/JeffSackmann/tennis_MatchChartingProject/master"
        sources = []
        for gender in ("m", "w"):
            sources.append((f"{gender}_matches", f"charting-{gender}-matches.csv", gender, "matches"))
            for period in ("2020s", "2010s", "to-2009"):
                name = f"{gender}_points_{period.replace('-', '_')}"
                sources.append((name, f"charting-{gender}-points-{period}.csv", gender, "points"))
        for name, filename, gender, kind in sources:
            url = f"{base}/{filename}"
            with self.archive.engine.connect() as conn:
                current = conn.execute(text("SELECT state_json FROM external_tennis_source_state WHERE source='match_charting_project' AND state_key=:key"), {"key": name}).scalar()
            headers = {"If-None-Match": current.get("etag")} if isinstance(current, dict) and current.get("etag") else {}
            result = await self._archive_external_get("match-charting-project", name, url, headers=headers, max_bytes=256 * 1024 * 1024)
            if result.get("status") == 200 and result.get("body"):
                try:
                    if kind == "matches":
                        diagnostics: list[dict[str, Any]] = []
                        rows = parse_match_metadata(result["body"], gender=gender, diagnostics=diagnostics)
                        self._store_charting_metadata(rows, url, result)
                        result["record_count"] = len(rows)
                        result["rejected_rows"] = diagnostics
                    else:
                        result["record_count"] = self._store_charting_points(iter_point_documents(result["body"]), url, result)
                    result["status"] = 200
                    result["retrospective"] = True
                    result["coverage"] = "selectively_charted_matches"
                    result["complete_match_coverage"] = False
                    result["parser_version"] = "match_charting_project_csv_v1"
                    self._save_source_snapshot("match_charting_project", name, result)
                except (ValueError, UnicodeError) as exc:
                    logger.exception("Match Charting Project import rejected partition=%s error=%s", name, type(exc).__name__)
                    self._save_source_snapshot("match_charting_project", name, {
                        "status": 0, "error": type(exc).__name__, "content_sha256": result.get("content_sha256"),
                        "key": result.get("key"), "raw_bytes": result.get("raw_bytes"),
                        "fetched_at": result.get("fetched_at"), "coverage": "not_imported",
                    })
            await asyncio.sleep(2)

    def _store_charting_metadata(self, rows: list[dict[str, Any]], source_url: str, result: dict[str, Any]) -> None:
        available_at = datetime.fromisoformat(result["fetched_at"])
        with self.archive.engine.begin() as conn:
            for row in rows:
                conn.execute(text("""
                  INSERT INTO ext_tennis_match_observations
                    (id,source,source_match_id,observation_hash,available_at,source_url,source_content_hash,
                     parser_version,payload,created_at)
                  SELECT gen_random_uuid(),'match_charting_project',:match_id,:row_hash,:available,:url,:file_hash,
                         'match_charting_project_csv_v1',CAST(:payload AS jsonb),now()
                  WHERE NOT EXISTS (
                    SELECT 1 FROM ext_tennis_match_observations
                    WHERE source='match_charting_project' AND source_match_id=:match_id
                      AND observation_hash=:row_hash
                  ) ON CONFLICT (source,source_match_id,available_at) DO NOTHING
                """), {"match_id": row["source_match_id"], "row_hash": row["observation_hash"],
                      "available": available_at, "url": source_url, "file_hash": result["content_sha256"],
                      "payload": json.dumps(row["payload"], ensure_ascii=False)})

    def _store_charting_points(self, documents, source_url: str, result: dict[str, Any]) -> int:
        available_at = datetime.fromisoformat(result["fetched_at"])
        saved = 0
        batch = []

        def persist_batch(items):
            nonlocal saved
            with self.archive.engine.begin() as conn:
                for document in items:
                    quality = {
                        **document["quality"],
                        "source_file_sha256": result["content_sha256"],
                        "raw_object_key": result["key"],
                        "source_url": source_url,
                        "retrospective": True,
                    }
                    inserted = conn.execute(text("""
                      WITH new_document AS (
                        INSERT INTO tennis_point_documents
                          (id,source,source_match_id,source_url,content_hash,archived_path,parser_version,
                           retrieved_at,parsed_at,available_at,availability_status,retrospective,parse_status,quality_json)
                        SELECT gen_random_uuid(),'match_charting_project',:match_id,:url,:content_hash,:raw_key,
                               'match_charting_project_csv_v1',:available,:available,:available,'known',true,'partial',CAST(:quality AS jsonb)
                        WHERE NOT EXISTS (
                          SELECT 1 FROM tennis_point_documents
                          WHERE source='match_charting_project' AND source_match_id=:match_id
                            AND content_hash=:content_hash AND parser_version='match_charting_project_csv_v1'
                        ) RETURNING id
                      )
                      INSERT INTO tennis_point_history_versions
                        (id,document_id,source_match_id,fonbet_event_id,parser_version,retrieved_at,parsed_at,
                         available_at,availability_status,events_json,quality_json,retrospective)
                      SELECT gen_random_uuid(),id,:match_id,NULL,'match_charting_project_csv_v1',:available,:available,
                             :available,'known',CAST(:events AS jsonb),CAST(:quality AS jsonb),true
                      FROM new_document RETURNING id
                    """), {"match_id": document["source_match_id"], "url": source_url,
                          "content_hash": document["content_hash"], "raw_key": result["key"], "available": available_at,
                          "quality": json.dumps(quality, ensure_ascii=False),
                          "events": json.dumps(document["events"], ensure_ascii=False)}).first()
                    saved += int(inserted is not None)

        for document in documents:
            batch.append(document)
            if len(batch) >= 50:
                persist_batch(batch)
                batch = []
        if batch:
            persist_batch(batch)
        return saved


async def main():
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s UTC [%(levelname)s] [tennis-sources] %(message)s")
    logging.Formatter.converter = time.gmtime
    worker = ExternalTennisSourcesWorker()
    await worker.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
