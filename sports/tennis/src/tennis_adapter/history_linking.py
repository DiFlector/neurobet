"""Evidence-only coordinates for linking archived tennis histories."""
from __future__ import annotations

import re
from .point_history import infer_point_winner


def finished_set(a, b):
    high, low = max(a, b), min(a, b)
    return (high >= 6 and high - low >= 2) or (high == 7 and low == 6)


def official_result(status, metadata):
    metadata = metadata if isinstance(metadata, dict) else {}
    result = metadata.get("fonbet_official_result") or {}
    if str(status).casefold() != "finished" or str(result.get("status", "")).upper() != "FINISHED":
        return None, None, "official_result_not_finished"
    if result.get("is_walkover") or result.get("is_retired"):
        return None, None, "walkover_or_retirement"
    winner = {"player_a": 0, "player_b": 1}.get(result.get("winner"))
    if winner is None:
        return None, None, "winner_missing_or_invalid"
    final = str(result.get("final_score") or "")
    total, sets = re.match(r"\s*(\d+)\s*:\s*(\d+)", final), re.search(r"\(([^)]*)\)", final)
    if not total or not sets:
        return None, None, "final_set_score_missing"
    scores = [(int(a), int(b)) for a, b in re.findall(r"(\d+)\s*[-:]\s*(\d+)", sets.group(1))]
    if not scores or any(not finished_set(a, b) for a, b in scores):
        return None, None, "invalid_final_set_score"
    won = (sum(a > b for a, b in scores), sum(b > a for a, b in scores))
    if won != (int(total[1]), int(total[2])):
        return None, None, "match_score_disagrees_with_sets"
    if (winner == 0) != (won[0] > won[1]):
        return None, None, "winner_disagrees_with_score"
    declared = metadata.get("set_winners")
    if isinstance(declared, dict):
        for number, (a, b) in enumerate(scores, 1):
            if declared.get(str(number)) and declared[str(number)] != ("player_a" if a > b else "player_b"):
                return None, None, "set_winner_disagrees_with_score"
    return winner, scores, "confirmed"


def _side(value):
    return {"first player": 0, "second player": 1, "1": 0, "2": 1}.get(str(value).strip().casefold())


def _positive(value):
    try:
        number = int(str(value))
        return number if number > 0 else None
    except (ValueError, TypeError):
        return None


def normalize_api_pbp(raw):
    """Preserve explicit game results and contiguous observed point transitions.

    A game result is a separate event, never an invented terminal point.
    Missing point indices/scores break continuity; later contiguous pairs can
    still supply their own independently confirmed transition.
    """
    output, gaps = [], []
    for game in raw.get("pointbypoint") or []:
        if not isinstance(game, dict):
            gaps.append({"reason": "invalid_game_row"})
            continue
        set_match = re.search(r"\d+", str(game.get("set_number") or ""))
        number = _positive(game.get("number_game"))
        after_match = re.fullmatch(r"\s*(\d+)\s*[-:]\s*(\d+)\s*", str(game.get("score") or ""))
        winner = _side(game.get("serve_winner"))
        if not set_match or number is None or not after_match or winner is None:
            gaps.append({"reason": "game_coordinates_or_winner_unknown"})
            continue
        set_no = int(set_match[0])
        after_game = [int(after_match[1]), int(after_match[2])]
        before_game = after_game.copy()
        before_game[winner] -= 1
        server = _side(game.get("player_served"))
        lost = _side(game.get("serve_lost"))
        if (min(before_game) < 0 or sum(after_game) != number or finished_set(*before_game)
                or (finished_set(*after_game) and winner != int(after_game[1] > after_game[0]))
                or (lost is not None and (lost != server or lost == winner))):
            gaps.append({"set": set_no, "game": number, "reason": "game_result_conflict"})
            continue
        tiebreak = before_game == [6, 6] or "tie" in str(game.get("set_number")).casefold()
        before, previous_index = ([0, 0] if tiebreak else ["0", "0"]), 0
        points = game.get("points")
        if not isinstance(points, list):
            points = []
            gaps.append({"set": set_no, "game": number, "reason": "point_sequence_missing"})
        for point in points:
            point = point if isinstance(point, dict) else {}
            index = _positive(point.get("number_point"))
            pattern = r"\s*(\d+)\s*[-:]\s*(\d+)\s*" if tiebreak else r"\s*(0|15|30|40|AD|A)\s*[-:]\s*(0|15|30|40|AD|A)\s*"
            match = re.fullmatch(pattern, str(point.get("score") or "").upper())
            if match is None:
                gaps.append({"set": set_no, "game": number, "point": index, "reason": "invalid_point_score"})
                before, previous_index = None, index
                continue
            after = [int(value) for value in match.groups()] if tiebreak else ["AD" if value == "A" else value for value in match.groups()]
            continuous = index is not None and previous_index is not None and index == previous_index + 1 and before is not None
            point_winner = None
            if continuous:
                if tiebreak:
                    changed = [i for i in (0, 1) if after[i] == before[i] + 1 and after[1-i] == before[1-i]]
                    point_winner = changed[0] if len(changed) == 1 else None
                else:
                    point_winner = infer_point_winner(before, after)
            output.append({"set": set_no, "game": number, "game_score": after_game,
                           "game_score_before": before_game, "point_index": index,
                           "state_before": before if continuous else None, "state_after": after,
                           "point_score": after, "server": _side(point.get("player_served")) if tiebreak else server,
                           "winner": point_winner, "confirmed": point_winner is not None,
                           "tie_break": tiebreak, "quality": "confirmed" if point_winner is not None else "ambiguous",
                           "retrospective": True})
            if point_winner is None:
                gaps.append({"set": set_no, "game": number, "point": index, "reason": "point_transition_unconfirmed"})
            before, previous_index = after, index
        output.append({"set": set_no, "game": number, "game_score": after_game,
                       "game_score_before": before_game, "winner": winner, "server": server,
                       "state_before": {"game_score": before_game}, "state_after": {"game_result": after_game},
                       "point_index": None, "confirmed": True, "event_kind": "game_result",
                       "quality": "confirmed_game_result", "tie_break": tiebreak, "retrospective": True})
    return output, {"provider": "api_tennis", "coordinate_schema": "api_pbp_coordinates_v2",
                    "complete": False, "completeness": "partial_or_unknown", "gaps": gaps,
                    "confirmed_points": sum(row["confirmed"] and row.get("event_kind") != "game_result" for row in output),
                    "confirmed_games": sum(row.get("event_kind") == "game_result" for row in output),
                    "event_count": len(output), "set_numbers": sorted({row["set"] for row in output})}
