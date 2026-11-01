from datetime import datetime,timezone,timedelta
from pathlib import Path
import json
import sqlite3
import sys
from uuid import UUID
import zlib

import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts/research'))
import tennis_event_archive as archive
import tennis_snapshot_cache as cache


def test_archive_groups_every_snapshot_and_preserves_time_and_evidence(tmp_path,monkeypatch):
    dbpath=tmp_path/'cache.sqlite'
    when=datetime(2026,10,14,23,59,59,999999,tzinfo=timezone.utc)
    events=[UUID(int=10),UUID(int=2**120)]
    data=[(events[1],UUID(int=3),when+timedelta(microseconds=1)),
          (events[0],UUID(int=1),when),
          (events[1],UUID(int=2),when)]
    state={'complete':True,'saved_rows':3,'event_ids_sha256':'fixed','cutoff':(when+timedelta(days=1)).isoformat()}
    with sqlite3.connect(dbpath) as db:
        db.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT)')
        db.execute('CREATE TABLE snapshots(event_id TEXT,observed_at TEXT,snapshot_id TEXT,payload BLOB)')
        db.execute('INSERT INTO metadata VALUES (?,?)',('state',json.dumps(state)))
        for e,s,t in data:
            row={'event_id':str(e),'id':str(s),'observed_at':t.isoformat(),'raw_hash':'a'*64,
                 'score':'0:0','sport_state':{'points_a':15}}
            db.execute('INSERT INTO snapshots VALUES (?,?,?,?)',(str(e),t.isoformat(),str(s),zlib.compress(json.dumps(row).encode())))
    destination=tmp_path/'event_archive'
    result=archive.build(dbpath,destination,tmp_path/'progress.json')
    assert result['records']==3 and result['events_with_snapshots']==2
    assert archive.build(dbpath,destination,tmp_path/'progress.json')==result
    monkeypatch.setenv('TENNIS_FULL_EVENT_ARCHIVE',str(destination))
    assert list(cache.iter_event(dbpath,events[1]))==[(UUID(int=2),when),(UUID(int=3),when+timedelta(microseconds=1))]
    payloads=list(cache.iter_event(dbpath,events[1],payload=True))
    assert payloads[0].sport_state=={'points_a':15} and payloads[0].raw_hash=='a'*64
    assert list(cache.iter_event(dbpath,UUID(int=777)))==[]
    filename=destination/(str(events[1])+'.bin')
    original=filename.read_bytes()
    altered=bytearray(original); altered[15]^=1
    filename.write_bytes(altered)
    with pytest.raises(ValueError,match='payload_key_mismatch'):
        list(cache.iter_event(dbpath,events[1],payload=True))
    filename.write_bytes(original)
    filename.unlink()
    with pytest.raises(ValueError,match='expected_file_missing'):
        list(cache.iter_event(dbpath,events[1]))
    filename.write_bytes(original)
    broken={**result,'records':4}; (destination/'manifest.json').write_text(json.dumps(broken))
    with pytest.raises(ValueError,match='frozen_input_mismatch'):
        list(cache.iter_event(dbpath,events[1]))
