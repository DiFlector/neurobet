"""Deterministic PIT player metrics for the odds-free tennis baseline."""

from __future__ import annotations

import hashlib
import json
import math
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable


INITIAL_ELO = 1500.0
ELO_K = 32.0
GLICKO_SCALE = 173.7178
NEUTRAL_SERVE = 0.62
NEUTRAL_RETURN = 0.38
SERVE_PRIOR_POINTS = 200.0
INDEPENDENT_METRICS_VERSION = 4


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _g(phi: float) -> float:
    return 1.0 / math.sqrt(1.0 + 3.0 * phi * phi / (math.pi * math.pi))


def _glicko_update(mu: float, phi: float, sigma: float, opponent_mu: float,
                   opponent_phi: float, score: float) -> tuple[float, float, float]:
    g = _g(opponent_phi)
    expected = 1.0 / (1.0 + math.exp(-g * (mu - opponent_mu)))
    variance = 1.0 / max(1e-12, g * g * expected * (1.0 - expected))
    delta = variance * g * (score - expected)
    a = math.log(sigma * sigma)
    tau = 0.5

    def f(x: float) -> float:
        ex = math.exp(x)
        denominator = phi * phi + variance + ex
        return ex * (delta * delta - phi * phi - variance - ex) / (2.0 * denominator * denominator) - (x - a) / (tau * tau)

    left = a
    if delta * delta > phi * phi + variance:
        right = math.log(delta * delta - phi * phi - variance)
    else:
        step = 1
        while f(a - step * tau) < 0 and step < 20:
            step += 1
        right = a - step * tau
    f_left, f_right = f(left), f(right)
    for _ in range(30):
        if abs(f_right - f_left) < 1e-12:
            break
        middle = left + (left - right) * f_left / (f_right - f_left)
        f_middle = f(middle)
        if f_middle * f_right < 0:
            left, f_left = right, f_right
        else:
            f_left *= 0.5
        right, f_right = middle, f_middle
        if abs(right - left) < 1e-6:
            break
    volatility = math.exp(right / 2.0)
    phi_star = math.sqrt(phi * phi + volatility * volatility)
    new_phi = 1.0 / math.sqrt(1.0 / (phi_star * phi_star) + 1.0 / variance)
    new_mu = mu + new_phi * new_phi * g * (score - expected)
    return new_mu, new_phi, volatility


def _state() -> dict[str, Any]:
    return {
        "elo": INITIAL_ELO,
        "surface_elo": {},
        "glicko_mu": 0.0,
        "glicko_phi": 350.0 / GLICKO_SCALE,
        "glicko_sigma": 0.06,
        "last_played_at": None,
        "matches": 0,
        "wins": 0,
        "results": deque(maxlen=365),
        "head_to_head": {},
        "serve_points": 0.0,
        "serve_won": 0.0,
        "serve_residual": 0.0,
        "return_points": 0.0,
        "return_won": 0.0,
        "return_residual": 0.0,
        "serve_observations": deque(),
        "return_observations": deque(),
    }


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    wins = sum(bool(row["won"]) for row in rows)
    matches = len(rows)
    return {"matches": matches, "wins": wins, "losses": matches - wins,
            "win_rate": wins / matches if matches else None}


