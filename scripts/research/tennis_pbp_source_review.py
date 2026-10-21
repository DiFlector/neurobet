"""Compare saved Flashscore PBP HTML with the tennis parser, without network access."""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone

from db.connection import SessionLocal
from db.models import Event, TennisPointDocument, TennisPointMatchLink
from app.storage import ResearchStorage
from tennis_adapter.point_history import (
    PARSER_VERSION, _TreeParser, _has, _point_pair, _server_side, _text, _walk, _walk_with_parent,
    parse_point_history_html,
)


def _raw_game_rows(html: str) -> list[dict]:
    """Extract score, server marker and point states directly from archived DOM."""
    parser = _TreeParser()
    parser.feed(html)
    rows, tie_break_rows = [], []
    for node, parent in _walk_with_parent(parser.root):
        if not _has(node, "sectionHeader") or parent is None:
            continue
        header = _text(node)
        set_match = re.search(r"(?:\bset\s*(\d+)|(\d+)\s*[-–]?\s*(?:й\s*)?(?:set|сет))", header, re.I)
        set_no = int(set_match.group(1) or set_match.group(2)) if set_match else None
        is_tie_break = bool(re.search(r"tie[\s-]*break|тай[\s-]*брейк|тайбрейк", header, re.I))
        siblings = parent.children
        start = siblings.index(node) + 1
        for index in range(start, len(siblings)):
            row = siblings[index]
            if _has(row, "sectionHeader"):
                break
            if not _has(row, "matchHistoryRow"):
                continue
            descendants = list(_walk(row))
            scorebox = next((item for item in descendants if _has(item, "matchHistoryRow__scoreBox")), None)
            spans = [item for item in _walk(scorebox) if item.tag == "span"] if scorebox else []
            if is_tie_break:
                point_spans = [item for item in spans if item.attrs.get("data-testid") == "wcl-pointByPointScore"]
                if len(point_spans) != 2:
                    continue
                try:
                    score = [int(re.sub(r"\D", "", _text(item))) for item in point_spans]
                except (TypeError, ValueError):
                    continue
                marked = [side for side, item in enumerate(point_spans) if item.attrs.get("data-winning") == "true"]
                tie_break_rows.append({"set": set_no, "score": score,
                                       "winner_marker": marked[0] if len(marked) == 1 else None,
                                       "server": _server_side(descendants)})
                continue
            if len(spans) != 2 or any(item.tag == "sup" for item in _walk(scorebox)):
                continue
            score = []
            for span in spans:
                found = re.search(r"\d+", _text(span))
                if not found:
                    break
                score.append(int(found.group()))
            if len(score) != 2:
                continue
            point_row = siblings[index + 1] if index + 1 < len(siblings) else None
            point_states = []
            if point_row and _has(point_row, "matchHistoryRow__fifteens"):
                for item in _walk(point_row):
                    if _has(item, "matchHistoryRow__fifteen"):
                        state = _point_pair(item.text.strip() or _text(item))
                        if state:
                            point_states.append(list(state))
            rows.append({"set": set_no, "game_score": score,
                         "server": _server_side(descendants), "point_states": point_states,
                         "game_result_present": bool(point_row and any(
                             _has(item, "matchHistoryRow__gameResult") for item in _walk(point_row)))})
    return rows, tie_break_rows


def _parsed_point_states_by_game(events: list[dict]) -> dict[tuple, list[dict]]:
    """Index every parsed point score, including states withheld from labels.

    Source parity checks parser extraction, so ambiguous transitions must still
    be compared with the DOM. Their `confirmed` flag remains false and the
    training audit continues to exclude them.
    """
    result: dict[tuple, list[dict]] = {}
    for item in events:
        score = item.get("point_score")
        game_score = item.get("game_score")
        if item.get("tie_break") or not isinstance(score, (list, tuple)) or len(score) != 2:
            continue
        if not isinstance(game_score, (list, tuple)) or len(game_score) != 2:
            continue
        result.setdefault((item.get("set"), tuple(game_score)), []).append(item)
    return result


