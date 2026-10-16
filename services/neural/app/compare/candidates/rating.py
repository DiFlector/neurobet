"""Elo and Glicko-2 built only from matches already available at the cutoff."""

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from ..rows import CompareRow

SCALE = 173.7178
PRIOR = {
    "grand_slam": 1650.0,
    "masters": 1600.0,
    "atp": 1550.0,
    "wta": 1550.0,
    "atp250": 1550.0,
    "atp500": 1560.0,
    "challenger": 1450.0,
    "itf": 1350.0,
    "unknown": 1500.0,
}


@dataclass
class HistoryMatch:
    available_at: datetime
    winner: str
    loser: str
    surface: str
    tour_level: str
    serve_points_winner: float = 0.0
    serve_won_winner: float = 0.0
    serve_points_loser: float = 0.0
    serve_won_loser: float = 0.0
    winner_rank: Optional[int] = None
    loser_rank: Optional[int] = None


def _prior(level: str) -> float:
    return PRIOR.get((level or "unknown").lower(), 1500.0)


def _g(phi: float) -> float:
    return 1.0 / math.sqrt(1.0 + 3.0 * phi * phi / (math.pi ** 2))


def _expect(mu: float, mu_j: float, phi_j: float) -> float:
    return 1.0 / (1.0 + math.exp(-_g(phi_j) * (mu - mu_j)))


def _glicko_update(mu: float, phi: float, sigma: float, mu_j: float, phi_j: float, score: float) -> Tuple[float, float, float]:
    gj = _g(phi_j)
    ej = _expect(mu, mu_j, phi_j)
    v = 1.0 / max(1e-8, gj * gj * ej * (1.0 - ej))
    delta = v * gj * (score - ej)
    a = math.log(sigma * sigma)
    tau = 0.5

    def f(x: float) -> float:
        ex = math.exp(x)
        diff = phi * phi + v + ex
        return ex * (delta * delta - phi * phi - v - ex) / (2.0 * diff * diff) - (x - a) / (tau * tau)

    aa = a
    if delta * delta > phi * phi + v:
        bb = math.log(delta * delta - phi * phi - v)
    else:
        step = 1
        while f(a - step * tau) < 0 and step < 20:
            step += 1
        bb = a - step * tau
    fa, fb = f(aa), f(bb)
    for _ in range(30):
        if abs(fb - fa) < 1e-12:
            break
        cc = aa + (aa - bb) * fa / (fb - fa)
        fc = f(cc)
        if fc * fb <= 0:
            aa, fa = bb, fb
        else:
            fa = fa / 2.0
        bb, fb = cc, fc
        if abs(bb - aa) < 1e-6:
            break
    sigma_new = math.exp(bb / 2.0)
    phi_star = math.sqrt(phi * phi + sigma_new * sigma_new)
    phi_new = 1.0 / math.sqrt(1.0 / (phi_star * phi_star) + 1.0 / v)
    mu_new = mu + phi_new * phi_new * gj * (score - ej)
    return mu_new, phi_new, sigma_new


class _Player:
    def __init__(self, rating: float):
        self.rating = rating
        self.matches = 0
        self.surface: Dict[str, float] = {}
        self.mu = (rating - 1500.0) / SCALE
        self.phi = 350.0 / SCALE
        self.sigma = 0.06
        self.serve_points = 0.0
        self.serve_won = 0.0
        self.rank: Optional[int] = None
        self.snapshots: List[tuple] = []

    def capture(self, moment: datetime) -> None:
        self.snapshots.append(
            (
                moment,
                self.rating,
                dict(self.surface),
                self.matches,
                self.mu,
                self.phi,
                self.serve_points,
                self.serve_won,
                self.rank,
            )
        )


