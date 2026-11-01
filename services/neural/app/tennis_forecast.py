"""Odds-free tennis forecasts and legal short-horizon score scenarios."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple
from contracts.tennis_pit import validate_feature_source_availability
from contracts.tennis_pit import resolve_event_attribute_observations


POINTS = {"0": 0, "00": 0, "15": 1, "30": 2, "40": 3, "A": 4, "AD": 4, "ADV": 4}
ODDS_FREE_FEATURE_VERSION_V1 = "tennis_odds_free_v1"
ODDS_FREE_FEATURE_VERSION = "tennis_odds_free_v2"
ODDS_FREE_BASELINE_FEATURE_VERSION = "tennis_odds_free_v3"
ODDS_FREE_BASELINE_MODEL_VERSION = "tennis_v1.2.0"
ODDS_FREE_BASELINE_PARAMETERS = {
    "elo_scale": 400.0, "glicko_scale": 400.0, "glicko_uncertainty_scale": 350.0,
    "rank_max_age_days": 30, "form_beta_alpha": 1.0, "form_beta_beta": 1.0,
    "serve_prior": 0.62, "return_prior": 0.38, "serve_prior_points": 200.0,
    "serve_probability_min": 0.35, "serve_probability_max": 0.90,
    "serve_stat_blend_points": 200.0, "strength_components_equal_weight": True,
    "h2h_beta_alpha": 1.0, "h2h_beta_beta": 1.0,
    "ranking_sources": ("atp_official", "wta_official"),
}
ODDS_FREE_BASELINE_PARAMETERS_DIGEST = hashlib.sha256(
    json.dumps(ODDS_FREE_BASELINE_PARAMETERS, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()
ODDS_FREE_FEATURE_SOURCE_GROUPS_V1 = {
    "player_metrics": (
        "elo_a", "elo_b", "elo_diff", "win_rate_90d_a", "win_rate_90d_b",
        "win_rate_90d_diff", "match_count_a", "match_count_b",
        "player_metrics_missing_a", "player_metrics_missing_b",
    ),
    "serve_metrics_a": ("serve_probability_a", "serve_sample_weight_a"),
    "serve_metrics_b": ("serve_probability_b", "serve_sample_weight_b"),
    "event_state": (
        "sets_a", "sets_b", "games_a", "games_b", "points_a", "points_b",
          "server_a", "server_b", "server_unknown", "is_tiebreak",
    ),
      "event_format": ("sets_to_win",),
    "event_surface": ("surface_hard", "surface_clay", "surface_grass", "surface_carpet", "surface_unknown"),
}
ODDS_FREE_FEATURE_NAMES_V1 = (
    "elo_a", "elo_b", "elo_diff",
    "win_rate_90d_a", "win_rate_90d_b", "win_rate_90d_diff",
    "match_count_a", "match_count_b",
    "player_metrics_missing_a", "player_metrics_missing_b",
    "serve_probability_a", "serve_probability_b",
    "serve_sample_weight_a", "serve_sample_weight_b",
    "sets_a", "sets_b", "games_a", "games_b", "points_a", "points_b",
    "server_a", "server_b", "server_unknown", "is_tiebreak", "sets_to_win",
    "surface_hard", "surface_clay", "surface_grass", "surface_carpet", "surface_unknown",
)
ODDS_FREE_FEATURE_SOURCE_GROUPS = {
    "player_metrics": ODDS_FREE_FEATURE_SOURCE_GROUPS_V1["player_metrics"],
    "serve_metrics_a": ODDS_FREE_FEATURE_SOURCE_GROUPS_V1["serve_metrics_a"],
    "serve_metrics_b": ODDS_FREE_FEATURE_SOURCE_GROUPS_V1["serve_metrics_b"],
    "event_state": ODDS_FREE_FEATURE_SOURCE_GROUPS_V1["event_state"],
    # A known format has timely source evidence. Unknown is represented by the
    # mask and zero value, and is therefore not a missing-source failure.
    "event_format": ("sets_to_win", "match_format_known"),
}
ODDS_FREE_FEATURE_NAMES = ODDS_FREE_FEATURE_NAMES_V1[:25] + ("match_format_known",)
ODDS_FREE_FEATURE_CONTRACTS = {
    ODDS_FREE_FEATURE_VERSION_V1: {
        "names": ODDS_FREE_FEATURE_NAMES_V1,
        "source_groups": ODDS_FREE_FEATURE_SOURCE_GROUPS_V1,
    },
    ODDS_FREE_FEATURE_VERSION: {
        "names": ODDS_FREE_FEATURE_NAMES,
        "source_groups": ODDS_FREE_FEATURE_SOURCE_GROUPS,
    },
}


def odds_free_feature_contract(version: str = ODDS_FREE_FEATURE_VERSION) -> dict:
    """Return an immutable versioned feature contract or reject unknown versions."""
    try:
        contract = ODDS_FREE_FEATURE_CONTRACTS[version]
    except KeyError as exc:
        raise ValueError("unsupported_odds_free_feature_contract") from exc
    return {"version": version, "names": contract["names"], "source_groups": contract["source_groups"]}
ODDS_FREE_BASELINE_FEATURE_NAMES = (
    "elo_a", "elo_b", "surface_elo_a", "surface_elo_b", "glicko_a", "glicko_b",
    "glicko_rd_a", "glicko_rd_b", "history_matches_a", "history_matches_b",
    "rank_a", "rank_b", "rank_missing_a", "rank_missing_b",
    "rolling_win_rate_90d_a", "rolling_win_rate_90d_b", "rolling_matches_90d_a", "rolling_matches_90d_b",
    "serve_rate_a", "serve_rate_b", "serve_points_a", "serve_points_b",
    "return_rate_a", "return_rate_b", "return_points_a", "return_points_b",
    "h2h_matches", "h2h_wins_a", "h2h_wins_b",
    "player_metrics_missing_a", "player_metrics_missing_b",
    "sets_a", "sets_b", "games_a", "games_b", "points_a", "points_b",
    "server_a", "server_b", "server_unknown", "is_tiebreak", "sets_to_win",
    "surface_hard", "surface_clay", "surface_grass", "surface_carpet", "surface_unknown",
)


def validate_odds_free_feature_availability(
    as_of: datetime, source_available_at: Dict[str, Any],
    contract_defaults: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Fail closed unless a source is causal or a declared contract prior is used."""
    defaulted = set(contract_defaults or ())
    result = validate_feature_source_availability(
        as_of,
        source_available_at,
        ODDS_FREE_FEATURE_SOURCE_GROUPS,
        allowed_missing_groups=defaulted,
    )
    result["contract_defaulted_sources"] = sorted(defaulted)
    return result


def point_in_time_surface(metadata: Any, as_of: datetime) -> Tuple[str, Optional[str], str]:
    """Use event surface only when its own source timestamp is explicit and causal."""
    values = metadata if isinstance(metadata, dict) else {}
    surface = str(values.get("surface") or "").strip().casefold()
    raw_available = values.get("surface_available_at")
    if isinstance(raw_available, str):
        try:
            raw_available = datetime.fromisoformat(raw_available.replace("Z", "+00:00"))
        except ValueError:
            raw_available = None
    if not isinstance(raw_available, datetime):
        return "unknown", None, "missing_source_timestamp"
    available = raw_available.replace(tzinfo=timezone.utc) if raw_available.tzinfo is None else raw_available.astimezone(timezone.utc)
    cutoff = as_of.replace(tzinfo=timezone.utc) if as_of.tzinfo is None else as_of.astimezone(timezone.utc)
    if available > cutoff:
        return "unknown", available.isoformat(), "source_after_cutoff"
    if surface not in {"hard", "clay", "grass", "carpet"}:
        return "unknown", available.isoformat(), "unsupported_or_missing_surface"
    return surface, available.isoformat(), "observed"


