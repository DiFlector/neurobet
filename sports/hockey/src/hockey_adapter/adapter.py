"""Hockey Sport Adapter implementation for Neurobet."""

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
from .normalizer import normalize_russian_hockey_team

logger = logging.getLogger("hockey.adapter")


class HockeySportAdapter:
    """
    Hockey Sport Adapter for Neurobet.
    Handles ice hockey event normalization, Russian/KHL/NHL club naming,
    hierarchical periods and state parsing (periods 1..3, OT, SO, shots, powerplays, empty net),
    and odds snapshotting (match winner, 1X2 60min, total goals, handicap).
    Preserves clean sport-independent core contracts.
    """

    sport_code: str = "hockey"

    def supported_markets(self) -> List[str]:
        return [
            "match_winner",
            "match_winner_regular",
            "total_goals",
            "handicap_goals",
            "period_winner",
        ]

    def detect_surface(self, tournament: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        """Ice surface."""
        return "ice"

    def normalize_player_name(self, raw_name: str) -> str:
        """Normalizes hockey club name to canonical Russian form."""
        return normalize_russian_hockey_team(raw_name)

    def parse_event(self, raw_payload: Dict[str, Any]) -> Event:
        """Constructs canonical Event contract from raw Fonbet hockey data."""
        source_id = str(raw_payload.get("source_event_id") or raw_payload.get("id") or "unknown")
        raw_tournament = (
            raw_payload.get("tournament")
            or raw_payload.get("competitionName")
            or "Хоккей. КХЛ"
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

        event_id = raw_payload.get("event_id") or f"fonbet_hockey_{source_id}"

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
                "surface": "ice",
                "raw_tournament": raw_tournament,
            },
        )

    def parse_state(self, raw_payload: Dict[str, Any]) -> EventState:
        """
        Parses live hierarchical match state for hockey:
        Total score (e.g. 3:2), period scores (e.g. 1:0, 1:1, 1:1), overtime/shootout status,
        shots on goal, powerplay status, empty net.
        Encapsulates hockey-specific details in stats dictionary to preserve generic contract purity.
        """
        event_id = str(raw_payload.get("event_id") or f"fonbet_hockey_{raw_payload.get('id', 'unknown')}")
        score_str = str(raw_payload.get("score") or "").strip()

        score_a, score_b = 0, 0
        hockey_periods: List[Dict[str, int]] = []
        current_period = int(raw_payload.get("current_period") or raw_payload.get("period") or 1)

        # Parse match score e.g. '3:2 (1:0, 1:1, 1:1)' or '4:3'
        match_score = re.search(r"^\s*(\d+)\s*[:\-]\s*(\d+)", score_str)
        if match_score:
            score_a = int(match_score.group(1))
            score_b = int(match_score.group(2))
        else:
            score_a = int(raw_payload.get("score_a", 0))
            score_b = int(raw_payload.get("score_b", 0))

        # Parse periods e.g. '(1:0, 1:1, 1:1)'
        if "(" in score_str and ")" in score_str:
            p_part = score_str[score_str.find("(") + 1 : score_str.find(")")]
            p_segments = [s.strip() for s in p_part.split(",") if s.strip()]
            for idx, seg in enumerate(p_segments, start=1):
                m = re.search(r"(\d+)\s*[:\-]\s*(\d+)", seg)
                if m:
                    hockey_periods.append({
                        "period": idx,
                        "goals_a": int(m.group(1)),
                        "goals_b": int(m.group(2)),
                    })
            if hockey_periods:
                current_period = len(hockey_periods)
        elif raw_payload.get("periods_detail"):
            hockey_periods = list(raw_payload["periods_detail"])
            current_period = len(hockey_periods)

        # Overtime / Shootout detection
        is_ot = bool("ОТ" in score_str or "OT" in score_str or current_period == 4)
        is_so = bool(" Б" in score_str or "SO" in score_str or current_period >= 5)

        # Shots on goal
        raw_shots = raw_payload.get("shots_on_goal") or {}
        shots_a = int(raw_shots.get("team_a") or raw_payload.get("shots_a", 0))
        shots_b = int(raw_shots.get("team_b") or raw_payload.get("shots_b", 0))

        # Powerplay status
        raw_pp = raw_payload.get("powerplay") or {}
        pp_active = bool(raw_pp.get("is_active") or raw_payload.get("is_powerplay", False))
        pp_side = raw_pp.get("team") or raw_payload.get("powerplay_team")

        # Empty net
        empty_net_a = bool(raw_payload.get("empty_net_a", False))
        empty_net_b = bool(raw_payload.get("empty_net_b", False))

        stats = {
            "score_a": score_a,
            "score_b": score_b,
            "hockey_periods": hockey_periods,
            "current_period": current_period,
            "shots_on_goal": {"team_a": shots_a, "team_b": shots_b},
            "powerplay": {
                "is_active": pp_active,
                "team": pp_side,
                "man_advantage": raw_pp.get("man_advantage", "5on4" if pp_active else "5on5"),
            },
            "empty_net_a": empty_net_a,
            "empty_net_b": empty_net_b,
            "is_overtime": is_ot,
            "is_shootout": is_so,
            "score_str": score_str,
        }

        curr_time = str(raw_payload.get("current_time") or raw_payload.get("time") or "00:00")
        stats["current_time"] = curr_time
        stats["is_break"] = bool(raw_payload.get("is_break", False))
        raw_possession = pp_side if pp_active else raw_payload.get("possession")
        possession = raw_possession if raw_possession in ("team_a", "team_b", "player_a", "player_b") else None

        return EventState(
            event_id=event_id,
            sport_code=self.sport_code,
            current_period=current_period,
            sets=[],
            possession=possession,
            stats=stats,
        )

    def parse_odds_snapshot(self, raw_payload: Dict[str, Any]) -> OddsSnapshot:
        """
        Parses bookmaker odds snapshot for hockey event:
        1. match_winner (moneyline including OT & Shootout)
        2. match_winner_regular (1X2 in regular 60 minutes)
        3. total_goals (over / under)
        4. handicap_goals (puck line)
        Computes SHA256 content_hash for deduplication.
        """
        event_id = str(raw_payload.get("event_id") or f"fonbet_hockey_{raw_payload.get('id', 'unknown')}")
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

        # Fallback default match_winner market if none parsed
        if not markets:
            p1_odds = float(raw_payload.get("odds_a") or raw_payload.get("p1") or 1.90)
            p2_odds = float(raw_payload.get("odds_b") or raw_payload.get("p2") or 1.90)
            markets.append(
                Market(
                    market_id=f"{event_id}_match_winner",
                    market_type="match_winner",
                    name="Победитель матча",
                    status="active",
                    selections=[
                        Selection(
                            selection_id=f"{event_id}_team_a",
                            outcome="team_a",
                            name="Команда 1",
                            odds=p1_odds,
                            probability_implied=round(1.0 / max(1.001, p1_odds), 4),
                            status="active",
                        ),
                        Selection(
                            selection_id=f"{event_id}_team_b",
                            outcome="team_b",
                            name="Команда 2",
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
            snapshot_id=f"snap_hockey_{event_id}_{content_hash[7:19]}",
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
        Settles hockey bet based on official final state:
        - match_winner (includes OT / Shootouts): team_a or team_b
        - match_winner_regular (60 min): team_a, draw, or team_b
        - total_goals: over / under against param
        - handicap_goals: team_a / team_b with handicap param
        """
        bet_id = str(bet_payload.get("bet_id") or "bet_unknown")
        market_type = str(bet_payload.get("market_type") or "match_winner")
        selection = str(bet_payload.get("selection") or bet_payload.get("outcome") or "team_a")
        amount = float(bet_payload.get("amount") or 1000.0)
        odds = float(bet_payload.get("odds") or 1.90)
        param = float(bet_payload.get("param") or 0.0)

        # Goals in match
        final_score_a = int(final_state.get("score_a", 0))
        final_score_b = int(final_state.get("score_b", 0))

        # Check regular time score if provided
        reg_score_a = int(final_state.get("reg_score_a", final_score_a))
        reg_score_b = int(final_state.get("reg_score_b", final_score_b))

        status: str = "LOST"
        reason: str = ""

        if market_type == "match_winner":
            winner = "team_a" if final_score_a > final_score_b else "team_b"
            if selection == winner:
                status = "WON"
                reason = f"Team won match {final_score_a}:{final_score_b} (including OT/SO)"
            else:
                status = "LOST"
                reason = f"Team lost match {final_score_a}:{final_score_b}"

        elif market_type == "match_winner_regular":
            if reg_score_a > reg_score_b:
                reg_winner = "team_a"
            elif reg_score_b > reg_score_a:
                reg_winner = "team_b"
            else:
                reg_winner = "draw"

            if selection == reg_winner:
                status = "WON"
                reason = f"Regular time outcome matched: {reg_winner} ({reg_score_a}:{reg_score_b})"
            else:
                status = "LOST"
                reason = f"Regular time outcome was {reg_winner}"

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
