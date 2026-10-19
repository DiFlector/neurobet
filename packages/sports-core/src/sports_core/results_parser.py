"""Parser for official Fonbet results feed (https://fon.bet/results)."""

import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from contracts import CanonicalMatchResult

logger = logging.getLogger("sports_core.results_parser")

# Sport ID 4 in Fonbet corresponds to Tennis, 3 to Basketball, 9 to Volleyball, 2 to Hockey, 1 to Football
FONBET_TENNIS_SPORT_ID = 4
FONBET_HOCKEY_SPORT_ID = 2
FONBET_BASKETBALL_SPORT_ID = 3
FONBET_VOLLEYBALL_SPORT_ID = 9
FONBET_FOOTBALL_SPORT_ID = 1


# Sub-event names to exclude (statistics markets, not match results)
STAT_SUB_EVENTS = {
    "эйсы",
    "двойные ошибки",
    "видеопросмотры",
    "брейк-поинты",
    "выигранные геймы",
    "процент 1-й подачи",
    "время матча",
    "угловые",
    "желтые карты",
    "удары в створ",
    "фолы",
    "офсайды",
    "штрафное время",
    "броски в створ",
}


class FonbetResultsParser:
    """
    Parses official results payload from https://fon.bet/results (results.json).
    Guarantees match results are verified from official sources and never guessed.
    """

    @classmethod
    def parse_participants(cls, name_str: str) -> Optional[Tuple[str, str]]:
        """
        Extract two participant names from match string like 'Синнер Я. – Джокович Н.'.
        Handles en-dash (–), em-dash (—), and standard hyphen (-).
        """
        clean_name = str(name_str).strip()
        if clean_name.lower() in STAT_SUB_EVENTS:
            return None

        # Split on dash with surrounding spaces
        parts = re.split(r"\s+[–—\-]\s+", clean_name)
        if len(parts) == 2:
            p_a, p_b = parts[0].strip(), parts[1].strip()
            if p_a and p_b:
                return p_a, p_b

        # Split on isolated dash
        parts = [p.strip() for p in re.split(r"[–—]", clean_name) if p.strip()]
        if len(parts) == 2:
            return parts[0], parts[1]

        return None

    @staticmethod
    def tennis_sets_from_score(score_str: str) -> Optional[Tuple[int, int]]:
        """Parse leading set score like '2:1 (6-4 ...)' into (sets_a, sets_b)."""
        match = re.search(r"^\s*(\d+)\s*[:\-]\s*(\d+)", str(score_str or "").strip())
        if not match:
            return None
        return int(match.group(1)), int(match.group(2))

    @classmethod
    def tennis_match_is_complete(cls, sets_a: int, sets_b: int) -> bool:
        """
        True when one side has enough sets to win the match.
        Best-of-3 ends at 2 sets; best-of-5 at 3. A single set (1:0 / 0:1) is never enough.
        """
        leader = max(sets_a, sets_b)
        trailer = min(sets_a, sets_b)
        if leader < 2:
            return False
        # Best-of-3: 2-0 or 2-1. Best-of-5: 3-0 / 3-1 / 3-2.
        if leader == 2:
            return trailer <= 1
        return leader >= 3 and trailer < leader

    @classmethod
    def parse_tennis_winner_and_status(
        cls,
        score_str: str,
        comments_str: str = "",
    ) -> Tuple[Optional[str], str, bool, bool]:
        """
        Extract winner ('player_a' or 'player_b') and status from tennis score.
        Returns: (winner, status, is_retired, is_walkover)

        A leading set score of 1:0 / 0:1 without retirement is an in-progress
        match, not FINISHED — Fonbet results feed often publishes that mid-match.
        """
        clean_score = str(score_str or "").strip()
        all_text = f"{clean_score} {comments_str}".lower()

        is_retired = any(k in all_text for k in ("отказ", "травм", "снял", "rt"))
        is_walkover = any(k in all_text for k in ("не сост", "отмен", "дисквал", "walkover", "wo", "w.o."))

        if is_walkover:
            return None, "CANCELLED", False, True

        # Check sets score pattern at start, e.g. '2:1 (6-4 3-6 6-1)' or '0-2 (4-6 2-6)'
        sets = cls.tennis_sets_from_score(clean_score)
        if sets is not None:
            sets_a, sets_b = sets

            if is_retired:
                # If retired with established winner in official result
                if sets_a > sets_b:
                    return "player_a", "RETIRED", True, False
                elif sets_b > sets_a:
                    return "player_b", "RETIRED", True, False
                else:
                    return None, "VOID", True, False

            # Mid-match one-set leads must not close the event.
            if not cls.tennis_match_is_complete(sets_a, sets_b):
                return None, "SETTLEMENT_REVIEW_REQUIRED", False, False

            if sets_a > sets_b:
                return "player_a", "FINISHED", False, False
            if sets_b > sets_a:
                return "player_b", "FINISHED", False, False

        if is_retired:
            return None, "VOID", True, False

        # If score is empty or cannot determine winner
        if not clean_score or clean_score == "-:-":
            return None, "SETTLEMENT_REVIEW_REQUIRED", False, False

        return None, "SETTLEMENT_REVIEW_REQUIRED", False, False

    @classmethod
    def parse_basketball_winner_and_status(
        cls,
        score_str: str,
        comments_str: str = "",
    ) -> Tuple[Optional[str], str, bool, bool]:
        """
        Extract winner ('team_a' or 'team_b') and status from basketball score.
        Returns: (winner, status, is_cancelled, is_walkover)
        """
        clean_score = str(score_str or "").strip()
        all_text = f"{clean_score} {comments_str}".lower()

        is_walkover = any(k in all_text for k in ("не сост", "отмен", "дисквал", "walkover", "wo", "w.o."))
        if is_walkover:
            return None, "CANCELLED", True, True

        match = re.search(r"^\s*(\d+)\s*[:\-]\s*(\d+)", clean_score)
        if match:
            pts_a = int(match.group(1))
            pts_b = int(match.group(2))
            if pts_a > pts_b:
                return "team_a", "FINISHED", False, False
            elif pts_b > pts_a:
                return "team_b", "FINISHED", False, False

        if not clean_score or clean_score == "-:-":
            return None, "SETTLEMENT_REVIEW_REQUIRED", False, False

        return None, "SETTLEMENT_REVIEW_REQUIRED", False, False

    @classmethod
    def parse_volleyball_winner_and_status(
        cls,
        score_str: str,
        comments_str: str = "",
    ) -> Tuple[Optional[str], str, bool, bool]:
        """
        Extract winner ('team_a' or 'team_b') and status from volleyball score.
        Returns: (winner, status, is_cancelled, is_walkover)
        """
        clean_score = str(score_str or "").strip()
        all_text = f"{clean_score} {comments_str}".lower()

        is_walkover = any(k in all_text for k in ("не сост", "отмен", "дисквал", "walkover", "wo", "w.o."))
        if is_walkover:
            return None, "CANCELLED", True, True

        match = re.search(r"^\s*(\d+)\s*[:\-]\s*(\d+)", clean_score)
        if match:
            sets_a = int(match.group(1))
            sets_b = int(match.group(2))
            if sets_a > sets_b:
                return "team_a", "FINISHED", False, False
            elif sets_b > sets_a:
                return "team_b", "FINISHED", False, False

        if not clean_score or clean_score == "-:-":
            return None, "SETTLEMENT_REVIEW_REQUIRED", False, False

        return None, "SETTLEMENT_REVIEW_REQUIRED", False, False

    @classmethod
    def parse_hockey_winner_and_status(
        cls,
        score_str: str,
        comments_str: str = "",
    ) -> Tuple[Optional[str], str, bool, bool]:
        """
        Extract winner ('team_a' or 'team_b') and status from hockey score.
        Handles regular time (e.g. '3:2 (1:0, 1:1, 1:1)'), overtime ('4:3 ОТ'),
        and shootouts ('3:2 Б').
        Returns: (winner, status, is_cancelled, is_walkover)
        """
        clean_score = str(score_str or "").strip()
        all_text = f"{clean_score} {comments_str}".lower()

        is_walkover = any(k in all_text for k in ("не сост", "отмен", "дисквал", "walkover", "wo", "w.o."))
        if is_walkover:
            return None, "CANCELLED", True, True

        match = re.search(r"^\s*(\d+)\s*[:\-]\s*(\d+)", clean_score)
        if match:
            goals_a = int(match.group(1))
            goals_b = int(match.group(2))
            if goals_a > goals_b:
                return "team_a", "FINISHED", False, False
            elif goals_b > goals_a:
                return "team_b", "FINISHED", False, False

        if not clean_score or clean_score == "-:-":
            return None, "SETTLEMENT_REVIEW_REQUIRED", False, False

        return None, "SETTLEMENT_REVIEW_REQUIRED", False, False

    @classmethod
    def parse_football_winner_and_status(
        cls,
        score_str: str,
        comments_str: str = "",
    ) -> Tuple[Optional[str], str, bool, bool]:
        """
        Extract winner ('team_a', 'team_b', or 'draw') and status from football score.
        Handles regular time (e.g. '2:1 (1:0, 1:1)' or '1:1 (0:0, 1:1)'),
        extra time (e.g. '2:2 (1:1, 1:1, доп.вр. 0:0, пен. 4:3)'), and penalties.
        Returns: (winner, status, is_cancelled, is_walkover)
        """
        clean_score = str(score_str or "").strip()
        all_text = f"{clean_score} {comments_str}".lower()

        is_walkover = any(k in all_text for k in ("не сост", "отмен", "дисквал", "walkover", "wo", "w.o."))
        if is_walkover:
            return None, "CANCELLED", True, True

        match = re.search(r"^\s*(\d+)\s*[:\-]\s*(\d+)", clean_score)
        if match:
            goals_a = int(match.group(1))
            goals_b = int(match.group(2))
            if goals_a > goals_b:
                return "team_a", "FINISHED", False, False
            elif goals_b > goals_a:
                return "team_b", "FINISHED", False, False
            else:
                return "draw", "FINISHED", False, False

        if not clean_score or clean_score == "-:-":
            return None, "SETTLEMENT_REVIEW_REQUIRED", False, False

        return None, "SETTLEMENT_REVIEW_REQUIRED", False, False

    FONBET_SPORT_TARGETS: Tuple[Tuple[int, str], ...] = (
        (FONBET_TENNIS_SPORT_ID, "tennis"),
        (FONBET_FOOTBALL_SPORT_ID, "football"),
        (FONBET_HOCKEY_SPORT_ID, "hockey"),
        (FONBET_BASKETBALL_SPORT_ID, "basketball"),
        (FONBET_VOLLEYBALL_SPORT_ID, "volleyball"),
    )

    @classmethod
    def parse_feed(
        cls,
        data: Dict[str, Any],
        target_sport_id: int = FONBET_TENNIS_SPORT_ID,
        sport_code: str = "tennis",
    ) -> List[CanonicalMatchResult]:
        """
        Parse complete Fonbet results.json payload and return list of CanonicalMatchResult.
        """
        sections = data.get("sections", [])
        events_list = data.get("events", [])
        event_map = {str(e.get("id")): e for e in events_list if "id" in e}

        results: List[CanonicalMatchResult] = []

        # Find sections matching target sport
        for section in sections:
            sec_sport_id = section.get("fonbetSportId")
            if sec_sport_id != target_sport_id:
                continue

            tournament_name = section.get("name", f"{sport_code.capitalize()} Tournament")
            event_ids = section.get("events", [])

            for eid in event_ids:
                event_data = event_map.get(str(eid))
                if not event_data:
                    continue

                name = event_data.get("name", "")
                participants = cls.parse_participants(name)
                if not participants:
                    continue

                p_a, p_b = participants
                score = event_data.get("score", "")
                comm1 = event_data.get("comment1", "") or ""
                comm2 = event_data.get("comment2", "") or ""
                comm3 = event_data.get("comment3", "") or ""
                comments = f"{comm1} {comm2} {comm3}".strip()

                if sport_code == "basketball":
                    winner, status, is_retired, is_walkover = cls.parse_basketball_winner_and_status(
                        score_str=score,
                        comments_str=comments,
                    )
                elif sport_code == "volleyball":
                    winner, status, is_retired, is_walkover = cls.parse_volleyball_winner_and_status(
                        score_str=score,
                        comments_str=comments,
                    )
                elif sport_code == "hockey":
                    winner, status, is_retired, is_walkover = cls.parse_hockey_winner_and_status(
                        score_str=score,
                        comments_str=comments,
                    )
                elif sport_code == "football":
                    winner, status, is_retired, is_walkover = cls.parse_football_winner_and_status(
                        score_str=score,
                        comments_str=comments,
                    )
                else:
                    winner, status, is_retired, is_walkover = cls.parse_tennis_winner_and_status(
                        score_str=score,
                        comments_str=comments,
                    )

                canonical = CanonicalMatchResult(
                    source_event_id=str(event_data.get("id")),
                    sport_code=sport_code,
                    tournament=tournament_name,
                    participant_a=p_a,
                    participant_b=p_b,
                    final_score=score,
                    winner=winner,
                    status=status,
                    is_retired=is_retired,
                    is_walkover=is_walkover,
                    comments=comments,
                )
                results.append(canonical)

        logger.info(
            "Parsed %d canonical %s results from Fonbet results feed.",
            len(results),
            sport_code,
        )
        return results

    @classmethod
    def parse_all_sports(cls, data: Dict[str, Any]) -> List[CanonicalMatchResult]:
        """Parse results for every supported Fonbet sport id in one pass."""
        results: List[CanonicalMatchResult] = []
        for sport_id, sport_code in cls.FONBET_SPORT_TARGETS:
            results.extend(
                cls.parse_feed(data, target_sport_id=sport_id, sport_code=sport_code)
            )
        return results

    FONBET_GET_BY_DATE_URLS: Tuple[str, ...] = (
        "https://clientsapi-vk-w.bk6bba-resources.ru/results/v2/getByDate?lang=ru&packetVersion=0&lineDate={date_str}&scopeMarket=1600",
        "https://clientsapi-lb61-w.bk6bba-resources.com/results/v2/getByDate?lang=ru&packetVersion=0&lineDate={date_str}&scopeMarket=1600",
        "https://clientsapi-lb51.bk6bba-resources.com/results/v2/getByDate?lang=ru&packetVersion=0&lineDate={date_str}&scopeMarket=1600",
    )

    @classmethod
    def parse_get_by_date(cls, data: Dict[str, Any]) -> List[CanonicalMatchResult]:
        """
        Parse official results payload from Fonbet results/v2/getByDate?lineDate=YYYY-MM-DD endpoint.
        Matches exact source_event_id, scores, and winners from official bookmaker records.
        """
        events = data.get("events", [])
        miscs = {str(m.get("id")): m for m in data.get("eventMiscs", []) if "id" in m}
        comps = {str(c.get("id")): c for c in data.get("competitions", []) if "id" in c}
        sports_map = {
            FONBET_FOOTBALL_SPORT_ID: "football",
            FONBET_HOCKEY_SPORT_ID: "hockey",
            FONBET_BASKETBALL_SPORT_ID: "basketball",
            FONBET_TENNIS_SPORT_ID: "tennis",
            FONBET_VOLLEYBALL_SPORT_ID: "volleyball",
        }

        results: List[CanonicalMatchResult] = []
        for ev in events:
            # Skip sub-events (e.g. statistics, sets, quarters)
            if ev.get("parentId"):
                continue

            comp = comps.get(str(ev.get("competitionId")), {})
            try:
                sport_id = int(comp.get("sportId", 0)) if comp.get("sportId") else 0
            except (ValueError, TypeError):
                sport_id = 0

            sport_code = sports_map.get(sport_id)
            if not sport_code:
                continue

            eid = str(ev.get("id"))
            p_a = str(ev.get("team1") or "").strip()
            p_b = str(ev.get("team2") or "").strip()

            if not p_a or not p_b:
                parsed = cls.parse_participants(ev.get("name", ""))
                if parsed:
                    p_a, p_b = parsed
                else:
                    continue

            misc = miscs.get(eid, {})
            s1 = misc.get("score1")
            s2 = misc.get("score2")
            win_team = misc.get("winningTeam")
            sub_scores = misc.get("subScores", [])
            status_code = ev.get("status")

            is_retired = False
            is_walkover = False
            comments = str(misc.get("comment") or ev.get("comment") or "").strip()

            if status_code == 4:
                status = "CANCELLED"
                is_walkover = True
                winner = None
            elif status_code == 2:
                status = "FINISHED"
                if win_team == 1:
                    winner = "player_a" if sport_code == "tennis" else "team_a"
                elif win_team == 2:
                    winner = "player_b" if sport_code == "tennis" else "team_b"
                elif s1 is not None and s2 is not None:
                    if s1 > s2:
                        winner = "player_a" if sport_code == "tennis" else "team_a"
                    elif s2 > s1:
                        winner = "player_b" if sport_code == "tennis" else "team_b"
                    elif sport_code == "football":
                        winner = "draw"
                    else:
                        winner = None
                else:
                    winner = None

                # In tennis: if match ended with max sets < 2, it indicates retirement
                if sport_code == "tennis" and s1 is not None and s2 is not None:
                    if max(s1, s2) < 2 and win_team in (1, 2):
                        is_retired = True
                        status = "RETIRED"
            else:
                status = "SETTLEMENT_REVIEW_REQUIRED"
                winner = None

            if comments:
                comm_lower = comments.lower()
                if any(k in comm_lower for k in ("отказ", "травм", "снял", "rt")):
                    is_retired = True
                    if status == "FINISHED":
                        status = "RETIRED"
                if any(k in comm_lower for k in ("не сост", "отмен", "дисквал", "walkover", "wo", "w.o.")):
                    is_walkover = True
                    status = "CANCELLED"
                    winner = None

            sub_pairs = [
                f"{s.get('score1', 0)}-{s.get('score2', 0)}"
                for s in sub_scores
                if s.get("score1") is not None and s.get("score2") is not None
            ]
            sub_str = " ".join(sub_pairs)
            if sub_str:
                final_score = f"{s1}:{s2} ({sub_str})"
            elif s1 is not None and s2 is not None:
                final_score = f"{s1}:{s2}"
            else:
                final_score = ""

            results.append(
                CanonicalMatchResult(
                    source_event_id=eid,
                    sport_code=sport_code,
                    tournament=comp.get("name", f"{sport_code.capitalize()} Tournament"),
                    participant_a=p_a,
                    participant_b=p_b,
                    final_score=final_score,
                    winner=winner,
                    status=status,
                    is_retired=is_retired,
                    is_walkover=is_walkover,
                    comments=comments,
                )
            )

        logger.info("Parsed %d canonical match results from getByDate payload.", len(results))
        return results

    @classmethod
    def fetch_results_by_date(cls, date_str: str, timeout: float = 10.0) -> Optional[Dict[str, Any]]:
        """
        Fetch results for a specific date (YYYY-MM-DD) from Fonbet official mirrors.
        Returns parsed JSON dict or None if all mirrors failed.
        """
        import gzip
        import json
        import urllib.request

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Encoding": "gzip",
            "Accept": "application/json",
        }

        for tmpl in cls.FONBET_GET_BY_DATE_URLS:
            url = tmpl.format(date_str=date_str)
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read()
                    if raw[:2] == b"\x1f\x8b":
                        raw = gzip.decompress(raw)
                    payload = json.loads(raw.decode("utf-8", errors="replace"))
                    if "events" in payload:
                        logger.info(
                            "Fetched %d events from Fonbet results for date %s via %s",
                            len(payload.get("events", [])),
                            date_str,
                            url.split("/")[2],
                        )
                        return payload
            except Exception as ex:
                logger.debug("Failed fetching Fonbet results from %s: %s", url, ex)

        logger.warning("Could not fetch Fonbet results for date %s from any mirror.", date_str)
        return None