def point_in_time_best_of(metadata: Any, as_of: datetime) -> Tuple[Optional[int], Optional[str], str]:
    """Use match format only when an explicit source value has a causal observation time."""
    values = metadata if isinstance(metadata, dict) else {}
    raw_best_of = values.get("best_of")
    try:
        if isinstance(raw_best_of, float) and not raw_best_of.is_integer():
            raise ValueError("non_integer_best_of")
        best_of = int(raw_best_of) if not isinstance(raw_best_of, bool) else None
    except (TypeError, ValueError, OverflowError):
        best_of = None
    if best_of not in (3, 5):
        return None, None, "missing_or_unsupported_value"
    raw_available = values.get("best_of_available_at")
    if isinstance(raw_available, str):
        try:
            raw_available = datetime.fromisoformat(raw_available.replace("Z", "+00:00"))
        except ValueError:
            raw_available = None
    if not isinstance(raw_available, datetime):
        return None, None, "missing_source_timestamp"
    available = raw_available.replace(tzinfo=timezone.utc) if raw_available.tzinfo is None else raw_available.astimezone(timezone.utc)
    cutoff = as_of.replace(tzinfo=timezone.utc) if as_of.tzinfo is None else as_of.astimezone(timezone.utc)
    if available > cutoff:
        return None, available.isoformat(), "source_after_cutoff"
    return best_of, available.isoformat(), "observed"


@dataclass(frozen=True)
class TennisState:
    sets_a: int = 0
    sets_b: int = 0
    games_a: int = 0
    games_b: int = 0
    points_a: int = 0
    points_b: int = 0
    server: Optional[str] = None
    tiebreak: bool = False
    sets_to_win: int = 2
    tiebreak_server: Optional[str] = None


def build_odds_free_feature_vector(
    state: TennisState,
    pair: Dict[str, Any],
    serve_a: Tuple[float, float, float],
    serve_b: Tuple[float, float, float],
    surface: str = "unknown",
) -> Tuple[float, ...]:
    """Build the odds-free v2 vector without surface or inferred match format."""
    values = {
        "elo_a": pair.get("elo_a", 1500.0), "elo_b": pair.get("elo_b", 1500.0),
        "elo_diff": pair.get("elo_diff", 0.0),
        "win_rate_90d_a": pair.get("win_rate_90d_a", 0.5),
        "win_rate_90d_b": pair.get("win_rate_90d_b", 0.5),
        "win_rate_90d_diff": pair.get("win_rate_90d_diff", 0.0),
        "match_count_a": pair.get("match_count_a", 0), "match_count_b": pair.get("match_count_b", 0),
        "player_metrics_missing_a": pair.get("player_metrics_missing_a", True),
        "player_metrics_missing_b": pair.get("player_metrics_missing_b", True),
        "serve_probability_a": serve_a[0] if serve_a[2] > 0 else 0.62,
        "serve_probability_b": serve_b[0] if serve_b[2] > 0 else 0.62,
        "serve_sample_weight_a": serve_a[2], "serve_sample_weight_b": serve_b[2],
        "sets_a": state.sets_a, "sets_b": state.sets_b,
        "games_a": state.games_a, "games_b": state.games_b,
        "points_a": state.points_a, "points_b": state.points_b,
        "server_a": state.server == "a", "server_b": state.server == "b",
        "server_unknown": state.server not in {"a", "b"},
        "is_tiebreak": state.tiebreak,
        "sets_to_win": state.sets_to_win if pair.get("match_format_known", False) else 0,
        "match_format_known": pair.get("match_format_known", False),
    }
    vector = tuple(float(bool(values[name])) if isinstance(values[name], bool) else float(values[name])
                   for name in ODDS_FREE_FEATURE_NAMES)
    if len(vector) != len(ODDS_FREE_FEATURE_NAMES) or any(not math.isfinite(value) for value in vector):
        raise ValueError("invalid_odds_free_feature_vector")
    return vector


def build_odds_free_feature_vector_v1(
    state: TennisState, pair: Dict[str, Any], serve_a: Tuple[float, float, float],
    serve_b: Tuple[float, float, float], surface: str = "unknown",
) -> Tuple[float, ...]:
    """Reproduce the archived v1 feature vector for existing audit artifacts."""
    surface_key = str(surface or "unknown").strip().casefold()
    surface_key = surface_key if surface_key in {"hard", "clay", "grass", "carpet"} else "unknown"
    values = {
        "elo_a": pair.get("elo_a", 1500.0), "elo_b": pair.get("elo_b", 1500.0),
        "elo_diff": pair.get("elo_diff", 0.0),
        "win_rate_90d_a": pair.get("win_rate_90d_a", 0.5),
        "win_rate_90d_b": pair.get("win_rate_90d_b", 0.5),
        "win_rate_90d_diff": pair.get("win_rate_90d_diff", 0.0),
        "match_count_a": pair.get("match_count_a", 0), "match_count_b": pair.get("match_count_b", 0),
        "player_metrics_missing_a": pair.get("player_metrics_missing_a", True),
        "player_metrics_missing_b": pair.get("player_metrics_missing_b", True),
        "serve_probability_a": serve_a[0], "serve_probability_b": serve_b[0],
        "serve_sample_weight_a": serve_a[2], "serve_sample_weight_b": serve_b[2],
        "sets_a": state.sets_a, "sets_b": state.sets_b, "games_a": state.games_a, "games_b": state.games_b,
        "points_a": state.points_a, "points_b": state.points_b,
        "server_a": state.server == "a", "server_b": state.server == "b",
        "server_unknown": state.server not in {"a", "b"}, "is_tiebreak": state.tiebreak,
        "sets_to_win": state.sets_to_win,
        "surface_hard": surface_key == "hard", "surface_clay": surface_key == "clay",
        "surface_grass": surface_key == "grass", "surface_carpet": surface_key == "carpet",
        "surface_unknown": surface_key == "unknown",
    }
    return tuple(float(bool(values[name])) if isinstance(values[name], bool) else float(values[name])
                 for name in ODDS_FREE_FEATURE_NAMES_V1)


def build_odds_free_baseline_feature_vector(
    state: TennisState, pair: Dict[str, Any], serve_a: Tuple[float, float, float],
    serve_b: Tuple[float, float, float], surface: str = "unknown",
) -> Tuple[float, ...]:
    """Version 2 baseline input, with explicit missingness and PIT-only player metrics."""
    surface_key = str(surface or "unknown").strip().casefold()
    surface_key = surface_key if surface_key in {"hard", "clay", "grass", "carpet"} else "unknown"
    rank_a, rank_b = pair.get("tour_rank_a"), pair.get("tour_rank_b")
    values = {
        "elo_a": pair.get("independent_elo_a", 1500.0), "elo_b": pair.get("independent_elo_b", 1500.0),
        "surface_elo_a": pair.get("independent_surface_elo_a", 1500.0), "surface_elo_b": pair.get("independent_surface_elo_b", 1500.0),
        "glicko_a": pair.get("independent_glicko_a", 1500.0), "glicko_b": pair.get("independent_glicko_b", 1500.0),
        "glicko_rd_a": pair.get("independent_rd_a", 350.0), "glicko_rd_b": pair.get("independent_rd_b", 350.0),
        "history_matches_a": pair.get("independent_matches_a", 0), "history_matches_b": pair.get("independent_matches_b", 0),
        "rank_a": rank_a or 0, "rank_b": rank_b or 0, "rank_missing_a": rank_a is None, "rank_missing_b": rank_b is None,
        "rolling_win_rate_90d_a": pair.get("rolling_90d_a", 0.5), "rolling_win_rate_90d_b": pair.get("rolling_90d_b", 0.5),
        "rolling_matches_90d_a": pair.get("rolling_90d_matches_a", 0), "rolling_matches_90d_b": pair.get("rolling_90d_matches_b", 0),
        "serve_rate_a": serve_a[0], "serve_rate_b": serve_b[0],
        "serve_points_a": pair.get("serve_points_a", 0), "serve_points_b": pair.get("serve_points_b", 0),
        "return_rate_a": pair.get("return_rate_a", 0.38), "return_rate_b": pair.get("return_rate_b", 0.38),
        "return_points_a": pair.get("return_points_a", 0), "return_points_b": pair.get("return_points_b", 0),
        "h2h_matches": pair.get("h2h_matches", 0),
        "h2h_wins_a": pair.get("h2h_wins_a", 0), "h2h_wins_b": pair.get("h2h_wins_b", 0),
        "player_metrics_missing_a": pair.get("player_metrics_missing_a", True), "player_metrics_missing_b": pair.get("player_metrics_missing_b", True),
        "sets_a": state.sets_a, "sets_b": state.sets_b, "games_a": state.games_a, "games_b": state.games_b,
        "points_a": state.points_a, "points_b": state.points_b,
        "server_a": state.server == "a", "server_b": state.server == "b", "server_unknown": state.server not in {"a", "b"},
        "is_tiebreak": state.tiebreak, "sets_to_win": state.sets_to_win if pair.get("match_format_known", True) else 0,
        "surface_hard": surface_key == "hard", "surface_clay": surface_key == "clay",
        "surface_grass": surface_key == "grass", "surface_carpet": surface_key == "carpet", "surface_unknown": surface_key == "unknown",
    }
    vector = tuple(float(bool(values[name])) if isinstance(values[name], bool) else float(values[name] or 0.0)
                   for name in ODDS_FREE_BASELINE_FEATURE_NAMES)
    if len(vector) != len(ODDS_FREE_BASELINE_FEATURE_NAMES) or any(not math.isfinite(value) for value in vector):
        raise ValueError("invalid_odds_free_baseline_feature_vector")
    return vector