def build_report(limit: int = 20, include_details: bool = False) -> dict:
    storage = ResearchStorage()
    matches, seen = [], set()
    with SessionLocal() as session:
        links = session.query(TennisPointMatchLink).filter(
            TennisPointMatchLink.status.in_(("matched", "manual")),
            TennisPointMatchLink.fonbet_event_id.is_not(None),
        ).order_by(TennisPointMatchLink.available_at.desc(), TennisPointMatchLink.id.desc()).all()
        for link in links:
            if link.source_match_id in seen:
                continue
            seen.add(link.source_match_id)
            document = session.query(TennisPointDocument).filter(
                TennisPointDocument.source_match_id == link.source_match_id,
                TennisPointDocument.archived_path.is_not(None),
            ).order_by(TennisPointDocument.retrieved_at.desc(), TennisPointDocument.id.desc()).first()
            if not document:
                continue
            archived = storage.read_document(document.archived_path)
            html = archived.get("html") if isinstance(archived, dict) else None
            if not isinstance(html, str) or not html:
                continue
            event = session.get(Event, link.fonbet_event_id)
            parsed = parse_point_history_html(html)
            raw_rows, raw_tie_break_rows = _raw_game_rows(html)
            parsed_by_game = _parsed_point_states_by_game(parsed.get("events", []))
            checked_games, point_state_mismatches, server_mismatches = 0, 0, 0
            point_mismatch_games, server_mismatch_games = [], []
            compared_source_points = 0
            tie_break_mismatches, tie_break_server_mismatches = 0, 0
            raw_tb_previous, parsed_tb_by_state = {}, {}
            for item in parsed.get("events", []):
                if item.get("tie_break") and item.get("quality") == "confirmed" and isinstance(item.get("state_after"), list):
                    parsed_tb_by_state[(item.get("set"), tuple(item["state_after"]))] = item
            tie_break_comparisons = []
            for row in raw_tie_break_rows:
                before = raw_tb_previous.get(row["set"], [0, 0])
                after = row["score"]
                changed = [side for side in (0, 1)
                           if after[side] == before[side] + 1 and after[1-side] == before[1-side]]
                source_winner = changed[0] if len(changed) == 1 and row["winner_marker"] == changed[0] else None
                parsed_item = parsed_tb_by_state.get((row["set"], tuple(after)))
                if parsed_item:
                    tie_break_mismatches += int(parsed_item.get("winner") != source_winner)
                    tie_break_server_mismatches += int(parsed_item.get("server") != row["server"])
                tie_break_comparisons.append({"set": row["set"], "score": after,
                                              "source_winner": source_winner,
                                              "parsed_winner": parsed_item.get("winner") if parsed_item else None,
                                              "source_server": row["server"],
                                              "parsed_server": parsed_item.get("server") if parsed_item else None})
                raw_tb_previous[row["set"]] = after
            for row in raw_rows:
                parsed_points = parsed_by_game.get((row["set"], tuple(row["game_score"])))
                if not row["point_states"] or parsed_points is None:
                    continue
                checked_games += 1
                source_points = row["point_states"]
                parsed_points = sorted(parsed_points, key=lambda item: item.get("point_index", -1))
                parsed_states = [item.get("point_score") for item in parsed_points]
                compared_source_points += len(source_points)
                if source_points != parsed_states:
                    point_state_mismatches += 1
                    point_mismatch_games.append({"set": row["set"], "game_score": row["game_score"],
                                                 "source": source_points, "parsed": parsed_states})
                mismatch_count = sum(item.get("server") != row["server"] for item in parsed_points)
                server_mismatches += mismatch_count
                if mismatch_count:
                    server_mismatch_games.append({"set": row["set"], "game_score": row["game_score"],
                                                  "source": row["server"],
                                                  "parsed": sorted(set(item.get("server") for item in parsed_points),
                                                                   key=lambda value: -1 if value is None else value),
                                                  "points": len(parsed_points)})
            title = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
            match_report = {
                "source_match_id": link.source_match_id,
                "fonbet_event_id": str(link.fonbet_event_id),
                "fonbet_participants": [event.participant_a_name, event.participant_b_name] if event else None,
                "official_final_score": ((event.metadata_json or {}).get("fonbet_official_result") or {}).get("final_score") if event else None,
                "external_title": title.group(1).strip() if title else None,
                "external_home_is_fonbet_a": (link.evidence_json or {}).get("external_home_is_fonbet_a"),
                "document_id": str(document.id), "content_hash": document.content_hash,
                "archived_path": document.archived_path, "stored_parser_version": document.parser_version,
                "retrieved_at": document.retrieved_at.isoformat(),
                "html_length": len(html), "raw_game_rows": len(raw_rows),
                "raw_tiebreak_rows": len(raw_tie_break_rows),
                "raw_point_rows": sum(bool(row["point_states"]) for row in raw_rows),
                "games_compared": checked_games, "source_point_states_compared": compared_source_points,
                "point_state_mismatches": point_state_mismatches,
                "point_mismatch_games": point_mismatch_games,
                "server_mismatches": server_mismatches,
                "server_mismatch_games": server_mismatch_games,
                "deuce_or_advantage_events": sum(
                    any(str(value).upper() in {"A", "AD", "ADV"}
                        for state in (item.get("state_before"), item.get("state_after"))
                        if isinstance(state, (list, tuple)) for value in state)
                    for item in parsed.get("events", [])),
                "tiebreak_point_events": sum(bool(item.get("tie_break")) and item.get("quality") == "confirmed"
                                               for item in parsed.get("events", [])),
                "terminal_game_events": sum(item.get("quality") == "terminal_from_game_result"
                                              for item in parsed.get("events", [])),
                "tiebreak_winner_mismatches": tie_break_mismatches,
                "tiebreak_server_mismatches": tie_break_server_mismatches,
                "tiebreak_sample": tie_break_comparisons[:4] + tie_break_comparisons[-2:],
                "source_server_sides": {"home": sum(row["server"] == 0 for row in raw_rows),
                                        "away": sum(row["server"] == 1 for row in raw_rows),
                                        "unknown": sum(row["server"] is None for row in raw_rows)},
                "parser_quality": parsed.get("quality"),
            }
            if include_details:
                match_report["source_url"] = document.source_url
                match_report["source_game_rows"] = raw_rows
                match_report["source_tiebreak_rows"] = raw_tie_break_rows
                match_report["parsed_point_events"] = [
                    {key: item.get(key) for key in (
                        "set", "game_score", "point_index", "state_before", "state_after",
                        "point_score", "server", "winner", "tie_break", "confirmed", "quality",
                    )}
                    for item in parsed.get("events", [])
                ]
            matches.append(match_report)
            if len(matches) >= limit:
                break
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "unique, confirmed Fonbet links with archived source HTML; newest retrievable document per source match",
        "network_used": False,
        "parser_version_under_review": PARSER_VERSION,
        "review_notes": [
            "The archived HTML was read from object storage; this check made no network requests and wrote no database rows.",
            "Source point-state rows were compared with parser point states game by game; active server markers and tie-break winner markers were independently read from the source DOM.",
            "The v3 persisted output was wrong for server side: UXYqpB76 has a source-highlighted home server in its first game, while all 40 parsed point records in that document say side 1. MZ1H6Phf and fVArlUH0 show the same all-side-1 v3 pattern. Parser v4 was deployed and appended from archived HTML; the older v3 documents and their earlier as_of reads remain unchanged.",
            "Ten of the 30 latest linked archives have no recognized point rows. They remain documented coverage gaps, not parser passes.",
        ],
        "summary": {
            "matches_reviewed": len(matches),
            "matches_with_source_point_rows": sum(item["raw_point_rows"] > 0 for item in matches),
            "matches_with_compared_games": sum(item["games_compared"] > 0 for item in matches),
            "compared_games": sum(item["games_compared"] for item in matches),
            "compared_point_states": sum(item["source_point_states_compared"] for item in matches),
            "point_state_mismatches": sum(item["point_state_mismatches"] for item in matches),
            "server_mismatches": sum(item["server_mismatches"] for item in matches),
            "deuce_or_advantage_events": sum(item["deuce_or_advantage_events"] for item in matches),
            "tiebreak_point_events": sum(item["tiebreak_point_events"] for item in matches),
            "terminal_game_events": sum(item["terminal_game_events"] for item in matches),
            "raw_tiebreak_rows": sum(item["raw_tiebreak_rows"] for item in matches),
            "tiebreak_winner_mismatches": sum(item["tiebreak_winner_mismatches"] for item in matches),
            "tiebreak_server_mismatches": sum(item["tiebreak_server_mismatches"] for item in matches),
            "matches_with_gaps": sum(bool((item["parser_quality"] or {}).get("gaps")) for item in matches),
        },
        "matches": matches,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--output")
    parser.add_argument("--include-details", action="store_true",
                        help="include extracted source rows and parsed point events for manual inspection")
    args = parser.parse_args()
    report = build_report(args.limit, include_details=args.include_details)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(rendered + "\n")
    print(rendered)
