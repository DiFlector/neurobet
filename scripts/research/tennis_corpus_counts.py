"""Resumable bounded metadata counts for the frozen tennis PBP corpus."""
from __future__ import annotations

import json
import sqlite3
from uuid import UUID
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from db.connection import SessionLocal


def compute(path, *, cutoff, timeout=30, page_size=500):
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS source_ids (source_id TEXT PRIMARY KEY)')
        row=db.execute('SELECT value FROM metadata WHERE key=?',('state',)).fetchone()
        state=json.loads(row[0]) if row else {'cutoff':cutoff.isoformat(),'table':'documents',
            'cursor':None,'documents_total':0,'availability_unknown_documents':0,
            'parsed_versions_total':0,'availability_unknown_versions':0,'source_matches_total':0,
            'scanned_documents':0,'scanned_versions':0,'complete':False}
        if state['cutoff']!=cutoff.isoformat(): raise ValueError('corpus_count_cutoff_mismatch')
        if state['complete']: return state
        while True:
            table=state['table']
            table_name='tennis_point_documents' if table=='documents' else 'tennis_point_history_versions'
            fields='id, retrieved_at, available_at' + (', source_match_id' if table=='versions' else '')
            cursor=state['cursor']
            size=state.get('page_size',page_size)
            try:
                with SessionLocal() as session:
                    session.execute(text(f"SET LOCAL statement_timeout='{timeout}s'"))
                    query=(f'SELECT {fields} FROM {table_name} WHERE retrieved_at <= :cutoff '+
                        ('AND id > :cursor ' if cursor else '')+'ORDER BY id LIMIT :page_size')
                    rows=session.execute(text(query),{'cutoff':cutoff,'cursor':UUID(cursor) if cursor else None,
                        'page_size':size}).all()
            except SQLAlchemyError as exc:
                if getattr(getattr(exc,'orig',None),'pgcode',None)!='57014' or size<=25:
                    raise
                state['page_size']=max(25,size//2)
                db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',('state',json.dumps(state)))
                db.commit()
                continue
            if table=='documents':
                state['documents_total']+=len(rows)
                state['availability_unknown_documents']+=sum(r.available_at is None for r in rows)
                state['scanned_documents']+=len(rows)
            else:
                state['parsed_versions_total']+=sum(r.available_at is not None and r.available_at<=cutoff for r in rows)
                state['availability_unknown_versions']+=sum(r.available_at is None for r in rows)
                state['scanned_versions']+=len(rows)
                db.executemany('INSERT OR IGNORE INTO source_ids VALUES (?)',
                    [(r.source_match_id,) for r in rows if r.source_match_id is not None])
            if rows: state['cursor']=str(rows[-1].id)
            if len(rows)<size:
                if table=='documents':
                    state.update(table='versions',cursor=None,page_size=page_size)
                else:
                    state['source_matches_total']=db.execute('SELECT count(*) FROM source_ids').fetchone()[0]
                    state['complete']=True
            db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',('state',json.dumps(state)))
            db.commit()
            if state['complete']: return state


def read_state(path):
    with sqlite3.connect(path,timeout=1) as db:
        row=db.execute('SELECT value FROM metadata WHERE key=?',('state',)).fetchone()
        return json.loads(row[0]) if row else {}