def independent_strength_prior(pair: Dict[str, Any]) -> Dict[str, Any]:
    """Combine only PIT-known strength components with equal logit weight."""
    components = []
    elo_a, elo_b = pair.get("strength_elo_a", pair.get("independent_elo_a")), pair.get("strength_elo_b", pair.get("independent_elo_b"))
    both_metrics = bool(pair.get("independent_metrics_available_a") and pair.get("independent_metrics_available_b"))
    if both_metrics and elo_a is not None and elo_b is not None:
        components.append({"name": "elo", "probability": _sigmoid((float(elo_a) - float(elo_b)) / 400.0)})
    rating_a, rating_b = pair.get("independent_glicko_a"), pair.get("independent_glicko_b")
    rd_a, rd_b = pair.get("independent_rd_a"), pair.get("independent_rd_b")
    if both_metrics and None not in (rating_a, rating_b, rd_a, rd_b):
        uncertainty = math.sqrt(1.0 + 3.0 * ((float(rd_a) ** 2 + float(rd_b) ** 2) / 2.0) / (math.pi ** 2 * 400.0 ** 2))
        components.append({"name": "glicko", "probability": _sigmoid((float(rating_a) - float(rating_b)) / (400.0 * uncertainty))})
    rank_a, rank_b = pair.get("tour_rank_a"), pair.get("tour_rank_b")
    if rank_a and rank_b and int(rank_a) > 0 and int(rank_b) > 0 and pair.get("ranking_comparable", True):
        components.append({"name": "tour_rank", "probability": math.sqrt(float(rank_b)) / (math.sqrt(float(rank_a)) + math.sqrt(float(rank_b)))})
    recent_a, recent_b = pair.get("rolling_90d_summary_a"), pair.get("rolling_90d_summary_b")
    if recent_a and recent_b and both_metrics and (int(recent_a.get("matches", 0)) + int(recent_b.get("matches", 0)) > 0):
        # Beta(1,1) smoothing preserves a neutral 0.5 when neither has played.
        p_a = (float(recent_a.get("wins", 0)) + 1.0) / (float(recent_a.get("matches", 0)) + 2.0)
        p_b = (float(recent_b.get("wins", 0)) + 1.0) / (float(recent_b.get("matches", 0)) + 2.0)
        components.append({"name": "rolling_90d", "probability": _sigmoid(_logit(p_a) - _logit(p_b))})
    h2h_matches = int(pair.get("h2h_matches", 0) or 0)
    if both_metrics and pair.get("h2h_available") and h2h_matches > 0:
        alpha = float(ODDS_FREE_BASELINE_PARAMETERS["h2h_beta_alpha"])
        beta = float(ODDS_FREE_BASELINE_PARAMETERS["h2h_beta_beta"])
        h2h_wins_a = float(pair.get("h2h_wins_a", 0))
        h2h_wins_b = float(pair.get("h2h_wins_b", 0))
        probability = (h2h_wins_a + alpha) / (h2h_wins_a + h2h_wins_b + alpha + beta)
        components.append({"name": "head_to_head", "probability": probability,
                           "matches": h2h_matches, "wins_a": int(h2h_wins_a), "wins_b": int(h2h_wins_b)})
    if not components:
        return {"probability": None, "components": [], "missing_components": ["elo", "glicko", "tour_rank", "rolling_90d", "head_to_head"]}
    prior = _sigmoid(sum(_logit(x["probability"]) for x in components) / len(components))
    present = {x["name"] for x in components}
    return {"probability": prior, "components": components,
            "missing_components": [name for name in ("elo", "glicko", "tour_rank", "rolling_90d", "head_to_head") if name not in present]}


def apply_independent_strength_prior(serve_a, serve_b, prior_probability, state, point_volumes):
    """Blend observed serve/return evidence into the prior via opposite logit shifts."""
    volumes = [max(0.0, float(value or 0.0)) for value in point_volumes]
    weight = sum(value / (value + 200.0) for value in volumes) / 4.0
    if weight <= 0:
        return serve_a, serve_b, {"applied": False, "reason": "NO_OBSERVED_SERVE_RETURN_VOLUME", "weight": 0.0}
    target = _sigmoid((1.0 - weight) * _logit(prior_probability) + weight * _logit(_match_win(
        state, int(round(serve_a[0] * 1000)), int(round(serve_b[0] * 1000)))))
    base_a, base_b = _logit(serve_a[0]), _logit(serve_b[0])
    low, high = -8.0, 8.0
    for _ in range(64):
        shift = (low + high) / 2.0
        pa = min(0.90, max(0.35, _sigmoid(base_a + shift)))
        pb = min(0.90, max(0.35, _sigmoid(base_b - shift)))
        probability = _match_win(state, int(round(pa * 1000)), int(round(pb * 1000)))
        if probability < target:
            low = shift
        else:
            high = shift
    shift = (low + high) / 2.0
    pa = min(0.90, max(0.35, _sigmoid(base_a + shift)))
    pb = min(0.90, max(0.35, _sigmoid(base_b - shift)))
    return ((pa, serve_a[1], serve_a[2]), (pb, serve_b[1], serve_b[2]),
            {"applied": True, "weight": round(weight, 6), "target_probability": round(target, 6),
             "logit_shift": round(shift, 8), "stat_volumes": volumes})


