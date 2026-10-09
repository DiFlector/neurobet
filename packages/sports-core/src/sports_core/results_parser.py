"""Parser for official Fonbet results feed (https://fon.bet/results)."""

import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from contracts import CanonicalMatchResult

logger = logging.getLogger("sports_core.results_parser")

# Sport ID 4 in Fonbet corresponds to Tennis
FONBET_TENNIS_SPORT_ID = 4

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

    @classmethod
    def parse_tennis_winner_and_status(
        cls,
        score_str: str,
        comments_str: str = "",
    ) -> Tuple[Optional[str], str, bool, bool]:
        """
        Extract winner ('player_a' or 'player_b') and status from tennis score.
        Returns: (winner, status, is_retired, is_walkover)
        """
        clean_score = str(score_str or "").strip()
        all_text = f"{clean_score} {comments_str}".lower()

        is_retired = any(k in all_text for k in ("отказ", "травм", "снял", "rt"))
        is_walkover = any(k in all_text for k in ("не сост", "отмен", "дисквал", "walkover", "wo", "w.o."))

        if is_walkover:
            return None, "CANCELLED", False, True

        # Check sets score pattern at start, e.g. '2:1 (6-4 3-6 6-1)' or '0-2 (4-6 2-6)'
        match = re.search(r"^\s*(\d+)\s*[:\-]\s*(\d+)", clean_score)
        if match:
            sets_a = int(match.group(1))
            sets_b = int(match.group(2))

            if is_retired:
                # If retired with established winner in official result
                if sets_a > sets_b:
                    return "player_a", "RETIRED", True, False
                elif sets_b > sets_a:
                    return "player_b", "RETIRED", True, False
                else:
                    return None, "VOID", True, False

            if sets_a > sets_b:
                return "player_a", "FINISHED", False, False
            elif sets_b > sets_a:
                return "player_b", "FINISHED", False, False

        if is_retired:
            return None, "VOID", True, False

        # If score is empty or cannot determine winner
        if not clean_score or clean_score == "-:-":
            return None, "SETTLEMENT_REVIEW_REQUIRED", False, False

        return None, "SETTLEMENT_REVIEW_REQUIRED", False, False

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
