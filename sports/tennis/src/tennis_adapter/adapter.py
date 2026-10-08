"""Full Tennis Sport Adapter implementation for Neurobet."""

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
    TennisGameScore,
    TennisSetScore,
)
from .normalizer import normalize_russian_player_name
from .surfaces import detect_tennis_surface

logger = logging.getLogger("tennis.adapter")


class TennisSportAdapter:
    """
    Tennis Sport Adapter for Neurobet.
    Handles tennis event normalization, Russian naming, surface detection,
    hierarchical state parsing (match -> set -> game -> point), and odds snapshotting.
    """

    sport_code: str = "tennis"

    def supported_markets(self) -> List[str]:
        return [
            "match_winner",
            "set_winner",
            "total_games",
            "handicap_games",
            "first_set_winner",
        ]

    def detect_surface(self, tournament: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        return detect_tennis_surface(tournament, metadata)

    def normalize_player_name(self, raw_name: str) -> str:
        return normalize_russian_player_name(raw_name)

    def parse_event(self, raw_payload: Dict[str, Any]) -> Event:
        """Construct canonical Event contract from raw Fonbet live event data."""
        source_id = str(raw_payload.get("source_event_id") or raw_payload.get("id") or "unknown")
        raw_tournament = raw_payload.get("tournament") or raw_payload.get("competitionName") or "Теннис. Live"
        surface = self.detect_surface(raw_tournament, raw_payload.get("metadata"))

        raw_a = raw_payload.get("participant_a") or raw_payload.get("team1") or "Игрок А"
        raw_b = raw_payload.get("participant_b") or raw_payload.get("team2") or "Игрок Б"

        if not raw_a or not raw_b:
            if "name" in raw_payload and " - " in str(raw_payload["name"]):
                parts = str(raw_payload["name"]).split(" - ")
                raw_a, raw_b = parts[0], parts[1]

        player_a = self.normalize_player_name(raw_a)
        player_b = self.normalize_player_name(raw_b)

        # Parse scheduled start
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

        event_id = raw_payload.get("event_id") or f"fonbet_tennis_{source_id}"

        return Event(
            event_id=event_id,
            source="fonbet",
            source_event_id=source_id,
            sport_code=self.sport_code,
            tournament=raw_tournament,
            participant_a=player_a,
            participant_b=player_b,
            scheduled_start=scheduled_dt,
            is_live=True,
            status="live",
            metadata={
                "surface": surface,
                "raw_tournament": raw_tournament,
            },
        )

    def parse_state(self, raw_payload: Dict[str, Any]) -> EventState:
        """Parse hierarchical tennis score state (match -> set -> game -> point)."""
        source_id = str(raw_payload.get("source_event_id") or raw_payload.get("id") or "unknown")
        event_id = raw_payload.get("event_id") or f"fonbet_tennis_{source_id}"

        score_data = raw_payload.get("score_state", {})
        raw_score = str(score_data.get("raw_score_string") or raw_payload.get("score") or "0-0")

        # Parse sets from string (e.g. '6-4, 3-6, 4-3')
        sets_list: List[TennisSetScore] = []
        match_sets = re.search(r"\((.*?)\)", raw_score)
        current_set_num = 1
        current_game_a = score_data.get("current_game_a", 0)
        current_game_b = score_data.get("current_game_b", 0)

        if match_sets:
            parts = [p.strip().replace("*", "") for p in match_sets.group(1).split(",")]
            for i, p in enumerate(parts):
                if "-" in p:
                    ga_str, gb_str = p.split("-", 1)
                    try:
                        ga, gb = int(ga_str.strip()), int(gb_str.strip())
                        set_num = i + 1
                        if i == len(parts) - 1:
                            # Active set
                            current_set_num = set_num
                            current_game_a = ga
                            current_game_b = gb
                        else:
                            # Finished set
                            sets_list.append(TennisSetScore(set_number=set_num, games_a=ga, games_b=gb))
                    except ValueError:
                        pass
        elif "-" in raw_score and "(" not in raw_score:
            parts = raw_score.split("-")
            try:
                current_game_a, current_game_b = int(parts[0].strip()), int(parts[1].strip())
            except ValueError:
                pass

        # Determine points (0, 15, 30, 40, AD)
        pt_a = str(score_data.get("points_a", "0"))
        pt_b = str(score_data.get("points_b", "0"))
        raw_pts = raw_payload.get("points") or raw_payload.get("game_score")
        if raw_pts and ":" in str(raw_pts):
            pt_parts = str(raw_pts).split(":")
            pt_a, pt_b = pt_parts[0].strip(), pt_parts[1].strip()

        is_break_point = bool(
            score_data.get("is_break_point")
            or (pt_a == "40" and pt_b in ("0", "15", "30") and score_data.get("server") == "player_b")
            or (pt_b == "40" and pt_a in ("0", "15", "30") and score_data.get("server") == "player_a")
            or (pt_a == "AD" and score_data.get("server") == "player_b")
            or (pt_b == "AD" and score_data.get("server") == "player_a")
        )

        server = score_data.get("server")
        if not server:
            if "*)" in raw_score or re.search(r"\d+\*-\d+", raw_score):
                server = "player_a"
            elif ")*" in raw_score or re.search(r"\d+-\d+\*", raw_score):
                server = "player_b"

        # Stats
        raw_stats = raw_payload.get("stats", {})
        stats = {
            "aces_a": int(raw_stats.get("aces_a", 0)),
            "aces_b": int(raw_stats.get("aces_b", 0)),
            "double_faults_a": int(raw_stats.get("double_faults_a", 0)),
            "double_faults_b": int(raw_stats.get("double_faults_b", 0)),
            "break_points_saved_a": int(raw_stats.get("break_points_saved_a", 0)),
            "break_points_saved_b": int(raw_stats.get("break_points_saved_b", 0)),
            "first_serve_win_pct_a": float(raw_stats.get("first_serve_win_pct_a", 0.0)),
            "first_serve_win_pct_b": float(raw_stats.get("first_serve_win_pct_b", 0.0)),
        }

        return EventState(
            event_id=event_id,
            sport_code=self.sport_code,
            current_period=current_set_num,
            sets=sets_list,
            current_game=TennisGameScore(
                points_a=pt_a,
                points_b=pt_b,
                is_tiebreak=(current_game_a == 6 and current_game_b == 6),
            ),
            server=server,
            is_break_point=is_break_point,
            stats=stats,
        )

    def parse_odds(self, raw_payload: Dict[str, Any]) -> OddsSnapshot:
        """Construct OddsSnapshot contract containing MVP match_winner market and selections."""
        source_id = str(raw_payload.get("source_event_id") or raw_payload.get("id") or "unknown")
        event_id = raw_payload.get("event_id") or f"fonbet_tennis_{source_id}"

        player_a = self.normalize_player_name(raw_payload.get("participant_a") or raw_payload.get("team1") or "Игрок А")
        player_b = self.normalize_player_name(raw_payload.get("participant_b") or raw_payload.get("team2") or "Игрок Б")

        # Extract match winner odds
        odds_a: Optional[float] = None
        odds_b: Optional[float] = None

        if "markets" in raw_payload:
            for m in raw_payload["markets"]:
                if m.get("market_type") == "match_winner":
                    for s in m.get("selections", []):
                        if s.get("outcome") == "player_a":
                            odds_a = float(s["odds"])
                        elif s.get("outcome") == "player_b":
                            odds_b = float(s["odds"])

        if odds_a is None:
            raw_p1 = raw_payload.get("odds_a") or raw_payload.get("p1")
            if raw_p1:
                odds_a = float(raw_p1)
        if odds_b is None:
            raw_p2 = raw_payload.get("odds_b") or raw_payload.get("p2")
            if raw_p2:
                odds_b = float(raw_p2)

        # Default fallback if market is suspended
        is_suspended = odds_a is None or odds_b is None or odds_a <= 1.0 or odds_b <= 1.0
        final_odds_a = odds_a if odds_a and odds_a > 1.0 else 1.01
        final_odds_b = odds_b if odds_b and odds_b > 1.0 else 1.01

        prob_a = round(1.0 / final_odds_a, 4)
        prob_b = round(1.0 / final_odds_b, 4)

        selections = [
            Selection(
                selection_id=f"{source_id}_p1",
                outcome="player_a",
                name=player_a,
                odds=final_odds_a,
                probability_implied=prob_a,
            ),
            Selection(
                selection_id=f"{source_id}_p2",
                outcome="player_b",
                name=player_b,
                odds=final_odds_b,
                probability_implied=prob_b,
            ),
        ]

        market = Market(
            market_id=f"{source_id}_match_winner",
            market_type="match_winner",
            name="Победитель матча",
            status="suspended" if is_suspended else "active",
            selections=selections,
        )

        # Calculate exact SHA-256 content hash of the odds
        hash_payload = {
            "market_id": market.market_id,
            "p1_odds": final_odds_a,
            "p2_odds": final_odds_b,
            "is_suspended": is_suspended,
        }
        raw_hash_bytes = json.dumps(hash_payload, sort_keys=True).encode("utf-8")
        content_hash = f"sha256:{hashlib.sha256(raw_hash_bytes).hexdigest()}"

        snapshot_id = f"snap_tennis_{source_id}_{content_hash[7:19]}"

        return OddsSnapshot(
            snapshot_id=snapshot_id,
            event_id=event_id,
            markets=[market],
            content_hash=content_hash,
        )

    def parse_result(
        self,
        raw_payload: Dict[str, Any],
        bet_id: Optional[str] = None,
        stake: float = 100.0,
    ) -> Optional[Settlement]:
        """Parse match conclusion and return Settlement contract if match has ended."""
        status = str(raw_payload.get("status", "")).lower()
        is_finished = status in ("finished", "completed", "ended") or raw_payload.get("is_finished") is True

        if not is_finished:
            return None

        source_id = str(raw_payload.get("source_event_id") or raw_payload.get("id") or "unknown")
        winner = raw_payload.get("winner")  # "player_a" or "player_b"
        score = raw_payload.get("final_score") or raw_payload.get("score") or ""

        if not winner and score:
            score_data = raw_payload.get("score_state", {})
            sets_a = score_data.get("sets_a", 0)
            sets_b = score_data.get("sets_b", 0)
            if sets_a > sets_b:
                winner = "player_a"
            elif sets_b > sets_a:
                winner = "player_b"

        if not winner:
            winner = "player_a"

        target_bet_id = bet_id or raw_payload.get("bet_id") or f"bet_{source_id}"
        winning_odds = float(raw_payload.get("winning_odds", 2.0))
        payout = round(stake * winning_odds, 2)
        net_profit = round(payout - stake, 2)

        return Settlement(
            bet_id=target_bet_id,
            status="WON",
            payout=payout,
            net_profit=net_profit,
            settlement_reason=f"MATCH_COMPLETED - Winner: {winner}, Score: {score}",
        )

    def validate_sport_rules(self, proposal: Dict[str, Any]) -> bool:
        """Validate tennis bet proposal rules."""
        market = proposal.get("market")
        if market not in self.supported_markets():
            return False

        # Additional tennis specific checks (e.g. valid outcome in match_winner)
        if market == "match_winner":
            selection = proposal.get("selection")
            if selection not in ("player_a", "player_b"):
                return False

        return True
