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


def set_winner_from_games(games_a: Any, games_b: Any) -> Optional[str]:
    """Winner of a closed tennis set. An unfinished or tied score stays unlabeled."""
    try:
        left = int(games_a)
        right = int(games_b)
    except (TypeError, ValueError):
        return None
    if left == right:
        return None
    high, low = max(left, right), min(left, right)
    if high < 6 or high > 7:
        return None
    if high == 6 and high - low < 2:
        return None
    if high == 7 and low < 5:
        return None
    return "player_a" if left > right else "player_b"


def closed_set_winners(set_lines: Any) -> Dict[str, str]:
    """Set number to winner. The live set is not closed yet."""
    winners: Dict[str, str] = {}
    for index, line in enumerate(set_lines or [], start=1):
        if not isinstance(line, dict) or line.get("live"):
            continue
        winner = set_winner_from_games(line.get("games_a"), line.get("games_b"))
        if winner:
            winners[str(index)] = winner
    return winners


def final_set_winners(set_lines: Any) -> Dict[str, str]:
    """Closed sets plus a live set whose score is already a finished set."""
    winners = closed_set_winners(set_lines)
    for index, line in enumerate(set_lines or [], start=1):
        key = str(index)
        if key in winners or not isinstance(line, dict) or not line.get("live"):
            continue
        winner = set_winner_from_games(line.get("games_a"), line.get("games_b"))
        if winner:
            winners[key] = winner
    return winners


