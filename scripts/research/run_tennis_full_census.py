"""Continue bounded read-only audit runs until every frozen event is processed."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def _checkpoint_progress(root):
    path=root/'checkpoint'/'checkpoint.json'
    if not path.exists():
        return {'completed_events':0,'total_events':None,'snapshot_rows':0,'completed_batches':0}
    checkpoint=json.loads(path.read_text())
    batches=checkpoint.get('completed_batches',[])
    progress = {'completed_events':sum(len(b['event_ids']) for b in batches),
            'total_events':len(checkpoint.get('frozen_event_ids',[])),
            'snapshot_rows':sum(b['snapshot_rows'] for b in batches),
            'completed_batches':len(batches),'checkpoint_status':checkpoint.get('status'),
            'pending_event_ids':(checkpoint.get('pending_batch') or {}).get('event_ids',[])}
    cache = root/'snapshots.sqlite'
    if cache.exists():
        from tennis_snapshot_cache import read_state
        try:
            progress['snapshot_export'] = read_state(cache)
        except Exception as exc:
            progress['snapshot_export_status'] = type(exc).__name__
    corpus = root/'checkpoint'/'corpus_counts.sqlite'
    if corpus.exists():
        from tennis_corpus_counts import read_state as read_corpus_state
        try:
            progress['corpus_counts'] = read_corpus_state(corpus)
        except Exception as exc:
            progress['corpus_counts_status'] = type(exc).__name__
    finalization = root/'checkpoint'/'finalization_progress.json'
    if finalization.exists():
        try:
            progress['finalization'] = json.loads(finalization.read_text())
        except (OSError,ValueError):
            pass
    return progress


def run(root,cutoff,*,event_batch_size=5,snapshot_page_size=5000,runtime=900,query_timeout=30,
        discipline_scope='singles'):
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True)
    lock=(root/'runner.lock').open('w')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    attempt=max([int(p.stem.split('_')[-1]) for p in root.glob('attempt_*.log')]+[0]);stalled=0
    try:
        while True:
            attempt+=1
            before=_checkpoint_progress(root)
            command=[sys.executable,str(Path(__file__).with_name('run_tennis_label_audit.py')),
                '--output',str(root/'report.json'),'--checkpoint-dir',str(root/'checkpoint'),
                '--cutoff',cutoff,'--event-batch-size',str(event_batch_size),
                '--snapshot-page-size',str(snapshot_page_size),'--query-timeout-seconds',str(query_timeout),
                '--max-runtime-seconds',str(runtime),'--discipline-scope',discipline_scope]
            if (root/'checkpoint'/'checkpoint.json').exists(): command.append('--resume')
            with (root/f'attempt_{attempt:04d}.log').open('a') as log:
                child_env={**os.environ,'TENNIS_FULL_SNAPSHOT_CACHE':str(root/'snapshots.sqlite')}
                if (root/'event_archive'/'manifest.json').exists():
                    child_env['TENNIS_FULL_EVENT_ARCHIVE']=str(root/'event_archive')
                child=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,env=child_env)
                try:
                    # The child self-checkpoints and exits after a bounded
                    # 15-minute slice. Wait on its process handle; resume the
                    # next slice automatically without periodic status polling.
                    child.wait()
                finally:
                    if child.poll() is None:
                        child.terminate();child.wait(timeout=35)
            after=_checkpoint_progress(root)
            report_path=root/'report.json'
            report=json.loads(report_path.read_text()) if report_path.exists() else {}
            if child.returncode==0 and report.get('audit_scope',{}).get('status')=='complete':
                progress={**after,'status':'complete','cutoff':cutoff,
                          'training_gate':report.get('training_gate',{}).get('status')}
                (root/'progress.json').write_text(json.dumps(progress,sort_keys=True,indent=2)+'\n')
                print(json.dumps(progress,sort_keys=True),flush=True)
                return 0
            if child.returncode not in (0,2):
                raise RuntimeError(f'audit_failed_exit_{child.returncode}:see_attempt_{attempt:04d}.log')
            log_tail = (root/f'attempt_{attempt:04d}.log').read_text(errors='replace')[-12000:].lower()
            if any(reason in log_tail for reason in ('database system is in recovery mode',
                    'database system is starting up', 'connection refused')):
                stalled = 0
                print(json.dumps({'status':'waiting_for_database_recovery','attempt':attempt}),flush=True)
                time.sleep(3600)
                continue
            stalled=stalled+1 if (after['completed_events']==before['completed_events'] and
                after.get('snapshot_export',{}).get('source_rows',0)==before.get('snapshot_export',{}).get('source_rows',0) and
                sum(after.get('corpus_counts',{}).get(k,0) for k in ('scanned_documents','scanned_versions')) ==
                sum(before.get('corpus_counts',{}).get(k,0) for k in ('scanned_documents','scanned_versions')) and
                after.get('finalization')==before.get('finalization')) else 0
            if stalled>=4:
                raise RuntimeError('same_checkpoint_failed_four_hourly_slices; inspect attempts before retry')
    finally:
        lock.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',required=True)
    parser.add_argument('--cutoff',required=True)
    parser.add_argument('--event-batch-size',type=int,default=5)
    parser.add_argument('--snapshot-page-size',type=int,default=5000)
    parser.add_argument('--query-timeout-seconds',type=int,default=30)
    parser.add_argument('--max-runtime-seconds',type=int,default=900)
    parser.add_argument('--discipline-scope',choices=('singles','doubles'),default='singles')
    args=parser.parse_args()
    raise SystemExit(run(args.output_dir,args.cutoff,event_batch_size=args.event_batch_size,
        snapshot_page_size=args.snapshot_page_size,runtime=args.max_runtime_seconds,
        query_timeout=args.query_timeout_seconds,discipline_scope=args.discipline_scope))


if __name__=='__main__':
    main()
