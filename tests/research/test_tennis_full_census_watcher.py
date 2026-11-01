import importlib.util
import json
from pathlib import Path


script = Path(__file__).resolve().parents[2] / 'scripts/research/watch_tennis_full_census.py'
spec = importlib.util.spec_from_file_location('census_watcher',script)
watcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watcher)


def test_finish_requires_complete_audit_and_checks_neural_input(tmp_path,monkeypatch):
    root=tmp_path/'output'; root.mkdir()
    remote='/archive'
    target={name:{'labelled_matches':2,'training_examples':3} for name in ('point','game','set','match')}
    report={'audit_scope':{'status':'complete','scanned_event_rows':2,'total_events':2},
            'training_examples_manifest':{'path':'/archive/checkpoint/final/examples.jsonl','examples':12},
            'training_gate':{'status':'blocked'},'targets':target}
    calls=[]
    def command(args,**kwargs):
        calls.append(args)
        if args[0:2]==['docker','cp'] and args[1:] and ':/' in args[2]:
            if args[2].endswith('/checkpoint'):
                path=Path(args[3])/'checkpoint/final/examples.jsonl'; path.parent.mkdir(parents=True); path.write_text('{}\n'); (path.parents[1]/'checkpoint.json').write_text('{}')
            elif args[2].endswith('/report.json'):
                Path(args[3]).write_text(json.dumps(report))
            elif args[2].endswith('.summary.json'):
                Path(args[3]).write_text(json.dumps(
                    {'input_examples':12,'accepted_examples':11,'rejected_examples':1}))
        return ''
    monkeypatch.setattr(watcher,'command',command)
    watcher.finish(root,'research',remote,'2026-10-28T09:53:30+00:00')
    status=json.loads((root/'postprocess_status.json').read_text())
    assert status['status']=='complete' and status['audit_status']=='blocked'
    assert status['neural_rejected']==1
    assert status['effective_admission']=='blocked_neural_validation'
    assert any('--stream' in args for args in calls)
    assert '11' in (root/'summary.md').read_text()
