import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts' / 'research'))
from prepare_tennis_recovered_cohort import prepare


def _inputs(root, replacement_time='2026-10-13T10:05:00Z'):
    event='00000000-0000-0000-0000-000000000001'
    old='00000000-0000-0000-0000-000000000002'
    new='00000000-0000-0000-0000-000000000003'
    source=root/'source.jsonl'
    source.write_text(json.dumps({'fonbet_event_id':event,'snapshot_id':old,
                                  'snapshot_observed_at':'2026-10-13T10:00:00Z'})+'\n')
    missing=root/'missing.json'
    missing.write_text(json.dumps([{'id':old,'event_id':event,'source_event_id':'12345',
        'observed_at':'2026-10-13T10:00:00Z','score_detail':{'stats':{}}}]))
    review=root/'review.json'
    review.write_text(json.dumps([{'old_snapshot_id':old,'source_event_id':'12345',
        'reason':'complete_later_snapshot','candidate':{'id':new,'event_id':event,
        'observed_at':replacement_time,'current_period':1,'sport_state':{},'score':'0-0 (1-1)',
        'score_detail':{'stats':{'set_lines':[{'live':True,'games_a':1,'games_b':1}]},
                        'current_game':{'points_a':'15','points_b':'0'}}}}]))
    checkpoint=root/'checkpoint.json'
    row={'id':event,'final_score':'2:0 (6-4 6-3)','metadata_time':'2026-10-21T12:00:00Z',
         'witness':{'snapshot_observed_at':'2026-10-13T12:00:00Z'}}
    checkpoint.write_text(json.dumps({'rows':[row],'candidates':[row],
        'settings':{'cutoff':'2026-10-28T05:25:39Z'}}))
    return source, missing, review, checkpoint, root/'output'


def test_replacement_keeps_its_real_time_and_does_not_rewrite_original(tmp_path):
    args=_inputs(tmp_path)
    original=args[0].read_bytes()
    first=prepare(*map(str,args))
    assert first['selected_complete_replacements']==1
    assert first['excluded_unrecoverable_states']==0
    assert args[0].read_bytes()==original
    rows=[json.loads(r) for r in Path(first['output_index']['path']).read_text().splitlines()]
    assert rows[-1]['snapshot_observed_at']=='2026-10-13T10:05:00+00:00'
    assert prepare(*map(str,args))==first


def test_complete_state_after_result_stays_excluded(tmp_path):
    args=_inputs(tmp_path, replacement_time='2026-10-13T12:00:00Z')
    report=prepare(*map(str,args))
    assert report['selected_complete_replacements']==0
    assert report['excluded_unrecoverable_states']==1
    assert report['output_snapshot_keys']==0
    # Event membership remains present for exclusion diagnostics.
    assert report['indexed_events']==1


def test_state_selection_requires_completed_receipt_recovery(tmp_path):
    args=_inputs(tmp_path)
    checkpoint=json.loads(args[3].read_text())
    checkpoint['rows']=[]
    args[3].write_text(json.dumps(checkpoint))
    with pytest.raises(ValueError,match='recovery_must_be_complete'):
        prepare(*map(str,args))