def _logit(value: float) -> float:
    value = min(1.0 - 1e-12, max(1e-12, float(value)))
    return math.log(value / (1.0 - value))


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exp_value = math.exp(value)
    return exp_value / (1.0 + exp_value)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_tennis_state(detail: Any, sport_state: Any = None, best_of: Optional[int] = None) -> Tuple[Optional[TennisState], Optional[str]]:
    """Parse a score even when match format is unknown; only match forecasts need it."""
    from tennis_adapter.point_history import is_legal_point_state
    if best_of is not None and best_of not in (3, 5):
        return None, "UNSUPPORTED_FORMAT"
    detail = detail if isinstance(detail, dict) else {}
    sport_state = sport_state if isinstance(sport_state, dict) else {}
    stats = sport_state.get("stats") if isinstance(sport_state.get("stats"), dict) else detail.get("stats", {})
    # Fonbet adapter stores the live set line under stats.set_lines and closed
    # set results under detail.sets. Prefer the live line to avoid treating the
    # last completed set as the current score.
    sets = stats.get("set_lines") or detail.get("set_lines") or detail.get("sets") or (sport_state.get("sets") if isinstance(sport_state.get("sets"), list) else [])
    current = next((row for row in reversed(sets) if isinstance(row, dict) and row.get("live")), None)
    if current is None and not stats.get("set_lines") and sets and isinstance(sets[-1], dict):
        current = sets[-1]
    games_a_raw = (current or {}).get("games_a", detail.get("current_game_a"))
    games_b_raw = (current or {}).get("games_b", detail.get("current_game_b"))
    if games_a_raw is None or games_b_raw is None:
        return None, "GAMES_SCORE_MISSING"
    games_a, games_b = _int(games_a_raw, -1), _int(games_b_raw, -1)
    sets_a = _int(stats.get("sets_a", detail.get("sets_a", 0)))
    sets_b = _int(stats.get("sets_b", detail.get("sets_b", 0)))
    game = detail.get("current_game") if isinstance(detail.get("current_game"), dict) else {}
    is_tiebreak = bool(
        detail.get("tiebreak")
        or (game.get("is_tiebreak") if isinstance(game, dict) else False)
        or sport_state.get("tiebreak")
        or (games_a == 6 and games_b == 6)
    )
    raw_points_a = str(game.get("points_a", detail.get("points_a", "0"))).upper()
    raw_points_b = str(game.get("points_b", detail.get("points_b", "0"))).upper()
    if is_tiebreak:
        try:
            points_a, points_b = int(raw_points_a), int(raw_points_b)
        except ValueError:
            points_a = points_b = None
    else:
        points_a, points_b = POINTS.get(raw_points_a), POINTS.get(raw_points_b)
    max_sets_ahead = (best_of // 2) if best_of in (3, 5) else 2
    if (
        points_a is None or points_b is None or games_a > 7 or games_b > 7
        or min(sets_a, sets_b) < 0 or max(sets_a, sets_b) > max_sets_ahead
        or (games_a == games_b == 7)
        or not is_legal_point_state(points_a, points_b, tie_break=is_tiebreak)
    ):
        return None, "UNSUPPORTED_SCORE"
    server = sport_state.get("server") or detail.get("server")
    server = {"player_a": "a", "player_b": "b", "1": "a", "2": "b"}.get(str(server).lower())
    tiebreak_server = sport_state.get("tiebreak_server") or detail.get("tiebreak_server")
    tiebreak_server = {"player_a": "a", "player_b": "b", "1": "a", "2": "b"}.get(str(tiebreak_server).lower())
    return TennisState(
        sets_a, sets_b, games_a, games_b, points_a, points_b, server,
        is_tiebreak,
        best_of // 2 + 1 if best_of in (3, 5) else 0,
        tiebreak_server,
    ), None


def _point_probability(state: TennisState, p_serve_a: float, p_serve_b: float) -> float:
    """P(A wins the next point); serve rates are independent of bookmaker data."""
    if state.server == "a":
        return p_serve_a
    if state.server == "b":
        return 1.0 - p_serve_b
    # Unknown server: average the legal serve assignment rather than guessing.
    return 0.5 * (p_serve_a + 1.0 - p_serve_b)


def _advance_point(state: TennisState, winner: str) -> TennisState:
    if state.sets_a >= state.sets_to_win or state.sets_b >= state.sets_to_win:
        return state
    pa, pb = state.points_a, state.points_b
    if state.tiebreak:
        pa += winner == "a"
        pb += winner == "b"
        if max(pa, pb) >= 7 and abs(pa - pb) >= 2:
            set_winner = "a" if pa > pb else "b"
            next_set_server = _next_server(state.tiebreak_server or state.server)
            state = _close_set(state, set_winner)
            return TennisState(**{**asdict(state), "server": next_set_server, "tiebreak": False, "tiebreak_server": None})
        first_server = state.tiebreak_server or state.server
        played = pa + pb
        switches = 0 if played == 0 else 1 + (played - 1) // 2
        next_server = first_server if switches % 2 == 0 else _next_server(first_server)
        return TennisState(**{**asdict(state), "points_a": pa, "points_b": pb, "server": next_server, "tiebreak_server": first_server})

    pa += winner == "a"
    pb += winner == "b"
    game_winner = None
    if max(pa, pb) >= 4 and abs(pa - pb) >= 2:
        game_winner = "a" if pa > pb else "b"
    if game_winner:
        ga = state.games_a + (game_winner == "a")
        gb = state.games_b + (game_winner == "b")
        set_winner = None
        if max(ga, gb) >= 6 and abs(ga - gb) >= 2:
            set_winner = "a" if ga > gb else "b"
        elif (ga, gb) in ((7, 6), (6, 7)):
            set_winner = "a" if ga > gb else "b"
        if set_winner:
            return TennisState(**{**asdict(_close_set(TennisState(**{**asdict(state), "games_a": ga, "games_b": gb}), set_winner)), "server": _next_server(state.server)})
        tie = ga == 6 and gb == 6
        next_server = _next_server(state.server)
        return TennisState(**{**asdict(state), "games_a": ga, "games_b": gb, "points_a": 0, "points_b": 0, "server": next_server, "tiebreak": tie, "tiebreak_server": next_server if tie else None})
    # Keep deuce/advantage in a bounded representation to avoid a 4:4 <-> 5:4 cycle.
    if pa >= 3 and pb >= 3:
        if pa == pb:
            pa = pb = 3
        elif pa > pb:
            pa, pb = 4, 3
        else:
            pa, pb = 3, 4
    return TennisState(**{**asdict(state), "points_a": pa, "points_b": pb})


def _close_set(state: TennisState, winner: str) -> TennisState:
    a = state.sets_a + (winner == "a")
    b = state.sets_b + (winner == "b")
    return TennisState(a, b, 0, 0, 0, 0, state.server, False, state.sets_to_win, None)


def _set_game_winner(state: TennisState, winner: str) -> TennisState:
    ga = state.games_a + (winner == "a")
    gb = state.games_b + (winner == "b")
    if (max(ga, gb) >= 6 and abs(ga - gb) >= 2) or (max(ga, gb) == 7 and min(ga, gb) == 6):
        closed = _close_set(TennisState(**{**asdict(state), "games_a": ga, "games_b": gb}), "a" if ga > gb else "b")
        return TennisState(**{**asdict(closed), "server": _next_server(state.tiebreak_server or state.server)})
    tie = ga == gb == 6
    server = _next_server(state.server)
    return TennisState(**{**asdict(state), "games_a": ga, "games_b": gb, "points_a": 0, "points_b": 0, "server": server, "tiebreak": tie, "tiebreak_server": server if tie else None})


@lru_cache(maxsize=300_000)
def _game_win(pa: int, pb: int, p: float) -> float:
    if pa >= 4 and pa - pb >= 2:
        return 1.0
    if pb >= 4 and pb - pa >= 2:
        return 0.0
    if pa >= 3 and pb >= 3:
        # Reduce any advantage score to deuce before recursing.
        if pa == pb:
            q = 1.0 - p
            return p * p / (p * p + q * q)
        if pa > pb:
            return p + (1.0 - p) * _game_win(3, 3, p)
        return p * _game_win(3, 3, p)
    return p * _game_win(pa + 1, pb, p) + (1.0 - p) * _game_win(pa, pb + 1, p)


def _point_prob(state: TennisState, p_a: float, p_b: float) -> float:
    return _point_probability(state, p_a, p_b)


@lru_cache(maxsize=300_000)
def _match_win(state: TennisState, p_a_milli: int, p_b_milli: int) -> float:
    pa, pb = p_a_milli / 1000.0, p_b_milli / 1000.0
    if state.sets_a >= state.sets_to_win:
        return 1.0
    if state.sets_b >= state.sets_to_win:
        return 0.0
    if state.server is None:
        left = _match_win(TennisState(**{**asdict(state), "server": "a"}), p_a_milli, p_b_milli)
        right = _match_win(TennisState(**{**asdict(state), "server": "b"}), p_a_milli, p_b_milli)
        return (left + right) / 2.0
    if state.tiebreak:
        p_set = _tiebreak_win(state, p_a_milli, p_b_milli, state.sets_a)
        next_a = _set_game_winner(state, "a")
        next_b = _set_game_winner(state, "b")
        return p_set * _match_win(next_a, p_a_milli, p_b_milli) + (1.0 - p_set) * _match_win(next_b, p_a_milli, p_b_milli)
    server_rate = pa if state.server == "a" else pb
    p_hold = _game_win(state.points_a, state.points_b, server_rate)
    p_game_a = p_hold if state.server == "a" else 1.0 - p_hold
    next_a = _set_game_winner(state, "a")
    next_b = _set_game_winner(state, "b")
    return p_game_a * _match_win(next_a, p_a_milli, p_b_milli) + (1.0 - p_game_a) * _match_win(next_b, p_a_milli, p_b_milli)


@lru_cache(maxsize=300_000)
def _tiebreak_win(state: TennisState, p_a_milli: int, p_b_milli: int, base_sets_a: int) -> float:
    """Exact tie-break win probability, solving the repeating two-point deuce tail."""
    if not state.tiebreak:
        return 1.0 if state.sets_a > base_sets_a else 0.0
    pa, pb = state.points_a, state.points_b
    if max(pa, pb) >= 7 and abs(pa - pb) >= 2:
        return 1.0 if pa > pb else 0.0
    if pa == pb and pa >= 6:
        # Four-point service cycle returns to deuce unless somebody wins by two.
        win_mass = 0.0
        return_mass = 0.0
        first = state.server or state.tiebreak_server or "a"
        server_cycle = (first, first, _next_server(first), _next_server(first))
        for mask in range(16):
            score_a, score_b, mass = pa, pb, 1.0
            terminal = None
            for index, server in enumerate(server_cycle):
                p_point_a = p_a_milli / 1000 if server == "a" else 1.0 - p_b_milli / 1000
                won = bool(mask & (1 << index))
                mass *= p_point_a if won else 1.0 - p_point_a
                if terminal is None:
                    score_a += won
                    score_b += not won
                    if max(score_a, score_b) >= 7 and abs(score_a - score_b) >= 2:
                        terminal = score_a > score_b
            if terminal is True:
                win_mass += mass
            elif terminal is None and score_a == score_b:
                return_mass += mass
        return win_mass / max(1e-12, 1.0 - return_mass)
    p_next = _point_prob(state, p_a_milli / 1000, p_b_milli / 1000)
    return p_next * _tiebreak_win(_advance_point(state, "a"), p_a_milli, p_b_milli, base_sets_a) + (1.0 - p_next) * _tiebreak_win(_advance_point(state, "b"), p_a_milli, p_b_milli, base_sets_a)


def _next_server(server: Optional[str]) -> Optional[str]:
    return {"a": "b", "b": "a"}.get(server)


def _normalise(values: Dict[str, float]) -> Dict[str, float]:
    total = sum(values.values())
    return {key: value / total for key, value in values.items()} if total else values


def _resolve_unknown_server(state: TennisState) -> List[Tuple[TennisState, float]]:
    """Branch the hidden server once and keep that assignment through the path."""
    if state.server in ("a", "b"):
        return [(state, 1.0)]
    if state.tiebreak and state.tiebreak_server in ("a", "b"):
        total = state.points_a + state.points_b
        switches = 0 if total == 0 else 1 + (total - 1) // 2
        serving = state.tiebreak_server if switches % 2 == 0 else _next_server(state.tiebreak_server)
        return [(TennisState(**{**asdict(state), "server": serving}), 1.0)]
    return [
        (TennisState(**{**asdict(state), "server": "a", "tiebreak_server": state.tiebreak_server or "a"}), 0.5),
        (TennisState(**{**asdict(state), "server": "b", "tiebreak_server": state.tiebreak_server or "b"}), 0.5),
    ]


def _point_scenarios(state: TennisState, p_a: float, p_b: float, depth: int = 3) -> List[dict]:
    branches: List[Tuple[TennisState, float, str]] = [
        (resolved, mass, "") for resolved, mass in _resolve_unknown_server(state)
    ]
    for _ in range(depth):
        next_branches = []
        for current, mass, path in branches:
            if current.sets_a >= current.sets_to_win or current.sets_b >= current.sets_to_win:
                next_branches.append((current, mass, path))
                continue
            p = _point_prob(current, p_a, p_b)
            next_branches.extend([
                (_advance_point(current, "a"), mass * p, path + "A"),
                (_advance_point(current, "b"), mass * (1 - p), path + "B"),
            ])
        branches = next_branches
    merged: Dict[TennisState, Tuple[float, List[str]]] = {}
    for final, probability, path in branches:
        mass, paths = merged.get(final, (0.0, []))
        merged[final] = (mass + probability, paths + [path])
    total = math.fsum(mass for mass, _paths in merged.values())
    if total > 0.0:
        merged = {state_value: (mass / total, paths) for state_value, (mass, paths) in merged.items()}
    return [
        {"points": "|".join(paths), "probability": mass, "state": asdict(final)}
        for final, (mass, paths) in sorted(merged.items(), key=lambda item: item[1][0], reverse=True)
    ]


def _game_scenarios(state: TennisState, p_a: float, p_b: float, depth: int = 2) -> List[dict]:
    branches: List[Tuple[TennisState, float, str]] = [
        (resolved, mass, "") for resolved, mass in _resolve_unknown_server(state)
    ]
    for _ in range(depth):
        following = []
        for current, mass, path in branches:
            if current.sets_a >= current.sets_to_win or current.sets_b >= current.sets_to_win:
                following.append((current, mass, path))
                continue
            server = current.server
            if current.tiebreak:
                p_game_a = _tiebreak_win(
                    current, int(round(p_a * 1000)), int(round(p_b * 1000)), current.sets_a,
                )
            else:
                p_server = p_a if server == "a" else p_b
                p_hold = _game_win(current.points_a if not path else 0, current.points_b if not path else 0, round(p_server, 3))
                p_game_a = p_hold if server == "a" else 1.0 - p_hold
            following.extend([
                (_set_game_winner(current, "a"), mass * p_game_a, path + "A"),
                (_set_game_winner(current, "b"), mass * (1 - p_game_a), path + "B"),
            ])
        branches = following
    merged: Dict[TennisState, Tuple[float, List[str]]] = {}
    for final, probability, path in branches:
        mass, paths = merged.get(final, (0.0, []))
        merged[final] = (mass + probability, paths + [path])
    total = math.fsum(mass for mass, _paths in merged.values())
    if total > 0.0:
        merged = {state_value: (mass / total, paths) for state_value, (mass, paths) in merged.items()}
    return [
        {"games": "|".join(paths), "probability": mass, "state": asdict(final)}
        for final, (mass, paths) in sorted(merged.items(), key=lambda item: item[1][0], reverse=True)
    ]


def _smoothed_rate(won: float, played: float, prior: float, strength: float = 80.0) -> Tuple[float, float]:
    """Shrink sparse serving/return samples toward the tour prior."""
    if played <= 0:
        return prior, 0.0
    return (won + prior * strength) / (played + strength), played / (played + strength)


def serve_retrospective_flags(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize retrospective provenance without hiding mixed source rows."""
    retrospective_count = sum(bool(item.get("retrospective")) for item in items)
    return {
        "retrospective": retrospective_count > 0,
        "all_retrospective": bool(items) and retrospective_count == len(items),
        "retrospective_count": retrospective_count,
    }


def _live_serve_rates(states, base_a, base_b, best_of: int = 3):
    """Score polls are not point events: an apparently one-point delta may hide rallies.

    Keep the return shape for callers, but do not turn score-compatible transitions
    into training evidence unless a source supplies a unique point-event sequence.
    Fonbet score snapshots currently do not provide that guarantee.
    """
    return base_a, base_b, {"a": 0.0, "b": 0.0}


def player_serve_rate_snapshot(
    session, player_a: str, player_b: str, cutoff, surface: str = "unknown"
) -> tuple[Tuple[float, float, float], Tuple[float, float, float], Dict[str, Any]]:
    """Return only identity-safe PIT serve inputs.

    Legacy external serve logs persist full player names, but not the provider
    player ID or identity confirmation timestamp. They cannot be joined without
    risking a same-name or late-identity leak. Participant-keyed immutable
    metric snapshots are loaded by ``load_player_metric`` instead.
    """
    unknown = {side: {"volume_known": False, "sample_points": 0.0, "freshness_days": None,
                      "source_event_at": None, "available_at": None, "sources": [],
                      "availability_provenance": [], "source_confidence": None,
                      "retrospective": None, "missingness": "serve_identity_provenance_missing"}
               for side in ("a", "b")}
    return (0.62, 0.62, 0.0), (0.62, 0.62, 0.0), unknown


def player_serve_rates(session, player_a: str, player_b: str, cutoff, surface: str = "unknown") -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
    """Compatibility wrapper for callers needing only rates and sample weights."""
    left, right, _details = player_serve_rate_snapshot(session, player_a, player_b, cutoff, surface)
    return left, right


def infer_live_best_of_from_continuation(snapshot: Any) -> Tuple[Optional[int], Optional[str]]:
    """Infer best-of-five only after a live source score proves best-of-three was passed."""
    if str(getattr(snapshot, "status", "") or "").casefold() != "live":
        return None, None
    detail = snapshot.score_detail if isinstance(snapshot.score_detail, dict) else {}
    sport = snapshot.sport_state if isinstance(snapshot.sport_state, dict) else {}
    stats = sport.get("stats") if isinstance(sport.get("stats"), dict) else detail.get("stats", {})
    sets_a = _int(stats.get("sets_a", detail.get("sets_a", 0)))
    sets_b = _int(stats.get("sets_b", detail.get("sets_b", 0)))
    if max(sets_a, sets_b) < 2:
        return None, None
    observed_at = getattr(snapshot, "observed_at", None)
    return 5, observed_at.isoformat() if observed_at is not None else None


def event_metadata_as_of(session, event, cutoff):
    """Resolve immutable format/surface observations for live and replay alike."""
    metadata = dict(event.metadata_json) if isinstance(event.metadata_json, dict) else {}
    event_id = getattr(event, "id", None)
    if event_id is None or not hasattr(session, "query"):
        return metadata
    from db.models.external_tennis import TennisEventAttributeObservation
    observations = session.query(TennisEventAttributeObservation).filter(
        TennisEventAttributeObservation.event_id == event_id,
        TennisEventAttributeObservation.available_at <= cutoff,
    ).order_by(TennisEventAttributeObservation.available_at,
               TennisEventAttributeObservation.id).all()
    selected = resolve_event_attribute_observations(observations, cutoff)
    # Do not let a mutable last-state projection leak backwards when no version
    # was available at the requested replay cutoff.
    for attr in ("best_of", "surface"):
        metadata.pop(attr, None)
        metadata.pop(f"{attr}_available_at", None)
        row = selected.get(attr)
        if row is not None:
            metadata[attr] = row.value_json.get("value")
            metadata[f"{attr}_available_at"] = row.available_at.isoformat()
    return metadata


def prelabel_pit_readiness(session, event, snapshot, cutoff, metric_a, metric_b, *, state_valid):
    """Record the six-group evidence check for this forecast, without certifying its label."""
    from contracts.tennis_pit import validate_feature_source_evidence
    from db.models.external_tennis import TennisEventAttributeObservation

    observations = session.query(TennisEventAttributeObservation).filter(
        TennisEventAttributeObservation.event_id == event.id,
        TennisEventAttributeObservation.available_at <= cutoff,
    ).order_by(TennisEventAttributeObservation.available_at,
               TennisEventAttributeObservation.id).all()
    selected = resolve_event_attribute_observations(observations, cutoff)

    def iso(value):
        return value.isoformat() if hasattr(value, "isoformat") else value

    player_evidence = []
    serve_sources = {"a": [], "b": []}
    for side, metric in (("a", metric_a), ("b", metric_b)):
        if not metric:
            player_evidence.append({})
            serve_sources[side].append({})
            continue
        player_evidence.append({
            "source": "confirmed_flashscore_player_metrics",
            "source_player_id": metric.get("source_player_id"),
            "snapshot_id": metric.get("metric_snapshot_id"),
            "available_at": metric.get("available_at"),
            "identity_available_at": metric.get("identity_available_at"),
            "history_digest": metric.get("history_digest"),
        })
        canonical_serve = (metric.get("canonical_metrics") or {}).get("serve") or {}
        serve_points = float(canonical_serve.get("points") or 0)
        if serve_points > 0 and metric.get("canonical_metric_snapshot_id"):
            serve_sources[side].append({
                "source": "confirmed_canonical_serve_history",
                "source_player_id": metric.get("source_player_id"),
                "snapshot_id": metric.get("canonical_metric_snapshot_id"),
                "available_at": metric.get("canonical_metrics_available_at"),
                "identity_available_at": metric.get("identity_available_at"),
                "history_digest": metric.get("canonical_metrics_digest"),
                "serve_points": serve_points,
            })
        else:
            serve_sources[side].append({})

    best_of_value = None
    format_row = selected.get("best_of")
    if format_row is not None:
        try:
            candidate = int(format_row.value_json.get("value"))
            best_of_value = candidate if candidate in (3, 5) else None
        except (TypeError, ValueError, OverflowError):
            best_of_value = None
    source_available_at = {
        "player_metrics": max(
            (metric.get("available_at") for metric in (metric_a, metric_b) if metric),
            default=None,
        ),
        "serve_metrics_a": (serve_sources["a"][0].get("available_at") if serve_sources["a"] else None),
        "serve_metrics_b": (serve_sources["b"][0].get("available_at") if serve_sources["b"] else None),
        "event_state": iso(getattr(snapshot, "observed_at", None)) if snapshot is not None else None,
        **({"event_format": iso(format_row.available_at)} if best_of_value is not None else {}),
    }
    source_evidence = {
        "player_metrics": player_evidence,
        "serve_metrics_a": serve_sources["a"],
        "serve_metrics_b": serve_sources["b"],
        "event_state": ([{
            "source": "fonbet_event_state_snapshot", "snapshot_id": str(snapshot.id),
            "available_at": iso(snapshot.observed_at), "raw_hash": snapshot.raw_hash,
            "raw_object_key": snapshot.raw_object_key,
        }] if snapshot is not None else []),
    }
    for attr, group in (("best_of", "event_format"),):
        row = selected.get(attr)
        source_evidence[group] = ([{
            "source": row.source, "snapshot_id": str(row.id),
            "available_at": iso(row.available_at),
            "identity_available_at": iso(row.identity_available_at),
            "raw_hash": row.source_content_hash,
            "raw_object_key": row.raw_object_key,
            "parser_version": row.parser_version,
        }] if row is not None else [])

    from contracts.tennis_pit import resolve_unknown_player_history, resolve_unknown_serve_history
    source_available_at, source_evidence, _, player_fallback = resolve_unknown_player_history(
        cutoff, source_available_at, source_evidence)
    source_available_at, source_evidence, _, serve_fallback = resolve_unknown_serve_history(
        cutoff, source_available_at, source_evidence)
    required_groups = tuple(group for group in ODDS_FREE_FEATURE_SOURCE_GROUPS
                            if group != "event_format" or best_of_value is not None)
    validation = validate_feature_source_evidence(
        cutoff, source_available_at, source_evidence, required_groups
    )
    reasons = []
    if not state_valid:
        reasons.append("event_state_not_parseable")
    for side, metric in (("a", metric_a), ("b", metric_b)):
        if side not in player_fallback and metric and (not metric.get("source_player_id") or not metric.get("identity_available_at")):
            reasons.append(f"confirmed_player_identity_missing_{side}")
    if snapshot is None or not snapshot.id:
        reasons.append("event_state_snapshot_missing")
    reasons.extend(validation.get("missing_sources", []))
    reasons.extend(validation.get("future_sources", []))
    return {
        "contract": ODDS_FREE_FEATURE_VERSION,
        "player_history_fallback": player_fallback,
        "serve_history_fallback": serve_fallback,
        "serve_history_missing": {side: side in serve_fallback for side in ("a", "b")},
        "match_format_known": best_of_value is not None,
        "format_unknown_reason": None if best_of_value is not None else "no_timely_explicit_best_of",
        "snapshot_at": iso(cutoff),
        "status": "ready_for_labeled_pit" if validation["certified"] and not reasons else "incomplete",
        "certified": bool(validation["certified"] and not reasons),
        "label_status": "pending",
        "source_available_at": {key: iso(value) for key, value in source_available_at.items()},
        "source_evidence": source_evidence,
        "validation": validation,
        "reasons": sorted(set(reasons)),
    }


def persist_event_forecasts(session, event, states, cutoff) -> int:
    """Store hybrid odds-free forecast snapshots for newly observed states."""
    import hashlib
    import json
    import uuid
    from db.models.predictions import TennisForecastSnapshot
    from contracts.predictions import TennisForecast

    event_metadata = event_metadata_as_of(session, event, cutoff)
    best_of, best_of_available_at, best_of_resolution = point_in_time_best_of(
        event_metadata, cutoff
    )
    unavailable_reason = None
    state_value = None
    if states:
        latest = states[-1]
        observed_at = latest.observed_at
        if (event.status == "live" or event.is_live) and str(latest.status).lower() != "live":
            unavailable_reason = "NO_LIVE_STATE"
        else:
            state_value, error = parse_tennis_state(latest.score_detail, latest.sport_state, best_of=best_of)
            if error or state_value is None:
                unavailable_reason = error or "UNSUPPORTED_SCORE"
    else:
        observed_at = cutoff
        if event.status == "live" or event.is_live:
            unavailable_reason = "NO_LIVE_STATE"
        else:
            # Local point/game/set forecasts do not depend on match length.
            state_value = TennisState(sets_to_win=best_of // 2 + 1 if best_of else 0)
    # Player data must have been available by the actual calculation time. The
    # forecast may intentionally wait for initial profile preparation after the
    # latest score poll, so observed_at and calculated_at can differ.
    source_cutoff = cutoff
    surface, surface_available_at, surface_resolution = point_in_time_surface(
        event_metadata, source_cutoff
    )
    serve_a, serve_b = (0.62, 0.62, 0.0), (0.62, 0.62, 0.0)
    serve_provenance = {
        side: {"volume_known": False, "sample_points": 0.0, "freshness_days": None,
               "source_event_at": None, "available_at": None, "sources": [],
               "availability_provenance": [], "source_confidence": None,
               "retrospective": None, "missingness": "no_confirmed_canonical_365d_metrics"}
        for side in ("a", "b")
    }
    from .player_metrics import load_player_metric, pair_features

    metric_a = load_player_metric(session, event.participant_a_id, source_cutoff)
    metric_b = load_player_metric(session, event.participant_b_id, source_cutoff)
    from contracts.tennis_pit import resolve_unknown_player_history
    history_evidence = {side: ({"source": "confirmed_flashscore_player_metrics",
        "available_at": metric.get("available_at"), "history_digest": metric.get("history_digest"),
        "source_player_id": metric.get("source_player_id"),
        "identity_available_at": metric.get("identity_available_at")}
        if metric and metric.get("source_player_id") and metric.get("identity_available_at") else {})
        for side, metric in (("a", metric_a), ("b", metric_b))}
    _, _, _, history_fallback = resolve_unknown_player_history(
        source_cutoff, {}, {"player_metrics": history_evidence})
    # Player-history fallback does not erase independently evidenced serving inputs.
    pair = pair_features(None if "a" in history_fallback else metric_a,
                         None if "b" in history_fallback else metric_b, surface=surface)
    pair["match_format_known"] = best_of is not None
    if metric_a and metric_a.get("canonical_metrics"):
        serve = metric_a["canonical_metrics"].get("serve") or {}
        serve_a = (float(serve.get("adjusted_rate", serve_a[0])), serve_a[1],
                   min(0.95, float(serve.get("points", 0.0)) / (float(serve.get("points", 0.0)) + 200.0)))
        serve_provenance["a"] = {**serve_provenance["a"], "canonical_metrics_digest": metric_a.get("canonical_metrics_digest"),
                                 "metrics_available_at": metric_a.get("canonical_metrics_available_at"),
                                 "adjustment": "opponent_adjusted_canonical_history" if serve.get("points", 0) else "neutral_prior"}
    if metric_b and metric_b.get("canonical_metrics"):
        serve = metric_b["canonical_metrics"].get("serve") or {}
        serve_b = (float(serve.get("adjusted_rate", serve_b[0])), serve_b[1],
                   min(0.95, float(serve.get("points", 0.0)) / (float(serve.get("points", 0.0)) + 200.0)))
        serve_provenance["b"] = {**serve_provenance["b"], "canonical_metrics_digest": metric_b.get("canonical_metrics_digest"),
                                 "metrics_available_at": metric_b.get("canonical_metrics_available_at"),
                                 "adjustment": "opponent_adjusted_canonical_history" if serve.get("points", 0) else "neutral_prior"}
    # Convert adjusted serve and opponent-return rates to the point probability
    # consumed by the Markov engine. Missing history contributes the stated priors.
    serve_a = (_sigmoid(_logit(serve_a[0]) + _logit(1.0 - pair.get("return_rate_b", 0.38)) - _logit(0.62)),
               serve_a[1], serve_a[2])
    serve_b = (_sigmoid(_logit(serve_b[0]) + _logit(1.0 - pair.get("return_rate_a", 0.38)) - _logit(0.62)),
               serve_b[1], serve_b[2])
    serve_provenance["a"]["opponent_return_adjustment"] = "pair_b_return"
    serve_provenance["b"]["opponent_return_adjustment"] = "pair_a_return"
    live_samples = {"a": 0.0, "b": 0.0}
    if state_value is not None:
        serve_a, serve_b, live_samples = _live_serve_rates(states, serve_a, serve_b, best_of)
    strength = independent_strength_prior(pair)
    strength_adjustment = {"applied": False, "reason": "MATCH_FORMAT_UNKNOWN" if best_of is None else "NO_STRENGTH_COMPONENTS"}
    if state_value is not None and best_of is not None and strength["probability"] is not None:
        serve_a, serve_b, strength_adjustment = apply_independent_strength_prior(
            serve_a, serve_b, strength["probability"], state_value,
            [pair.get("serve_points_a", 0), pair.get("return_points_a", 0),
             pair.get("serve_points_b", 0), pair.get("return_points_b", 0)],
        )
    missing = ["NO_LIVE_STATE"] if not states else []
    if state_value is not None and not state_value.server:
        missing.append("SERVER_UNKNOWN")
    if serve_a[2] == 0:
        missing.append("SERVE_HISTORY_A_MISSING")
    if serve_b[2] == 0:
        missing.append("SERVE_HISTORY_B_MISSING")
    if not pair.get("independent_metrics_available_a"):
        missing.append("PLAYER_METRICS_A_MISSING")
    if not pair.get("independent_metrics_available_b"):
        missing.append("PLAYER_METRICS_B_MISSING")
    if states:
        missing.append("POINT_SEQUENCE_UNAVAILABLE")
    if best_of is None:
        missing.append("MATCH_FORMAT_UNKNOWN")
    quality = 0.0 if unavailable_reason else 1.0 - min(1.0, len(missing) * 0.25)
    if state_value is None:
        value = {
            "schema_version": "1.0.0", "model_name": "tennis_score_baseline", "model_version": ODDS_FREE_BASELINE_MODEL_VERSION,
            "state": {}, "probabilities": {}, "scenarios": {"next_3_points": [], "next_2_games": []},
            "data_quality": 0.0, "reasons": [unavailable_reason], "forecast_available": False,
            "unavailable_reason": unavailable_reason, "serve_point_probability": {}, "sample_weight": {},
        }
    else:
        value = forecast(
            state_value, serve_a=serve_a, serve_b=serve_b, data_quality=quality,
            reasons=missing, match_format_known=best_of is not None,
            version=ODDS_FREE_BASELINE_MODEL_VERSION,
        )
        value["forecast_available"] = True
        if best_of is None:
            value["state"]["sets_to_win"] = None
        value["data_quality_detail"] = {
            "historical_serve_sample_weight": value["sample_weight"],
            "live_point_sequence_confirmed": False,
            "live_score_state_count": len(states),
            "surface": surface,
            "surface_available_at": surface_available_at,
            "surface_resolution": surface_resolution,
            "match_win_probability_available": best_of is not None,
            "match_win_probability_reason": None if best_of is not None else "MATCH_FORMAT_UNKNOWN",
            "baseline_formula_version": ODDS_FREE_BASELINE_FEATURE_VERSION,
            "baseline_parameters_digest": ODDS_FREE_BASELINE_PARAMETERS_DIGEST,
            "strength_components": strength["components"],
            "strength_missing_components": strength["missing_components"],
            "strength_prior_probability": strength["probability"],
            "strength_blend": strength_adjustment,
            "opponent_adjusted_serve_return": {
                "serve_a": round(serve_a[0], 6), "serve_b": round(serve_b[0], 6),
                "return_a": pair.get("return_rate_a", 0.38), "return_b": pair.get("return_rate_b", 0.38),
                "serve_points_a_365d": pair.get("serve_points_a", 0.0),
                "serve_points_b_365d": pair.get("serve_points_b", 0.0),
                "return_points_a_365d": pair.get("return_points_a", 0.0),
                "return_points_b_365d": pair.get("return_points_b", 0.0),
            },
            "independent_metrics_missingness": {
                "a": not pair.get("independent_metrics_available_a", False),
                "b": not pair.get("independent_metrics_available_b", False),
            },
            "baseline_player_metrics": {
                "a": {"available": pair.get("independent_metrics_available_a", False),
                      "elo": pair.get("independent_elo_a"), "surface_elo": pair.get("independent_surface_elo_a"),
                      "glicko": pair.get("independent_glicko_a"), "rd": pair.get("independent_rd_a"),
                      "matches": pair.get("independent_matches_a"), "rolling_90d": pair.get("rolling_90d_summary_a"),
                      "form_by_surface": {"surface": pair.get("surface_form_key"),
                                          "windows": pair.get("surface_form_a", {})},
                      "ranking": metric_a.get("ranking") if metric_a else None,
                      "metrics_available_at": metric_a.get("canonical_metrics_available_at") if metric_a else None},
                "b": {"available": pair.get("independent_metrics_available_b", False),
                      "elo": pair.get("independent_elo_b"), "surface_elo": pair.get("independent_surface_elo_b"),
                      "glicko": pair.get("independent_glicko_b"), "rd": pair.get("independent_rd_b"),
                      "matches": pair.get("independent_matches_b"), "rolling_90d": pair.get("rolling_90d_summary_b"),
                      "form_by_surface": {"surface": pair.get("surface_form_key"),
                                          "windows": pair.get("surface_form_b", {})},
                      "ranking": metric_b.get("ranking") if metric_b else None,
                      "metrics_available_at": metric_b.get("canonical_metrics_available_at") if metric_b else None},
            },
        }
        value["model_uncertainty"] = {
            "serve_rate_a": round(1.0 - value["sample_weight"]["a"], 4),
            "serve_rate_b": round(1.0 - value["sample_weight"]["b"], 4),
        }
    value["player_metrics"] = {
        "a": {
            "participant_id": metric_a["participant_id"],
            "metrics_version": metric_a["metrics_version"],
            "available_at": metric_a["available_at"],
            "history_digest": metric_a["history_digest"],
            "sync_status": metric_a["sync_status"],
        } if metric_a else None,
        "b": {
            "participant_id": metric_b["participant_id"],
            "metrics_version": metric_b["metrics_version"],
            "available_at": metric_b["available_at"],
            "history_digest": metric_b["history_digest"],
            "sync_status": metric_b["sync_status"],
        } if metric_b else None,
        "pair_features": pair,
    }
    value["data_quality_detail"] = {
        **value.get("data_quality_detail", {}),
        "best_of_available_at": best_of_available_at,
        "best_of_resolution": best_of_resolution,
        "historical_serve_provenance": serve_provenance,
        "player_metrics_a_version": metric_a["metrics_version"] if metric_a else None,
        "player_metrics_b_version": metric_b["metrics_version"] if metric_b else None,
        "player_metrics_a_available_at": metric_a["available_at"] if metric_a else None,
        "player_metrics_b_available_at": metric_b["available_at"] if metric_b else None,
            "baseline_formula_version": ODDS_FREE_BASELINE_FEATURE_VERSION,
            "baseline_parameters_digest": ODDS_FREE_BASELINE_PARAMETERS_DIGEST,
            "strength_components": strength["components"],
            "strength_prior_probability": strength["probability"],
            "strength_blend": strength_adjustment,
            "independent_metrics_a_digest": metric_a.get("canonical_metrics_digest") if metric_a else None,
            "independent_metrics_b_digest": metric_b.get("canonical_metrics_digest") if metric_b else None,
            "independent_metrics_a_available_at": metric_a.get("canonical_metrics_available_at") if metric_a else None,
            "independent_metrics_b_available_at": metric_b.get("canonical_metrics_available_at") if metric_b else None,
            "ranking_a": metric_a.get("ranking") if metric_a else None,
            "ranking_b": metric_b.get("ranking") if metric_b else None,
            "baseline_feature_names": list(ODDS_FREE_BASELINE_FEATURE_NAMES),
    }
    value["data_quality_detail"]["prelabel_pit_readiness"] = prelabel_pit_readiness(
        session, event, states[-1] if states else None, observed_at, metric_a, metric_b,
        state_valid=state_value is not None and not unavailable_reason,
    )
    fingerprint_payload = {
        "state": value["state"], "serve": value["serve_point_probability"],
        "sample_weight": value["sample_weight"], "live_samples": live_samples,
        "feature_vector": build_odds_free_baseline_feature_vector(state_value, pair, serve_a, serve_b, surface)
        if state_value is not None else None,
        "baseline_parameters_digest": ODDS_FREE_BASELINE_PARAMETERS_DIGEST,
        "player_metrics_a": metric_a["history_digest"] if metric_a else None,
        "player_metrics_b": metric_b["history_digest"] if metric_b else None,
        "canonical_metrics_digest_a": pair.get("canonical_metrics_digest_a"),
        "canonical_metrics_digest_b": pair.get("canonical_metrics_digest_b"),
        "surface_form_a": pair.get("surface_form_a"),
        "surface_form_b": pair.get("surface_form_b"),
        "unavailable_reason": unavailable_reason,
        # Keep a new evidence snapshot when identities or source proofs change,
        # even if the score and numerical feature vector did not.
        "prelabel_pit_readiness": value["data_quality_detail"].get("prelabel_pit_readiness"),
    }
    fingerprint = hashlib.sha256(json.dumps(fingerprint_payload, sort_keys=True).encode()).hexdigest()
    # Lock the event row so concurrent polling cannot allocate the same
    # sequence number. Only collapse an identical adjacent state: A -> B -> A
    # must produce three snapshots.
    from db.models.events import Event
    session.query(Event.id).filter(Event.id == event.id).with_for_update().first()
    latest = session.query(TennisForecastSnapshot).filter_by(
        event_id=event.id, model_name=value["model_name"], model_version=value["model_version"],
    ).order_by(TennisForecastSnapshot.sequence_no.desc()).first()
    if latest and latest.state_digest == fingerprint:
        return 0
    sequence_no = (latest.sequence_no + 1) if latest else 1
    forecast_id = uuid.uuid4()
    packet = TennisForecast.from_forecast(
        str(event.id), value, observed_at, ODDS_FREE_BASELINE_FEATURE_VERSION, str(forecast_id)
    )
    session.add(TennisForecastSnapshot(
        id=forecast_id,
        event_id=event.id, model_name=value["model_name"], model_version=value["model_version"],
        feature_version=ODDS_FREE_BASELINE_FEATURE_VERSION, sequence_no=sequence_no,
        observed_at=observed_at, state_digest=fingerprint,
        forecast_json=packet.model_dump(mode="json"),
    ))
    return 1


def forecast(
    state: TennisState,
    *,
    serve_a: Tuple[float, float, float] = (0.62, 0.62, 0.0),
    serve_b: Tuple[float, float, float] = (0.62, 0.62, 0.0),
    model_name: str = "tennis_score_baseline",
    version: str = "tennis_v1.0.0",
    data_quality: float = 0.0,
    reasons: Optional[Sequence[str]] = None,
    match_format_known: bool = True,
) -> dict:
    """Forecast match/set and local paths. Inputs contain no odds or market probabilities."""
    p_a = min(0.9, max(0.35, float(serve_a[0])))
    p_b = min(0.9, max(0.35, float(serve_b[0])))
    weight_a, weight_b = float(serve_a[2]), float(serve_b[2])
    current_point = _point_prob(state, p_a, p_b)
    point_a = min(0.999, max(0.001, current_point))
    set_a = (_match_win(state, int(round(p_a * 1000)), int(round(p_b * 1000)))
             if match_format_known else None)
    # Evaluate the current-set winner by treating a set win as terminal.
    one_set = TennisState(0, 0, state.games_a, state.games_b, state.points_a, state.points_b, state.server, state.tiebreak, 1, state.tiebreak_server)
    p_set = _match_win(one_set, int(round(p_a * 1000)), int(round(p_b * 1000)))
    if state.tiebreak:
        p_game_a = point_a
    elif state.server is None:
        p_hold_a = _game_win(state.points_a, state.points_b, p_a)
        p_hold_b = _game_win(state.points_a, state.points_b, p_b)
        p_game_a = (p_hold_a + 1.0 - p_hold_b) / 2.0
    else:
        p_game_hold = _game_win(state.points_a, state.points_b, p_a if state.server == "a" else p_b)
        p_game_a = p_game_hold if state.server == "a" else 1.0 - p_game_hold
    return {
        "schema_version": "1.0.0", "model_name": model_name, "model_version": version,
        "state": asdict(state),
        "probabilities": {
            "next_point_a": round(point_a, 6), "next_game_a": round(p_game_a, 6),
            "current_set_a": round(p_set, 6), **({"match_a": round(set_a, 6)} if match_format_known else {}),
        },
        "serve_point_probability": {"a": round(p_a, 6), "b": round(p_b, 6)},
        "data_quality": round(max(0.0, min(1.0, data_quality)), 4),
        "reasons": list(reasons or []),
        "scenarios": {
            "next_3_points": _point_scenarios(state, p_a, p_b) if match_format_known else [],
            "next_2_games": _game_scenarios(state, p_a, p_b) if match_format_known else [],
        },
        "sample_weight": {"a": round(weight_a, 4), "b": round(weight_b, 4)},
    }

