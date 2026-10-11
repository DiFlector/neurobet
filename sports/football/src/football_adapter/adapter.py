"""Football Sport Adapter implementation for Neurobet."""

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from contracts import (
    Event,
    EventState,
    Market,
    OddsSnapshot,
    Selection,
    Settlement,
)
from .normalizer import normalize_russian_football_team

logger = logging.getLogger("football.adapter")


class FootballSportAdapter:
    """
    Football Sport Adapter for Neurobet.
    Handles football/soccer event normalization, Russian RPL / European club naming,
    hierarchical halves and match state parsing (1st half, 2nd half, extra time, penalty shootout,
    yellow/red cards, corners, shots on goal, fouls, xG), and odds snapshotting
    (1X2 match winner, double chance, total goals, handicap, both teams to score).
    Preserves clean sport-independent core contracts.
    """

    sport_code: str = "football"

    def supported_markets(self) -> List[str]:
        return [
            "match_winner",
            "double_chance",
            "total_goals",
            "handicap_goals",
            "both_teams_to_score",
        ]

    def detect_surface(self, tournament: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        """Grass or hybrid surface."""
        return "grass"

    def normalize_player_name(self, raw_name: str) -> str:
        """Normalizes football club name to canonical form."""
        return normalize_russian_football_team(raw_name)

    def parse_event(self, raw_payload: Dict[str, Any]) -> Event:
        """Constructs canonical Event contract from raw Fonbet football data."""
        source_id = str(raw_payload.get("source_event_id") or raw_payload.get("id") or "unknown")
        raw_tournament = (
            raw_payload.get("tournament")
            or raw_payload.get("competitionName")
            or "Футбол. Чемпионат России. Премьер-Лига"
        )

        raw_a = raw_payload.get("participant_a") or raw_payload.get("team1") or "Команда А"
        raw_b = raw_payload.get("participant_b") or raw_payload.get("team2") or "Команда Б"

        if not raw_a or not raw_b:
            if "name" in raw_payload and " - " in str(raw_payload["name"]):
                parts = str(raw_payload["name"]).split(" - ")
                raw_a, raw_b = parts[0], parts[1]

        team_a = self.normalize_player_name(raw_a)
        team_b = self.normalize_player_name(raw_b)

        raw_start = raw_payload.get("scheduled_start") or raw_payload.get("startTime")
        scheduled_dt = datetime.now(timezone.utc)
        if raw_start:
            if isinstance(raw_start, (int, float)):
                scheduled_dt = datetime.fromtimestamp(raw_start, tz=timezone.utc)
            elif isinstance(raw_start, str):
                try:
                    scheduled_dt = datetime.fromisoformat(raw_start)
                    if scheduled_dt.tzinfo is None:
                        scheduled_dt = scheduled_dt.replace(tzinfo=timezone.utc)
                except Exception:
                    pass

        event_id = raw_payload.get("event_id") or f"fonbet_football_{source_id}"

        return Event(
            event_id=event_id,
            source="fonbet",
            source_event_id=source_id,
            sport_code=self.sport_code,
            tournament=raw_tournament,
            participant_a=team_a,
            participant_b=team_b,
            scheduled_start=scheduled_dt,
            is_live=bool(raw_payload.get("is_live", True)),
            status=raw_payload.get("status", "live"),
            metadata={
                "surface": "grass",
                "raw_tournament": raw_tournament,
            },
        )

    def parse_state(self, raw_payload: Dict[str, Any]) -> EventState:
        """
        Parses live hierarchical match state for football:
        Total score (e.g. 2:1), half scores (e.g. 1:0, 1:1), extra time, penalties,
        cards, corners, shots on goal, fouls, possession %, xG.
        Encapsulates football-specific details in stats dictionary to preserve generic contract purity.
        """
        event_id = str(raw_payload.get("event_id") or f"fonbet_football_{raw_payload.get('id', 'unknown')}")
        score_str = str(raw_payload.get("score") or "").strip()

        score_a, score_b = 0, 0
        football_halves: List[Dict[str, int]] = []
        current_period = int(raw_payload.get("current_period") or raw_payload.get("half") or 1)

        # Parse match score e.g. '2:1 (1:0, 1:1)' or '1:1'
        match_score = re.search(r"^\s*(\d+)\s*[:\-]\s*(\d+)", score_str)
        if match_score:
            score_a = int(match_score.group(1))
            score_b = int(match_score.group(2))
        else:
            score_a = int(raw_payload.get("score_a", 0))
            score_b = int(raw_payload.get("score_b", 0))

        # Parse halves e.g. '(1:0, 1:1)'
        if "(" in score_str and ")" in score_str:
            p_part = score_str[score_str.find("(") + 1 : score_str.find(")")]
            p_segments = [s.strip() for s in p_part.split(",") if s.strip()]
            for idx, seg in enumerate(p_segments, start=1):
                m = re.search(r"(\d+)\s*[:\-]\s*(\d+)", seg)
                if m:
                    football_halves.append({
                        "half": idx,
                        "goals_a": int(m.group(1)),
                        "goals_b": int(m.group(2)),
                    })
            if football_halves:
                current_period = len(football_halves)
        elif raw_payload.get("halves_detail"):
            football_halves = list(raw_payload["halves_detail"])
            current_period = len(football_halves)

        # Extra time / Shootout detection
        is_et = bool("доп" in score_str.lower() or "дв" in score_str.lower() or "et" in score_str.lower() or current_period == 3)
        is_pen = bool("пен" in score_str.lower() or "pen" in score_str.lower() or current_period >= 4)

        # Minute of play
        minute = int(raw_payload.get("minute") or raw_payload.get("current_minute") or (45 if current_period == 1 else 90))

        # Cards
        raw_yellow = raw_payload.get("yellow_cards") or {}
        yellow_a = int(raw_yellow.get("team_a") or raw_payload.get("yellow_cards_a", 0))
        yellow_b = int(raw_yellow.get("team_b") or raw_payload.get("yellow_cards_b", 0))

        raw_red = raw_payload.get("red_cards") or {}
        red_a = int(raw_red.get("team_a") or raw_payload.get("red_cards_a", 0))
        red_b = int(raw_red.get("team_b") or raw_payload.get("red_cards_b", 0))

        # Corners
        raw_corners = raw_payload.get("corners") or {}
        corners_a = int(raw_corners.get("team_a") or raw_payload.get("corners_a", 0))
        corners_b = int(raw_corners.get("team_b") or raw_payload.get("corners_b", 0))

        # Shots on goal
        raw_shots = raw_payload.get("shots_on_goal") or {}
        shots_a = int(raw_shots.get("team_a") or raw_payload.get("shots_a", 0))
        shots_b = int(raw_shots.get("team_b") or raw_payload.get("shots_b", 0))

        # Fouls
        raw_fouls = raw_payload.get("fouls") or {}
        fouls_a = int(raw_fouls.get("team_a") or raw_payload.get("fouls_a", 0))
        fouls_b = int(raw_fouls.get("team_b") or raw_payload.get("fouls_b", 0))

        # Possession %
        raw_possession = raw_payload.get("possession_pct") or {}
        poss_a = float(raw_possession.get("team_a") or raw_payload.get("possession_a", 50.0))
        poss_b = float(raw_possession.get("team_b") or raw_payload.get("possession_b", 50.0))

        # Expected goals (xG)
        raw_xg = raw_payload.get("xg") or {}
        xg_a = float(raw_xg.get("team_a") or raw_payload.get("xg_a", 0.0))
        xg_b = float(raw_xg.get("team_b") or raw_payload.get("xg_b", 0.0))

        stats = {
            "score_a": score_a,
            "score_b": score_b,
            "football_halves": football_halves,
            "current_period": current_period,
            "minute": minute,
            "yellow_cards": {"team_a": yellow_a, "team_b": yellow_b},
            "red_cards": {"team_a": red_a, "team_b": red_b},
            "corners": {"team_a": corners_a, "team_b": corners_b},
            "shots_on_goal": {"team_a": shots_a, "team_b": shots_b},
            "fouls": {"team_a": fouls_a, "team_b": fouls_b},
            "possession_pct": {"team_a": poss_a, "team_b": poss_b},
            "xg": {"team_a": xg_a, "team_b": xg_b},
            "is_extra_time": is_et,
            "is_penalties": is_pen,
            "is_break": bool(raw_payload.get("is_break", False)),
            "score_str": score_str,
        }

        curr_time = str(raw_payload.get("current_time") or f"{minute}:00")
        stats["current_time"] = curr_time

        possession_team = raw_payload.get("possession")
        possession = possession_team if possession_team in ("team_a", "team_b", "player_a", "player_b") else None

        return EventState(
            event_id=event_id,
            sport_code=self.sport_code,
            current_period=current_period,
            sets=[],
            possession=possession,
            match_clock_seconds=minute * 60,
            stats=stats,
        )

    def parse_odds_snapshot(self, raw_payload: Dict[str, Any]) -> OddsSnapshot:
        """
        Parses bookmaker odds snapshot for football event:
        1. match_winner (1X2 outcome: team_a, draw, team_b)
        2. double_chance (1X, 12, X2)
        3. total_goals (over / under)
        4. handicap_goals (asian / 3-way handicap)
        5. both_teams_to_score (yes / no)
        Computes SHA256 content_hash for deduplication.
        """
        event_id = str(raw_payload.get("event_id") or f"fonbet_football_{raw_payload.get('id', 'unknown')}")
        obs_time = datetime.now(timezone.utc)
        raw_markets = raw_payload.get("markets") or []
        markets: List[Market] = []

        for m_data in raw_markets:
            m_type = m_data.get("market_type") or m_data.get("type") or "match_winner"
            if m_type not in self.supported_markets():
                continue

            selections: List[Selection] = []
            for sel_data in m_data.get("selections", []):
                odds_val = float(sel_data.get("odds") or sel_data.get("price") or 1.0)
                prob_implied = round(1.0 / max(1.001, odds_val), 4)
                outc = str(sel_data.get("outcome") or sel_data.get("name") or "team_a")

                selections.append(
                    Selection(
                        selection_id=str(sel_data.get("selection_id") or f"sel_{m_type}_{outc}"),
                        outcome=outc,
                        name=str(sel_data.get("name") or outc),
                        odds=odds_val,
                        probability_implied=prob_implied,
                        status="active" if sel_data.get("status", "active") == "active" else "suspended",
                    )
                )

            if len(selections) >= 2:
                markets.append(
                    Market(
                        market_id=str(m_data.get("market_id") or f"{event_id}_{m_type}"),
                        market_type=m_type,
                        name=str(m_data.get("name") or m_type),
                        status="active",
                        selections=selections,
                    )
                )

        # Fallback default 1X2 match_winner market if none parsed
        if not markets:
            p1_odds = float(raw_payload.get("odds_a") or raw_payload.get("p1") or 2.10)
            draw_odds = float(raw_payload.get("odds_x") or raw_payload.get("x") or 3.20)
            p2_odds = float(raw_payload.get("odds_b") or raw_payload.get("p2") or 3.50)
            markets.append(
                Market(
                    market_id=f"{event_id}_match_winner",
                    market_type="match_winner",
                    name="Результат матча (1X2)",
                    status="active",
                    selections=[
                        Selection(
                            selection_id=f"{event_id}_team_a",
                            outcome="team_a",
                            name="П1",
                            odds=p1_odds,
                            probability_implied=round(1.0 / max(1.001, p1_odds), 4),
                            status="active",
                        ),
                        Selection(
                            selection_id=f"{event_id}_draw",
                            outcome="draw",
                            name="X",
                            odds=draw_odds,
                            probability_implied=round(1.0 / max(1.001, draw_odds), 4),
                            status="active",
                        ),
                        Selection(
                            selection_id=f"{event_id}_team_b",
                            outcome="team_b",
                            name="П2",
                            odds=p2_odds,
                            probability_implied=round(1.0 / max(1.001, p2_odds), 4),
                            status="active",
                        ),
                    ],
                )
            )

        hash_payload = [
            {
                "market_id": m.market_id,
                "market_type": m.market_type,
                "selections": [
                    {
                        "outcome": s.outcome,
                        "odds": s.odds,
                        "status": s.status,
                    }
                    for s in m.selections
                ],
            }
            for m in markets
        ]
        content_str = json.dumps(hash_payload, sort_keys=True)
        content_hash = f"sha256:{hashlib.sha256(content_str.encode('utf-8')).hexdigest()}"

        return OddsSnapshot(
            snapshot_id=f"snap_football_{event_id}_{content_hash[7:19]}",
            event_id=event_id,
            observed_at=obs_time,
            markets=markets,
            content_hash=content_hash,
        )

    def parse_settlement(
        self,
        bet_payload: Dict[str, Any],
        final_state: Dict[str, Any],
    ) -> Settlement:
        """
        Settles football bet based on official final state:
        - match_winner (1X2 90 min): team_a, draw, or team_b
        - double_chance: 1x, 12, x2
        - total_goals: over / under against param
        - handicap_goals: team_a / team_b with handicap param
        - both_teams_to_score: yes / no
        """
        bet_id = str(bet_payload.get("bet_id") or "bet_unknown")
        market_type = str(bet_payload.get("market_type") or "match_winner")
        selection = str(bet_payload.get("selection") or bet_payload.get("outcome") or "team_a").lower()
        amount = float(bet_payload.get("amount") or 1000.0)
        odds = float(bet_payload.get("odds") or 1.90)
        param = float(bet_payload.get("param") or 0.0)

        # Final match goals (regulation 90 min)
        final_score_a = int(final_state.get("score_a", 0))
        final_score_b = int(final_state.get("score_b", 0))

        status: str = "LOST"
        reason: str = ""

        if market_type == "match_winner":
            if final_score_a > final_score_b:
                actual = "team_a"
            elif final_score_b > final_score_a:
                actual = "team_b"
            else:
                actual = "draw"

            if selection == actual:
                status = "WON"
                reason = f"Match outcome matched: {actual} ({final_score_a}:{final_score_b})"
            else:
                status = "LOST"
                reason = f"Match outcome was {actual} ({final_score_a}:{final_score_b})"

        elif market_type == "double_chance":
            if final_score_a > final_score_b:
                is_win = selection in ("1x", "12", "team_a")
            elif final_score_b > final_score_a:
                is_win = selection in ("x2", "12", "team_b")
            else:
                is_win = selection in ("1x", "x2", "draw")

            status = "WON" if is_win else "LOST"
            reason = f"Double chance selection {selection} evaluated on {final_score_a}:{final_score_b}"

        elif market_type == "total_goals":
            total_goals = final_score_a + final_score_b
            if selection == "over":
                status = "WON" if total_goals > param else "LOST"
            elif selection == "under":
                status = "WON" if total_goals < param else "LOST"
            reason = f"Total goals {total_goals} vs line {param}"

        elif market_type == "handicap_goals":
            diff = (final_score_a + param) - final_score_b if selection == "team_a" else (final_score_b + param) - final_score_a
            if diff > 0:
                status = "WON"
                reason = f"Handicap covered: diff {diff} with hcap {param}"
            elif diff == 0:
                status = "VOID"
                reason = f"Handicap push: diff 0 with hcap {param}"
            else:
                status = "LOST"
                reason = f"Handicap failed: diff {diff} with hcap {param}"

        elif market_type == "both_teams_to_score":
            both_scored = (final_score_a > 0 and final_score_b > 0)
            if selection == "yes":
                status = "WON" if both_scored else "LOST"
            elif selection == "no":
                status = "WON" if not both_scored else "LOST"
            reason = f"Both teams scored: {both_scored} ({final_score_a}:{final_score_b})"

        payout = round(amount * odds, 2) if status == "WON" else (amount if status == "VOID" else 0.0)
        net_profit = round(payout - amount, 2)

        return Settlement(
            bet_id=bet_id,
            status=status,
            payout=payout,
            net_profit=net_profit,
            settlement_reason=reason,
            settled_at=datetime.now(timezone.utc),
        )