def winners_from_score_text(text: Any) -> Dict[str, str]:
    """Winners inside a final score such as ``0:2 (3-6 4-6)``."""
    raw = str(text or "")
    start, end = raw.find("("), raw.rfind(")")
    if start < 0 or end <= start:
        return {}
    winners: Dict[str, str] = {}
    for index, pair in enumerate(re.finditer(r"(\d+)\s*[-:]\s*(\d+)", raw[start + 1:end]), start=1):
        winner = set_winner_from_games(pair.group(1), pair.group(2))
        if winner is None:
            break
        winners[str(index)] = winner
    return winners


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

        fonbet_score = raw_payload.get("fonbet_score") if isinstance(raw_payload.get("fonbet_score"), dict) else None
        point_detail = None
        sets_won_a = None
        sets_won_b = None
        if fonbet_score and isinstance(fonbet_score.get("lines"), list) and fonbet_score["lines"]:
            sets_list = []
            lines = fonbet_score["lines"]
            for index, line in enumerate(lines):
                try:
                    ga, gb = int(line.get("games_a")), int(line.get("games_b"))
                except (TypeError, ValueError):
                    continue
                if index < len(lines) - 1:
                    sets_list.append(TennisSetScore(set_number=index + 1, games_a=ga, games_b=gb))
                else:
                    current_set_num = index + 1
                    current_game_a, current_game_b = ga, gb
                    point_detail = line.get("detail")
            if fonbet_score.get("points_a") is not None:
                score_data = dict(score_data)
                score_data["points_a"] = fonbet_score.get("points_a")
                score_data["points_b"] = fonbet_score.get("points_b")
                # Канонические очки из scores[2] важнее «зашумленного» detail.
                left = str(fonbet_score.get("points_a"))
                right = str(fonbet_score.get("points_b"))
                serve_flag_pts = fonbet_score.get("serve")
                if serve_flag_pts == 1:
                    left = f"{left}*"
                elif serve_flag_pts == 2:
                    right = f"{right}*"
                point_detail = f"{left}-{right}"
            serve_flag = fonbet_score.get("serve")
            if serve_flag == 1:
                score_data = dict(score_data)
                score_data["server"] = "player_a"
            elif serve_flag == 2:
                score_data = dict(score_data)
                score_data["server"] = "player_b"
            try:
                sets_won_a = int(fonbet_score.get("sets_a"))
                sets_won_b = int(fonbet_score.get("sets_b"))
            except (TypeError, ValueError):
                sets_won_a = sets_won_b = None

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
        if point_detail is None and (pt_a != "0" or pt_b != "0"):
            point_detail = f"{pt_a}-{pt_b}"
        set_lines = [
            {"games_a": row.games_a, "games_b": row.games_b, "live": False, "detail": None}
            for row in sets_list
        ]
        set_lines.append({
            "games_a": int(current_game_a or 0),
            "games_b": int(current_game_b or 0),
            "live": True,
            "detail": point_detail,
        })
        stats["set_lines"] = set_lines
        if sets_won_a is not None:
            stats["sets_a"] = sets_won_a
            stats["sets_b"] = sets_won_b

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
        """Match winner and every quoted set. A missing side is left out, not replaced by 1.01."""
        source_id = str(raw_payload.get("source_event_id") or raw_payload.get("id") or "unknown")
        event_id = raw_payload.get("event_id") or f"fonbet_tennis_{source_id}"
        player_a = self.normalize_player_name(raw_payload.get("participant_a") or raw_payload.get("team1") or "Игрок А")
        player_b = self.normalize_player_name(raw_payload.get("participant_b") or raw_payload.get("team2") or "Игрок Б")

        markets = self._markets_from_payload(source_id, player_a, player_b, raw_payload)
        hash_payload = [
            {
                "market_id": market.market_id,
                "line": getattr(market, "line_value", None),
                "prices": [selection.odds for selection in market.selections],
            }
            for market in markets
        ]
        raw_hash_bytes = json.dumps(hash_payload, sort_keys=True).encode("utf-8")
        content_hash = f"sha256:{hashlib.sha256(raw_hash_bytes).hexdigest()}"
        return OddsSnapshot(
            snapshot_id=f"snap_tennis_{source_id}_{content_hash[7:19]}",
            event_id=event_id,
            markets=markets,
            content_hash=content_hash,
        )

    def _markets_from_payload(self, source_id: str, player_a: str, player_b: str, raw_payload: Dict[str, Any]) -> List[Market]:
        found: List[Market] = []
        seen = set()
        for raw in raw_payload.get("markets") or []:
            market = self._market_from_raw(source_id, player_a, player_b, raw)
            if market is None or market.market_id in seen:
                continue
            seen.add(market.market_id)
            found.append(market)
        if not any(market.market_type == "match_winner" for market in found):
            fallback = self._market_from_raw(source_id, player_a, player_b, {
                "market_type": "match_winner",
                "name": "Победитель матча",
                "selections": [
                    {"outcome": "player_a", "name": player_a, "odds": raw_payload.get("odds_a") or raw_payload.get("p1")},
                    {"outcome": "player_b", "name": player_b, "odds": raw_payload.get("odds_b") or raw_payload.get("p2")},
                ],
            })
            if fallback is not None:
                found.insert(0, fallback)
        return found

    def _market_from_raw(self, source_id: str, player_a: str, player_b: str, raw: Dict[str, Any]) -> Optional[Market]:
        market_type = str(raw.get("market_type") or "match_winner")
        prices: Dict[str, float] = {}
        for selection in raw.get("selections") or []:
            outcome = str(selection.get("outcome") or "")
            try:
                price = float(selection.get("odds"))
            except (TypeError, ValueError):
                continue
            if outcome in ("player_a", "player_b") and price > 1.0:
                prices[outcome] = price
        if "player_a" not in prices or "player_b" not in prices:
            return None
        line_value = raw.get("line_value")
        suffix = "match" if market_type == "match_winner" else f"set{int(line_value or 0)}"
        name = raw.get("name") or ("Победитель матча" if market_type == "match_winner" else f"Победа в сете {line_value}")
        selections = [
            Selection(
                selection_id=f"{source_id}_{suffix}_p1",
                outcome="player_a",
                name=player_a,
                odds=prices["player_a"],
                probability_implied=round(1.0 / prices["player_a"], 4),
            ),
            Selection(
                selection_id=f"{source_id}_{suffix}_p2",
                outcome="player_b",
                name=player_b,
                odds=prices["player_b"],
                probability_implied=round(1.0 / prices["player_b"], 4),
            ),
        ]
        parsed_line = None if line_value in (None, "") else int(line_value)
        return Market(
            market_id=str(raw.get("market_id") or f"{source_id}_{suffix}"),
            market_type=market_type,
            name=str(name),
            status="active",
            selections=selections,
            line_value=parsed_line,
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
        market = str(proposal.get("market") or "")
        base = market.split(":", 1)[0]
        if market not in self.supported_markets() and base not in self.supported_markets():
            return False

        selection = proposal.get("selection") or proposal.get("outcome")
        if base in ("match_winner", "set_winner") and selection not in ("player_a", "player_b"):
            return False

        return True

    def assess_execution(self, proposal: Dict[str, Any]) -> tuple:
        """Apply tennis windows, assigned lines and quote gates. Core stays sport-agnostic."""
        from .policy import (
            load_tennis_policy,
            market_limits,
            model_commits_market,
            set_is_observation_only,
            set_is_stake_allowed,
            windows_allow,
        )
        from .stake import expected_value

        market = str(proposal.get("market") or "")
        sport = str(proposal.get("sport_code") or "tennis")
        if set_is_observation_only(market, sport) or not set_is_stake_allowed(market, sport):
            return False, "SPORT_RULE_VIOLATION", {"reason": "SET_NOT_STAKED"}
        model_name = proposal.get("model_name")
        model_version = proposal.get("model_version")
        if model_name and model_version and not model_commits_market(str(model_name), str(model_version), market):
            return False, "SPORT_RULE_VIOLATION", {"reason": "MODEL_MARKET_MISMATCH"}
        probability = float(proposal.get("model_probability") or 0.0)
        odds = float(proposal.get("bookmaker_odds") or 0.0)
        allowed, window_reason = windows_allow(probability, odds, market)
        if not allowed:
            return False, "SPORT_RULE_VIOLATION", {"reason": window_reason}
        if expected_value(probability, odds) <= 0.0:
            return False, "EDGE_TOO_LOW", {"reason": "NEGATIVE_EV"}
        quality = proposal.get("data_quality_score")
        limits = load_tennis_policy()
        if quality is not None and float(quality) < float(limits.min_data_quality):
            return False, "QUALITY_TOO_LOW", {"quality": quality}
        research_quality = proposal.get("research_quality")
        if research_quality is not None and float(research_quality) < float(limits.min_research_quality):
            return False, "LLM_DATA_INSUFFICIENT", {"research_quality": research_quality}
        verdict = str(proposal.get("llm_verdict") or "").upper()
        if verdict == "NO_BET":
            return False, "LLM_DATA_INSUFFICIENT", {"reason": "LLM_NO_BET"}
        mkt_limits = market_limits(market)
        return True, None, {
            "policy_min_edge": mkt_limits["min_edge"],
            "policy_max_edge": mkt_limits.get("max_edge"),
        }
