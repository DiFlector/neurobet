"""Select existing complete snapshots, preserving rejected originals as evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

from recover_tennis_result_witnesses import _utc, _atomic
from tennis_label_audit import (_read_saved_snapshot_cohort, _materialize_odds_free_feature_values,
                                _missing_feature_values)
from tennis_result_observation import score_signature


def prepare(source_index, missing_snapshots, state_review, result_checkpoint, output_dir):
    source = _read_saved_snapshot_cohort(source_index)
    old_rows = json.loads(Path(missing_snapshots).read_text())
    review = json.loads(Path(state_review).read_text())
    checkpoint = json.loads(Path(result_checkpoint).read_text())
    if len(checkpoint['rows']) != len(checkpoint['candidates']):
        raise ValueError('result_recovery_must_be_complete')
    cutoff = _utc(checkpoint['settings']['cutoff'])
    labels = {r['id']: r for r in checkpoint['rows']}
    reviewed = {r['old_snapshot_id']: r for r in review}
    keys = dict(source['keys'])
    decisions = []
    for old in old_rows:
        old_key = (old['id'], _utc(old['observed_at']))
        db_key = (uuid.UUID(old_key[0]), old_key[1])
        event_id = uuid.UUID(old['event_id'])
        if keys.get(db_key) != event_id:
            raise ValueError('missing_state_key_not_in_source_cohort')
        # These are the original DB payloads reviewed by the raw recovery job.
        if (old.get('score_detail') or {}).get('stats', {}).get('set_lines'):
            raise ValueError('reviewed_original_state_is_not_missing_set_lines')
        del keys[db_key]
        decision = {'event_id': str(event_id), 'old_snapshot_id': old_key[0],
                    'old_observed_at': old_key[1].isoformat(), 'status': 'excluded_missing_original_state'}
        r = reviewed.get(old_key[0])
        candidate = r.get('candidate') if r else None
        label = labels.get(str(event_id))
        if r and r['reason'] == 'complete_later_snapshot' and candidate and label:
            if str(candidate['event_id']) != str(event_id) or r['source_event_id'] != old['source_event_id']:
                raise ValueError('replacement_event_identity_mismatch')
            at = _utc(candidate['observed_at'])
            times = [cutoff]
            if label.get('metadata_time'):
                times.append(_utc(label['metadata_time']))
            if label.get('witness'):
                times.append(_utc(label['witness']['snapshot_observed_at']))
            snapshot = SimpleNamespace(**{**candidate, 'observed_at': at})
            values = _materialize_odds_free_feature_values(SimpleNamespace(metadata_json={}), snapshot, at, {}, {})
            before_result = at < min(times)
            if (not _missing_feature_values(values) and before_result and at >= old_key[1]
                    and score_signature(candidate.get('score')) != score_signature(label['final_score'])):
                new_key = (uuid.UUID(candidate['id']), at)
                if new_key in keys and keys[new_key] != event_id:
                    raise ValueError('replacement_snapshot_conflicts_with_source_event')
                keys[new_key] = event_id
                decision.update(status='selected_distinct_saved_complete_snapshot',
                                new_snapshot_id=candidate['id'], new_observed_at=at.isoformat())
        decisions.append(decision)
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = root / 'recovered_feature_snapshot_index.jsonl'
    output = [{'fonbet_event_id': str(e)} for e in sorted(source['event_ids'], key=str)]
    output += [{'fonbet_event_id': str(e), 'snapshot_id': str(s), 'snapshot_observed_at': t.isoformat()}
               for (s, t), e in sorted(keys.items(), key=lambda item: (item[0][1], str(item[0][0])))]
    temporary = path.with_suffix('.jsonl.tmp')
    temporary.write_text(''.join(json.dumps(r, sort_keys=True)+'\n' for r in output))
    temporary.replace(path)
    report = {'format_version': 1, 'status': 'complete', 'source_index_sha256': source['sha256'],
              'source_snapshot_keys': len(source['keys']), 'reviewed_missing_state_snapshots': len(decisions),
              'selected_complete_replacements': sum(r['status']=='selected_distinct_saved_complete_snapshot' for r in decisions),
              'excluded_unrecoverable_states': sum(r['status']=='excluded_missing_original_state' for r in decisions),
              'output_snapshot_keys': len(keys), 'indexed_events': len(source['event_ids']),
              'output_index': {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()},
              'decisions': decisions, 'training_gate': 'blocked_selection_is_not_admission'}
    _atomic(root / 'state_selection_report.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-index', required=True)
    parser.add_argument('--missing-snapshots', required=True)
    parser.add_argument('--state-review', required=True)
    parser.add_argument('--result-checkpoint', required=True)
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args()
    report = prepare(args.source_index,args.missing_snapshots,args.state_review,args.result_checkpoint,args.output_dir)
    print(json.dumps({k:v for k,v in report.items() if k!='decisions'},sort_keys=True))


if __name__ == '__main__':
    main()
