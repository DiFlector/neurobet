"""Dry-run then append evidence-backed match/point/game links from saved API data."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import signal
import uuid

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from db.connection import SessionLocal
from db.models import Event
from app.tennis_history_linking import candidate_event_ids, plan_api_link, append_api_link


def run(output, *, apply_plan=None, limit=1000):
    cutoff = datetime.now(timezone.utc)
    previous = json.loads(Path(apply_plan).read_text()) if apply_plan else None
    expected = {row['event_id']: row['semantic_digest'] for row in previous['rows']
                if row.get('semantic_digest')} if previous else {}
    with SessionLocal() as session:
        session.execute(text('SET TRANSACTION READ ONLY'))
        session.execute(text('SET LOCAL statement_timeout=30000'))
        candidates = [uuid.UUID(key) for key in expected] if previous else candidate_event_ids(session, cutoff)
        total_finished = session.query(Event.id).filter(Event.source=='fonbet', Event.sport_code=='tennis',
            Event.status=='finished', Event.created_at <= cutoff).count()
    rows, counts = [], Counter()
    status = 'complete'
    try:
        for event_id in candidates[:limit]:
            with SessionLocal() as session:
                if not previous:
                    session.execute(text('SET TRANSACTION READ ONLY'))
                session.execute(text('SET LOCAL statement_timeout=30000'))
                event = session.get(Event, event_id)
                diagnostics = {}
                plan, reason = plan_api_link(session, event, as_of=cutoff, diagnostics=diagnostics)
                row = {'event_id': str(event_id), 'fonbet_source_event_id': event.source_event_id, 'reason': reason,
                       'participants': [event.participant_a_name, event.participant_b_name], 'evidence': diagnostics}
                if plan is not None:
                    row.update(source_match_id=plan['source_match_id'], semantic_digest=plan['semantic_digest'],
                        confirmed_points=plan['quality']['confirmed_points'], confirmed_games=plan['quality']['confirmed_games'],
                        evidence=plan['evidence'], existing_raw_document_id=str(plan['document_id']) if plan['document_id'] else None)
                    if previous and expected[str(event_id)] != plan['semantic_digest']:
                        row['reason'] = 'evidence_changed_since_dry_run'
                    elif previous:
                        version, reason = append_api_link(session, plan)
                        row['reason'] = reason
                        if version:
                            row.update(version_id=str(version.id), available_at=version.available_at.isoformat())
                        session.commit()
                counts[row['reason']] += 1
                rows.append(row)
                print(json.dumps({'processed': len(rows), 'reason': row['reason'], 'event': event.source_event_id}), flush=True)
    except (TimeoutError, SQLAlchemyError) as exc:
        status = 'incomplete'
        counts[type(exc).__name__] += 1
    if len(rows) < len(candidates):
        status = 'incomplete'
    report = {'status': status, 'mode': 'apply' if previous else 'dry_run', 'as_of': cutoff.isoformat(),
              'finished_fonbet_events': total_finished, 'candidate_events': len(candidates),
              'events_without_confirmed_canonical_pair': total_finished-len(candidates) if not previous else None,
              'processed_events': len(rows), 'counts': dict(counts), 'rows': rows,
              'training_gate': 'blocked_not_an_admission_report'}
    output = Path(output);output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix('.tmp');tmp.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n');tmp.replace(output)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--apply-plan', help='Append only unchanged ready decisions from this dry-run report')
    parser.add_argument('--limit', type=int, default=1000)
    parser.add_argument('--all', action='store_true', help='Process every candidate; no total-event limit')
    parser.add_argument('--max-runtime-seconds', type=int, default=900)
    args = parser.parse_args()
    if not 1 <= args.limit <= 1000 or not 1 <= args.max_runtime_seconds <= 900:
        parser.error('limit must be 1..1000 and runtime 1..900')
    def expired(_signum, _frame):
        raise TimeoutError('linking_runtime_timeout')
    signal.signal(signal.SIGALRM, expired);signal.alarm(args.max_runtime_seconds)
    try:
        result = run(args.output, apply_plan=args.apply_plan, limit=None if args.all else args.limit)
    finally:
        signal.alarm(0)
    print(json.dumps({k:v for k,v in result.items() if k!='rows'},sort_keys=True))
    raise SystemExit(0 if result['status']=='complete' else 2)
