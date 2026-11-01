"""Bounded chronological DB export for exhaustive local tennis snapshot audits."""
from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from uuid import UUID
import zlib

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from db.connection import SessionLocal
from db.models import Event


def read_state(path):
    with sqlite3.connect(path, timeout=1) as db:
        row = db.execute('SELECT value FROM metadata WHERE key=?', ('state',)).fetchone()
        return json.loads(row[0]) if row else {}


def prepare(path, event_ids, *, cutoff, timeout=30, page_size=500):
    """Commit each page together with its cursor; interruptions cannot skip rows."""
    membership_hash = hashlib.sha256('\n'.join(event_ids).encode()).hexdigest()
    with SessionLocal() as session:
        session.execute(text(f"SET LOCAL statement_timeout='{timeout}s'"))
        headers = session.query(Event.id, Event.first_seen_at, Event.created_at).filter(
            Event.id.in_([UUID(value) for value in event_ids])).all()
    bounds = {str(row.id): row.first_seen_at or row.created_at for row in headers}
    if not bounds:
        lower = cutoff
    else:
        lower = min(bounds.values())
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA cache_size=-32768')
        db.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS snapshots (event_id TEXT,observed_at TEXT,snapshot_id TEXT,payload BLOB)')
        row = db.execute('SELECT value FROM metadata WHERE key=?', ('state',)).fetchone()
        state = json.loads(row[0]) if row else {'cutoff':cutoff.isoformat(),
            'event_ids_sha256':membership_hash,'source_rows':0,'saved_rows':0,'cursor':None,'complete':False}
        if state['cutoff'] != cutoff.isoformat() or state['event_ids_sha256'] != membership_hash:
            raise ValueError('snapshot_cache_frozen_input_mismatch')
        if state['complete']:
            return state
        while True:
            cursor = state['cursor']
            range_start = datetime.fromisoformat(state['range_start']) if state.get('range_start') else lower
            query_lower = datetime.fromisoformat(cursor[0]) if cursor else range_start
            range_end = query_lower.replace(hour=0,minute=0,second=0,microsecond=0) + timedelta(days=1)
            effective_page_size = state.get('page_size', min(page_size,2000))
            try:
                with SessionLocal() as session:
                    session.execute(text(f"SET LOCAL statement_timeout='{timeout}s'"))
                    rows = session.execute(text("""
                    SELECT id,observed_at,event_id,status,score,current_period,raw_hash,raw_object_key,
                      CASE WHEN status='live' THEN score_detail ELSE '{}'::jsonb END AS score_detail,
                      CASE WHEN status='live' THEN sport_state ELSE '{}'::jsonb END AS sport_state
                    FROM event_state_snapshots
                    WHERE sport_code='tennis' AND observed_at>=:lower AND observed_at<=:cutoff
                      AND observed_at<:range_end
                """ + (' AND (observed_at,id)>(:cursor_time,:cursor_id)' if cursor else '') + """
                    ORDER BY observed_at,id LIMIT :page_size
                """), {'lower':query_lower,'range_end':range_end,
                         'cutoff':cutoff,'page_size':effective_page_size,
                         'cursor_time':datetime.fromisoformat(cursor[0]) if cursor else None,
                         'cursor_id':UUID(cursor[1]) if cursor else None}).all()
            except SQLAlchemyError as exc:
                if getattr(getattr(exc,'orig',None),'pgcode',None) != '57014' or effective_page_size <= 25:
                    raise
                state['page_size'] = max(25,effective_page_size//2)
                db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',('state',json.dumps(state)))
                db.commit()
                print(json.dumps({'snapshot_export_retry_page_size':state['page_size']}),flush=True)
                continue
            values = []
            for row in rows:
                event_id = str(row.event_id)
                if event_id not in bounds or row.observed_at < bounds[event_id]:
                    continue
                payload = dict(row._mapping)
                payload.update(id=str(row.id),event_id=event_id,observed_at=row.observed_at.isoformat())
                values.append((event_id,payload['observed_at'],payload['id'],
                               zlib.compress(json.dumps(payload,ensure_ascii=False).encode(),1)))
            if rows:
                state['cursor'] = [rows[-1].observed_at.isoformat(),str(rows[-1].id)]
            state['source_rows'] += len(rows)
            state['saved_rows'] += len(values)
            is_last_page = len(rows) < effective_page_size
            done = is_last_page and range_end > cutoff
            if is_last_page and not done:
                state['range_start'] = range_end.isoformat()
                state['cursor'] = None
                state['page_size'] = min(page_size,2000)
            db.executemany('INSERT INTO snapshots VALUES (?,?,?,?)',values)
            db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',('state',json.dumps(state)))
            db.commit()
            if done:
                break
            if state['source_rows'] % 10000 == 0:
                print(json.dumps({'snapshot_export':state}),flush=True)
        db.execute('CREATE INDEX IF NOT EXISTS snapshot_event_time ON snapshots(event_id,observed_at,snapshot_id)')
        state['complete'] = True
        db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',('state',json.dumps(state)))
        db.commit()
        print(json.dumps({'snapshot_export':state}),flush=True)
        return state


def iter_event(path,event_id,*,payload=False):
    state = read_state(path)
    if not state.get('complete'):
        raise ValueError('snapshot_cache_incomplete')
    archive = os.environ.get('TENNIS_FULL_EVENT_ARCHIVE')
    if archive:
        manifest = json.loads((Path(archive)/'manifest.json').read_text())
        if (manifest.get('status') != 'complete' or manifest.get('cutoff') != state.get('cutoff')
                or manifest.get('event_ids_sha256') != state.get('event_ids_sha256')
                or manifest.get('records') != state.get('saved_rows')):
            raise ValueError('event_archive_frozen_input_mismatch')
        from tennis_event_archive import iter_event as iter_archived_event
        yield from iter_archived_event(archive,event_id,payload=payload)
        return
    with sqlite3.connect(path) as db:
        fields = 'payload' if payload else 'snapshot_id,observed_at'
        for row in db.execute(f'SELECT {fields} FROM snapshots WHERE event_id=? ORDER BY observed_at,snapshot_id',(str(event_id),)):
            if payload:
                value = json.loads(zlib.decompress(row[0]))
                value.update(id=UUID(value['id']),event_id=UUID(value['event_id']),
                             observed_at=datetime.fromisoformat(value['observed_at']))
                yield SimpleNamespace(**value)
            else:
                yield UUID(row[0]),datetime.fromisoformat(row[1])
