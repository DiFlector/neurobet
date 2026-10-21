"""Incremental, point-in-time metrics for a single tennis participant."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable


METRICS_VERSION = 1
INITIAL_ELO = 1500.0
ELO_K = 32.0
GLICKO_SCALE = 173.7178


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _bucket() -> dict[str, Any]:
    return {"matches": 0, "wins": 0, "losses": 0}


def empty_metrics() -> dict[str, Any]:
    return {
        "metrics_version": METRICS_VERSION,
        "overall": _bucket(),
        "by_surface": {},
        "by_level": {},
        "recent": {"last_10": _bucket(), "last_20": _bucket(), "days_90": _bucket(), "days_365": _bucket()},
        "elo": {"overall": INITIAL_ELO, "by_surface": {}, "rating_deviation": 350.0},
        "glicko": {"rating": INITIAL_ELO, "rd": 350.0, "volatility": 0.06},
        "serve": {"points": 0.0, "points_won": 0.0, "unknown_volume_matches": 0},
        "load": {"last_match_at": None, "matches_7d": 0, "matches_30d": 0, "sets_7d": 0},
        "coverage": {"match_history": "empty", "serve_volume_known": True},
        "recent_results": [],
    }


def _increment(bucket: dict[str, Any], won: bool) -> None:
    bucket["matches"] = int(bucket.get("matches", 0)) + 1
    key = "wins" if won else "losses"
    bucket[key] = int(bucket.get(key, 0)) + 1


def _elo_update(rating: float, won: bool) -> float:
    expected = 1.0 / (1.0 + 10.0 ** ((INITIAL_ELO - rating) / 400.0))
    return rating + ELO_K * ((1.0 if won else 0.0) - expected)


def _glicko_update(state: dict[str, float], won: bool) -> dict[str, float]:
    """One-opponent Glicko-2 update against a neutral, maximally uncertain prior.

    Opponent-specific ratings are used by the richer replay builder. For this
    compact per-player cache, 1500/RD=350 is an explicit fallback prior.
    """
    mu = (float(state["rating"]) - 1500.0) / GLICKO_SCALE
    phi = float(state["rd"]) / GLICKO_SCALE
    sigma = float(state.get("volatility", 0.06))
    mu_j = 0.0
    phi_j = 350.0 / GLICKO_SCALE
    score = 1.0 if won else 0.0
    g = 1.0 / math.sqrt(1.0 + 3.0 * phi_j * phi_j / (math.pi * math.pi))
    expected = 1.0 / (1.0 + math.exp(-g * (mu - mu_j)))
    variance = 1.0 / max(1e-8, g * g * expected * (1.0 - expected))
    delta = variance * g * (score - expected)
    a = math.log(sigma * sigma)
    tau = 0.5

    def function(x: float) -> float:
        ex = math.exp(x)
        d = phi * phi + variance + ex
        return ex * (delta * delta - phi * phi - variance - ex) / (2.0 * d * d) - (x - a) / (tau * tau)

    left = a
    if delta * delta > phi * phi + variance:
        right = math.log(delta * delta - phi * phi - variance)
    else:
        step = 1
        while function(a - step * tau) < 0 and step < 20:
            step += 1
        right = a - step * tau
    f_left, f_right = function(left), function(right)
    for _ in range(30):
        if abs(f_right - f_left) < 1e-12:
            break
        middle = left + (left - right) * f_left / (f_right - f_left)
        f_middle = function(middle)
        if f_middle * f_right <= 0:
            left, f_left = right, f_right
        else:
            f_left /= 2.0
        right, f_right = middle, f_middle
        if abs(right - left) < 1e-6:
            break
    volatility = math.exp(right / 2.0)
    phi_star = math.sqrt(phi * phi + volatility * volatility)
    phi_new = 1.0 / math.sqrt(1.0 / (phi_star * phi_star) + 1.0 / variance)
    mu_new = mu + phi_new * phi_new * g * (score - expected)
    return {
        "rating": round(1500.0 + GLICKO_SCALE * mu_new, 3),
        "rd": round(GLICKO_SCALE * phi_new, 3),
        "volatility": round(volatility, 6),
    }


def append_result(metrics: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Apply one previously unseen result; caller handles corrections/reordering."""
    data = json.loads(json.dumps(metrics or empty_metrics()))
    played_at = _utc(result["played_at"])
    won = bool(result["won"])
    surface = (result.get("surface") or "unknown").lower()
    level = result.get("tour_level") or "unknown"
    _increment(data["overall"], won)
    _increment(data["by_surface"].setdefault(surface, _bucket()), won)
    _increment(data["by_level"].setdefault(level, _bucket()), won)

    rating = float(data["elo"].get("overall", INITIAL_ELO))
    data["elo"]["overall"] = round(_elo_update(rating, won), 3)
    surface_rating = float(data["elo"].setdefault("by_surface", {}).get(surface, INITIAL_ELO))
    data["elo"]["by_surface"][surface] = round(_elo_update(surface_rating, won), 3)
    count = int(data["overall"]["matches"])
    data["elo"]["rating_deviation"] = round(max(60.0, 350.0 / math.sqrt(1.0 + count / 4.0)), 3)
    data["glicko"] = _glicko_update(data.get("glicko") or {"rating": 1500.0, "rd": 350.0, "volatility": 0.06}, won)

    serve_points = result.get("serve_points")
    serve_points_won = result.get("serve_points_won")
    if serve_points is not None and serve_points_won is not None and float(serve_points) > 0:
        data["serve"]["points"] = float(data["serve"].get("points", 0.0)) + float(serve_points)
        data["serve"]["points_won"] = float(data["serve"].get("points_won", 0.0)) + float(serve_points_won)
    else:
        data["serve"]["unknown_volume_matches"] = int(data["serve"].get("unknown_volume_matches", 0)) + 1

    recent = data.setdefault("recent_results", [])
    recent.append({
        "played_at": played_at.isoformat(), "won": won, "surface": surface, "tour_level": level,
        "sets_played": int(result.get("sets_played") or 0),
        "duration_minutes": result.get("duration_minutes"),
    })
    recent.sort(key=lambda row: row["played_at"])
    cutoff_365 = played_at - timedelta(days=365)
    keep_from = max(0, len(recent) - 20)
    recent[:] = [
        row for index, row in enumerate(recent)
        if index >= keep_from or _utc(datetime.fromisoformat(row["played_at"])) >= cutoff_365
    ]
    # Rolling statistics are materialized at the latest fact time, so inference reads one JSON row.
    latest = played_at
    last_10 = recent[-10:]
    last_20 = recent[-20:]
    days_90 = [row for row in recent if _utc(datetime.fromisoformat(row["played_at"])) >= latest - timedelta(days=90)]
    days_365 = recent
    data["recent"] = {
        "last_10": _summarize(last_10), "last_20": _summarize(last_20),
        "days_90": _summarize(days_90), "days_365": _summarize(days_365),
    }
    data["load"] = {
        "last_match_at": latest.isoformat(),
        "matches_7d": sum(_utc(datetime.fromisoformat(row["played_at"])) >= latest - timedelta(days=7) for row in recent),
        "matches_30d": sum(_utc(datetime.fromisoformat(row["played_at"])) >= latest - timedelta(days=30) for row in recent),
        "sets_7d": sum(int(row.get("sets_played", 0)) for row in recent if _utc(datetime.fromisoformat(row["played_at"])) >= latest - timedelta(days=7)),
    }
    data["coverage"] = {"match_history": "available", "serve_volume_known": data["serve"]["unknown_volume_matches"] == 0}
    return data


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    wins = sum(bool(row["won"]) for row in rows)
    matches = len(rows)
    return {"matches": matches, "wins": wins, "losses": matches - wins,
            "win_rate": round(wins / matches, 6) if matches else None}


