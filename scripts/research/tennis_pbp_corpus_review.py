"""Read-only structural consistency report for linked Flashscore PBP matches.

This report is machine-assisted source/result validation. It explicitly does
not replace a human review of the original archived HTML.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone

from db.connection import SessionLocal
from db.models import Event, TennisPointHistoryVersion, TennisPointMatchLink
from tennis_adapter.point_history import (
    infer_point_winner,
    is_authoritative_point_history_version,
)


def _official_sets(score: str) -> list[list[int]]:
    match = re.search(r"\(([^)]*)\)", score or "")
    return [[int(a), int(b)] for a, b in re.findall(r"(\d+)\s*[-:]\s*(\d+)", match.group(1))] if match else []


def build_report(limit: int = 20) -> dict:
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        links = session.query(TennisPointMatchLink).filter(
            TennisPointMatchLink.fonbet_event_id.is_not(None),
            TennisPointMatchLink.available_at <= now,
        ).order_by(TennisPointMatchLink.available_at.desc(), TennisPointMatchLink.id.desc()).all()
        current_links = {}
        for link in links:
            current_links.setdefault(link.source_match_id, link)
        links = [link for link in current_links.values() if link.status in ("matched", "manual")][:limit]
        source_ids = [link.source_match_id for link in links]
        versions = session.query(TennisPointHistoryVersion).filter(
            TennisPointHistoryVersion.source_match_id.in_(source_ids),
            TennisPointHistoryVersion.available_at.is_not(None),
            TennisPointHistoryVersion.available_at <= now,
        ).order_by(TennisPointHistoryVersion.available_at.desc(), TennisPointHistoryVersion.id.desc()).all()
        latest_by_set = {}
        for version in versions:
            if not is_authoritative_point_history_version(version.events_json, version.quality_json):
                continue
            set_numbers = {item.get("set") for item in version.events_json or []
                           if isinstance(item, dict) and item.get("set") is not None}
            if (version.quality_json or {}).get("set_number") is not None:
                set_numbers.add(version.quality_json["set_number"])
            for set_no in set_numbers or {None}:
                latest_by_set.setdefault((version.source_match_id, set_no), version)

        matches = []
        for link in links:
            event = session.get(Event, link.fonbet_event_id)
            official = (event.metadata_json or {}).get("fonbet_official_result") or {} if event else {}
            expected = _official_sets(official.get("final_score") or "")
            orientation = (link.evidence_json or {}).get("external_home_is_fonbet_a")
            terminal_by_set, event_count, gap_count, deuce_count, tiebreak_count = {}, 0, 0, 0, 0
            transition_errors, parser_versions = [], set()
            for (source_id, set_no), version in latest_by_set.items():
                if source_id != link.source_match_id:
                    continue
                parser_versions.add(version.parser_version)
                events = version.events_json or []
                event_count += len(events)
                gap_count += len((version.quality_json or {}).get("gaps") or [])
                for item in events:
                    quality = item.get("quality")
                    if quality == "terminal_from_game_result":
                        score = item.get("game_score")
                        if isinstance(score, list) and len(score) == 2:
                            prior = terminal_by_set.get(set_no)
                            if prior is None or sum(score) >= sum(prior):
                                terminal_by_set[set_no] = score
                    elif quality == "terminal_from_tiebreak_result":
                        score = item.get("game_score")
                        if isinstance(score, list) and len(score) == 2:
                            terminal_by_set[set_no] = score
                    elif quality == "confirmed":
                        before, after, winner = item.get("state_before"), item.get("state_after"), item.get("winner")
                        if item.get("tie_break"):
                            tiebreak_count += 1
                            valid = (isinstance(before, list) and isinstance(after, list) and len(before) == 2
                                     and len(after) == 2 and winner in (0, 1)
                                     and after[winner] == before[winner] + 1
                                     and after[1-winner] == before[1-winner])
                        else:
                            deuce_count += int("AD" in (before or []) or "A" in (before or [])
                                               or "AD" in (after or []) or "A" in (after or []))
                            valid = (isinstance(before, list) and isinstance(after, list)
                                     and infer_point_winner(tuple(before), tuple(after)) == winner)
                        if not valid:
                            transition_errors.append({"set": set_no, "quality": quality})
            actual = sorted(terminal_by_set.items(), key=lambda item: item[0] or 0)
            actual_scores = [score if orientation is True else score[::-1] for _, score in actual] if orientation in (True, False) else []
            score_match = bool(expected and len(expected) == len(actual_scores) and expected == actual_scores)
            matches.append({
                "source_match_id": link.source_match_id,
                "fonbet_event_id": str(link.fonbet_event_id),
                "participants": [event.participant_a_name, event.participant_b_name] if event else None,
                "fonbet_final_score": official.get("final_score"),
                "side_order_confirmed": orientation in (True, False),
                "parser_versions": sorted(parser_versions),
                "sets_with_pbp": len({set_no for sid, set_no in latest_by_set
                                      if sid == link.source_match_id and latest_by_set[(sid, set_no)].events_json}),
                "confirmed_events": event_count, "gaps": gap_count,
                "deuce_transitions": deuce_count, "tiebreak_points": tiebreak_count,
                "terminal_set_scores_fonbet_order": actual_scores,
                "final_score_matches": score_match,
                "transition_errors": transition_errors,
            })
    summary = {
        "unique_linked_matches_reviewed": len(matches),
        "matches_with_parsed_points": sum(row["confirmed_events"] > 0 for row in matches),
        "final_score_matches": sum(row["final_score_matches"] for row in matches),
        "side_order_confirmed": sum(row["side_order_confirmed"] for row in matches),
        "matches_with_gaps": sum(row["gaps"] > 0 for row in matches),
        "matches_with_transition_errors": sum(bool(row["transition_errors"]) for row in matches),
        "deuce_transitions": sum(row["deuce_transitions"] for row in matches),
        "tiebreak_points": sum(row["tiebreak_points"] for row in matches),
        "human_html_review_completed": False,
    }
    return {"generated_at": now.isoformat(), "scope": "latest confirmed links and latest available parser version per set",
            "interpretation": "automated consistency report; archived HTML still requires human review",
            "summary": summary, "matches": matches}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--output")
    args = parser.parse_args()
    report = build_report(args.limit)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(rendered + "\n")
    print(rendered)
