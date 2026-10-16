"""Parser for FON.BET live tennis data with Russian localization and strict live filtering."""

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("collector.parser")

# Tennis sport identifier in Fonbet (sportId 4 is Tennis)
FONBET_TENNIS_SPORT_IDS = {4, 104, 114}
_SET_NUMBER = re.compile(
    r"(?:(\d+)\s*(?:[-–]?\s*[ййя]|st|nd|rd|th)?\s*сет)|(?:(?:set|сет)\s*(\d+))",
    re.IGNORECASE,
)


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

            # 4. Extract markets and odds. A side without a price is omitted, never replaced.
            markets = []
            match_prices = cls._two_sided_prices(card_data.get("factors"))
            odds_a = card_data.get("odds_a") or card_data.get("p1")
            odds_b = card_data.get("odds_b") or card_data.get("p2")
            if match_prices is not None:
                odds_a, odds_b = match_prices
            match_market = cls._winner_market(
                source_id, "match_winner", "Победитель матча", player_a, player_b, odds_a, odds_b, None
            )
            if match_market is not None:
                markets.append(match_market)
            for set_market in card_data.get("set_markets") or []:
                number = set_market.get("set_number")
                prices = cls._two_sided_prices(set_market.get("factors"))
                if prices is None or number is None:
                    continue
                built = cls._winner_market(
                    source_id,
                    "set_winner",
                    f"Победа в сете {int(number)}",
                    player_a,
                    player_b,
                    prices[0],
                    prices[1],
                    int(number),
                )
                if built is not None:
                    markets.append(built)

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
                "fonbet_clock": card_data.get("fonbet_clock"),
                "fonbet_score": card_data.get("fonbet_score"),
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

    @classmethod
    def _root_sport_id(cls, sports: Dict[Any, Dict[str, Any]], sport_id: Any, guard: int = 0) -> Any:
        sport = sports.get(sport_id)
        if not sport or guard > 12:
            return sport_id
        parent = sport.get("parentId")
        if not parent:
            return sport_id
        return cls._root_sport_id(sports, parent, guard + 1)

    @classmethod
    def _live_score(cls, info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Structured Fonbet score: set games plus the current game in parentheses."""
        scores = info.get("scores") or []
        if not isinstance(scores, list) or len(scores) < 2:
            return None
        try:
            sets_row = scores[0][0]
            set_games = list(scores[1] or [])
            points = scores[2][0] if len(scores) > 2 and scores[2] else {}
        except (IndexError, TypeError):
            return None
        if not isinstance(sets_row, dict) or not set_games:
            return None

        # Очки текущего гейма: scores[2] каноничнее, чем comment из subscores
        # (у Fonbet comment иногда дублирует «0-0 0-0 …»).
        detail = None
        if isinstance(points, dict) and points.get("c1") is not None:
            left, right = str(points.get("c1")), str(points.get("c2"))
            if points.get("serve") == 1:
                left = f"{left}*"
            elif points.get("serve") == 2:
                right = f"{right}*"
            detail = f"{left}-{right}"
        if not detail:
            for sub in info.get("subscores") or []:
                if isinstance(sub, dict) and sub.get("comment"):
                    detail = str(sub["comment"]).strip()
            if detail and detail.startswith("(") and detail.endswith(")"):
                detail = detail[1:-1]
            if detail:
                # Оставляем только последнюю пару очков (15-40, 00-00*, AD-40 …).
                matches = re.findall(
                    r"(?:AD|\d+)\*?-(?:AD|\d+)\*?",
                    detail,
                    flags=re.IGNORECASE,
                )
                detail = matches[-1] if matches else detail.split()[-1]

        lines = []
        last = len(set_games) - 1
        for index, part in enumerate(set_games):
            if not isinstance(part, dict):
                continue
            lines.append({
                "games_a": part.get("c1"),
                "games_b": part.get("c2"),
                "live": index == last,
                "detail": detail if index == last else None,
            })
        if not lines:
            return None
        return {
            "sets_a": sets_row.get("c1"),
            "sets_b": sets_row.get("c2"),
            "points_a": points.get("c1") if isinstance(points, dict) else None,
            "points_b": points.get("c2") if isinstance(points, dict) else None,
            "serve": points.get("serve") if isinstance(points, dict) else None,
            "lines": lines,
        }

    @classmethod
    def _score_from_live_info(cls, info: Dict[str, Any]) -> str:
        """Turn Fonbet liveEventInfos into a score string the card parser understands."""
        if not info:
            return ""
        scores = info.get("scores") or []
        try:
            sets = scores[0][0]
            set_games = scores[1]
            games = ", ".join(f"{part.get('c1')}-{part.get('c2')}" for part in set_games)
            points = scores[2][0] if len(scores) > 2 and scores[2] else {}
            star = "*" if points.get("serve") == 1 else ""
            return f"{sets.get('c1')}-{sets.get('c2')} ({games}{star})"
        except (IndexError, TypeError, AttributeError):
            return str(info.get("scoreComment") or "")

    @classmethod
    def from_line_feed(cls, feed: Dict[str, Any]) -> Dict[str, Any]:
        """
        Reduce the official line list payload to live tennis cards.

        Segment sport ids sit under tennis (sport id 4). Match-winner prices
        are custom factors 921 and 923.
        """
        sports = {
            item.get("id"): item
            for item in feed.get("sports") or []
            if isinstance(item, dict)
        }
        tennis_ids = {
            sport_id
            for sport_id in sports
            if cls._root_sport_id(sports, sport_id) in FONBET_TENNIS_SPORT_IDS
        }
        factors_by_event: Dict[Any, List[Any]] = {}
        for block in feed.get("customFactors") or []:
            if isinstance(block, dict) and block.get("e") is not None:
                factors_by_event[block["e"]] = block.get("factors") or []
        info_by_event = {
            item.get("eventId"): item
            for item in feed.get("liveEventInfos") or []
            if isinstance(item, dict)
        }
        misc_by_event = {
            item.get("id"): item
            for item in feed.get("eventMiscs") or []
            if isinstance(item, dict)
        }

        parents: List[Dict[str, Any]] = []
        children: List[Dict[str, Any]] = []
        for event in feed.get("events") or []:
            if not isinstance(event, dict):
                continue
            if event.get("place") != "live":
                continue
            if event.get("sportId") not in tennis_ids:
                continue
            if event.get("parentId"):
                children.append(event)
                continue
            if not event.get("team1") or not event.get("team2"):
                continue
            parents.append(event)

        parent_ids = {event.get("id") for event in parents}
        sets_by_parent: Dict[Any, List[Dict[str, Any]]] = {}
        for child in children:
            if child.get("parentId") not in parent_ids:
                continue
            number = cls._set_number(child)
            if number is None:
                continue
            sets_by_parent.setdefault(child.get("parentId"), []).append({
                "set_number": number,
                "factors": factors_by_event.get(child.get("id"), []),
            })

        cards: List[Dict[str, Any]] = []
        for event in parents:
            sport = sports.get(event.get("sportId")) or {}
            info = info_by_event.get(event.get("id")) or {}
            misc = misc_by_event.get(event.get("id")) or {}
            cards.append({
                "id": event.get("id"),
                "place": "live",
                "team1": event.get("team1"),
                "team2": event.get("team2"),
                "competitionName": sport.get("name") or "Теннис. Live",
                "score": cls._score_from_live_info(info),
                "fonbet_score": cls._live_score(info),
                "factors": factors_by_event.get(event.get("id"), []),
                "set_markets": sets_by_parent.get(event.get("id"), []),
                "fonbet_clock": cls._clock_from_line(event, info, misc),
            })
        return {"events": cards}

    @staticmethod
    def _set_number(event: Dict[str, Any]) -> Optional[int]:
        text = " ".join(str(event.get(key) or "") for key in ("name", "nameRu", "kindName"))
        match = _SET_NUMBER.search(text)
        if match is None:
            return None
        raw = match.group(1) or match.group(2)
        number = int(raw)
        if 1 <= number <= 5:
            return number
        return None

    @staticmethod
    def _two_sided_prices(factors: Any) -> Optional[tuple]:
        odds_a = None
        odds_b = None
        for factor in factors or []:
            if not isinstance(factor, dict):
                continue
            kind = factor.get("f") if factor.get("f") is not None else factor.get("type")
            raw = factor.get("v") if factor.get("v") is not None else factor.get("odds")
            if raw in (None, ""):
                continue
            try:
                price = float(raw)
            except (TypeError, ValueError):
                continue
            if price <= 1.0:
                continue
            if kind in (921, "p1", "1"):
                odds_a = price
            elif kind in (923, "p2", "2"):
                odds_b = price
        if odds_a is None or odds_b is None:
            return None
        return odds_a, odds_b

    @staticmethod
    def _winner_market(source_id, market_type, name, player_a, player_b, odds_a, odds_b, set_number):
        try:
            price_a = float(odds_a)
            price_b = float(odds_b)
        except (TypeError, ValueError):
            return None
        if price_a <= 1.0 or price_b <= 1.0:
            return None
        suffix = "match" if set_number is None else f"set{int(set_number)}"
        return {
            "market_id": f"{source_id}_{suffix}",
            "market_type": market_type,
            "name": name,
            "line_value": None if set_number is None else int(set_number),
            "selections": [
                {"selection_id": f"{source_id}_{suffix}_p1", "outcome": "player_a", "name": player_a, "odds": price_a},
                {"selection_id": f"{source_id}_{suffix}_p2", "outcome": "player_b", "name": player_b, "odds": price_b},
            ],
        }

    @staticmethod
    def _clock_from_line(event: Dict[str, Any], info: Dict[str, Any], misc: Dict[str, Any]) -> Dict[str, Any]:
        """Clock fields exactly as Fonbet publishes them on the line list."""
        tail = str(info.get("scoreCommentTail") or "").strip()
        comment = str(info.get("scoreComment") or "")
        pause_label = None
        for marker in ("Просмотр", "Перерыв", "Пауза", "Тайм-аут", "Таймаут"):
            if marker.lower() in tail.lower() or marker.lower() in comment.lower():
                pause_label = marker
                break
        has_timer = "timerSeconds" in misc or "timerDirection" in misc
        return {
            "has_timer": has_timer,
            "timer_seconds": misc.get("timerSeconds") if has_timer else None,
            "timer_direction": misc.get("timerDirection") if has_timer else None,
            "timer_updated_at_ms": misc.get("timerUpdateTimestampMsec") if has_timer else None,
            "start_time": event.get("startTime"),
            "pause_label": pause_label,
        }