class RatingBook:
    """Replay history in available_at order and answer with the previous snapshot."""

    def __init__(self, history: Sequence[HistoryMatch]):
        self.players: Dict[str, _Player] = {}
        ordered = sorted(history, key=lambda item: item.available_at)
        for match in ordered:
            self._apply(match)

    def _get(self, name: str, level: str) -> _Player:
        player = self.players.get(name)
        if player is None:
            player = _Player(_prior(level))
            self.players[name] = player
        return player

    def _apply(self, match: HistoryMatch) -> None:
        if match.winner == match.loser:
            player = self._get(match.winner, match.tour_level)
            if match.serve_points_winner > 0:
                player.serve_points += match.serve_points_winner
                player.serve_won += match.serve_won_winner
            player.capture(match.available_at)
            return
        winner = self._get(match.winner, match.tour_level)
        loser = self._get(match.loser, match.tour_level)
        for player, points, won, rank in (
            (winner, match.serve_points_winner, match.serve_won_winner, match.winner_rank),
            (loser, match.serve_points_loser, match.serve_won_loser, match.loser_rank),
        ):
            if points > 0:
                player.serve_points += points
                player.serve_won += won
            if rank:
                player.rank = rank
        k_w = max(8.0, 32.0 * 15.0 / (15.0 + winner.matches))
        k_l = max(8.0, 32.0 * 15.0 / (15.0 + loser.matches))
        pre_w, pre_l = winner.rating, loser.rating
        expected = 1.0 / (1.0 + 10 ** ((pre_l - pre_w) / 400.0))
        winner.rating += k_w * (1.0 - expected)
        loser.rating += k_l * (0.0 - (1.0 - expected))
        surface = match.surface or "unknown"
        winner.surface[surface] = winner.surface.get(surface, pre_w) + k_w * (1.0 - expected)
        loser.surface[surface] = loser.surface.get(surface, pre_l) + k_l * (0.0 - (1.0 - expected))
        mu_w, phi_w, mu_l, phi_l = winner.mu, winner.phi, loser.mu, loser.phi
        winner.mu, winner.phi, winner.sigma = _glicko_update(mu_w, phi_w, winner.sigma, mu_l, phi_l, 1.0)
        loser.mu, loser.phi, loser.sigma = _glicko_update(mu_l, phi_l, loser.sigma, mu_w, phi_w, 0.0)
        winner.matches += 1
        loser.matches += 1
        winner.capture(match.available_at)
        loser.capture(match.available_at)

    def lookup(self, name: str, cutoff: datetime, surface: str, level: str) -> dict:
        player = self.players.get(name)
        if player is None or not player.snapshots or player.snapshots[0][0] > cutoff:
            base = _prior(level)
            return {"elo": base, "surface_elo": base, "glicko": base, "rd": 350.0, "serve": 0.62, "rank": None, "seen": False}
        chosen = player.snapshots[0]
        for snap in player.snapshots:
            if snap[0] <= cutoff:
                chosen = snap
            else:
                break
        _moment, elo, surfaces, _matches, mu, phi, points, won, rank = chosen
        # The snapshot is taken when the match becomes available, so it is excluded
        # when the cutoff is earlier than that moment. The loop above already does that.
        if chosen[0] > cutoff:
            base = _prior(level)
            return {"elo": base, "surface_elo": base, "glicko": base, "rd": 350.0, "serve": 0.62, "rank": None, "seen": False}
        serve = (won / points) if points >= 20 else 0.62
        return {
            "elo": elo,
            "surface_elo": surfaces.get(surface, elo),
            "glicko": 1500.0 + SCALE * mu,
            "rd": SCALE * phi,
            "serve": serve,
            "rank": rank,
            "seen": True,
        }

    def attach(self, rows: Sequence[CompareRow]) -> None:
        for row in rows:
            if row.observed_at > row.finished_at:
                raise RuntimeError("Срез позже момента, когда результат уже был известен.")
            left = self.lookup(row.player_a, row.observed_at, row.surface, row.tour_level)
            right = self.lookup(row.player_b, row.observed_at, row.surface, row.tour_level)
            row.elo_diff = left["elo"] - right["elo"]
            row.surface_elo_diff = left["surface_elo"] - right["surface_elo"]
            row.glicko_diff = left["glicko"] - right["glicko"]
            row.glicko_rd = 0.5 * (left["rd"] + right["rd"])
            row.serve_rate_diff = left["serve"] - right["serve"]
            if left["rank"] and right["rank"]:
                row.rank_diff = (1.0 / left["rank"]) - (1.0 / right["rank"])


class RatingCandidate:
    name = "B_elo_glicko"

    def fit(self, rows: Sequence[CompareRow]) -> None:
        return None

    def predict(self, rows: Sequence[CompareRow]) -> np.ndarray:
        out = []
        for row in rows:
            rating_diff = 0.5 * row.elo_diff + 0.3 * row.surface_elo_diff + 0.2 * row.glicko_diff
            out.append(1.0 / (1.0 + 10 ** (-rating_diff / 400.0)))
        return np.asarray(out, dtype=float)
