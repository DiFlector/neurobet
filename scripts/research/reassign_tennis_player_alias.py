#!/usr/bin/env python3
"""Audit and reassign a confirmed provider ID to a reviewed canonical identity."""

from __future__ import annotations

import argparse
import json
import uuid

from db.connection import SessionLocal
from app.canonical_player_aliases import reassign_confirmed_player_alias


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--source-player-id", required=True)
    parser.add_argument("--expected-current-owner-id", required=True, type=uuid.UUID)
    parser.add_argument("--canonical-player-id", required=True, type=uuid.UUID)
    parser.add_argument("--rationale", required=True)
    parser.add_argument("--evidence-json", required=True)
    parser.add_argument("--decided-by", required=True)
    args = parser.parse_args()
    evidence = json.loads(args.evidence_json)
    if not isinstance(evidence, dict):
        parser.error("--evidence-json must be a JSON object")
    with SessionLocal() as session:
        decision = reassign_confirmed_player_alias(
            session,
            source=args.source,
            source_player_id=args.source_player_id,
            expected_current_owner_id=args.expected_current_owner_id,
            canonical_player_id=args.canonical_player_id,
            rationale=args.rationale,
            evidence=evidence,
            decided_by=args.decided_by,
        )
        session.commit()
        print(json.dumps({
            "decision_id": str(decision.id), "source": decision.source,
            "source_player_id": decision.source_player_id,
            "canonical_player_id": str(decision.canonical_player_id),
            "rejected_candidate_ids": decision.rejected_candidate_ids,
            "created_at": decision.created_at.isoformat(),
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
