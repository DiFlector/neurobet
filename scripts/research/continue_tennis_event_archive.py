"""Resume the full census after its per-event archive has been published."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import time


def archive_builder_running():
    for process in Path('/proc').iterdir():
        if not process.name.isdigit() or int(process.name)==os.getpid():
            continue
        try:
            if b'/app/scripts/tennis_event_archive.py' in (process/'cmdline').read_bytes().split(b'\0'):
                return True
        except (OSError,ProcessLookupError):
            continue
    return False


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',required=True)
    parser.add_argument('--cutoff',required=True)
    args=parser.parse_args()
    root=Path(args.output_dir)
    while not (root/'event_archive'/'manifest.json').is_file():
        if not archive_builder_running():
            raise RuntimeError('event_archive_builder_stopped_before_completion')
        time.sleep(10)
    from tennis_event_archive import seal_archive
    seal_archive(root/'event_archive')
    command=[sys.executable,str(Path(__file__).with_name('run_tennis_full_census.py')),
        '--output-dir',str(root),'--cutoff',args.cutoff,'--event-batch-size','5',
        '--snapshot-page-size','5000','--query-timeout-seconds','30','--max-runtime-seconds','900']
    os.execv(sys.executable,command)


if __name__=='__main__':
    main()
