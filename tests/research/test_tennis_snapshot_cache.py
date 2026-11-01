import json
from datetime import datetime,timezone,timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts/research'))
import tennis_snapshot_cache as cache


def test_export_resume_preserves_every_key_and_original_state(tmp_path,monkeypatch):
    at=datetime(2026,10,20,tzinfo=timezone.utc)
    event=UUID(int=10)
    def snapshot(i,event_id=event):
        value=dict(id=UUID(int=i),event_id=event_id,observed_at=at+timedelta(seconds=i),
                   status='live',score='0:0',current_period='1',raw_hash='a'*64,
                   raw_object_key='saved/raw.json',score_detail={'games':[0,1]},sport_state={'points_a':15})
        return SimpleNamespace(**value,_mapping=value)
    rows=[snapshot(1),snapshot(2,UUID(int=99)),snapshot(3)]
    controls={'fail':True,'queries':0}
    class Session:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def query(self,*args): return self
        def filter(self,*args): return self
        def all(self): return [SimpleNamespace(id=event,first_seen_at=at,created_at=at)]
        def execute(self,sql,params=None):
            if params is None: return None
            controls['queries']+=1
            if controls['fail'] and controls['queries']==2: raise TimeoutError('interruption')
            cursor=(params['cursor_time'],params['cursor_id']) if params['cursor_time'] else None
            selected=[r for r in rows if params['lower']<=r.observed_at<params['range_end']
                      and r.observed_at<=params['cutoff'] and (not cursor or (r.observed_at,r.id)>cursor)]
            return SimpleNamespace(all=lambda:selected[:params['page_size']])
    monkeypatch.setattr(cache,'SessionLocal',Session)
    resumed=tmp_path/'resumed.sqlite'
    cutoff=at+timedelta(days=1)
    with pytest.raises(TimeoutError):
        cache.prepare(resumed,[str(event)],cutoff=cutoff,page_size=1)
    assert cache.read_state(resumed)['saved_rows']==1
    assert not cache.read_state(resumed)['complete']
    with pytest.raises(ValueError,match='incomplete'): list(cache.iter_event(resumed,event))
    controls['fail']=False
    state=cache.prepare(resumed,[str(event)],cutoff=cutoff,page_size=1)
    assert state['source_rows']==3 and state['saved_rows']==2 and state['complete']
    continuous=tmp_path/'continuous.sqlite'
    assert cache.prepare(continuous,[str(event)],cutoff=cutoff,page_size=1)==state
    assert list(cache.iter_event(resumed,event))==list(cache.iter_event(continuous,event))
    payloads=list(cache.iter_event(resumed,event,payload=True))
    assert [r.id for r in payloads]==[UUID(int=1),UUID(int=3)]
    assert payloads[0].sport_state==rows[0].sport_state
    assert payloads[0].raw_hash==rows[0].raw_hash
    assert cache.prepare(resumed,[str(event)],cutoff=cutoff,page_size=1)==state
    with pytest.raises(ValueError,match='frozen_input_mismatch'):
        cache.prepare(resumed,[str(event)],cutoff=cutoff+timedelta(days=1))


def test_timeout_reduces_page_without_advancing_cursor(tmp_path,monkeypatch):
    from sqlalchemy.exc import OperationalError
    at=datetime(2026,10,20,tzinfo=timezone.utc)
    event=UUID(int=10)
    sizes=[]
    class QueryCanceled(Exception):
        pgcode='57014'
    class Session:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def query(self,*args): return self
        def filter(self,*args): return self
        def all(self): return [SimpleNamespace(id=event,first_seen_at=at,created_at=at)]
        def execute(self,sql,params=None):
            if params is None: return None
            sizes.append(params['page_size'])
            assert params['cursor_time'] is None
            if len(sizes)==1: raise OperationalError('query',{},QueryCanceled())
            return SimpleNamespace(all=lambda:[])
    monkeypatch.setattr(cache,'SessionLocal',Session)
    state=cache.prepare(tmp_path/'cache.sqlite',[str(event)],cutoff=at+timedelta(days=1),page_size=500)
    assert sizes==[500,250,500]
    assert state['complete'] and state['source_rows']==0 and state['cursor'] is None


def test_daily_boundaries_include_all_rows_once(tmp_path,monkeypatch):
    at=datetime(2026,10,20,23,59,59,tzinfo=timezone.utc)
    event=UUID(int=8)
    def row(i, timestamp):
        value=dict(id=UUID(int=i),event_id=event,observed_at=timestamp,status='live',
            score='0:0',current_period=1,raw_hash='a'*64,raw_object_key='raw',score_detail={},sport_state={})
        return SimpleNamespace(**value,_mapping=value)
    rows=[row(1,at),row(2,at+timedelta(seconds=1)),row(3,at+timedelta(seconds=1)),
          row(4,at+timedelta(days=1,seconds=1))]
    class Session:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def query(self,*args): return self
        def filter(self,*args): return self
        def all(self): return [SimpleNamespace(id=event,first_seen_at=at,created_at=at)]
        def execute(self,sql,params=None):
            if params is None: return None
            cursor=(params['cursor_time'],params['cursor_id']) if params['cursor_time'] else None
            selected=[r for r in rows if params['lower']<=r.observed_at<params['range_end']
                and r.observed_at<=params['cutoff'] and (not cursor or (r.observed_at,r.id)>cursor)]
            return SimpleNamespace(all=lambda:selected[:params['page_size']])
    monkeypatch.setattr(cache,'SessionLocal',Session)
    path=tmp_path/'boundary.sqlite'
    result=cache.prepare(path,[str(event)],cutoff=at+timedelta(days=1,seconds=1),page_size=1)
    assert result['saved_rows']==4
    assert [entry[0] for entry in cache.iter_event(path,event)]==[r.id for r in rows]