def replay_independent_metrics(matches: Iterable[dict[str, Any]], cutoff: datetime, *,
                               canonical_sorted: bool = False,
                               head_to_head_player_ids: set[str] | None = None) -> dict[str, dict[str, Any]]:
    """Replay confirmed singles results by played time using only facts known by cutoff.

    Each row is one canonical match and includes winner_id/loser_id, played_at,
    available_at, surface/tour_level, and optional point/won counts for both sides.
    """
    limit = _utc(cutoff)
    people: dict[str, dict[str, Any]] = {}
    latest_by_id: dict[str, dict[str, Any]] = {}
    if canonical_sorted:
        ordered = matches
    else:
        for row in matches:
            try:
                available = _utc(row["available_at"])
                played = _utc(row["played_at"])
            except (KeyError, TypeError, ValueError):
                continue
            match_id = str(row.get("canonical_match_id") or row.get("source_match_id") or "")
            winner, loser = str(row.get("winner_id") or ""), str(row.get("loser_id") or "")
            if not match_id or not winner or not loser or winner == loser or available > limit or played > limit:
                continue
            current = latest_by_id.get(match_id)
            normalized = {**row, "canonical_match_id": match_id, "played_at": played, "available_at": available}
            if current is None or available > current["available_at"]:
                latest_by_id[match_id] = normalized
        ordered = sorted(latest_by_id.values(), key=lambda row: (row["played_at"], row["canonical_match_id"]))
    for match in ordered:
        try:
            match_id = str(match.get("canonical_match_id") or match.get("source_match_id") or "")
            played = _utc(match["played_at"])
            available = _utc(match["available_at"])
            winner, loser = str(match.get("winner_id") or ""), str(match.get("loser_id") or "")
        except (KeyError, TypeError, ValueError):
            continue
        if (not match_id or not winner or not loser or winner == loser or available > limit
                or played > limit or not match.get("valid", True)):
            continue
        match = {**match, "canonical_match_id": match_id, "played_at": played}
        a_id, b_id = winner, loser
        a, b = people.setdefault(a_id, _state()), people.setdefault(b_id, _state())
        played = match["played_at"]
        surface = str(match.get("surface") or "unknown").lower()

        # Capture pre-match ratings; both sides update from the same state.
        elo_a, elo_b = float(a["elo"]), float(b["elo"])
        expected_a = 1.0 / (1.0 + 10.0 ** ((elo_b - elo_a) / 400.0))
        surface_a = float(a["surface_elo"].get(surface, INITIAL_ELO))
        surface_b = float(b["surface_elo"].get(surface, INITIAL_ELO))
        expected_surface_a = 1.0 / (1.0 + 10.0 ** ((surface_b - surface_a) / 400.0))

        days_a = max(0.0, (played - a["last_played_at"]).total_seconds() / 86400.0) if a["last_played_at"] else 0.0
        days_b = max(0.0, (played - b["last_played_at"]).total_seconds() / 86400.0) if b["last_played_at"] else 0.0
        period_a, period_b = max(0, int(days_a / 30.0)), max(0, int(days_b / 30.0))
        phi_a = min(350.0 / GLICKO_SCALE, math.sqrt(a["glicko_phi"] ** 2 + period_a * a["glicko_sigma"] ** 2))
        phi_b = min(350.0 / GLICKO_SCALE, math.sqrt(b["glicko_phi"] ** 2 + period_b * b["glicko_sigma"] ** 2))
        mu_a, mu_b = float(a["glicko_mu"]), float(b["glicko_mu"])
        # Update the winner and loser symmetrically from the same pre-match state.
        new_mu_a, new_phi_a, new_sigma_a = _glicko_update(mu_a, phi_a, a["glicko_sigma"], mu_b, phi_b, 1.0)
        new_mu_b, new_phi_b, new_sigma_b = _glicko_update(mu_b, phi_b, b["glicko_sigma"], mu_a, phi_a, 0.0)

        serve_a, won_a = match.get("serve_points_a"), match.get("serve_points_won_a")
        serve_b, won_b = match.get("serve_points_b"), match.get("serve_points_won_b")
        valid_a = serve_a is not None and won_a is not None and float(serve_a) > 0 and 0 <= float(won_a) <= float(serve_a)
        valid_b = serve_b is not None and won_b is not None and float(serve_b) > 0 and 0 <= float(won_b) <= float(serve_b)
        if valid_a and valid_b:
            # Both denominator and server identity are observed in the same canonical match.
            pre_return_a = float(a.get("return_rate", NEUTRAL_RETURN))
            pre_return_b = float(b.get("return_rate", NEUTRAL_RETURN))
            pre_serve_a = float(a.get("serve_rate", NEUTRAL_SERVE))
            pre_serve_b = float(b.get("serve_rate", NEUTRAL_SERVE))
            serve_residual_a = float(won_a) - float(serve_a) * (1.0 - pre_return_b)
            serve_residual_b = float(won_b) - float(serve_b) * (1.0 - pre_return_a)
            return_a, return_won_a = float(serve_b), float(serve_b) - float(won_b)
            return_b, return_won_b = float(serve_a), float(serve_a) - float(won_a)
            return_residual_a = return_won_a - return_a * (1.0 - pre_serve_b)
            return_residual_b = return_won_b - return_b * (1.0 - pre_serve_a)
            a["serve_points"] += float(serve_a)
            a["serve_won"] += float(won_a)
            a["serve_residual"] += serve_residual_a
            b["serve_points"] += float(serve_b)
            b["serve_won"] += float(won_b)
            b["serve_residual"] += serve_residual_b
            a["return_points"] += return_a
            a["return_won"] += return_won_a
            a["return_residual"] += return_residual_a
            b["return_points"] += return_b
            b["return_won"] += return_won_b
            b["return_residual"] += return_residual_b
            a["serve_observations"].append((played, float(serve_a), float(won_a), serve_residual_a))
            b["serve_observations"].append((played, float(serve_b), float(won_b), serve_residual_b))
            a["return_observations"].append((played, return_a, return_won_a, return_residual_a))
            b["return_observations"].append((played, return_b, return_won_b, return_residual_b))
            a["serve_rate"] = min(0.9, max(0.35, NEUTRAL_SERVE + a["serve_residual"] / (SERVE_PRIOR_POINTS + a["serve_points"])))
            b["serve_rate"] = min(0.9, max(0.35, NEUTRAL_SERVE + b["serve_residual"] / (SERVE_PRIOR_POINTS + b["serve_points"])))
            a["return_rate"] = min(0.65, max(0.1, NEUTRAL_RETURN + a["return_residual"] / (SERVE_PRIOR_POINTS + a["return_points"])))
            b["return_rate"] = min(0.65, max(0.1, NEUTRAL_RETURN + b["return_residual"] / (SERVE_PRIOR_POINTS + b["return_points"])))

        a["elo"] = elo_a + ELO_K * (1.0 - expected_a)
        b["elo"] = elo_b + ELO_K * (0.0 - (1.0 - expected_a))
        a["surface_elo"][surface] = surface_a + ELO_K * (1.0 - expected_surface_a)
        b["surface_elo"][surface] = surface_b + ELO_K * (0.0 - (1.0 - expected_surface_a))
        a["glicko_mu"], a["glicko_phi"], a["glicko_sigma"] = new_mu_a, new_phi_a, new_sigma_a
        b["glicko_mu"], b["glicko_phi"], b["glicko_sigma"] = new_mu_b, new_phi_b, new_sigma_b
        a["matches"] += 1
        a["wins"] += 1
        b["matches"] += 1
        if (head_to_head_player_ids is None
                or (a_id in head_to_head_player_ids and b_id in head_to_head_player_ids)):
            h2h_a = a["head_to_head"].setdefault(
                b_id, {"matches": 0, "wins": 0, "losses": 0, "last_meeting_at": None}
            )
            h2h_b = b["head_to_head"].setdefault(
                a_id, {"matches": 0, "wins": 0, "losses": 0, "last_meeting_at": None}
            )
            h2h_a["matches"] += 1
            h2h_a["wins"] += 1
            h2h_b["matches"] += 1
            h2h_b["losses"] += 1
            h2h_a["last_meeting_at"] = h2h_b["last_meeting_at"] = played.isoformat()
        a["results"].append({"played_at": played.isoformat(), "won": True, "surface": surface})
        b["results"].append({"played_at": played.isoformat(), "won": False, "surface": surface})
        for state in (a, b):
            while state["serve_observations"] and state["serve_observations"][0][0] < limit - timedelta(days=365):
                state["serve_observations"].popleft()
            while state["return_observations"] and state["return_observations"][0][0] < limit - timedelta(days=365):
                state["return_observations"].popleft()
        a["last_played_at"] = b["last_played_at"] = played

    output: dict[str, dict[str, Any]] = {}
    for player_id, value in people.items():
        recent = list(value["results"])
        serve_365 = [row for row in value["serve_observations"] if row[0] >= limit - timedelta(days=365)]
        return_365 = [row for row in value["return_observations"] if row[0] >= limit - timedelta(days=365)]
        serve_volume_365 = sum(row[1] for row in serve_365)
        return_volume_365 = sum(row[1] for row in return_365)
        serve_residual_365 = sum(row[3] for row in serve_365)
        return_residual_365 = sum(row[3] for row in return_365)
        days_90 = [row for row in recent if _utc(datetime.fromisoformat(row["played_at"])) >= limit - timedelta(days=90)]
        days_365 = [row for row in recent if _utc(datetime.fromisoformat(row["played_at"])) >= limit - timedelta(days=365)]
        surfaces = sorted({str(row.get("surface") or "unknown").lower() for row in recent})
        recent_by_surface = {}
        for item_surface in surfaces:
            surface_rows = [row for row in recent if str(row.get("surface") or "unknown").lower() == item_surface]
            surface_90 = [row for row in surface_rows
                          if _utc(datetime.fromisoformat(row["played_at"])) >= limit - timedelta(days=90)]
            surface_365 = [row for row in surface_rows
                           if _utc(datetime.fromisoformat(row["played_at"])) >= limit - timedelta(days=365)]
            recent_by_surface[item_surface] = {
                "last_10": _summary(surface_rows[-10:]),
                "last_20": _summary(surface_rows[-20:]),
                "days_90": _summary(surface_90),
                "days_365": _summary(surface_365),
            }
        output[player_id] = {
            "metrics_version": INDEPENDENT_METRICS_VERSION,
            "elo": {"overall": round(value["elo"], 3), "by_surface": {k: round(v, 3) for k, v in value["surface_elo"].items()}},
            "glicko": {"rating": round(1500.0 + GLICKO_SCALE * value["glicko_mu"], 3),
                       "rd": round(GLICKO_SCALE * value["glicko_phi"], 3), "volatility": round(value["glicko_sigma"], 6)},
            "overall": {"matches": value["matches"], "wins": value["wins"], "losses": value["matches"] - value["wins"]},
            "recent": {"last_10": _summary(recent[-10:]), "last_20": _summary(recent[-20:]),
                       "days_90": _summary(days_90), "days_365": _summary(days_365)},
            "recent_by_surface": recent_by_surface,
            "head_to_head": {
                opponent_id: {**record, "win_rate": record["wins"] / record["matches"] if record["matches"] else None}
                for opponent_id, record in value["head_to_head"].items()
            },
            "serve": {"points": serve_volume_365,
                      "points_won": sum(row[2] for row in serve_365),
                      "adjusted_rate": min(0.9, max(0.35, NEUTRAL_SERVE + serve_residual_365 / (SERVE_PRIOR_POINTS + serve_volume_365))),
                      "sample_points": serve_volume_365, "available_matches": len(serve_365)},
            "return": {"points": return_volume_365,
                       "points_won": sum(row[2] for row in return_365),
                       "adjusted_rate": min(0.65, max(0.1, NEUTRAL_RETURN + return_residual_365 / (SERVE_PRIOR_POINTS + return_volume_365))),
                       "sample_points": return_volume_365, "available_matches": len(return_365)},
            "source_watermark": max((row["played_at"] for row in recent), default=None),
        }
    return output


def digest_metrics(metrics: dict[str, Any]) -> str:
    payload = json.dumps(metrics, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
