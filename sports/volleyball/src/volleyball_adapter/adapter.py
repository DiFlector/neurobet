"""Volleyball Sport Adapter implementation for Neurobet."""

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
from .normalizer import normalize_russian_volleyball_team

logger = logging.getLogger("volleyball.adapter")


class VolleyballSportAdapter:
    """
    Volleyball Sport Adapter for Neurobet.
    Handles volleyball event normalization, Russian club naming,
    hierarchical sets and points state parsing (match -> set -> points/server/timeouts),
    and volleyball odds snapshotting (moneyline, set handicap, points total).
    Preserves clean sport-independent core contracts.
    """

    sport_code: str = "volleyball"

    def supported_markets(self) -> List[str]:
        return [
            "match_winner",
            "set_winner",
            "total_points",
            "handicap_sets",
            "handicap_points",
        ]

    def detect_surface(self, tournament: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        """Volleyball surface is indoor taraflex or parquet."""
        return "indoor_taraflex"

    def normalize_player_name(self, raw_name: str) -> str:
        """Normalizes volleyball team name to canonical Russian form."""
        return normalize_russian_volleyball_team(raw_name)

    def parse_event(self, raw_payload: Dict[str, Any]) -> Event:
        """Constructs canonical Event contract from raw Fonbet volleyball data."""
        source_id = str(raw_payload.get("source_event_id") or raw_payload.get("id") or "unknown")
        raw_tournament = (
            raw_payload.get("tournament")
            or raw_payload.get("competitionName")
            or "Волейбол. Суперлига"
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

        event_id = raw_payload.get("event_id") or f"fonbet_volleyball_{source_id}"

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
                "surface": "indoor_taraflex",
                "raw_tournament": raw_tournament,
            },
        )

    def parse_state(self, raw_payload: Dict[str, Any]) -> EventState:
        """
        Parses live hierarchical match state for volleyball:
        Sets won (e.g. 2:1), set points (e.g. 25-22, 23-25, 25-18, 14-12), serving team, timeouts.
        Encapsulates volleyball-specific set details in stats dictionary to preserve generic contract purity.
        """
        event_id = str(raw_payload.get("event_id") or f"fonbet_volleyball_{raw_payload.get('id', 'unknown')}")
        score_str = str(raw_payload.get("score") or "").strip()

        volleyball_sets: List[Dict[str, int]] = []
        sets_won_a, sets_won_b = 0, 0
        current_period = int(raw_payload.get("current_period") or raw_payload.get("set") or 1)

        # Parse match sets e.g. '2:1 (25-20, 22-25, 25-21, 14-12)'
        match_sets = re.search(r"^\s*(\d+)\s*[:\-]\s*(\d+)", score_str)
        if match_sets:
            sets_won_a = int(match_sets.group(1))
            sets_won_b = int(match_sets.group(2))

        if "(" in score_str and ")" in score_str:
            set_part = score_str[score_str.find("(") + 1 : score_str.find(")")]
            set_segments = [s.strip() for s in set_part.split(",") if s.strip()]
            for idx, s_seg in enumerate(set_segments, start=1):
                m = re.search(r"(\d+)\s*[:\-]\s*(\d+)", s_seg)
                if m:
                    volleyball_sets.append({
                        "set": idx,
                        "points_a": int(m.group(1)),
                        "points_b": int(m.group(2)),
                    })
            if volleyball_sets:
                current_period = len(volleyball_sets)
        elif raw_payload.get("sets_detail"):
            volleyball_sets = list(raw_payload["sets_detail"])
            current_period = len(volleyball_sets)

        # Serving team / Server
        raw_serv = raw_payload.get("server") or raw_payload.get("possession") or raw_payload.get("serving_team")
        serving_team = None
        if raw_serv in ("team_a", "player_a", "1", 1, "home"):
            serving_team = "team_a"
        elif raw_serv in ("team_b", "player_b", "2", 2, "away"):
            serving_team = "team_b"

        # Current set score
        curr_pts_a, curr_pts_b = 0, 0
        if volleyball_sets:
            curr_pts_a = volleyball_sets[-1]["points_a"]
            curr_pts_b = volleyball_sets[-1]["points_b"]
        else:
            curr_pts_a = int(raw_payload.get("points_a", 0))
            curr_pts_b = int(raw_payload.get("points_b", 0))

        # Check match point condition (e.g. 24-20 in sets 1-4 or 14-12 in set 5 with 2 sets won)
        target_pts = 15 if current_period == 5 else 25
        is_match_point = False
        if sets_won_a == 2 and curr_pts_a >= (target_pts - 1) and (curr_pts_a - curr_pts_b) >= 1:
            is_match_point = True
        elif sets_won_b == 2 and curr_pts_b >= (target_pts - 1) and (curr_pts_b - curr_pts_a) >= 1:
            is_match_point = True

        stats = {
            "sets_won_a": sets_won_a,
            "sets_won_b": sets_won_b,
            "current_set_score": {"points_a": curr_pts_a, "points_b": curr_pts_b},
            "volleyball_sets": volleyball_sets,
            "timeouts_a": int(raw_payload.get("timeouts_a", 0)),
            "timeouts_b": int(raw_payload.get("timeouts_b", 0)),
            "is_tiebreak_set": (current_period == 5),
            "is_match_point": is_match_point,
            "score_str": score_str,
        }

        return EventState(
            event_id=event_id,
            sport_code=self.sport_code,
            current_period=current_period,
            sets=[],
            quarters=[],
            server=None,
            possession=serving_team,
            is_break_point=False,
            match_clock_seconds=None,
            stats=stats,
        )

    def parse_odds(self, raw_payload: Dict[str, Any]) -> OddsSnapshot:
        """
        Extracts canonical volleyball odds:
        Moneyline (match_winner), Set Handicap (handicap_sets), Points Total (total_points).
        """
        event_id = str(raw_payload.get("event_id") or f"fonbet_volleyball_{raw_payload.get('id', 'unknown')}")
        raw_markets = raw_payload.get("markets") or []
        obs_time = datetime.now(timezone.utc)

        parsed_markets: List[Market] = []

        if isinstance(raw_markets, list):
            for m in raw_markets:
                m_type = str(m.get("market_type") or m.get("type") or "match_winner")

                selections: List[Selection] = []
                for s in m.get("selections", []):
                    outc = str(s.get("outcome") or s.get("name") or "team_a")
                    odds_val = float(s.get("odds") or s.get("price") or 1.90)
                    prob_imp = round(1.0 / max(1.001, odds_val), 4)
                    selections.append(
                        Selection(
                            selection_id=s.get("selection_id") or f"sel_{m_type}_{outc}",
                            outcome=outc,
                            name=str(s.get("name") or outc),
                            odds=odds_val,
                            probability_implied=prob_imp,
                            status="active" if bool(s.get("is_active", True)) else "suspended",
                        )
                    )

                if len(selections) >= 2:
                    parsed_markets.append(
                        Market(
                            market_id=m.get("market_id") or f"mkt_{event_id}_{m_type}",
                            market_type=m_type,
                            name=m.get("name") or m_type,
                            status="active",
                            selections=selections,
                        )
                    )

        if not parsed_markets:
            odds_a = float(raw_payload.get("odds_a") or 1.85)
            odds_b = float(raw_payload.get("odds_b") or 1.95)
            parsed_markets.append(
                Market(
                    market_id=f"mkt_{event_id}_match_winner",
                    market_type="match_winner",
                    name="Победитель матча",
                    status="active",
                    selections=[
                        Selection(
                            selection_id=f"sel_{event_id}_a",
                            outcome="team_a",
                            name="Команда 1",
                            odds=odds_a,
                            probability_implied=round(1.0 / max(1.001, odds_a), 4),
                            status="active",
                        ),
                        Selection(
                            selection_id=f"sel_{event_id}_b",
                            outcome="team_b",
                            name="Команда 2",
                            odds=odds_b,
                            probability_implied=round(1.0 / max(1.001, odds_b), 4),
                            status="active",
                        ),
                    ],
                )
            )

        content_str = json.dumps(
            [m.model_dump() for m in parsed_markets],
            sort_keys=True,
            default=str,
        )
        content_hash = f"sha256:{hashlib.sha256(content_str.encode('utf-8')).hexdigest()}"
        snapshot_id = f"snap_volleyball_{event_id}_{content_hash[7:19]}"

        return OddsSnapshot(
            snapshot_id=snapshot_id,
            event_id=event_id,
            observed_at=obs_time,
            markets=parsed_markets,
            content_hash=content_hash,
        )

    def parse_result(self, raw_payload: Dict[str, Any]) -> Optional[Settlement]:
        """
        Parses final volleyball match score (e.g. 3:1, 3:2, 3:0) and generates Settlement.
        """
        status = str(raw_payload.get("status") or "").lower()
        if status not in ("finished", "completed", "ended"):
            return None

        event_id = str(raw_payload.get("event_id") or f"fonbet_volleyball_{raw_payload.get('id', 'unknown')}")
        score_str = str(raw_payload.get("score") or "").strip()

        sets_a, sets_b = 0, 0
        m = re.search(r"^\s*(\d+)\s*[:\-]\s*(\d+)", score_str)
        if m:
            sets_a = int(m.group(1))
            sets_b = int(m.group(2))
        else:
            sets_a = int(raw_payload.get("sets_a", 0))
            sets_b = int(raw_payload.get("sets_b", 0))

        if sets_a > sets_b:
            winner = "team_a"
        elif sets_b > sets_a:
            winner = "team_b"
        else:
            winner = "draw"

        bet_id = str(raw_payload.get("bet_id") or f"bet_settle_{event_id}")
        target_outcome = str(raw_payload.get("outcome") or "team_a")
        stake = float(raw_payload.get("stake") or 1000.0)
        odds = float(raw_payload.get("odds") or 1.90)

        if winner == "draw":
            settle_status = "VOID"
            payout = stake
            net_profit = 0.0
        elif winner == target_outcome:
            settle_status = "WON"
            payout = round(stake * odds, 2)
            net_profit = round(payout - stake, 2)
        else:
            settle_status = "LOST"
            payout = 0.0
            net_profit = -stake

        return Settlement(
            bet_id=bet_id,
            status=settle_status,
            payout=payout,
            net_profit=net_profit,
            settled_at=datetime.now(timezone.utc),
            settlement_reason=f"Final score: {sets_a}:{sets_b} sets",
        )

    def validate_sport_rules(self, proposal: Dict[str, Any]) -> bool:
        """
        Volleyball-specific rule validation:
        1. Market must be supported.
        2. Outcome must be valid ('team_a', 'team_b', 'over', 'under').
        3. Odds must be positive and >= 1.01.
        """
        market = str(proposal.get("market") or "")
        if market not in self.supported_markets():
            return False

        outcome = str(proposal.get("outcome") or "")
        valid_outcomes = ("team_a", "team_b", "over", "under")
        if outcome not in valid_outcomes:
            return False

        odds = float(proposal.get("bookmaker_odds") or 0.0)
        if odds < 1.01:
            return False

        return True