def aggregate_results(results: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Build an initial profile or a correction rebuild in stable chronological order."""
    metrics = empty_metrics()
    rows = sorted(results, key=lambda row: (_utc(row["played_at"]), str(row.get("source_match_id", ""))))
    for row in rows:
        metrics = append_result(metrics, row)
    return metrics


def digest_metrics(metrics: dict[str, Any]) -> str:
    payload = json.dumps(metrics, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def metrics_as_of(metrics: dict[str, Any], cutoff: datetime) -> dict[str, Any]:
    """Expire rolling windows at query time without rescanning the match archive."""
    from copy import deepcopy

    data = deepcopy(metrics or {})
    recent = data.get("recent_results") or []
    at = _utc(cutoff)

    def moment(row):
        return _utc(datetime.fromisoformat(row["played_at"]))

    recent = [row for row in recent if moment(row) <= at]
    data["recent"] = {
        "last_10": _summarize(recent[-10:]),
        "last_20": _summarize(recent[-20:]),
        "days_90": _summarize([row for row in recent if moment(row) >= at - timedelta(days=90)]),
        "days_365": _summarize([row for row in recent if moment(row) >= at - timedelta(days=365)]),
    }
    data["load"] = {
        "last_match_at": max((row["played_at"] for row in recent), default=None),
        "matches_7d": sum(moment(row) >= at - timedelta(days=7) for row in recent),
        "matches_30d": sum(moment(row) >= at - timedelta(days=30) for row in recent),
        "sets_7d": sum(int(row.get("sets_played", 0)) for row in recent if moment(row) >= at - timedelta(days=7)),
    }
    return data
