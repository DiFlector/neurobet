#!/usr/bin/env python3
"""Re-run canonical projection for explicitly selected saved source results."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from db.connection import engine
from db.models.external_tennis import ExtTennisMatch
from app.canonical_tennis_projection import (
    project_api_tennis_results,
    project_recent_tennis_explorer,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, choices=("api_tennis", "tennis_explorer"))
    parser.add_argument("--source-match-id", action="append", default=[])
    parser.add_argument("--source-player-id", action="append", default=[],
                        help="reproject saved matches involving this provider player ID")
    args = parser.parse_args()
    ids = sorted({str(value).strip() for value in args.source_match_id if str(value).strip()})
    player_ids = sorted({str(value).strip() for value in args.source_player_id if str(value).strip()})
    if not ids and not player_ids:
        parser.error("provide at least one --source-match-id or --source-player-id")

    with Session(engine) as session:
        filters = []
        if ids:
            filters.append(ExtTennisMatch.source_match_id.in_(ids))
        if player_ids:
            filters.append(or_(
                ExtTennisMatch.winner_player_id.in_(player_ids),
                ExtTennisMatch.loser_player_id.in_(player_ids),
            ))
        rows = session.execute(select(ExtTennisMatch).where(
            ExtTennisMatch.source == args.source,
            or_(*filters),
        ).order_by(ExtTennisMatch.played_at, ExtTennisMatch.source_match_id)).scalars().all()
        found = {row.source_match_id for row in rows}
        if ids and not set(ids).issubset(found):
            raise SystemExit(f"refusing partial replay; missing source IDs: {sorted(set(ids) - found)}")
        by_day = defaultdict(list)
        for row in rows:
            by_day[row.played_at.date()].append({
                "source_match_id": row.source_match_id,
                "played_at": row.played_at,
                "available_at": row.available_at,
                "tour_level": row.tour_level,
                "tournament": row.tournament,
                "surface": row.surface,
                "best_of": row.best_of,
                "round_name": row.round_name,
                "gender": row.gender,
                "winner_name": row.winner_name,
                "loser_name": row.loser_name,
                "winner_player_id": row.winner_player_id,
                "loser_player_id": row.loser_player_id,
                "score": row.score,
            })
        totals = {"projected": 0, "merged": 0, "ambiguous": 0, "unlinked": 0, "unchanged": 0}
        for _day, items in sorted(by_day.items()):
            result = (project_api_tennis_results(session, items) if args.source == "api_tennis"
                      else project_recent_tennis_explorer(session, items))
            for key in totals:
                totals[key] += int(result.get(key, 0))
            session.commit()
        print(json.dumps({
            "source": args.source, "requested_source_match_ids": ids,
            "requested_source_player_ids": player_ids, "source_matches": len(rows),
            "date_batches": len(by_day), **totals,
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
