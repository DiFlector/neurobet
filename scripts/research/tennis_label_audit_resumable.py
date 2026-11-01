"""Checkpointed full-database tennis PIT audit using bounded event/snapshot pages."""
from __future__ import annotations

from collections import Counter, defaultdict
from bisect import bisect_right
from datetime import datetime, timezone
import hashlib
import heapq
import json
import os
import sqlite3
from pathlib import Path
import time
import uuid

from sqlalchemy import text, tuple_

from db.connection import SessionLocal
from db.models import Event, EventStateSnapshot, TennisPointDocument, TennisPointHistoryVersion
from tennis_label_audit import (
    _training_gate_status,
    _walk_forward_admission,
    _write_audit_jsonl,
    _write_training_examples,
    build_report,
    _official_winner,
)


CHECKPOINT_VERSION = 9


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                         encoding="utf-8")
    os.replace(temporary, path)


def _append_counts(target: dict, source: dict) -> None:
    """Recursively sum integer counters and retain stable descriptive values."""
    for key, value in source.items():
        current = target.get(key)
        if isinstance(value, dict):
            if not isinstance(current, dict):
                target[key] = {}
            _append_counts(target[key], value)
        elif isinstance(value, int) and not isinstance(value, bool):
            target[key] = int(current or 0) + value
        elif current is None and value is not None:
            target[key] = value


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _example_sort_key(row: dict):
    return (row["target"], row["fonbet_event_id"], row.get("snapshot_id") or "",
            int(row["label"]))


def _finalization_progress(path, stage, done):
    for parent in [path,*path.parents]:
        if (parent/'checkpoint.json').exists():
            _atomic_json(parent/'finalization_progress.json', {'stage':stage,'done':done})
            break


def _cached_sorted_output(output, inputs):
    metadata=output.with_suffix(output.suffix+'.meta.json')
    if not output.is_file() or not metadata.is_file():
        return False
    try:
        saved=json.loads(metadata.read_text())
        return (saved['inputs']==[_artifact_fingerprint(p) for p in inputs]
                and saved['output']==_artifact_fingerprint(output))
    except (OSError,ValueError,KeyError):
        return False


def _record_sorted_output(output,inputs):
    _atomic_json(output.with_suffix(output.suffix+'.meta.json'),{
        'inputs':[_artifact_fingerprint(p) for p in inputs],
        'output':_artifact_fingerprint(output)})


