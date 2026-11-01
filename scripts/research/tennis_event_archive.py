"""Reorganize the complete frozen snapshot cache into per-event files."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone, timedelta
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import struct
import tempfile
from uuid import UUID
import zlib

from tennis_snapshot_cache import read_state

_BUCKET_HEADER = struct.Struct('>16sI')
_EVENT_HEADER = struct.Struct('>16sqI')
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _progress(path, value):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, sort_keys=True)+'\n')
    os.replace(tmp,path)


def build(cache_path, destination, progress_path):
    """Build into a private directory; publish only after all counts and hashes agree."""
    destination=Path(destination)
    if destination.exists():
        manifest=json.loads((destination/'manifest.json').read_text())
        state=read_state(cache_path)
        if (manifest.get('status')!='complete' or manifest.get('event_ids_sha256')!=state.get('event_ids_sha256')
                or manifest.get('cutoff')!=state.get('cutoff') or manifest.get('records')!=state.get('saved_rows')):
            raise ValueError('event_archive_frozen_input_mismatch')
        seal_archive(destination)
        return manifest
    state=read_state(cache_path)
    if not state.get('complete'):
        raise ValueError('snapshot_cache_incomplete')
    temporary=Path(tempfile.mkdtemp(prefix='event_archive_build_',dir=destination.parent))
    buckets=temporary/'buckets'; buckets.mkdir()
    output=temporary/'archive'; output.mkdir()
    handles=[(buckets/f'{i:02x}.bin').open('wb',buffering=65536) for i in range(256)]
    total=0
    try:
        with sqlite3.connect(f'file:{Path(cache_path)}?mode=ro',uri=True) as db:
            for event_id,blob in db.execute('SELECT event_id,payload FROM snapshots NOT INDEXED ORDER BY rowid'):
                event_bytes=UUID(event_id).bytes
                handles[event_bytes[0]].write(_BUCKET_HEADER.pack(event_bytes,len(blob)))
                handles[event_bytes[0]].write(blob)
                total+=1
                if total%100000==0:
                    _progress(Path(progress_path),{'status':'partitioning','rows':total,'expected_rows':state['saved_rows']})
    finally:
        for handle in handles:
            handle.close()
    if total!=state['saved_rows']:
        raise ValueError('event_archive_source_count_mismatch')
    counts=0; events=0; digest=hashlib.sha256()
    for index in range(256):
        groups=defaultdict(list)
        with (buckets/f'{index:02x}.bin').open('rb') as handle:
            while header:=handle.read(_BUCKET_HEADER.size):
                if len(header)!=_BUCKET_HEADER.size:
                    raise ValueError('event_archive_truncated_bucket')
                event_bytes,size=_BUCKET_HEADER.unpack(header)
                blob=handle.read(size)
                if len(blob)!=size:
                    raise ValueError('event_archive_truncated_payload')
                value=json.loads(zlib.decompress(blob))
                if UUID(value['event_id']).bytes!=event_bytes:
                    raise ValueError('event_archive_event_mismatch')
                at=datetime.fromisoformat(value['observed_at']).astimezone(timezone.utc)
                delta=at-_EPOCH
                micros=(delta.days*86400+delta.seconds)*1000000+delta.microseconds
                groups[event_bytes].append((micros,UUID(value['id']).bytes,blob))
        for event_bytes,rows in sorted(groups.items()):
            rows.sort(key=lambda x:(x[0],x[1]))
            event_id=str(UUID(bytes=event_bytes)); h=hashlib.sha256()
            with (output/(event_id+'.bin')).open('wb') as handle:
                previous=None
                for micros,snapshot_id,blob in rows:
                    key=(micros,snapshot_id)
                    if key==previous:
                        raise ValueError('event_archive_duplicate_snapshot_key')
                    previous=key
                    header=_EVENT_HEADER.pack(snapshot_id,micros,len(blob))
                    handle.write(header);handle.write(blob);h.update(header);h.update(blob)
            digest.update(event_bytes);digest.update(h.digest())
            counts+=len(rows);events+=1
        _progress(Path(progress_path),{'status':'grouping','buckets_done':index+1,'buckets_total':256,
            'rows':counts,'expected_rows':total})
        (buckets/f'{index:02x}.bin').unlink()
    if counts!=total:
        raise ValueError('event_archive_output_count_mismatch')
    manifest={'status':'complete','records':counts,'events_with_snapshots':events,
        'cutoff':state['cutoff'],'event_ids_sha256':state['event_ids_sha256'],'sha256':digest.hexdigest()}
    (output/'manifest.json').write_text(json.dumps(manifest,sort_keys=True,indent=2)+'\n')
    os.replace(output,destination)
    seal_archive(destination)
    buckets.rmdir();temporary.rmdir()
    _progress(Path(progress_path),manifest)
    return manifest



def seal_archive(path):
    """Persist the complete event membership before any per-event reads."""
    path=Path(path)
    manifest=json.loads((path/'manifest.json').read_text())
    events=sorted(item.stem for item in path.glob('*.bin'))
    if manifest.get('status')!='complete' or len(events)!=manifest.get('events_with_snapshots'):
        raise ValueError('event_archive_event_membership_mismatch')
    for event in events:
        if str(UUID(event))!=event:
            raise ValueError('event_archive_invalid_event_filename')
    value={'archive_sha256':manifest['sha256'],'event_ids':events}
    target=path/'event_ids.json'
    if target.exists():
        if json.loads(target.read_text())!=value:
            raise ValueError('event_archive_event_membership_changed')
    else:
        tmp=path/'event_ids.json.tmp'
        tmp.write_text(json.dumps(value,sort_keys=True)+'\n')
        os.replace(tmp,target)
    return len(events)


@lru_cache(maxsize=4)
def _event_membership(path):
    value=json.loads((Path(path)/'event_ids.json').read_text())
    manifest=json.loads((Path(path)/'manifest.json').read_text())
    if value.get('archive_sha256')!=manifest.get('sha256') or len(value['event_ids'])!=manifest.get('events_with_snapshots'):
        raise ValueError('event_archive_event_membership_mismatch')
    return frozenset(value['event_ids'])

def iter_event(path,event_id,*,payload=False):
    from types import SimpleNamespace
    event_id=str(UUID(str(event_id)))
    expected=event_id in _event_membership(str(Path(path).resolve()))
    file=Path(path)/(event_id+'.bin')
    if not file.exists():
        if expected: raise ValueError('event_archive_expected_file_missing')
        return
    if not expected: raise ValueError('event_archive_unexpected_file')
    with file.open('rb') as handle:
        while header:=handle.read(_EVENT_HEADER.size):
            if len(header)!=_EVENT_HEADER.size: raise ValueError('event_archive_truncated_header')
            snapshot_id,micros,size=_EVENT_HEADER.unpack(header)
            blob=handle.read(size)
            if len(blob)!=size: raise ValueError('event_archive_truncated_payload')
            at=_EPOCH+timedelta(microseconds=micros)
            if payload:
                value=json.loads(zlib.decompress(blob))
                if (UUID(value['id']) != UUID(bytes=snapshot_id) or UUID(value['event_id']) != UUID(str(event_id))
                        or datetime.fromisoformat(value['observed_at']) != at):
                    raise ValueError('event_archive_payload_key_mismatch')
                value.update(id=UUID(bytes=snapshot_id),event_id=UUID(value['event_id']),observed_at=at)
                yield SimpleNamespace(**value)
            else:
                yield UUID(bytes=snapshot_id),at


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache',required=True)
    parser.add_argument('--destination',required=True)
    parser.add_argument('--progress',required=True)
    args=parser.parse_args()
    print(json.dumps(build(args.cache,args.destination,args.progress),sort_keys=True))
