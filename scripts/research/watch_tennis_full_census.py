"""Mirror a running census and validate its final examples in Neural."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import time
import tempfile


def command(args, *, timeout=None):
    return subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, timeout=timeout).stdout


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    os.replace(temporary, path)


def sync(root, container, remote):
    report_text = command(['docker','exec',container,'cat',remote+'/progress.json'],timeout=30)
    progress = json.loads(report_text)
    write_json(root/'progress.live.json', progress)
    return progress


def copy_checkpoint(root, container, remote):
    destination = Path(tempfile.mkdtemp(prefix='checkpoint_copy_',dir=root))
    command(['docker','cp',f'{container}:{remote}/checkpoint',str(destination)])
    return destination/'checkpoint'


def copy_remote_file(source, destination):
    fd, name = tempfile.mkstemp(prefix='.census_copy_',dir=destination.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        command(['docker','cp',source,str(temporary)])
        os.replace(temporary,destination)
    finally:
        temporary.unlink(missing_ok=True)


def finish(root, container, remote, cutoff):
    write_json(root/'postprocess_status.json', {'status':'copying_completed_audit'})
    marker = root/'completed_checkpoint_copy.json'
    checkpoint = None
    if marker.exists():
        saved = json.loads(marker.read_text())
        candidate = Path(saved['path'])
        if saved.get('cutoff') == cutoff and (candidate/'checkpoint.json').is_file():
            checkpoint = candidate
    if checkpoint is None:
        checkpoint = copy_checkpoint(root,container,remote)
        write_json(marker,{'path':str(checkpoint),'cutoff':cutoff})
    copy_remote_file(f'{container}:{remote}/report.json',root/'report.json')
    report = json.loads((root/'report.json').read_text())
    if report.get('audit_scope',{}).get('status') != 'complete':
        raise ValueError('cannot_validate_incomplete_census')
    source = Path(report['training_examples_manifest']['path'])
    relative = source.relative_to(Path(remote)/'checkpoint')
    dataset = checkpoint/relative
    if not dataset.is_file():
        raise ValueError('copied_training_examples_missing')
    write_json(root/'postprocess_status.json', {'status':'validating_examples_in_neural',
        'examples':report['training_examples_manifest']['examples']})
    command(['docker','cp',str(dataset),'neurobet_neural:/tmp/tennis_full_census_examples.jsonl'])
    script = Path(__file__).with_name('build_odds_free_dataset.py')
    command(['docker','cp',str(script),'neurobet_neural:/tmp/build_tennis_full_census_dataset.py'])
    command(['docker','exec','neurobet_neural','python','/tmp/build_tennis_full_census_dataset.py',
        '--input','/tmp/tennis_full_census_examples.jsonl',
        '--output','/tmp/tennis_full_census_checked.jsonl','--dataset-as-of',cutoff,'--stream'])
    copy_remote_file('neurobet_neural:/tmp/tennis_full_census_checked.jsonl.summary.json',
        root/'neural_builder_summary.json')
    builder = json.loads((root/'neural_builder_summary.json').read_text())
    if builder['input_examples'] != report['training_examples_manifest']['examples']:
        raise ValueError('neural_validation_input_count_mismatch')
    effective_admission = ('blocked_neural_validation' if builder['rejected_examples'] or not builder['input_examples']
                           else report['training_gate']['status'])
    write_json(root/'postprocess_status.json', {'status':'complete',
        'audit_status':report['training_gate']['status'],'effective_admission':effective_admission,
        'checkpoint_copy':str(checkpoint),
        'neural_accepted':builder['accepted_examples'],'neural_rejected':builder['rejected_examples'],
        'finished_at':datetime.now(timezone.utc).isoformat()})
    counts = report['targets']
    lines=['# Полный теннисный census', '',
        f"Проверено событий: {report['audit_scope']['scanned_event_rows']} / {report['audit_scope']['total_events']}. "
        f"Срез данных: {cutoff}.", '', '| Цель | Уникальных матчей | Примеров |',
        '|---|---:|---:|']
    for target, label in [('point','Очко'),('game','Гейм'),('set','Сет'),('match','Матч')]:
        value=counts[target]
        lines.append(f"| {label} | {value['labelled_matches']} | {value['training_examples']} |")
    lines.extend(['', '| Цель | Начальный train / дефицит | Validation / дефицит | Holdout / дефицит |',
        '|---|---:|---|---:|'])
    coverage=report['training_gate'].get('coverage_by_target',{})
    for target,label in [('point','Очко'),('game','Гейм'),('set','Сет'),('match','Матч')]:
        value=coverage.get(target)
        if not value:
            continue
        validation=', '.join(f'{n}/{d}' for n,d in zip(value['validation_matches_by_fold'],value['validation_deficits_by_fold']))
        lines.append(f"| {label} | {value['initial_train_matches']} / {value['initial_train_deficit']} | {validation} | {value['holdout_matches']} / {value['holdout_deficit']} |")
    lines.extend(['',f"Допуск после проверки Neural: **{effective_admission}**. "
        f"Neural builder: принято {builder['accepted_examples']}, исключено {builder['rejected_examples']}.",
        'Пороги: 500 матчей в начальном train, 100 в каждом validation, 200 в holdout, оба класса и временная корректность.',
        'Причины недопуска: '+(', '.join(report['training_gate'].get('reasons',[])) or 'нет по итогам аудита')+'.',
        'Обучение, оценка holdout и выпуск моделей не запускались.', '',
        '[Полный отчёт](report.json) · [Проверка Neural](neural_builder_summary.json)', ''])
    (root/'summary.md').write_text('\n'.join(lines))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',required=True)
    parser.add_argument('--remote-dir',required=True)
    parser.add_argument('--container',default='neurobet_research')
    parser.add_argument('--cutoff',required=True)
    args=parser.parse_args()
    root=Path(args.output_dir).resolve()
    root.mkdir(parents=True,exist_ok=True)
    while True:
        try:
            progress=sync(root,args.container,args.remote_dir)
            if progress.get('status')=='complete':
                finish(root,args.container,args.remote_dir,args.cutoff)
                return
            try:
                grouping=json.loads(command(['docker','exec',args.container,'cat',
                    args.remote_dir+'/event_archive_progress.json'],timeout=30))
                write_json(root/'event_archive_progress.live.json',grouping)
            except (subprocess.SubprocessError, OSError, ValueError):
                grouping={}
            write_json(root/'postprocess_status.json', {'status':'preparing_event_archive' if grouping.get('status') in ('partitioning','grouping') else 'audit_running',
                'updated_at':datetime.now(timezone.utc).isoformat()})
            # An exited supervisor cannot make further progress. Preserve the
            # latest checkpoint and an explicit incomplete state for review.
            probe="import os; from pathlib import Path; print(int(any(any(x in (p/'cmdline').read_bytes().split(bytes([0])) for x in (b'/app/scripts/run_tennis_full_census.py',b'/app/scripts/tennis_event_archive.py',b'/app/scripts/continue_tennis_event_archive.py')) for p in Path('/proc').iterdir() if p.name.isdigit() and int(p.name)!=os.getpid())))"
            if command(['docker','exec',args.container,'python','-c',probe],timeout=30).strip()=='0':
                checkpoint = copy_checkpoint(root,args.container,args.remote_dir)
                write_json(root/'postprocess_status.json',{'status':'incomplete_supervisor_stopped',
                    'checkpoint_copy':str(checkpoint),'updated_at':datetime.now(timezone.utc).isoformat()})
                return
        except (subprocess.SubprocessError, OSError) as exc:
            write_json(root/'postprocess_status.json',{'status':'waiting_for_container',
                'error':str(exc),'updated_at':datetime.now(timezone.utc).isoformat()})
        except Exception as exc:
            write_json(root/'postprocess_status.json',{'status':'validation_failed',
                'error':str(exc),'updated_at':datetime.now(timezone.utc).isoformat()})
            raise
        time.sleep(30)


if __name__=='__main__':
    main()