def _sort_example_files(paths: list[Path], merge_dir: Path) -> list[Path]:
    """Sort shards and bounded merge groups, retaining completed work on resume."""
    merge_dir.mkdir(parents=True, exist_ok=True)
    sorted_paths = []
    for number, source in enumerate(paths, 1):
        sorted_path = merge_dir / f"{number:06d}.jsonl"
        if not _cached_sorted_output(sorted_path,[source]):
            rows = _read_jsonl(source)
            rows.sort(key=_example_sort_key)
            temporary = sorted_path.with_suffix(".jsonl.tmp")
            with temporary.open("w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            os.replace(temporary, sorted_path)
            _record_sorted_output(sorted_path,[source])
            _finalization_progress(merge_dir,'sort_'+merge_dir.name,number)
        sorted_paths.append(sorted_path)
    fan_in = 64
    level = 0
    while len(sorted_paths) > fan_in:
        level += 1
        next_paths = []
        for offset in range(0,len(sorted_paths),fan_in):
            inputs = sorted_paths[offset:offset+fan_in]
            output = merge_dir/f'merge_{level:02d}_{offset//fan_in:06d}.jsonl'
            if not _cached_sorted_output(output,inputs):
                temporary = output.with_suffix('.jsonl.tmp')
                handles = [path.open(encoding='utf-8') for path in inputs]
                try:
                    iterators = [(json.loads(line) for line in handle if line.strip()) for handle in handles]
                    with temporary.open('w',encoding='utf-8') as dest:
                        for row in heapq.merge(*iterators,key=_example_sort_key):
                            dest.write(json.dumps(row,ensure_ascii=False,sort_keys=True)+'\n')
                    os.replace(temporary,output)
                finally:
                    for handle in handles: handle.close()
                _record_sorted_output(output,inputs)
                _finalization_progress(merge_dir,'merge_'+merge_dir.name,offset//fan_in+1)
            next_paths.append(output)
        sorted_paths = next_paths
    return sorted_paths


def _write_merged_training_examples(sorted_paths: list[Path], output_dir: Path) -> dict:
    """Merge with resumable input offsets and a durable output checkpoint."""
    output_dir.mkdir(parents=True,exist_ok=True)
    fingerprints=[_artifact_fingerprint(p) for p in sorted_paths]
    manifest_path=output_dir/'merge_manifest.json'
    if manifest_path.exists():
        saved=json.loads(manifest_path.read_text())
        result=saved['result']; path=Path(result['path'])
        if saved.get('inputs')==fingerprints and path.is_file() and saved.get('output')==_artifact_fingerprint(path):
            return result
    temporary=output_dir/'tennis_training_examples_streamed.jsonl.tmp'
    state_path=output_dir/'merge_state.json'
    state=json.loads(state_path.read_text()) if state_path.exists() else {}
    if state.get('inputs')!=fingerprints or not temporary.exists():
        state={'inputs':fingerprints,'offsets':[0]*len(sorted_paths),'bytes':0,'count':0}
        temporary.write_bytes(b'')
        _atomic_json(state_path,state)
    handles=[p.open('rb') for p in sorted_paths]
    heap=[]
    def push(index):
        handle=handles[index]
        while True:
            position=handle.tell(); line=handle.readline()
            if not line:
                state['offsets'][index]=position
                return
            if line.strip():
                row=json.loads(line)
                state['offsets'][index]=position
                heapq.heappush(heap,(_example_sort_key(row),index,row))
                return
    try:
        for index,handle in enumerate(handles):
            handle.seek(state['offsets'][index]);push(index)
        with temporary.open('r+b') as dest:
            dest.truncate(state['bytes']);dest.seek(state['bytes'])
            while heap:
                _,index,row=heapq.heappop(heap)
                rendered=(json.dumps(row,ensure_ascii=False,sort_keys=True)+'\n').encode('utf-8')
                dest.write(rendered);state['count']+=1
                state['offsets'][index]=handles[index].tell()
                push(index)
                if state['count']%10000==0:
                    dest.flush();os.fsync(dest.fileno());state['bytes']=dest.tell()
                    _atomic_json(state_path,state)
                    _finalization_progress(output_dir,'stream_'+output_dir.name,state['count'])
            dest.flush();os.fsync(dest.fileno());state['bytes']=dest.tell()
            _atomic_json(state_path,state)
    finally:
        for handle in handles: handle.close()
    logical=hashlib.sha256();file_digest=hashlib.sha256();count=0
    with temporary.open('rb') as handle:
        for line in handle:
            file_digest.update(line)
            row=json.loads(line)
            compact=json.dumps(row,sort_keys=True,separators=(',',':')).encode('utf-8')
            logical.update((b'\n' if count else b'')+compact);count+=1
    if count!=state['count']: raise ValueError('merged_output_count_mismatch')
    final_path=output_dir/f'tennis_training_examples_{logical.hexdigest()[:12]}.jsonl'
    os.replace(temporary,final_path)
    result={'path':str(final_path),'examples':count,'sha256':file_digest.hexdigest(),
            'logical_digest':logical.hexdigest()}
    _atomic_json(manifest_path,{'inputs':fingerprints,'output':_artifact_fingerprint(final_path),'result':result})
    _finalization_progress(output_dir,'stream_complete_'+output_dir.name,count)
    return result



def _scan_example_files(paths, database_path):
    """Aggregate whole-match timing in transactional, resumable input chunks."""
    database_path.parent.mkdir(parents=True,exist_ok=True)
    fingerprints=[_artifact_fingerprint(p) for p in paths]
    with sqlite3.connect(database_path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS records (target TEXT,event_id TEXT,value TEXT,PRIMARY KEY(target,event_id))')
        row=db.execute('SELECT value FROM metadata WHERE key=?',('state',)).fetchone()
        state=json.loads(row[0]) if row else {'inputs':fingerprints,'file':0,'offset':0,'rows':0,'complete':False}
        if state['inputs']!=fingerprints:
            raise ValueError('finalization_scan_inputs_changed')
        while not state['complete']:
            if state['file']>=len(paths):
                state['complete']=True
                db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',('state',json.dumps(state)))
                db.commit();break
            path=paths[state['file']]
            groups={}
            with path.open('rb') as handle:
                handle.seek(state['offset'])
                lines=0
                while lines<5000:
                    line=handle.readline()
                    if not line: break
                    if not line.strip(): continue
                    row=json.loads(line); key=(row['target'],row['fonbet_event_id'])
                    item=groups.setdefault(key,{'target':key[0],'event_id':key[1],'labels':[],
                        'forecast_at':None,'label_available_at':None,'missing_fields':[],'uncertified':False})
                    label=int(row['label'])
                    if label not in item['labels']: item['labels'].append(label)
                    forecast=datetime.fromisoformat(row['snapshot_observed_at'].replace('Z','+00:00'))
                    old=datetime.fromisoformat(item['forecast_at']) if item['forecast_at'] else None
                    if old is None or forecast<old: item['forecast_at']=forecast.isoformat()
                    if row.get('label_available_at'):
                        available=datetime.fromisoformat(row['label_available_at'].replace('Z','+00:00'))
                        old=datetime.fromisoformat(item['label_available_at']) if item['label_available_at'] else None
                        if old is None or available>old: item['label_available_at']=available.isoformat()
                    item['missing_fields']=sorted(set(item['missing_fields'])|set(row.get('feature_missing_fields',[])))
                    item['uncertified'] |= (row.get('feature_availability',{}).get('certified') is not True
                                            or bool(row.get('feature_missing_fields')))
                    lines+=1
                state['offset']=handle.tell()
            for key,item in groups.items():
                previous=db.execute('SELECT value FROM records WHERE target=? AND event_id=?',key).fetchone()
                if previous:
                    old=json.loads(previous[0]); item['labels']=sorted(set(old['labels'])|set(item['labels']))
                    item['missing_fields']=sorted(set(old['missing_fields'])|set(item['missing_fields']))
                    item['uncertified'] |= old['uncertified']
                    times=[datetime.fromisoformat(t) for t in (old['forecast_at'],item['forecast_at']) if t]
                    item['forecast_at']=min(times).isoformat() if times else None
                    times=[datetime.fromisoformat(t) for t in (old['label_available_at'],item['label_available_at']) if t]
                    item['label_available_at']=max(times).isoformat() if times else None
                db.execute('INSERT OR REPLACE INTO records VALUES (?,?,?)',(key[0],key[1],json.dumps(item)))
            state['rows']+=lines
            if lines<5000: state['file']+=1;state['offset']=0
            db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',('state',json.dumps(state)))
            db.commit()
            if lines: _finalization_progress(database_path.parent,'scan_examples',state['rows'])
        return [json.loads(value) for (value,) in db.execute('SELECT value FROM records')]

def _candidate_event_page(cursor: str | None, *, cutoff: datetime, timeout: int, limit: int,
                          discipline_scope: str = "singles") -> list:
    with SessionLocal() as session:
        session.execute(text(f"SET LOCAL statement_timeout = '{timeout}s'"))
        query = session.query(Event.id).filter(
            Event.source == "fonbet", Event.sport_code == "tennis",
            Event.created_at <= cutoff,
        )
        if discipline_scope == "doubles":
            query = query.filter(Event.status == "finished", Event.finished_at.is_not(None),
                (Event.participant_a_name.like("%/%") | Event.participant_b_name.like("%/%")))
        if cursor:
            query = query.filter(Event.id > uuid.UUID(cursor))
        return [row[0] for row in query.order_by(Event.id).limit(limit).all()]


def _freeze_event_ids(cutoff, timeout, discipline_scope="singles"):
    with SessionLocal() as session:
        session.execute(text(f"SET LOCAL statement_timeout='{timeout}s'"))
        query = session.query(Event.id).filter(
            Event.source == "fonbet", Event.sport_code == "tennis", Event.created_at <= cutoff,
        ).order_by(Event.id)
        if discipline_scope == "doubles":
            query = session.query(Event.id).filter(
                Event.source == "fonbet", Event.sport_code == "tennis", Event.created_at <= cutoff,
                Event.status == "finished", Event.finished_at.is_not(None),
                (Event.participant_a_name.like("%/%") | Event.participant_b_name.like("%/%")),
            ).order_by(Event.id)
        return [str(row.id) for row in query.all()]


def _write_snapshot_index(event_ids: list, path: Path, *, cutoff: datetime,
                          timeout: int, page_size: int) -> int:
    """Persist all indexed event snapshots for one event page, with keyset paging."""
    # Small pages bound cold heap reads on the shared Timescale volume.
    page_size = min(page_size, 500)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    count = 0
    with SessionLocal() as session:
        session.execute(text(f"SET LOCAL statement_timeout = '{timeout}s'"))
        first_seen_at = dict(session.query(Event.id, Event.first_seen_at).filter(
            Event.id.in_(event_ids), Event.created_at <= cutoff).all())
    with temporary.open("w", encoding="utf-8") as handle:
        for event_id in event_ids:
            handle.write(json.dumps({"fonbet_event_id": str(event_id)}, separators=(",", ":")) + "\n")
        cache_path = os.environ.get('TENNIS_FULL_SNAPSHOT_CACHE')
        if cache_path:
            from tennis_snapshot_cache import iter_event
            for event_id in event_ids:
                for snapshot_id, observed_at in iter_event(cache_path,event_id):
                    handle.write(json.dumps({'fonbet_event_id':str(event_id),'snapshot_id':str(snapshot_id),
                        'snapshot_observed_at':observed_at.isoformat()},separators=(',',':'))+'\n')
                    count += 1
            handle.flush(); os.fsync(handle.fileno())
            os.replace(temporary,path)
            return count
        # Query each event through the hypertable's (event_id, observed_at)
        # index. A cross-event ORDER BY encourages TimescaleDB to merge/sort
        # millions of rows across chunks before LIMIT can take effect.
        for event_id in event_ids:
            lower_bound = first_seen_at.get(event_id)
            if lower_bound is None:
                continue
            cursor = None
            while True:
                with SessionLocal() as session:
                    session.execute(text(f"SET LOCAL statement_timeout = '{timeout}s'"))
                    query = session.query(EventStateSnapshot.id, EventStateSnapshot.observed_at,
                        EventStateSnapshot.event_id).filter(
                            EventStateSnapshot.event_id == event_id,
                            EventStateSnapshot.sport_code == "tennis",
                            EventStateSnapshot.observed_at >= lower_bound,
                            EventStateSnapshot.observed_at <= cutoff,
                        )
                    if cursor is not None:
                        query = query.filter(tuple_(EventStateSnapshot.observed_at,
                            EventStateSnapshot.id) > tuple_(*cursor))
                    rows = query.order_by(EventStateSnapshot.observed_at,
                        EventStateSnapshot.id).limit(page_size).all()
                if not rows:
                    break
                for row in rows:
                    handle.write(json.dumps({"fonbet_event_id": str(row.event_id),
                        "snapshot_id": str(row.id), "snapshot_observed_at": row.observed_at.isoformat()},
                        separators=(",", ":")) + "\n")
                count += len(rows)
                last = rows[-1]
                cursor = (last.observed_at, last.id)
                if len(rows) < page_size:
                    break
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    return count


def _artifact_fingerprint(path: Path):
    stat = path.stat()
    return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]


def _completed_batch_is_valid(batch: dict, root: Path) -> bool:
    names = ["index_path", "report_path", "examples_path", "diagnostics_path"]
    if batch.get('rejected_examples_path'):
        names.append('rejected_examples_path')
    verified = dict(batch.get('artifact_stats', {}))
    for name in names:
        value = batch.get(name)
        if not value:
            return False
        path = root / value
        if not path.is_file():
            return False
        before = _artifact_fingerprint(path)
        # On filesystems with coarse timestamp updates, a same-size rewrite
        # can retain ctime within the current tick. Do not trust a cached
        # fingerprint while it is recent enough to share that tick.
        ctime_age_ns = time.time_ns() - before[-1]
        if verified.get(name) != before or ctime_age_ns < 1_000_000_000:
            if _sha256(path) != batch.get(name.replace("_path", "_sha256")):
                return False
            if _artifact_fingerprint(path) != before:
                return False
            verified[name] = before
    batch['artifact_stats'] = verified
    return True


def _load_full_snapshot_batch(event_ids, index_path, *, cutoff, timeout, page_size):
    """Read event/time ranges directly; avoid millions of random PK lookups."""
    from tennis_label_audit import _read_saved_snapshot_cohort
    page_size = min(page_size, 500)
    cohort = _read_saved_snapshot_cohort(str(index_path))
    cache_path = os.environ.get('TENNIS_FULL_SNAPSHOT_CACHE')
    if cache_path:
        from tennis_snapshot_cache import iter_event
        return [row for event_id in event_ids for row in iter_event(cache_path,event_id,payload=True)
                if cohort['keys'].get((row.id,row.observed_at)) == row.event_id]
    with SessionLocal() as session:
        session.execute(text(f"SET LOCAL statement_timeout='{timeout}s'"))
        events = session.query(Event).filter(Event.id.in_(event_ids)).all()
        bounds = {e.id:(e.first_seen_at or e.created_at,
                       str(e.source_event_id).isdigit() and _official_winner(e)[0] in (0,1)) for e in events}
    output = []
    for event_id in event_ids:
        lower, include_features = bounds[event_id]
        cursor = None
        while True:
            with SessionLocal() as session:
                session.execute(text(f"SET LOCAL statement_timeout='{timeout}s'"))
                rows = session.execute(text("""
                    SELECT id,observed_at,event_id,status,score,current_period,raw_hash,raw_object_key,
                      CASE WHEN :features AND status='live' THEN score_detail ELSE '{}'::jsonb END AS score_detail,
                      CASE WHEN :features AND status='live' THEN sport_state ELSE '{}'::jsonb END AS sport_state
                    FROM event_state_snapshots
                    WHERE event_id=:event_id AND sport_code='tennis'
                      AND observed_at>=:lower AND observed_at<=:cutoff
                """ + (" AND (observed_at,id)>(:cursor_time,:cursor_id)" if cursor else "") + """
                    ORDER BY observed_at,id LIMIT :page_size
                """), {'features':include_features,'event_id':event_id,'lower':lower,'cutoff':cutoff,
                        'cursor_time':cursor[0] if cursor else None,'cursor_id':cursor[1] if cursor else None,
                        'page_size':page_size}).all()
            for row in rows:
                if cohort['keys'].get((row.id,row.observed_at)) == row.event_id:
                    output.append(row)
            if not rows or len(rows)<page_size:
                break
            cursor = (rows[-1].observed_at,rows[-1].id)
    return output


def run_resumable(*, output_path: str, checkpoint_dir: str, cutoff: datetime,
                  resume: bool = False, event_batch_size: int = 100,
                  snapshot_page_size: int = 500, query_timeout_seconds: int = 30,
                  max_runtime_seconds: int = 900, discipline_scope: str = "singles") -> dict:
    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise ValueError("audit_cutoff_requires_timezone")
    if not 1 <= event_batch_size <= 100 or not 1 <= snapshot_page_size <= 5000:
        raise ValueError("audit_batch_size_out_of_bounds")
    if not 1 <= query_timeout_seconds <= 30 or not 1 <= max_runtime_seconds <= 900:
        raise ValueError("audit_runtime_or_query_limit_out_of_bounds")
    if discipline_scope not in {"singles", "doubles"}:
        raise ValueError("discipline_scope_must_be_singles_or_doubles")
    cutoff = cutoff.astimezone(timezone.utc)
    root = Path(checkpoint_dir).resolve()
    checkpoint_path = root / "checkpoint.json"
    root.mkdir(parents=True, exist_ok=True)
    if checkpoint_path.exists():
        if not resume:
            raise ValueError("checkpoint_exists_pass_resume")
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if checkpoint.get("version") != CHECKPOINT_VERSION:
            raise ValueError("checkpoint_version_mismatch")
        if checkpoint.get("cutoff") != cutoff.isoformat():
            raise ValueError("checkpoint_cutoff_mismatch")
        settings = {"event_batch_size": event_batch_size,
                    "snapshot_page_size": snapshot_page_size,
                    "query_timeout_seconds": query_timeout_seconds,
                    "discipline_scope": discipline_scope}
        saved_settings = checkpoint.get("settings") or {}
        legacy_singles_settings = discipline_scope == "singles" and "discipline_scope" not in saved_settings
        if (saved_settings != ({key: value for key, value in settings.items()
                               if key != "discipline_scope"} if legacy_singles_settings else settings)):
            raise ValueError("checkpoint_settings_mismatch")
        checkpoint["settings"].setdefault("discipline_scope", "singles")
        # Persist completed verification incrementally, so the 15-minute
        # budget cannot force an ever-growing rehash from the beginning.
        for number,batch in enumerate(checkpoint.get("completed_batches", []),1):
            if not _completed_batch_is_valid(batch,root):
                raise ValueError("checkpoint_artifact_digest_mismatch")
            if number % 20 == 0:
                _atomic_json(checkpoint_path,checkpoint)
        _atomic_json(checkpoint_path,checkpoint)
    else:
        if resume:
            raise ValueError("checkpoint_missing")
        checkpoint = {"version": CHECKPOINT_VERSION, "cutoff": cutoff.isoformat(),
            "settings": {"event_batch_size": event_batch_size,
                         "snapshot_page_size": snapshot_page_size,
                         "query_timeout_seconds": query_timeout_seconds,
                         "discipline_scope": discipline_scope},
            "created_at": datetime.now(timezone.utc).isoformat(), "status": "running",
            "event_cursor": None, "pending_batch": None, "completed_batches": [],
            "next_batch_number": 1}
        frozen_ids = _freeze_event_ids(cutoff,query_timeout_seconds,discipline_scope)
        checkpoint['frozen_event_ids'] = frozen_ids
        checkpoint['frozen_event_ids_sha256'] = hashlib.sha256('\n'.join(frozen_ids).encode()).hexdigest()
        _atomic_json(checkpoint_path, checkpoint)
    frozen_ids = checkpoint['frozen_event_ids']
    if hashlib.sha256('\n'.join(frozen_ids).encode()).hexdigest() != checkpoint['frozen_event_ids_sha256']:
        raise ValueError('checkpoint_event_universe_digest_mismatch')
    if checkpoint.get("status") == "complete":
        final_path = Path(checkpoint["final_report_path"])
        if _sha256(final_path) != checkpoint["final_report_sha256"]:
            raise ValueError("checkpoint_final_report_digest_mismatch")
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(final_path.read_bytes())
        return json.loads(final_path.read_text(encoding="utf-8"))

    cache_path = os.environ.get('TENNIS_FULL_SNAPSHOT_CACHE')
    if cache_path:
        from tennis_snapshot_cache import prepare
        prepare(cache_path,frozen_ids,cutoff=cutoff,timeout=query_timeout_seconds,page_size=snapshot_page_size)
        checkpoint['snapshot_cache'] = {'path':cache_path,'strategy':'chronological_pages_then_local_event_index'}
        _atomic_json(checkpoint_path,checkpoint)
    started = time.monotonic()
    while time.monotonic() - started < max_runtime_seconds:
        pending = checkpoint.get("pending_batch")
        if pending is None:
            offset = bisect_right(frozen_ids,checkpoint['event_cursor']) if checkpoint.get('event_cursor') else 0
            event_ids = [uuid.UUID(value) for value in frozen_ids[offset:offset+event_batch_size]]
            if not event_ids:
                break
            number = checkpoint["next_batch_number"]
            batch_dir = root / "batches" / f"{number:06d}"
            index_path = batch_dir / "snapshot_index.jsonl"
            checkpoint["pending_batch"] = {"number": number, "event_ids": [str(x) for x in event_ids],
                "index_path": str(index_path.relative_to(root))}
            _atomic_json(checkpoint_path, checkpoint)
            _write_snapshot_index(event_ids, index_path, cutoff=cutoff,
                timeout=query_timeout_seconds, page_size=snapshot_page_size)
            checkpoint["pending_batch"]["index_sha256"] = _sha256(index_path)
            _atomic_json(checkpoint_path, checkpoint)
            pending = checkpoint["pending_batch"]
        batch_dir = root / "batches" / f"{pending['number']:06d}"
        index_path = root / pending["index_path"]
        if not index_path.exists():
            _write_snapshot_index([uuid.UUID(value) for value in pending["event_ids"]], index_path,
                cutoff=cutoff, timeout=query_timeout_seconds, page_size=snapshot_page_size)
            checkpoint["pending_batch"]["index_sha256"] = _sha256(index_path)
            _atomic_json(checkpoint_path, checkpoint)
        if not pending.get("index_sha256"):
            checkpoint["pending_batch"]["index_sha256"] = _sha256(index_path)
            _atomic_json(checkpoint_path, checkpoint)
        if pending.get("index_sha256") and _sha256(index_path) != pending["index_sha256"]:
            raise ValueError("pending_snapshot_index_digest_mismatch")
        with index_path.open() as handle:
            indexed_rows = sum(1 for _ in handle)
        if indexed_rows>20000 and len(pending['event_ids'])>1:
            single_ids = pending['event_ids'][:1]
            single_path = index_path.with_name('snapshot_index_single.jsonl')
            _write_snapshot_index([uuid.UUID(value) for value in single_ids],single_path,
                cutoff=cutoff,timeout=query_timeout_seconds,page_size=snapshot_page_size)
            index_path = single_path
            pending.update(event_ids=single_ids,index_path=str(index_path.relative_to(root)),index_sha256=_sha256(index_path))
            checkpoint['pending_batch'] = pending
            _atomic_json(checkpoint_path,checkpoint)
        batch_dir.mkdir(parents=True, exist_ok=True)
        report_path = batch_dir / "report.json"
        os.environ["TENNIS_TRAINING_MANIFEST_PATH"] = str(batch_dir / "local_manifest.json")
        preloaded = _load_full_snapshot_batch([uuid.UUID(value) for value in pending['event_ids']],index_path,
            cutoff=cutoff,timeout=query_timeout_seconds,page_size=snapshot_page_size)
        report = build_report(snapshot_index_path=str(index_path), preloaded_snapshot_rows=preloaded,
            filter_unusable_examples=True,defer_admission_windows=True,
            training_examples_output_dir=str(batch_dir), audit_cutoff=cutoff,
            event_batch_size=event_batch_size, snapshot_page_size=snapshot_page_size,
            query_timeout_seconds=query_timeout_seconds, discipline_scope=discipline_scope)
        _atomic_json(report_path, report)
        examples_path = root / report["training_examples_manifest"]["path"]
        diagnostics_path = root / report["match_exclusions_manifest"]["path"]
        batch_record = {
            "number": pending["number"], "event_ids": pending["event_ids"],
            "index_path": str(index_path.relative_to(root)), "index_sha256": _sha256(index_path),
            "report_path": str(report_path.relative_to(root)), "report_sha256": _sha256(report_path),
            "examples_path": str(examples_path.relative_to(root)), "examples_sha256": _sha256(examples_path),
            "diagnostics_path": str(diagnostics_path.relative_to(root)),
            "diagnostics_sha256": _sha256(diagnostics_path),
            "snapshot_rows": report["audit_scope"].get("indexed_snapshot_keys", 0),
        }
        rejected_path = Path(report['rejected_examples_manifest']['path'])
        batch_record.update(rejected_examples_path=str(rejected_path.relative_to(root)),
                            rejected_examples_sha256=_sha256(rejected_path))
        # Seal files against changes during hashing and record filesystem
        # identities; later resumes rehash any file whose identity changed.
        if not _completed_batch_is_valid(batch_record,root):
            raise ValueError('completed_batch_artifact_changed_during_sealing')
        checkpoint["completed_batches"].append(batch_record)
        checkpoint["event_cursor"] = pending["event_ids"][-1]
        checkpoint["next_batch_number"] = pending["number"] + 1
        checkpoint["pending_batch"] = None
        checkpoint["updated_at"] = datetime.now(timezone.utc).isoformat()
        _atomic_json(checkpoint_path, checkpoint)
        print(json.dumps({'census_progress':{'completed_batches':len(checkpoint['completed_batches']),
            'completed_events':sum(len(b['event_ids']) for b in checkpoint['completed_batches']),
            'total_events':len(frozen_ids),
            'snapshot_rows':sum(b['snapshot_rows'] for b in checkpoint['completed_batches']),
            'last_cursor':checkpoint['event_cursor']}}),flush=True)
        del preloaded, report

    offset = bisect_right(frozen_ids,checkpoint['event_cursor']) if checkpoint.get('event_cursor') else 0
    next_page = frozen_ids[offset:offset+1] if checkpoint.get('pending_batch') is None else [True]
    if next_page:
        partial = {"generated_at": datetime.now(timezone.utc).isoformat(),
            "audit_scope": {"mode": "full_database", "full_database_census": False,
                "status": "incomplete", "as_of": cutoff.isoformat(),
                "event_as_of_predicate": "events.created_at <= cutoff",
                "snapshot_as_of_predicate": "event.first_seen_at <= observed_at <= cutoff",
                "completed_batches": len(checkpoint["completed_batches"]),
                'total_events':len(frozen_ids),
                'completed_events':sum(len(b['event_ids']) for b in checkpoint['completed_batches']),
                "checkpoint_path": str(checkpoint_path)},
            "training_gate": {"status": "blocked", "windows_evaluated": False,
                "point_in_time_certified": False, "reasons": ["audit_incomplete"]}}
        _atomic_json(Path(output_path), partial)
        checkpoint["status"] = "incomplete"
        _atomic_json(checkpoint_path, checkpoint)
        return partial

    report = _finalize(checkpoint, root=root, output_path=Path(output_path), cutoff=cutoff)
    return report


def _finalize(checkpoint: dict, *, root: Path, output_path: Path, cutoff: datetime) -> dict:
    reports, example_paths, diagnostics = [], [], []
    for batch in checkpoint["completed_batches"]:
        reports.append(json.loads((root / batch["report_path"]).read_text(encoding="utf-8")))
        example_paths.append(root / batch["examples_path"])
        diagnostics.extend(_read_jsonl(root / batch["diagnostics_path"]))
    scanned_ids = [value for batch in checkpoint['completed_batches'] for value in batch['event_ids']]
    if len(scanned_ids)!=len(set(scanned_ids)) or sorted(scanned_ids)!=checkpoint['frozen_event_ids']:
        raise ValueError('full_census_event_membership_incomplete_or_duplicated')
    if {r['fonbet_event_id'] for r in diagnostics} != set(scanned_ids):
        raise ValueError('full_census_event_diagnostics_incomplete')
    discipline_scope = checkpoint.get("settings", {}).get("discipline_scope", "singles")
    aggregate = {"generated_at": datetime.now(timezone.utc).isoformat(),
        "audit_format_version": 6, "alignment_policy": "confirmed_pre_game_score_v1",
        "history_link_schema": "provider_scoped_links_v2",
        "source_scope": f"numeric Fonbet finished tennis {discipline_scope} events; target-specific PIT",
        "dataset_contract": "tennis_odds_free_v2" if discipline_scope == "singles" else "tennis_doubles_odds_free_v1",
        "audit_scope": {"mode": "full_database", "full_database_census": True,
            "discipline_scope": discipline_scope,
            "status": "complete", "as_of": cutoff.isoformat(),
            "event_as_of_predicate": "events.created_at <= cutoff",
            "snapshot_as_of_predicate": "event.first_seen_at <= observed_at <= cutoff",
            "completed_batches": len(reports),
            'total_events':len(checkpoint['frozen_event_ids']),
            'frozen_event_ids_sha256':checkpoint['frozen_event_ids_sha256'],
            'snapshot_payload_read_strategy':checkpoint.get('snapshot_cache',{}).get('strategy','bounded_event_time_pages'),
            "scanned_event_rows": sum(len(batch["event_ids"]) for batch in checkpoint["completed_batches"])},
        "targets": {}, "by_tour": {}, "by_discipline": {}, "by_competition": {},
        "tour_classification_sources": {}, "result_time_evidence": {}, "point_history": {},
        "feature_availability": {}, "snapshot_source_coverage": {}, "exclusions": {},
        "target_diagnostics": {}, "candidate_targets":{},"example_filter":{},"training_gate": {"status": "blocked",
            "required_per_target": {"initial_train_matches": 500,
                "each_validation_window_matches": 100, "holdout_matches": 200,
                "both_classes": True, "point_in_time_clean": True},
            "windows_evaluated": False, "point_in_time_certified": False,
            "chronology_policy": "official_result_source_time_or_exact_previously_observed_final_score_snapshot"}}
    for report in reports:
        for key in ("targets", "by_tour", "by_discipline", "by_competition",
                    "tour_classification_sources", "result_time_evidence", "point_history",
                    "feature_availability", "snapshot_source_coverage", "exclusions","candidate_targets","example_filter"):
            if key in report:
                _append_counts(aggregate[key], report[key])
        for target, values in report.get("target_diagnostics", {}).items():
            if target not in aggregate["target_diagnostics"]:
                aggregate["target_diagnostics"][target] = {
                    "unique_matches_with_parsed_labels": 0,
                    "unique_matches_with_snapshot_aligned_labels": 0,
                    "unique_matches_with_feature_certified_labels": 0,
                    "unique_matches_with_causal_train_labels_by_fold": [],
                    "stage_exclusions": {}, "secondary_exclusions": {}}
            _append_counts(aggregate["target_diagnostics"][target], values)

    # Count corpus volume without loading archived HTML or JSONB event arrays.
    # The audit only reads linked versions in each shard; these totals describe
    # the independently larger source corpus and keep availability unknowns visible.
    timeout = int(checkpoint.get("settings", {}).get("query_timeout_seconds", 30))
    from tennis_corpus_counts import compute as compute_corpus_counts
    corpus = compute_corpus_counts(str(root/'corpus_counts.sqlite'),cutoff=cutoff,timeout=timeout)
    aggregate['point_history'].update(
        documents_total=corpus['documents_total'],
        availability_unknown_documents=corpus['availability_unknown_documents'],
        parsed_versions_total=corpus['parsed_versions_total'],
        availability_unknown_versions=corpus['availability_unknown_versions'],
        source_matches_total=corpus['source_matches_total'],
        confirmed_outcomes_total=None,
        global_history_scan_status='document_and_version_counts_complete_jsonb_outcome_scan_deferred',
    )

    target_rows = {name: set() for name in ("point", "game", "set", "match")}
    target_temporal = defaultdict(dict)
    labels_by_target_event = defaultdict(set)
    missing_fields_by_target_event = defaultdict(set)
    seen_example_events = {name: set() for name in target_rows}
    uncertified_example_events = {name: set() for name in target_rows}
    sorted_paths = _sort_example_files(example_paths, root / "final" / "sorted_batches")
    scan_records = _scan_example_files(sorted_paths,root/'final'/'example_scan.sqlite')
    for record in scan_records:
        target=record['target']; event_id=uuid.UUID(record['event_id'])
        labels=set(record['labels'])
        target_rows[target].update((event_id,label) for label in labels)
        labels_by_target_event[(target,event_id)].update(labels)
        seen_example_events[target].add(event_id)
        missing_fields_by_target_event[(target,event_id)].update(record['missing_fields'])
        if record['uncertified']:
            uncertified_example_events[target].add(event_id)
        target_temporal[target][event_id]={
            'forecast_at':datetime.fromisoformat(record['forecast_at']) if record['forecast_at'] else None,
            'label_available_at':datetime.fromisoformat(record['label_available_at']) if record['label_available_at'] else None}
    event_times = {}
    for per_target in target_temporal.values():
        for event_id, values in per_target.items():
            if values["forecast_at"] is not None:
                event_times[event_id] = min(event_times[event_id],values['forecast_at']) if event_id in event_times else values['forecast_at']

    final_dir = root / "final"
    aggregate["training_examples_manifest"] = _write_merged_training_examples(sorted_paths, final_dir)
    rejected_paths = [root/b['rejected_examples_path'] for b in checkpoint['completed_batches']]
    sorted_rejected = _sort_example_files(rejected_paths,root/'final'/'sorted_rejected_batches')
    aggregate['rejected_feature_examples_manifest'] = _write_merged_training_examples(
        sorted_rejected,final_dir/'rejected_features')
    rejected_by_event = defaultdict(Counter)
    for path in rejected_paths:
        with path.open(encoding='utf-8') as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                key = (row['target'],uuid.UUID(row['fonbet_event_id']))
                rejected_by_event[key].update(row.get('rejection_reasons',[]))
    aggregate['rejected_example_reasons_by_target'] = {}
    for target in target_rows:
        counts = Counter()
        for (row_target,_),reasons in rejected_by_event.items():
            if row_target == target:
                counts.update(reasons)
        aggregate['rejected_example_reasons_by_target'][target] = dict(sorted(counts.items()))
    aggregate["match_exclusions_manifest"] = _write_audit_jsonl(
        sorted(diagnostics, key=lambda row: row["fonbet_event_id"]), str(final_dir),
        "tennis_match_target_diagnostics")
    manifest_path = root / "final" / "tennis_training_manifest.json"
    os.environ["TENNIS_TRAINING_MANIFEST_PATH"] = str(manifest_path)
    walk_forward, manifest = _walk_forward_admission(target_rows, event_times, target_temporal)
    aggregate["training_gate"]["walk_forward"] = walk_forward
    aggregate["training_gate"]["manifest"] = manifest
    for target in target_rows:
        diag = aggregate["target_diagnostics"][target]
        diag["unique_matches_with_causal_train_labels_by_fold"] = [
            fold["train"]["matches"] for fold in walk_forward[target]["folds"]]
    feature = aggregate["feature_availability"]
    aggregate["training_gate"]["windows_evaluated"] = True
    coverage_ok = all(aggregate["targets"][target]["labelled_matches"] >= 500
        and aggregate["targets"][target]["class_a"] > 0
        and aggregate["targets"][target]["class_b"] > 0 for target in target_rows)
    windows_ok = all(value["folds_meet_minimums"] and value["holdout_meets_minimums"]
                     for value in walk_forward.values())
    temporal_ok = all(value["folds"] and all(fold["temporal_precedence"]["passed"]
                         for fold in value["folds"]) for value in walk_forward.values())
    point_in_time_ok = (feature.get("checked_examples", 0) > 0
        and feature.get("uncertified_examples", 0) == 0
        and feature["feature_values_missing_examples"] == 0)
    status, reasons = _training_gate_status(base_coverage_ok=coverage_ok,
        windows_ok=windows_ok, temporal_ok=temporal_ok,
        point_in_time_ok=point_in_time_ok, manifest_result_times_certified=temporal_ok)
    aggregate["training_gate"].update(status=status, reasons=reasons,
        base_coverage_status="sufficient_for_window_assessment" if coverage_ok else "insufficient",
        point_in_time_certified=point_in_time_ok,
        manifest_result_availability_certified=temporal_ok,
        chronology_timestamp_missing_matches=sum(
            temporal.get("label_available_at") is None
            for temporal in target_temporal["match"].values()))
    aggregate['training_gate'].update(
        result_availability_unknown_matches=aggregate['result_time_evidence'].get('unknown_valid_results',0),
        result_time_sources=aggregate['result_time_evidence'].get('sources',{}),
        scheduled_time_used_as_chronology=0,
        unknown_result_time_policy='retain evaluation candidates; purge unknown-time labels from causal training')
    for target in target_rows:
        aggregate["target_diagnostics"][target]["unique_matches_with_feature_certified_labels"] = len(
            seen_example_events[target] - uncertified_example_events[target])
    aggregate["training_gate"]["coverage_by_target"] = {}
    for target, target_walk in walk_forward.items():
        folds = target_walk["folds"]
        first_train = folds[0]["train"]["matches"] if folds else 0
        validation = [fold["validation"]["matches"] for fold in folds]
        holdout = target_walk["holdout"]["matches"]
        aggregate["training_gate"]["coverage_by_target"][target] = {
            "aligned_unique_matches": aggregate["targets"][target]["labelled_matches"],
            "feature_certified_unique_matches": aggregate["target_diagnostics"][target][
                "unique_matches_with_feature_certified_labels"],
            "initial_train_matches": first_train,
            "initial_train_deficit": max(0, 500 - first_train),
            "validation_matches_by_fold": validation,
            "validation_deficits_by_fold": [max(0, 100 - value) for value in validation],
            "holdout_matches": holdout,
            "holdout_deficit": max(0, 200 - holdout),
            "both_classes_by_fold": [fold["train"]["both_classes"]
                                     and fold["validation"]["both_classes"] for fold in folds],
            "holdout_both_classes": target_walk["holdout"]["both_classes"],
        }

    # Add split and causal-train membership to each per-match diagnostic record.
    diagnostic_map = {row["fonbet_event_id"]: row for row in diagnostics}
    for target, target_report in walk_forward.items():
        for fold in target_report["folds"]:
            train_ids = set(fold.get("train_candidates", {}).get("event_ids", []))
            # The manifest is the source of whole-match membership; target_train
            # IDs include only labels that were available at that fold boundary.
            manifest_fold = next((item for item in json.loads(Path(manifest["path"]).read_text()).get("folds", [])
                                  if item["fold"] == fold["fold"]), {})
            target_train = set(manifest_fold.get("target_train_event_ids", {}).get(target, []))
            all_train = set(manifest_fold.get("train_event_ids", []))
            validation = set(manifest_fold.get("validation_event_ids", []))
            for event_id in all_train | validation:
                key = str(event_id)
                record = diagnostic_map.get(key)
                if record is None:
                    continue
                diagnostics_for_target = record["targets"][target]
                diagnostics_for_target.setdefault("fold_membership", []).append({
                    "fold": fold["fold"],
                    "split": "train" if key in all_train else "validation",
                    "causal_train": key in target_train,
                })
                if key in all_train and (target, uuid.UUID(key)) in labels_by_target_event:
                    if key not in target_train:
                        temporal = target_temporal[target].get(uuid.UUID(key), {})
                        reason = "label_time_unknown" if temporal.get("label_available_at") is None \
                            else "label_received_after_validation_boundary"
                        additional = diagnostics_for_target.setdefault("additional_reasons", [])
                        if reason not in additional:
                            additional.append(reason)
                            counts = aggregate["target_diagnostics"][target]["secondary_exclusions"]
                            counts[reason] = counts.get(reason, 0) + 1
    state_fields = {"sets_a", "sets_b", "games_a", "games_b", "points_a", "points_b",
                    "server_a", "server_b", "server_unknown", "is_tiebreak"}
    for (target,event_id),reasons in rejected_by_event.items():
        record = diagnostic_map.get(str(event_id))
        if record is None:
            continue
        details = record['targets'][target]
        details['rejected_example_reasons'] = dict(sorted(reasons.items()))
        missing = {reason.split(':',1)[1] for reason in reasons if reason.startswith('feature_value_missing:')}
        if missing:
            details['missing_feature_fields'] = sorted(missing)
            reason = 'incomplete_state' if state_fields.intersection(missing) else 'feature_fields_missing'
            additional = details.setdefault('additional_reasons',[])
            if reason not in additional:
                additional.append(reason)
                secondary = aggregate['target_diagnostics'][target]['secondary_exclusions']
                secondary[reason] = secondary.get(reason,0)+1
            if event_id not in seen_example_events[target] and details.get('status') == 'feature_pit_not_certified':
                details['status'] = reason
                if reason in additional:
                    additional.remove(reason)
                    secondary = aggregate['target_diagnostics'][target]['secondary_exclusions']
                    secondary[reason] -= 1
                    if secondary[reason] == 0:
                        del secondary[reason]
    for (target, event_id), missing_fields in missing_fields_by_target_event.items():
        if not missing_fields:
            continue
        record = diagnostic_map.get(str(event_id))
        if record is None:
            continue
        details = record["targets"][target]
        reason = "incomplete_state" if state_fields.intersection(missing_fields) else "feature_fields_missing"
        additional = details.setdefault("additional_reasons", [])
        if reason not in additional:
            additional.append(reason)
            counts = aggregate["target_diagnostics"][target]["secondary_exclusions"]
            counts[reason] = counts.get(reason, 0) + 1
        details["missing_feature_fields"] = sorted(missing_fields)
        if details.get("status") == "feature_certified_candidate":
            details["status"] = reason
    aggregate["match_exclusions_manifest"] = _write_audit_jsonl(
        sorted(diagnostic_map.values(), key=lambda row: row["fonbet_event_id"]), str(final_dir),
        "tennis_match_target_diagnostics")
    for target in target_rows:
        counts = Counter(row['targets'][target]['status'] for row in diagnostic_map.values())
        aggregate['target_diagnostics'][target]['stage_exclusions'] = dict(counts)
    _atomic_json(output_path, aggregate)
    checkpoint.update(status="complete", completed_at=datetime.now(timezone.utc).isoformat(),
        final_report_path=str(output_path.resolve()),
        final_report_sha256=_sha256(output_path.resolve()))
    _atomic_json(root / "checkpoint.json", checkpoint)
    return aggregate
