"""Parser for FON.BET live tennis data with Russian localization and strict live filtering."""

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("collector.parser")

# Tennis sport identifier in Fonbet (sportId 4 is Tennis)
FONBET_TENNIS_SPORT_IDS = {4, 104, 114}


class FonbetLiveTennisParser:
    """Parses live tennis events with Russian names and excludes non-live / prematch events."""

    @staticmethod
    def is_event_live(event_data: Dict[str, Any]) -> bool:
        """Verify that event is currently LIVE and in progress."""
        # Check explicit live flags
        if event_data.get("place") == "live":
            return True
        if event_data.get("is_live") is True:
            return True
        if event_data.get("liveEvent") is True:
            return True

        # Check status / timer presence
        status = str(event_data.get("status", "")).lower()
        if status in ("live", "in_progress", "active"):
            return True

        # If it has active tennis game score, it is live
        score = event_data.get("score") or event_data.get("current_score")
        if score and any(c.isdigit() for c in str(score)):
            return True

        return False

    @classmethod
    def parse_score_string(cls, score_str: str) -> Dict[str, Any]:
        """Parse tennis score string like '1-1 (6-4, 3-6, 4-3*)' into structured score."""
        result = {
            "raw_score_string": score_str,
            "sets_a": 0,
            "sets_b": 0,
            "current_set": 1,
            "current_game_a": 0,
            "current_game_b": 0,
            "points_a": "0",
            "points_b": "0",
            "server": None,
        }
        if not score_str:
            return result

        # Check server asterisk (e.g. * indicates server)
        if "*)" in score_str or re.search(r"\d+\*-\d+", score_str):
            result["server"] = "player_a"
        elif ")*" in score_str or re.search(r"\d+-\d+\*", score_str):
            result["server"] = "player_b"

        # Match set scores in parentheses: (6-4, 3-6, 4-3)
        match_sets = re.search(r"\((.*?)\)", score_str)
        if match_sets:
            set_parts = [p.strip().replace("*", "") for p in match_sets.group(1).split(",")]
            completed_sets_a = 0
            completed_sets_b = 0
            for i, part in enumerate(set_parts):
                if "-" in part:
                    p_a, p_b = part.split("-", 1)
                    try:
                        ga, gb = int(p_a.strip()), int(p_b.strip())
                        if i == len(set_parts) - 1:
                            # Current set games
                            result["current_game_a"] = ga
                            result["current_game_b"] = gb
                            result["current_set"] = i + 1
                        else:
                            if ga > gb:
                                completed_sets_a += 1
                            elif gb > ga:
                                completed_sets_b += 1
                    except ValueError:
                        pass
            result["sets_a"] = completed_sets_a
            result["sets_b"] = completed_sets_b
        return result

    @classmethod
    def parse_single_live_card(cls, card_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Parse one live tennis match card.
        Strictly returns None if match is prematch or not live.
        Catches individual card errors to prevent collector crash.
        """
        try:
            # 1. Filter out non-live events
            if not cls.is_event_live(card_data):
                logger.debug("Skipping event %s: not live", card_data.get("id"))
                return None

            source_id = str(card_data.get("id") or card_data.get("source_event_id") or "")
            if not source_id:
                return None

            # 2. Extract Russian participant names
            # Fonbet provides team1/team2 in Russian under ru locale
            player_a = card_data.get("team1") or card_data.get("participant_a") or ""
            player_b = card_data.get("team2") or card_data.get("participant_b") or ""

            # Fallback if participant names are combined in "name": "Игрок А - Игрок Б"
            if not player_a and "name" in card_data:
                parts = str(card_data["name"]).split(" - ")
                if len(parts) >= 2:
                    player_a, player_b = parts[0].strip(), parts[1].strip()

            if not player_a or not player_b:
                logger.debug("Skipping event %s: missing participant names", source_id)
                return None

            tournament = card_data.get("competitionName") or card_data.get("tournament") or "Теннис. Live"

            # 3. Parse score
            score_raw = str(card_data.get("score") or card_data.get("comment") or card_data.get("score_str") or "")
            score_state = cls.parse_score_string(score_raw)

            # 4. Extract markets and odds
            markets = []
            selections = []

            # Check for Match Winner odds (P1 / P2)
            odds_a = card_data.get("odds_a") or card_data.get("p1")
            odds_b = card_data.get("odds_b") or card_data.get("p2")

            # Check Fonbet custom factors table if provided
            factors = card_data.get("factors", [])
            for f in factors:
                f_type = f.get("f") or f.get("type")
                f_val = f.get("v") or f.get("odds")
                if f_type in (921, "p1", "1") and f_val:
                    odds_a = float(f_val)
                elif f_type in (923, "p2", "2") and f_val:
                    odds_b = float(f_val)

            if odds_a and odds_b:
                selections.append({
                    "selection_id": f"{source_id}_p1",
                    "outcome": "player_a",
                    "name": player_a,
                    "odds": float(odds_a),
                })
                selections.append({
                    "selection_id": f"{source_id}_p2",
                    "outcome": "player_b",
                    "name": player_b,
                    "odds": float(odds_b),
                })
                markets.append({
                    "market_id": f"{source_id}_match_winner",
                    "market_type": "match_winner",
                    "name": "Победитель матча",
                    "selections": selections,
                })

            return {
                "event_id": f"fonbet_{source_id}",
                "source": "fonbet",
                "source_event_id": source_id,
                "sport_code": "tennis",
                "is_live": True,
                "status": "live",
                "tournament": tournament,
                "participant_a": player_a,
                "participant_b": player_b,
                "score_state": score_state,
                "markets": markets,
            }
        except Exception as e:
            logger.warning("Error parsing individual event card: %s", e)
            return None

    @classmethod
    def parse_events_feed(cls, feed_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Parse raw feed and return list of live tennis events."""
        events_list = []
        raw_events = feed_data.get("events", [])
        if not isinstance(raw_events, list):
            return events_list

        for item in raw_events:
            parsed = cls.parse_single_live_card(item)
            if parsed is not None:
                events_list.append(parsed)

        logger.info("Parsed %d live tennis events from feed.", len(events_list))
        return events_list
